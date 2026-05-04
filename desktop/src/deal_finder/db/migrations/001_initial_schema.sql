-- Initial SQLite schema for the desktop app.
--
-- Roughly mirrors the Postgres schema in the personal tool with type
-- translations: SERIAL -> INTEGER PRIMARY KEY AUTOINCREMENT, JSONB -> TEXT,
-- TIMESTAMPTZ -> TEXT (ISO 8601 strings), ARRAY -> TEXT (JSON-encoded).

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- user_searches: the watches the user is monitoring
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    must_include TEXT,           -- JSON array
    must_exclude TEXT,           -- JSON array
    latitude REAL,
    longitude REAL,
    radius_km INTEGER,
    price_min INTEGER,
    price_max INTEGER,
    active INTEGER DEFAULT 1,    -- SQLite has no BOOLEAN
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- listings: every scraped item with its appraisal results
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    search_id INTEGER REFERENCES user_searches(id) ON DELETE SET NULL,
    title TEXT,
    price REAL,
    raw_price REAL,
    price_extracted_from_description INTEGER DEFAULT 0,
    previous_price TEXT,
    is_pending INTEGER DEFAULT 0,
    photo_url TEXT,
    seller_name TEXT,
    seller_location TEXT,
    seller_type TEXT,
    description TEXT,
    listing_url TEXT,
    category_id TEXT,
    listed_at TEXT,
    scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
    detail_source TEXT,
    detail_latitude REAL,
    detail_longitude REAL,
    rejected INTEGER DEFAULT 0,
    rejection_reason TEXT,
    appraised INTEGER DEFAULT 0,
    appraised_at TEXT,
    deal_score INTEGER,
    fair_value REAL,
    appraisal_note TEXT,
    appraisal_breakdown TEXT,    -- JSON
    comp_sample_size INTEGER,
    comp_median REAL,
    comp_search_term TEXT,
    comp_source TEXT,
    notified INTEGER DEFAULT 0,
    notified_at TEXT,
    summarized_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_search ON listings(search_id);
CREATE INDEX IF NOT EXISTS idx_listings_scraped ON listings(scraped_at);
CREATE INDEX IF NOT EXISTS idx_listings_appraised ON listings(appraised, deal_score);
CREATE INDEX IF NOT EXISTS idx_listings_notified ON listings(notified, notified_at);

-- ---------------------------------------------------------------------------
-- scheduler_events: append-only audit log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduler_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    detail TEXT,                 -- JSON
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_type ON scheduler_events(event_type, created_at);

-- ---------------------------------------------------------------------------
-- comps + comps_meta: local fallback cache when cloud is offline
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_term TEXT NOT NULL,
    source TEXT NOT NULL,
    price REAL,
    title TEXT,
    listing_url TEXT,
    location TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comps_term_source ON comps(search_term, source);

CREATE TABLE IF NOT EXISTS comps_meta (
    search_term TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    sample_size INTEGER,
    PRIMARY KEY (search_term, source)
);

-- ---------------------------------------------------------------------------
-- city_geocache: Nominatim cache for city -> lat/lng
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS city_geocache (
    label TEXT PRIMARY KEY,
    latitude REAL,
    longitude REAL,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- app_state: misc. key-value store (install_id, cached_license, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
