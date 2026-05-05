-- 010_telemetry_grant.sql — let authenticated + anon insert telemetry rows.
--
-- The RLS policy on telemetry_events from 005 + 007 is
--   USING/CHECK (auth.uid() = user_id OR user_id IS NULL)
-- which permits anonymous (user_id = NULL) rows. But RLS only kicks
-- in *after* the table-level GRANT check; without an explicit
-- INSERT grant, a non-service-role insert returns 42501 / 403 even
-- when RLS would have allowed it.
--
-- The /telemetry edge function uses the service-role admin client
-- (which bypasses both GRANTs and RLS), so this isn't strictly
-- necessary for the production path. But:
--   1. Direct REST inserts from clients (e.g. for ops debugging or
--      a future browser-side telemetry pixel) would otherwise fail
--      silently with a confusing 403.
--   2. The adversarial-RLS findings flagged this as a missing
--      grant; making it explicit prevents future regressions when
--      someone tightens default privileges.

GRANT INSERT ON telemetry_events TO authenticated, anon;

-- The id column is BIGSERIAL — postgres automatically grants USAGE
-- on the underlying sequence to PUBLIC unless that's been revoked.
-- Add it explicitly to be safe.
GRANT USAGE, SELECT ON SEQUENCE telemetry_events_id_seq TO authenticated, anon;
