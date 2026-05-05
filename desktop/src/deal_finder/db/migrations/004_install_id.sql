-- 004_install_id.sql — telemetry install_id slot in app_state.
--
-- The `app_state` key/value table already exists from migration 001.
-- We don't create the row here (we'd need a UUID at SQL time and we
-- want it generated lazily on first emit so test runs and
-- short-lived dev installs don't all share a single hard-coded id).
--
-- Instead, record the ROW for telemetry_opt_out with a default of 0
-- ("opt-in") so the first read always succeeds without a NULL check.
-- The Python layer (`cloud.telemetry.get_install_id`) inserts the
-- 'install_id' row on first call and reads it on every subsequent
-- call.

INSERT OR IGNORE INTO app_state (key, value)
VALUES ('telemetry_opt_out', '0');
