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
    const trial = body.trial !== false  // default true

    // Look up existing stripe_customer_id (if user has subscribed
    // before, we re-use the customer record so they keep their
    // payment-method history).
    const db = adminClient()
    const { data: existing } = await db
        .from("licenses")
        .select("stripe_customer_id")
        .eq("user_id", user.id)
        .maybeSingle()

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
