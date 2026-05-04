-- Pending email queue — handles overflow:
--   * Free-tier: extra matches that arrive after today's digest sent get
--     queued for tomorrow's 8am-local slot.
--   * Paid-tier: if Resend's daily limit is hit (rare), instant emails
--     queue and drain when the limit resets.
-- Drained by `/queue-worker` Edge Function on a pg_cron schedule.

CREATE TABLE IF NOT EXISTS email_queue (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    email_type TEXT NOT NULL,
    matches_json JSONB NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    sent BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Partial index — only the still-pending rows, which is what the
-- worker queries.
CREATE INDEX IF NOT EXISTS idx_queue_pending
    ON email_queue(scheduled_for)
    WHERE sent = FALSE;
