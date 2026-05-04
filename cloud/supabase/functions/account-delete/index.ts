// /account-delete — GDPR/PIPEDA cascade delete.
//
// Flow:
//   1. requireUser
//   2. (Optional) confirm via password / OAuth re-auth
//   3. Cancel any active Stripe subscription
//   4. DELETE FROM auth.users WHERE id = user_id
//      (RLS cascades wipe licenses, user_watches, email_log, email_queue,
//      telemetry_events via FK ON DELETE CASCADE)
//   5. Return { ok: true }
//
// Client clears keyring tokens + local SQLite + exits the app.

Deno.serve(async (req: Request) => {
    return new Response("not implemented", { status: 501 })
})
