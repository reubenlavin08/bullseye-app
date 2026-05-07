-- Phase 3: granular notification preferences.
--
-- Store per-user toggles for the local toasts the desktop fires. Wispr
-- Flow has the same idea in Settings → Notifications: per-category mute
-- so a user can silence milestone confetti without losing error toasts.
--
-- Email-level mute (digest/instant) is NOT in this migration. Those
-- emails are sent by the cloud, not the desktop, so muting them
-- requires a column on the cloud's `licenses` table + a check in the
-- alerts-send Edge Function. Tracked as Phase 3.5 follow-up.
--
-- All defaults = 1 (on). Existing rows get the default via SQLite's
-- ALTER TABLE … ADD COLUMN semantics; new rows get the same.

ALTER TABLE user_settings ADD COLUMN notif_milestones INTEGER DEFAULT 1;
ALTER TABLE user_settings ADD COLUMN notif_first_deal INTEGER DEFAULT 1;
ALTER TABLE user_settings ADD COLUMN notif_kill_switch_banner INTEGER DEFAULT 1;
