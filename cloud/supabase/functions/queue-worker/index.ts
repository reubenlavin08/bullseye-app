// /queue-worker — drain the email_queue. Triggered hourly by pg_cron
// (see migrations/008_pg_cron.sql).
//
// Flow:
//   1. SELECT * FROM email_queue WHERE scheduled_for <= NOW() AND sent = FALSE
//   2. For each row: render + send via Resend
//   3. UPDATE email_queue SET sent=true, sent_at=NOW()
//   4. INSERT into email_log
//
// Auth: service-role only (pg_cron passes the service role key).
// Reject anything without it.

Deno.serve(async (req: Request) => {
    // const auth = req.headers.get('Authorization')
    // if (auth !== `Bearer ${getEnv('SUPABASE_SERVICE_ROLE_KEY')}`) return 401
    return new Response("not implemented", { status: 501 })
})
