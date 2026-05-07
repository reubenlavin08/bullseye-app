-- Referrals — "Give a month, get a month" growth loop.
--
-- Wispr Flow's biggest organic-growth mechanism. Each user gets a
-- unique short referral_code at signup. When user B signs up via a
-- /referral-claim with user A's code:
--   1. A row is inserted into `referrals` linking A (referrer) to B
--      (referee), status='pending'.
--   2. When B activates a paid subscription (or maintains trial+free
--      usage past a threshold), the row flips to status='earned' and
--      both A and B get a 1-month Pro coupon credit applied to their
--      next Stripe invoice.
--
-- The coupon application is handled out-of-band by the stripe-webhook
-- function (when it sees an invoice for a user whose `referrals` has
-- pending earned rewards, it applies the discount). Keeping the
-- accounting in our DB rather than Stripe metadata gives us a clean
-- audit trail for "how much free Pro have we given away?"
--
-- Cap: a user can earn UNLIMITED months by referring more people, but
-- a single referee only earns ONE month for the referrer (no chain
-- referrals or multi-claim).

-- 1. Per-user code on licenses.
ALTER TABLE licenses
    ADD COLUMN IF NOT EXISTS referral_code TEXT UNIQUE,
    ADD COLUMN IF NOT EXISTS referred_by_user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS referral_months_earned INTEGER NOT NULL DEFAULT 0;

-- Index for fast lookup of "who owns code XYZ".
CREATE INDEX IF NOT EXISTS idx_licenses_referral_code
    ON licenses(referral_code) WHERE referral_code IS NOT NULL;

-- 2. The referral ledger.
CREATE TABLE IF NOT EXISTS referrals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    referee_user_id  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    -- 'pending'  — referee signed up with code, hasn't activated yet
    -- 'earned'   — referee subscribed; both sides get a month
    -- 'voided'   — referee deleted account / refunded / chargeback
    status TEXT NOT NULL CHECK (status IN ('pending', 'earned', 'voided')),
    earned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- One referral per (referrer, referee) pair. Stops a user from
    -- multi-claiming the same friend by signing them up twice.
    UNIQUE (referrer_user_id, referee_user_id),
    -- A given user can only BE referred once (their first signup
    -- attribution wins). Prevents double-attribution gaming.
    UNIQUE (referee_user_id)
);

CREATE INDEX IF NOT EXISTS idx_referrals_referrer
    ON referrals(referrer_user_id, status);

-- RLS: users see only their own referrals (sent OR received).
ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users see their referrals"
    ON referrals FOR SELECT
    USING (auth.uid() = referrer_user_id OR auth.uid() = referee_user_id);

-- No INSERT/UPDATE/DELETE policies — only the service role (cloud
-- functions with admin client) can write. Keeps users from forging
-- earned rewards.

-- 3. Backfill referral codes for existing licenses. The code is a
-- short URL-safe string derived from user_id; deterministic so the
-- same user always has the same code. Format: 8 chars, base32.
UPDATE licenses
SET referral_code = UPPER(SUBSTR(REPLACE(REPLACE(REPLACE(
    encode(decode(REPLACE(user_id::text, '-', ''), 'hex'), 'base64'),
    '+', ''), '/', ''), '=', ''), 1, 8))
WHERE referral_code IS NULL;
