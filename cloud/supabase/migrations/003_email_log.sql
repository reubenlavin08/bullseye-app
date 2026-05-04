-- Email log — record of every email we sent. Used for:
--   1. Free-tier "have we sent the daily digest yet?" idempotency check
--   2. Resend daily-budget tracking
--   3. User-visible "your email history" page (future)

CREATE TABLE IF NOT EXISTS email_log (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    email_type TEXT NOT NULL CHECK (email_type IN ('digest', 'instant', 'welcome', 'payment_failed')),
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    -- Denormalized date for the partial unique index below. Postgres
    -- requires partial-index expressions to be IMMUTABLE; `sent_at::date`
    -- is STABLE (depends on session timezone), so we can't use it
    -- directly. Storing the date explicitly at insert time sidesteps
    -- this. CURRENT_DATE in a column DEFAULT is fine — it's evaluated
    -- once per insert, not per index lookup.
    sent_date DATE NOT NULL DEFAULT CURRENT_DATE,
    match_count INT,
    resend_message_id TEXT      -- for delivery tracking via Resend webhooks (future)
);

CREATE INDEX IF NOT EXISTS idx_email_log_user_date ON email_log(user_id, sent_at DESC);

-- Enforce idempotency: at most one digest per user per day.
-- The partial unique index uses sent_date (a real DATE column), which
-- IS immutable from the index's perspective.
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_digest_per_day
    ON email_log(user_id, sent_date)
    WHERE email_type = 'digest';
