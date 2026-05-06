-- 006_global_notification_toggles.sql
--
-- Two account-wide notification master toggles. Replaces the per-watch
-- `email` column workflow with a single global switch in Settings →
-- Notifications.
--
-- Rules:
--   - notif_email_global   : Pro-tier only. Defaults ON. Free users
--                            never receive email regardless of this
--                            value (cloud alerts-send checks tier
--                            before sending).
--   - notif_desktop_global : All tiers. Defaults ON. Master switch
--                            above the granular flags (notif_milestones,
--                            notif_first_deal). When OFF, the local
--                            toast layer suppresses everything.
--
-- Existing per-watch `email` column is left in place for one release
-- so we don't break legacy data; it's no longer read by the alert
-- pipeline (the cloud alerts-send function now reads
-- notif_email_global for routing).

ALTER TABLE user_settings ADD COLUMN notif_email_global INTEGER DEFAULT 1;
ALTER TABLE user_settings ADD COLUMN notif_desktop_global INTEGER DEFAULT 1;
