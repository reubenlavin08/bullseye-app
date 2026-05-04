-- Telemetry events — anonymous opt-out usage tracking.
-- Feeds the v1.1 gamification engine (streaks, unlocks, badges).

CREATE TABLE IF NOT EXISTS telemetry_events (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    install_id TEXT,            -- per-installation UUID; survives logout
    event_name TEXT NOT NULL,
    properties JSONB,
    app_version TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telemetry_user_date
    ON telemetry_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_event
    ON telemetry_events(event_name, created_at DESC);
