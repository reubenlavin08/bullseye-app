-- 013_normalized_listings.sql
--
-- Cache table for the LLM-normalized form of a Marketplace listing.
-- Each row is one listing keyed on its source URL (which is stable for
-- the life of the listing — Marketplace doesn't reassign URLs).
--
-- Why a cache table at all: the MiniMax normalize call costs real
-- money per listing (~$0.0002 batched). The same listing gets re-seen
-- every time a user re-appraises it OR every time a watch poll
-- re-encounters it before it's deleted. Without a cache we'd pay for
-- the same normalization repeatedly.
--
-- Why keyed on listing_url and not (user_id, listing_url): the
-- normalization is purely a function of the listing content, not the
-- user. Two users seeing the same listing should hit the same cache
-- row. (User-specific scoring — the comp lookup, the per-user score
-- band — happens downstream, NOT here.)
--
-- prompt_version is a manual-bump column. When we materially change
-- the system prompt or model, bump the version constant in
-- appraise-normalize/index.ts. Old rows naturally cease to be
-- consulted because the cache check filters on prompt_version =
-- current. Old data stays for analytics / cost accounting.

CREATE TABLE IF NOT EXISTS normalized_listings (
    -- Primary key: the Marketplace listing URL. Includes the leading
    -- "https://www.facebook.com/marketplace/item/" so we don't have
    -- to know the prefix at lookup time.
    listing_url TEXT PRIMARY KEY,

    -- Output fields from the LLM normalize call.
    canonical_kind  TEXT NOT NULL,           -- e.g. "iPhone 12 64GB"
    coarse_low      INT  NOT NULL,           -- low end of expected range, CAD
    coarse_high     INT  NOT NULL,           -- high end of expected range, CAD
    confidence      TEXT NOT NULL            -- 'low' | 'medium' | 'high'
                    CHECK (confidence IN ('low', 'medium', 'high')),
    worth_deep      BOOLEAN NOT NULL,        -- false = junk/WTB/services/empty
    red_flags       JSONB NOT NULL DEFAULT '[]'::jsonb,
    reasoning       TEXT,                    -- LLM's explanation, mostly for debugging

    -- Provenance.
    prompt_version  INT  NOT NULL,           -- bumped manually when prompt changes
    model           TEXT NOT NULL,           -- e.g. "MiniMax-Text-01"
    normalized_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- The raw input the LLM saw. Useful when the LLM produces a
    -- weird canonical_kind and we need to reproduce. Kept short
    -- (truncated to 1000 chars on insert) to bound row size.
    input_title     TEXT,
    input_body      TEXT,
    input_ask       NUMERIC
);

-- Most queries are point lookups by URL (fast on PK). The two
-- supporting indexes are for analytics:
--   - by canonical_kind: "how many listings normalized to iPhone 12?"
--   - by normalized_at:  "show me last 24h of normalizations"
CREATE INDEX IF NOT EXISTS normalized_listings_kind_idx
    ON normalized_listings (canonical_kind);
CREATE INDEX IF NOT EXISTS normalized_listings_normalized_at_idx
    ON normalized_listings (normalized_at DESC);

-- The function reads + writes via the service role; no RLS policies
-- needed (RLS only applies to anon / authed JWTs, not service-role).
-- We deliberately do NOT expose this table to PostgREST clients —
-- everything goes through the edge function.

COMMENT ON TABLE normalized_listings IS
    'Cache of LLM-normalized canonical product names per listing URL. '
    'Populated by appraise-normalize edge function. Read by /appraise + '
    'watch-poll path before comp lookup, so two listings with different '
    'raw titles but the same canonical_kind hit the same comp set.';
