-- 015_rate_limits.sql
--
-- Per-IP rate-limit ledger for the anonymous /comps endpoint.
--
-- The endpoint is intentionally anon-callable so the public landing
-- page's Test Appraiser works without sign-in. Without a rate limit
-- a single attacker can drain the daily eBay quota (~5k/day) by
-- hammering unique search terms that bypass the comps_cache.
--
-- The /comps function records one row per cache MISS keyed by IP and
-- gates on > 60 misses in the last 10 minutes. Cache HITS are not
-- recorded — abuse-cost remains asymmetric (cheap for us, expensive
-- for the attacker).

CREATE TABLE IF NOT EXISTS comps_rate_limit (
    id              BIGSERIAL PRIMARY KEY,
    caller_ip       TEXT NOT NULL,
    search_term     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for the rate-limit count query (caller_ip + created_at).
CREATE INDEX IF NOT EXISTS idx_comps_rate_limit_caller_at
    ON comps_rate_limit (caller_ip, created_at DESC);

-- Optional auto-cleanup: drop rows older than 24h via a scheduled
-- job. We don't strictly need to keep history beyond the rate-limit
-- window. If pg_cron isn't enabled, manual `DELETE` from the dash is
-- fine — the table stays small under normal traffic anyway.
