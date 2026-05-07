-- 016_trial_email_history.sql
--
-- Persistent record of which (normalized) email addresses have ever
-- redeemed a free trial. Survives account deletion — the obvious
-- exploit otherwise: delete account → re-signup with same email →
-- get another free trial → repeat.
--
-- We store a SHA-256 hash of the normalized email rather than the
-- raw email so the table is GDPR-friendly: we can prove an email
-- has been seen before without retaining the email itself.
--
-- Normalization (mirrors trial-start/index.ts:normalizeEmail):
--     - lowercase + trim
--     - for gmail/googlemail/outlook/hotmail/live: strip `+suffix`
--       and `.` from local-part (the four providers that treat dots
--       and pluses as aliases)
-- This means `me+1@gmail.com` and `m.e@gmail.com` and `me@gmail.com`
-- all hash to the same value — one trial across all of them.
--
-- This table is intentionally NOT linked to auth.users (no FK), so
-- deleting an auth.users row does not delete the history record.

CREATE TABLE IF NOT EXISTS trial_email_history (
    -- SHA-256 hex of the normalized email. 64 chars.
    email_hash TEXT PRIMARY KEY,
    -- When the trial was first granted to this email. Informational
    -- only; the row's existence is what blocks re-redemption.
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Optional: which auth.users id originally redeemed it. Useful
    -- for support ("who did this hash belong to?") but the user_id
    -- may no longer exist if the account was deleted. Stored as TEXT
    -- (not UUID FK) so we don't accidentally re-introduce a cascade.
    original_user_id TEXT
);

-- RLS: blocklist is only ever read/written by Edge Functions running
-- as service-role. Lock down all client access.
ALTER TABLE trial_email_history ENABLE ROW LEVEL SECURITY;
-- No policies = no client access. Service role bypasses RLS.
