-- 014_category_hint.sql
--
-- Adds category_hint columns to both cache tables so the eBay
-- categoryId filter (Option 1 from the architecture review) can
-- traffic alongside canonical_kind without re-keying everything.
--
-- normalized_listings.category_hint:
--     The LLM's semantic label ("motorcycle", "car", "phone", ...).
--     Mapped to an eBay categoryId server-side before /comps queries
--     eBay. NULL for old rows; the edge function reads NULL as
--     "other" and skips the category filter (graceful degradation).
--
-- comps_cache.category_hint:
--     Pinned to the search that produced the row. Two queries with
--     the same canonical_kind but different hints (rare — would only
--     happen if the LLM disagrees) cache to different rows. Old
--     pre-014 rows have hint=NULL and only match callers that don't
--     pass a hint either.
--
-- Both columns are nullable. No backfill needed — the edge functions
-- treat NULL as "no hint" and the next pass through normalize re-
-- populates with a real hint.

ALTER TABLE normalized_listings ADD COLUMN IF NOT EXISTS category_hint TEXT;
ALTER TABLE comps_cache         ADD COLUMN IF NOT EXISTS category_hint TEXT;

-- Help analytics queries that group on hint (e.g. "comp size by
-- category"). Cheap; both tables stay small.
CREATE INDEX IF NOT EXISTS normalized_listings_hint_idx
    ON normalized_listings (category_hint);
CREATE INDEX IF NOT EXISTS comps_cache_hint_idx
    ON comps_cache (category_hint);

COMMENT ON COLUMN normalized_listings.category_hint IS
    'Semantic category from LLM (motorcycle/car/phone/...). Maps to '
    'eBay categoryId in HINT_TO_EBAY_CAT (cloud/_shared/ebay.ts). '
    'NULL = old row, treated as "other".';
COMMENT ON COLUMN comps_cache.category_hint IS
    'Hint that produced this comp set. Cache row only matches on '
    'subsequent calls with the same hint.';
