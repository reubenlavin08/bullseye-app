// /alerts-send — render + send email via Resend, with tier rules.
//
// Tier behavior:
//   free: at most 1 digest/day per user. Idempotency enforced by the
//         partial unique index `uq_one_digest_per_day`. If today's
//         digest already sent, push the matches to email_queue for
//         tomorrow's 8am-local slot.
//   paid: instant emails with 60s batching (the desktop side handles
//         the hold; this function just sends what it's given).
//         Resend daily-limit check: if over budget, queue.
//
// Flow:
//   1. requireUser
//   2. Read { matches, type } from body
//   3. Get user license + email
//   4. Branch on tier + idempotency check
//   5. Render HTML+text via _shared/resend.ts
//   6. Send via Resend
//   7. INSERT INTO email_log
//   8. Return { sent: bool, queued: bool, count }

Deno.serve(async (req: Request) => {
    return new Response("not implemented", { status: 501 })
})
