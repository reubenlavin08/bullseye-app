-- 007_force_account_reset.sql
--
-- One-time hard reset of user-scoped local data, applied when an
-- existing install upgrades to the version that ships the
-- account_switch.handle_sign_in() guard.
--
-- Why this is necessary:
--
--     The desktop SQLite schema is account-blind — no `user_id`
--     column on user_searches/listings/etc. The account_switch fix
--     in handle_sign_in() compares the JWT user_id to a stored
--     `last_user_id` in app_state and wipes user data on a mismatch.
--     Existing installs (pre-fix) have NO `last_user_id` row, so the
--     "first sign-in after upgrade" path treats it as a fresh DB and
--     skips the wipe — meaning user A's pre-fix data persists across
--     a switch to user B. (Bug found 2026-05-07: "my two accounts
--     are still sharing the same information.")
--
-- This migration runs ONCE per local DB on first startup of the
-- post-fix app build. After it runs, the regular handle_sign_in
-- guard takes over and per-account isolation works as designed
-- (every future switch produces a clean wipe).
--
-- Cost: the user loses their pre-existing watches and scored finds
-- on this one upgrade. Per-watch settings and notification prefs
-- also reset. The trade-off is correct because data leakage between
-- accounts is a significant security/privacy issue and we don't
-- have a per-row way to attribute existing rows to the right user.

DELETE FROM user_searches;
DELETE FROM listings;
DELETE FROM scheduler_events;
DELETE FROM subscribers;
DELETE FROM user_settings;

-- Stamp app_state so handle_sign_in's first-run path knows the DB
-- has been freshly reset and can correctly identify the next sign-in
-- as the new owner. Sentinel value is intentionally non-UUID so any
-- real user_id will differ and trigger the per-signin wipe path —
-- belt and suspenders on top of this one-shot DELETE.
INSERT OR REPLACE INTO app_state (key, value)
VALUES ('last_user_id', '_post_migration_reset_');
