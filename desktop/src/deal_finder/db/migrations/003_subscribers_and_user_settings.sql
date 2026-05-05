-- subscribers + user_settings — needed by the webapp port from the
-- personal tool. The desktop app is single-user (one row in
-- user_settings, one subscriber per watch by convention) but we keep
-- the multi-row schema for compatibility with the existing query
-- shapes ported from deal_finder/webapp/app.py.

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT NOT NULL,
    phone TEXT,
    search_id INTEGER NOT NULL REFERENCES user_searches(id) ON DELETE CASCADE,
    score_threshold INTEGER DEFAULT 70,
    daily_summary_enabled INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (email, search_id)
);

CREATE INDEX IF NOT EXISTS idx_subscribers_search ON subscribers(search_id);

-- user_settings: single-user home location + future preferences. The
-- Postgres version had `user_id INT PRIMARY KEY DEFAULT 1`; we keep
-- the same shape so ported queries work unchanged.
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY DEFAULT 1,
    home_label TEXT,
    home_latitude REAL,
    home_longitude REAL,
    telemetry_opt_out INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
