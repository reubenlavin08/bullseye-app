-- The shared comp cache — THE moat.
-- One row per (normalized_search_term, region). All users share it.
-- 12-hour TTL: cache hit when fetched_at > NOW() - INTERVAL '12 hours'.

CREATE TABLE IF NOT EXISTS comps_cache (
    search_term_normalized TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'EBAY-ENCA',
    stats_json JSONB NOT NULL,        -- {median, mean, min, max, p10, q1, q3, p90, sample_size, ...}
    raw_comps_json JSONB,             -- the actual comp listings (for the comp drawer)
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (search_term_normalized, region)
);

CREATE INDEX IF NOT EXISTS idx_comps_freshness ON comps_cache(fetched_at);
