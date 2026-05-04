// /stripe-webhook — handle Stripe lifecycle events.
//
// CRITICAL: signature verification is MANDATORY. The endpoint accepts
// no JWT auth (Stripe calls it from their own servers); the only
// thing keeping fakes out is the signing secret check.
//
// Events handled:
//   checkout.session.completed       link customer_id <-> user_id
//   customer.subscription.created    set tier=paid|trial, set period_end
//   customer.subscription.updated    sync period_end, cancel_at_period_end
//   customer.subscription.deleted    set tier=free
//   invoice.payment_failed           log + send warning email
//
// Stripe sends events at-least-once and may retry on 5xx. Make every
// handler idempotent (UPSERT, not INSERT).

Deno.serve(async (req: Request) => {
    // const signature = req.headers.get('stripe-signature')
    // const body = await req.text()
    // const event = await verifyWebhookSignature(body, signature!)
    // switch (event.type) { ... }
    return new Response("not implemented", { status: 501 })
})
