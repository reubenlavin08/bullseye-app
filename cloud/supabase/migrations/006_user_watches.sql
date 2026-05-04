-- Cloud copy of the user's watch list. Two purposes:
--   1. Backup — if the user reinstalls Windows or buys a new laptop,
--      they restore their watches on first login.
--   2. (Future) cross-device sync — when Mac/mobile clients exist.
--
-- The desktop app stays the source of truth during normal operation;
-- it pushes nightly snapshots up here. Conflict resolution (Phase 2.6
-- when sync becomes a real feature) will use updated_at LWW.

CREATE TABLE IF NOT EXISTS user_watches (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    must_include TEXT[],
    must_exclude TEXT[],
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    radius_km INT,
    price_min INT,
    price_max INT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watches_user ON user_watches(user_id);
