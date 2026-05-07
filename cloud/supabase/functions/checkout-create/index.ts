// /checkout-create — start a Stripe Checkout subscription session.
//
// Body: { plan: 'monthly'|'yearly', trial: boolean }
// Returns: { url: 'https://checkout.stripe.com/...' }
//
// Flow:
//   1. requireUser
//   2. Look up user's existing stripe_customer_id (null on first call)
//   3. Build a Checkout session:
//        - subscription mode
//        - 7-day trial if requested
//        - success_url + cancel_url
//        - client_reference_id = user_id  (so webhook can link)
//   4. Return URL; client redirects to it
//
// Why we don't pre-create the customer here: Stripe Checkout will
// create one for us if we pass customer_email instead of customer.
// One fewer API call, one fewer thing that can go wrong. The webhook
// catches the new customer_id when checkout.session.completed fires
// and links it back to the user's licenses row.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import { createCheckoutSession } from "../_shared/stripe.ts"

const SUCCESS_URL = "https://getbullseye.app/upgrade-success?session_id={CHECKOUT_SESSION_ID}"
const CANCEL_URL = "https://getbullseye.app/pricing"

interface Body {
    plan?: "monthly" | "yearly"
    trial?: boolean
}

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST") {
        return errorResponse("method not allowed", 405)
    }

    let user
    try {
        user = await requireUser(req)
    } catch (r) {
        if (r instanceof Response) return r
        throw r
    }

    let body: Body
    try {
        body = await req.json()
    } catch {
        return errorResponse("invalid JSON body", 400)
    }
    const plan = body.plan === "yearly" ? "yearly" : "monthly"
    let trial = body.trial !== false  // default true; may be coerced false below

    // Look up existing license + customer linkage. We need:
    //   - stripe_customer_id (re-use if present so payment-method history stays)
    //   - trial_ends_at      (was a trial ever started? if yes, no fresh trial)
    //   - tier               (already paid → no trial; already mid-trial → 400)
    const db = adminClient()
    const { data: existing } = await db
        .from("licenses")
        .select("stripe_customer_id, trial_ends_at, tier, current_period_end")
        .eq("user_id", user.id)
        .maybeSingle<{
            stripe_customer_id: string | null
            trial_ends_at: string | null
            tier: "free" | "paid" | "trial"
            current_period_end: string | null
        }>()

    // Trial double-redemption guard. Findings doc S9: if a user has
    // ever redeemed a trial (trial_ends_at is set), force trial=false
    // on subsequent checkouts. Stripe doesn't dedupe trials per identity,
    // so without this a user can repeatedly cancel + restart for free
    // forever. Guard is per-user-id (Supabase auth), not per-email,
    // so account deletion + re-signup with the same email DOES grant a
    // fresh trial — that's a separate, harder problem (would need a
    // PII-aware trial fingerprint table) and is documented for later.
    if (trial && existing?.trial_ends_at) {
        trial = false
        console.log(
            `user ${user.id} previously had a trial ` +
            `(ended ${existing.trial_ends_at}); forcing trial=false`,
        )
    }
    // Already paid: a checkout flow during an active subscription is
    // a no-op or would create a duplicate sub. Send them to the
    // customer portal instead. 409 lets the client distinguish from
    // unrelated 4xx errors.
    if (existing?.tier === "paid" && existing.current_period_end) {
        const stillValid = new Date(existing.current_period_end) > new Date()
        if (stillValid) {
            return errorResponse(
                "already on paid tier; manage at customer portal instead",
                409,
            )
        }
    }

    // Stripe-side duplicate-subscription guard. Even if our local
    // tier='trial' state allows lock-in (below), a previous successful
    // checkout may have already created a `trialing` subscription in
    // Stripe. Re-running the flow here would mint ANOTHER subscription
    // (same customer or new), and the user ends up paying multiple
    // times once the trials end.
    //
    // If we know a customer_id, list their active subscriptions and
    // refuse if any are already in `trialing` or `active` state. The
    // client should then send the user to the billing portal to
    // manage the existing sub instead. (Bug found 2026-05-07: user
    // accumulated 3 duplicate Bullseye Pro trial subs.)
    if (existing?.stripe_customer_id) {
        try {
            const { getStripe } = await import("../_shared/stripe.ts")
            const stripe = getStripe()
            const subs = await stripe.subscriptions.list({
                customer: existing.stripe_customer_id,
                status: "all",
                limit: 10,
            })
            const liveSub = subs.data.find(
                s => s.status === "trialing" || s.status === "active"
                    || s.status === "past_due"
            )
            if (liveSub) {
                console.warn(
                    `user ${user.id} has an existing ${liveSub.status} ` +
                    `subscription ${liveSub.id}; refusing to create another`,
                )
                return errorResponse(
                    "You already have an active Bullseye Pro subscription. " +
                    "Manage it from Settings → Account → Manage / cancel.",
                    409,
                )
            }
        } catch (e) {
            // Don't fail the whole flow on a Stripe-side hiccup —
            // worst case we slip through and create a duplicate, which
            // is the existing buggy behaviour. Log and continue.
            console.warn(
                `subscription dedupe check failed (continuing): ` +
                `${e instanceof Error ? e.message : String(e)}`,
            )
        }
    }
    // Currently mid-trial: this is no longer a hard block. The user
    // can hit /upgrade and click "Permanently upgrade to Pro" to lock
    // in payment now and have Stripe honor the rest of their app
    // trial. Determine whether we're in that lock-in flow:
    //   - tier === 'trial' AND we have a trial_ends_at in the future
    //   → set trial=false (no fresh trial) and pass trial_end_unix so
    //     Stripe doesn't charge until the existing trial expires.
    let trial_end_unix: number | null = null
    if (existing?.tier === "trial") {
        const ends = existing.trial_ends_at
            ? new Date(existing.trial_ends_at).getTime()
            : 0
        if (ends > Date.now()) {
            trial = false
            trial_end_unix = Math.floor(ends / 1000)
            console.log(
                `user ${user.id} converting trial to paid; ` +
                `trial_end_unix=${trial_end_unix}`,
            )
        } else {
            // Trial already expired but tier still says trial (race
            // between cron and webhook). Treat as fresh paid signup.
            trial = false
        }
    }

    // Stripe-side stale-customer recovery: when a user (or admin) deletes
    // a Stripe customer in the dashboard, our `licenses.stripe_customer_id`
    // still points at it. The next Checkout call then errors out with
    // "No such customer: 'cus_...'" and the user is stuck.
    //
    // Strategy: try once with the cached id; on the specific
    // "No such customer" error, NULL out the column and retry without
    // it (Checkout will mint a fresh customer on its own). The webhook
    // re-links the new customer_id when checkout.session.completed
    // fires, so the DB self-heals from there.
    async function attempt(customerId: string | null) {
        return await createCheckoutSession({
            user_id: user.id,
            user_email: user.email,
            plan,
            trial,
            success_url: SUCCESS_URL,
            cancel_url: CANCEL_URL,
            existing_customer_id: customerId,
            trial_end_unix,
        })
    }

    try {
        let session
        try {
            session = await attempt(existing?.stripe_customer_id ?? null)
        } catch (e) {
            const msg = e instanceof Error ? e.message : String(e)
            const isNoSuchCustomer =
                /No such customer/i.test(msg) &&
                !!existing?.stripe_customer_id
            if (!isNoSuchCustomer) throw e

            console.warn(
                `Stale stripe_customer_id ${existing?.stripe_customer_id} ` +
                `for user ${user.id}; clearing and retrying`,
            )
            // Drop the dead reference. Webhook re-links on the new
            // customer's checkout.session.completed event.
            await db
                .from("licenses")
                .update({ stripe_customer_id: null })
                .eq("user_id", user.id)

            session = await attempt(null)
        }
        return jsonResponse({ url: session.url })
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error("checkout session creation failed:", msg)
        return errorResponse(`stripe error: ${msg}`, 502)
    }
})
