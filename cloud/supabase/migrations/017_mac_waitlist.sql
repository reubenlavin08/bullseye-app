-- 017_mac_waitlist.sql
--
-- Email capture for users waiting on the macOS build. Lives in its
-- own tiny table rather than re-using auth.users because:
--   - Most signups here will NOT create a Bullseye account; they're
--     just leaving an email until the dmg ships.
--   - We want to dump emails into Resend / a CSV without touching
--     Auth.
--   - Lifecycle is independent: when we ship Mac, we batch-email
--     everyone here once and can wipe the table.
--
-- Idempotent: same normalized email twice = no-op via UNIQUE +
-- ON CONFLICT DO NOTHING in the edge function. No PII beyond the
-- email itself, which the user volunteered.

CREATE TABLE IF NOT EXISTS mac_waitlist (
    id            BIGSERIAL PRIMARY KEY,
    -- Lowercased + trimmed email. UNIQUE so the edge function can
    -- safely upsert without dup-key races.
    email         TEXT NOT NULL UNIQUE,
    -- Where on the site they signed up: "download" / "landing" /
    -- "footer". Lets us see which placement converts.
    source        TEXT,
    -- IP address — only kept for spam/abuse triage. Null when
    -- the proxy didn't pass an IP through.
    ip_address    INET,
    -- User-Agent — same purpose as ip_address. Truncated to 256
    -- chars before write.
    user_agent    TEXT,
    -- When they joined the list. Set server-side; client can't
    -- forge backdated entries.
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Set to a non-null timestamp once we email them the dmg.
    -- Prevents double-emailing on a re-run of the launch blast.
    notified_at   TIMESTAMPTZ
);

-- Index for the launch query: "give me everyone who hasn't been
-- notified, oldest first."
CREATE INDEX IF NOT EXISTS mac_waitlist_unnotified_idx
    ON mac_waitlist (created_at)
    WHERE notified_at IS NULL;

-- RLS: client never reads or writes this directly. The /mac-waitlist
-- edge function uses the service role and validates inputs.
ALTER TABLE mac_waitlist ENABLE ROW LEVEL SECURITY;
-- No policies = no client access. Service role bypasses RLS.
