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
    /**
     * If set, sets Stripe's `subscription_data.trial_end` to this
     * Unix timestamp (seconds). Used when the user is mid-app-trial
     * and chooses "Permanently upgrade" — we want to lock in payment
     * NOW but not charge until their app trial ends. Mutually exclusive
     * with `trial=true` (would error: can't have both trial_period_days
     * and trial_end). Caller should set `trial=false` when passing this.
     */
    trial_end_unix?: number | null
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

    // No-card-required trial config:
    //   payment_method_collection: 'if_required' tells Checkout to skip
    //   the card step when a trial is in play. The user never sees a
    //   card form during sign-up — matches the "no card required"
    //   marketing copy.
    //
    //   trial_settings.end_behavior.missing_payment_method: 'cancel'
    //   tells Stripe what to do at the end of the trial if the user
    //   never added a card: cancel the subscription (no charge attempt,
    //   no failed-invoice email, no retry storm). User has to come
    //   back and explicitly add a card to keep Pro.
    // Three trial modes:
    //   1. fresh 14-day trial (free user signing up): trial=true,
    //      trial_end_unix=null → Stripe runs trial_period_days=14.
    //   2. lock-in mid-trial (trial user clicking "Permanently
    //      upgrade"): trial=false, trial_end_unix=<remaining trial>
    //      → Stripe charges $0 today, charges normal price at trial_end.
    //   3. straight checkout (paid signup, no trial): trial=false,
    //      trial_end_unix=null → charged immediately.
    let subscription_data: Record<string, unknown> | undefined
    if (args.trial) {
        subscription_data = {
            trial_period_days: 14,
            trial_settings: {
                end_behavior: { missing_payment_method: "cancel" },
            },
        }
    } else if (args.trial_end_unix && args.trial_end_unix > 0) {
        subscription_data = {
            trial_end: args.trial_end_unix,
            trial_settings: {
                end_behavior: { missing_payment_method: "cancel" },
            },
        }
    }
    const isTrialFlow = args.trial || !!args.trial_end_unix

    const session = await stripe.checkout.sessions.create({
        ...customerArg,
        mode: "subscription",
        line_items: [{ price: priceId, quantity: 1 }],
        subscription_data,
        // For both fresh AND lock-in trials we still want a card on file
        // (lock-in is the user EXPLICITLY adding payment), so use
        // 'always' when trial_end_unix is set. 'if_required' is only
        // for the no-card fresh-trial path, which has been replaced by
        // /api/trial/start (no Stripe round-trip at all).
        payment_method_collection:
            args.trial && !args.trial_end_unix ? "if_required" : "always",
        // client_reference_id lets the webhook link the resulting
        // subscription back to our user_id without an extra round-trip.
        client_reference_id: args.user_id,
        success_url: args.success_url,
        cancel_url: args.cancel_url,
        // Allow promo codes on the Checkout page (independent of trial).
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

/**
 * Credit a Stripe customer one Pro-month worth of dollars, applied to
 * their next invoice automatically.
 *
 * Stripe semantics: customer.balance is in cents, NEGATIVE = credit
 * (Stripe deducts it from the next invoice). We READ the current
 * balance and subtract one monthly price — never overwrite, so stacked
 * credits accumulate cleanly across multiple referrals.
 *
 * Default credit = 999¢ ($9.99 monthly). Override via env
 * STRIPE_REFERRAL_CREDIT_CENTS if pricing changes.
 *
 * Idempotency: callers MUST gate this with the `referrals.status`
 * pending→earned flip BEFORE invoking — Stripe webhook retries will
 * otherwise re-credit on every retry.
 */
export async function creditCustomerOneMonth(
    customerId: string,
    note: string,
): Promise<void> {
    const stripe = getStripe()
    const creditCents = parseInt(
        Deno.env.get("STRIPE_REFERRAL_CREDIT_CENTS") ?? "999",
        10,
    )
    if (!Number.isFinite(creditCents) || creditCents <= 0) {
        throw new Error(`bad STRIPE_REFERRAL_CREDIT_CENTS: ${creditCents}`)
    }
    const customer = await stripe.customers.retrieve(customerId)
    if ((customer as { deleted?: boolean }).deleted) {
        console.warn(`skip credit, customer ${customerId} deleted`)
        return
    }
    const current = (customer as Stripe.Customer).balance ?? 0
    const next = current - creditCents
    await stripe.customers.update(customerId, {
        balance: next,
        metadata: {
            last_referral_credit_at: new Date().toISOString(),
            last_referral_credit_note: note,
        },
    })
    console.log(
        `credited customer ${customerId}: $${creditCents / 100} ` +
        `(balance ${current} -> ${next}) [${note}]`,
    )
}
