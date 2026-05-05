-- Streaks + Pro-day banking — v1.1 retention engine.
--
-- Rationale:
--   The personal tool's audience is hobbyists; the productized v1 needs
--   a sticky daily-use loop. Streaks give that loop a feedback signal,
--   and "Pro days banked" turns the streak into a ladder that converts
--   free users into paid ones (a 7-day free trial worth of Pro features
--   redeemable from the desktop app at the click of a button).
--
-- One row per user. Updated by the /streak Edge Function on every
-- daily-active check. Writes are service-role-only — clients can SELECT
-- their own row but cannot mutate (otherwise a determined user could
-- pad their streak by writing rows directly).

CREATE TABLE IF NOT EXISTS user_streaks (
    user_id            UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    current_streak     INT NOT NULL DEFAULT 0,
    longest_streak     INT NOT NULL DEFAULT 0,
    last_active_date   DATE,
    -- "Streak freeze" — one free skip per month so a sick day or a
    -- weekend trip doesn't break a 90-day streak. We track only the
    -- month it was used in; the next month a fresh freeze becomes
    -- available automatically.
    freeze_used_month  TEXT,                -- 'YYYY-MM' or null
    -- Pro-day banking. Earned through engagement milestones, redeemable
    -- for a free 7-day trial-equivalent. Cap enforced at award time
    -- (max 14 banked at once, which is two redemptions).
    pro_days_banked    INT NOT NULL DEFAULT 0,
    pro_days_lifetime  INT NOT NULL DEFAULT 0,  -- ever earned (analytics)
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

-- Partial index — we only ever query "active streaks of length >= 7"
-- for analytics and milestone notifications. Keeping it partial keeps
-- the index tiny relative to the full users table.
CREATE INDEX IF NOT EXISTS idx_streaks_active
    ON user_streaks(last_active_date DESC) WHERE current_streak >= 7;

ALTER TABLE user_streaks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users see own streak"
    ON user_streaks FOR SELECT
    USING (auth.uid() = user_id);
-- Writes only via service-role from Edge Functions (no client policy).
-- This is intentional — letting clients UPDATE would let a determined
-- user pad their streak by replaying the request with a fake date.


-- Track unlocks separately so we can fire one-shot rewards. Each
-- (user_id, milestone) is unique; the Edge Function tries to insert
-- once and inspects the affected-rows count to decide whether to
-- award Pro days (idempotent — calling /streak twice on the day a
-- milestone is reached awards the bonus exactly once).
CREATE TABLE IF NOT EXISTS user_unlocks (
    id            BIGSERIAL PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    milestone     TEXT NOT NULL,
    earned_at     TIMESTAMPTZ DEFAULT NOW(),
    pro_days_awarded INT DEFAULT 0,
    UNIQUE (user_id, milestone)
);

CREATE INDEX IF NOT EXISTS idx_unlocks_user
    ON user_unlocks(user_id, earned_at DESC);

ALTER TABLE user_unlocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users see own unlocks"
    ON user_unlocks FOR SELECT
    USING (auth.uid() = user_id);

-- Grants for the postgres role (used by triggers and service-role
-- writes) — mirror the pattern from migration 008.
GRANT SELECT, INSERT, UPDATE ON public.user_streaks TO postgres;
GRANT SELECT, INSERT, UPDATE ON public.user_unlocks TO postgres;
GRANT USAGE, SELECT ON SEQUENCE public.user_unlocks_id_seq TO postgres;
