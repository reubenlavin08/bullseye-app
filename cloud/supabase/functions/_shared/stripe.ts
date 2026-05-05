// Stripe helpers — Checkout sessions + webhook signature verification.
//
// Uses the official Stripe Node SDK via esm.sh (Deno-compatible).
// One getStripe() factory because Deno cold-starts can re-init module
// scope; caching the client across requests within a warm function
// instance saves tens of ms.

import Stripe from "https://esm.sh/stripe@17?target=deno"
import { getEnv } from "./normalize.ts"

let _stripe: Stripe | null = null

export function getStripe(): Stripe {
    if (_stripe) return _stripe
    _stripe = new Stripe(getEnv("STRIPE_SECRET_KEY"), {
        apiVersion: "2024-09-30.acacia",
        // Tell Stripe we're in Deno so their User-Agent is accurate
        // (helps with their support diagnostics).
        httpClient: Stripe.createFetchHttpClient(),
    })
    return _stripe
}

export interface CheckoutSessionArgs {
    user_id: string
    user_email: string
    plan: "monthly" | "yearly"
    trial: boolean
    success_url: string
    cancel_url: string
    existing_customer_id?: string | null
}

export async function createCheckoutSession(
    args: CheckoutSessionArgs,
): Promise<{ url: string; session_id: string; customer_id: string }> {
    const stripe = getStripe()
    const priceId = args.plan === "yearly"
        ? getEnv("STRIPE_PRICE_YEARLY")
        : getEnv("STRIPE_PRICE_MONTHLY")

    // Re-use the customer if we already have one. Otherwise let
    // Checkout create one (and we link it back via the webhook).
    const customerArg = args.existing_customer_id
        ? { customer: args.existing_customer_id }
        : { customer_email: args.user_email }

    const session = await stripe.checkout.sessions.create({
        ...customerArg,
        mode: "subscription",
        line_items: [{ price: priceId, quantity: 1 }],
        subscription_data: args.trial
            ? { trial_period_days: 7 }
            : undefined,
        // client_reference_id lets the webhook link the resulting
        // subscription back to our user_id without an extra round-trip.
        client_reference_id: args.user_id,
        success_url: args.success_url,
        cancel_url: args.cancel_url,
        // Allow auto-cancel of trial subs that fail their first
        // payment so we don't hold a forever-trial.
        allow_promotion_codes: true,
    })

    return {
        url: session.url ?? "",
        session_id: session.id,
        customer_id: typeof session.customer === "string"
            ? session.customer
            : (session.customer?.id ?? ""),
    }
}

/**
 * Verify a Stripe webhook signature against the raw request body.
 *
 * CRITICAL: this runs against the raw bytes (not parsed JSON) — calling
 * `await req.json()` first will mutate the body and signature
 * verification fails. Webhook handlers must use `await req.text()`.
 *
 * Uses constructEventAsync because Deno's WebCrypto is the only HMAC
 * impl available; the sync version (`constructEvent`) requires
 * Node's `crypto` module.
 */
export async function verifyWebhookSignature(
    rawBody: string,
    signature: string,
): Promise<Stripe.Event> {
    const stripe = getStripe()
    const secret = getEnv("STRIPE_WEBHOOK_SECRET")
    return await stripe.webhooks.constructEventAsync(
        rawBody, signature, secret,
    )
}

/** Look up the user_id this Stripe customer belongs to. */
export async function getUserIdForCustomer(
    db: any,
    customerId: string,
): Promise<string | null> {
    const { data } = await db
        .from("licenses")
        .select("user_id")
        .eq("stripe_customer_id", customerId)
        .maybeSingle()
    return data?.user_id ?? null
}
