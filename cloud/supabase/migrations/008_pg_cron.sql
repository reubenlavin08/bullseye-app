-- pg_cron schedules.
--
-- pg_cron is built into Supabase. Schedules survive across migrations.
-- This migration is idempotent (UNSCHEDULE before re-creating).
--
-- v0.1: only the trial-expiry sweeper. The email-queue worker cron
-- needs to call out to an Edge Function via pg_net + the service-role
-- key, which we can't safely embed in a committed SQL file. That cron
-- gets wired up at step 7 (alerts) — for now, the email_queue table
-- exists but isn't auto-drained. Manual drain via direct function
-- invocation works for testing.

-- Trial-expiry sweeper — runs daily, downgrades any 'trial' tier
-- whose trial_ends_at has passed. Pure SQL, no external calls, safe
-- to commit.
SELECT cron.unschedule('trial-expiry-sweep')
    WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'trial-expiry-sweep');

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
