// /billing-portal — return a short-lived Stripe Billing Portal URL
// for the calling user.
//
// Body: {} (no input — user identity comes from JWT)
// Returns: { url: 'https://billing.stripe.com/...' }
//
// The returned URL is valid for ~1 hour and lets the user manage their
// subscription end-to-end: change card, switch plan, cancel, view
// invoices. Stripe hosts the UI; we just bounce the user there.
//
// Cancellation: the portal includes a "Cancel subscription" button. So
// just exposing this endpoint satisfies the "let users cancel their
// trial" requirement — no separate /billing/cancel endpoint needed.
//
// Auth: requireUser. We look up the caller's `stripe_customer_id` in
// the `licenses` table and pass it to the portal-session create call.
// If the user has no Stripe customer yet (free tier, never started a
// trial), return 404 with a clear error.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import { getStripe } from "../_shared/stripe.ts"

const RETURN_URL = "https://getbullseye.app/upgrade-success?from=portal"

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST" && req.method !== "GET") {
        return errorResponse("method not allowed", 405)
    }

    let user
    try {
        user = await requireUser(req)
    } catch (r) {
        if (r instanceof Response) return r
        throw r
    }

    const db = adminClient()
    const { data: license } = await db
        .from("licenses")
        .select("stripe_customer_id")
        .eq("user_id", user.id)
        .maybeSingle<{ stripe_customer_id: string | null }>()

    if (!license?.stripe_customer_id) {
        return errorResponse(
            "no stripe customer for this user — start a trial first",
            404,
        )
    }

    try {
        const stripe = getStripe()
        const session = await stripe.billingPortal.sessions.create({
            customer: license.stripe_customer_id,
            return_url: RETURN_URL,
        })
        return jsonResponse({ url: session.url })
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error("billing portal session create failed:", msg)
        return errorResponse(`stripe error: ${msg}`, 502)
    }
})
