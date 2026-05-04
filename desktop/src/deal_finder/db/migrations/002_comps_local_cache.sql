-- Local mirror of the cloud comps cache. Last-known-good fallback for
-- when the cloud /comps function is unreachable (transient network
-- failure, Supabase incident, user offline).
--
-- Different shape from the personal-tool's `comps` + `comps_meta`
-- tables: those store per-row comp observations from direct eBay/FB
-- queries; this stores the entire JSON response (stats + raw items)
-- the cloud function returned. Keeps the cache layers independent so
-- one can be rebuilt without rebuilding the other.

CREATE TABLE IF NOT EXISTS comps_local_cache (
    search_term TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT 'EBAY-ENCA',
    stats_json  TEXT NOT NULL,                       -- JSON: {median, mean, ...}
    raw_comps_json TEXT,                              -- JSON: [{title, price, ...}, ...]
    fetched_at REAL NOT NULL,                         -- unix epoch seconds (Python time.time())
    PRIMARY KEY (search_term, region)
);

CREATE INDEX IF NOT EXISTS idx_comps_local_freshness
    ON comps_local_cache(fetched_at);
