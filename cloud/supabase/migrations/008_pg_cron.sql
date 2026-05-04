-- pg_cron schedules — triggered hourly, the function decides what
-- needs draining based on scheduled_for timestamps.
--
-- pg_cron is built into Supabase (extension is auto-installed).
-- Schedules persist across migrations; UNSCHEDULE before re-creating
-- so this migration is idempotent.

-- Drain the email_queue every hour. The Edge Function reads
-- scheduled_for <= NOW() and processes user-by-user, accounting for
-- per-user timezone (free tier sends at 8am LOCAL, not 8am UTC).
SELECT cron.unschedule('queue-worker') WHERE EXISTS (
    SELECT 1 FROM cron.job WHERE jobname = 'queue-worker'
);

SELECT cron.schedule(
    'queue-worker',
    '0 * * * *',                                 -- top of every hour
    $$
        SELECT net.http_post(
            url := current_setting('app.settings.supabase_url') || '/functions/v1/queue-worker',
            headers := jsonb_build_object(
                'Authorization', 'Bearer ' || current_setting('app.settings.service_role_key'),
                'Content-Type', 'application/json'
            ),
            body := '{}'::jsonb
        );
    $$
);

-- Trial-expiry sweeper — runs daily, downgrades any 'trial' tier
-- whose trial_ends_at has passed.
SELECT cron.unschedule('trial-expiry-sweep') WHERE EXISTS (
    SELECT 1 FROM cron.job WHERE jobname = 'trial-expiry-sweep'
);

SELECT cron.schedule(
    'trial-expiry-sweep',
    '15 0 * * *',                                -- 00:15 UTC daily
    $$
        UPDATE licenses
        SET tier = 'free',
            updated_at = NOW()
        WHERE tier = 'trial' AND trial_ends_at < NOW();
    $$
);
