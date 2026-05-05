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

const SUCCESS_URL = "https://bullseye.app/upgrade-success?session_id={CHECKOUT_SESSION_ID}"
const CANCEL_URL = "https://bullseye.app/pricing"

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
    // Currently mid-trial — same logic; tell the client.
    if (existing?.tier === "trial") {
        return errorResponse(
            "trial already active; check your dashboard",
            409,
        )
    }

    try {
        const session = await createCheckoutSession({
            user_id: user.id,
            user_email: user.email,
            plan,
            trial,
            success_url: SUCCESS_URL,
            cancel_url: CANCEL_URL,
            existing_customer_id: existing?.stripe_customer_id ?? null,
        })
        return jsonResponse({ url: session.url })
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error("checkout session creation failed:", msg)
        return errorResponse(`stripe error: ${msg}`, 502)
    }
})
