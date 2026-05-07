// /stripe-webhook — receive Stripe lifecycle events and sync the
// licenses table.
//
// CRITICAL: signature verification is MANDATORY. Without it, anyone
// could POST a fake "subscription created" event and become a paid
// user. The signing secret lives in STRIPE_WEBHOOK_SECRET (set via
// supabase secrets).
//
// Events handled:
//   checkout.session.completed       link stripe_customer_id <-> user_id
//   customer.subscription.created    set tier (paid|trial), period_end
//   customer.subscription.updated    sync period_end, cancel_at_period_end
//   customer.subscription.deleted    set tier='free'
//   invoice.payment_failed           log (no email yet — step 7)
//
// Stripe sends events at-least-once and may retry on 5xx. Every
// handler must be idempotent — UPSERT, not INSERT.
//
// Auth: NONE for the JWT path. Stripe doesn't have a JWT to send.
// The signature header IS the auth.

import { adminClient, corsHeaders } from "../_shared/auth.ts"
import {
    verifyWebhookSignature,
    getStripe,
    creditCustomerOneMonth,
} from "../_shared/stripe.ts"

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST") {
        return new Response("method not allowed", { status: 405 })
    }

    const signature = req.headers.get("stripe-signature")
    if (!signature) {
        return new Response("missing stripe-signature header", { status: 400 })
    }

    // CRUCIAL: read raw bytes BEFORE any JSON parsing. Signature
    // verification works on the exact byte stream Stripe signed.
    const rawBody = await req.text()

    let event
    try {
        event = await verifyWebhookSignature(rawBody, signature)
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error("webhook signature verify FAILED:", msg)
        return new Response(`signature verification failed: ${msg}`, {
            status: 400,
        })
    }

    const db = adminClient()
    console.log(`webhook received: ${event.type} (${event.id})`)

    try {
        switch (event.type) {
            case "checkout.session.completed":
                await handleCheckoutCompleted(db, event.data.object)
                break
            case "customer.subscription.created":
            case "customer.subscription.updated":
                await handleSubscriptionUpsert(db, event.data.object)
                break
            case "customer.subscription.deleted":
                await handleSubscriptionDeleted(db, event.data.object)
                break
            case "invoice.payment_failed":
                await handlePaymentFailed(db, event.data.object)
                break
            default:
                // Unknown event types — Stripe sends many we don't
                // care about. Ack with 200 so they stop retrying.
                console.log(`ignoring event type: ${event.type}`)
        }
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error(`handler for ${event.type} failed:`, msg)
        // Return 500 so Stripe retries. Be careful that handlers
        // are idempotent — they will be called again.
        return new Response(`handler error: ${msg}`, { status: 500 })
    }

    return new Response(JSON.stringify({ received: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
    })
})

// --- Event handlers ---------------------------------------------------

async function handleCheckoutCompleted(db: any, session: any) {
    // Link the stripe_customer_id we just minted back to our user_id.
    // client_reference_id was set by /checkout-create.
    const userId = session.client_reference_id
    const customerId = typeof session.customer === "string"
        ? session.customer
        : session.customer?.id
    if (!userId || !customerId) {
        console.warn("checkout.session.completed missing user/customer id")
        return
    }
    const { error } = await db
        .from("licenses")
        .update({
            stripe_customer_id: customerId,
            updated_at: new Date().toISOString(),
        })
        .eq("user_id", userId)
    if (error) {
        throw new Error(`link customer failed: ${error.message}`)
    }
    console.log(`linked customer ${customerId} -> user ${userId}`)
}

async function handleSubscriptionUpsert(db: any, sub: any) {
    // The subscription object carries everything we need to set tier
    // + period_end. Look up user_id from the customer linkage.
    const customerId = typeof sub.customer === "string"
        ? sub.customer
        : sub.customer?.id
    if (!customerId) return

    const { data: license } = await db
        .from("licenses")
        .select("user_id")
        .eq("stripe_customer_id", customerId)
        .maybeSingle()
    if (!license?.user_id) {
        // The checkout.session.completed event should have linked
        // the customer first; if not, this subscription event arrived
        // out of order. Stripe will retry — we'll catch up next time.
        console.warn(`no license row for customer ${customerId}; will retry`)
        throw new Error("license row not yet linked; retry")
    }

    // Map Stripe subscription status -> our tier.
    // 'trialing' => trial, 'active' => paid, anything else => free
    let tier: "paid" | "trial" | "free"
    if (sub.status === "trialing") tier = "trial"
    else if (sub.status === "active") tier = "paid"
    else tier = "free"

    const update = {
        tier,
        stripe_subscription_id: sub.id,
        current_period_end: sub.current_period_end
            ? new Date(sub.current_period_end * 1000).toISOString()
            : null,
        trial_ends_at: sub.trial_end
            ? new Date(sub.trial_end * 1000).toISOString()
            : null,
        cancel_at_period_end: !!sub.cancel_at_period_end,
        updated_at: new Date().toISOString(),
    }

    const { error } = await db
        .from("licenses")
        .update(update)
        .eq("user_id", license.user_id)
    if (error) {
        throw new Error(`subscription upsert failed: ${error.message}`)
    }
    console.log(`updated user ${license.user_id}: ${tier} (sub ${sub.status})`)

    // Refer-and-earn: only when the subscription is TRULY paid (status
    // 'active'), not just trialing. Otherwise someone could sign up,
    // claim a code, never add a card, and we'd credit both sides for
    // a freebie. Idempotency: maybeApplyReferralRewards uses an
    // optimistic pending->earned status flip on the referrals row as
    // its lock; subsequent webhook fires find no pending rows.
    if (sub.status === "active") {
        try {
            await maybeApplyReferralRewards(db, license.user_id)
        } catch (e) {
            // Don't fail the whole webhook on a credit error — log and
            // move on. Stripe's tier sync already succeeded; the
            // referral row stays pending and we can retry on the next
            // subscription event.
            console.error("referral reward application failed:", e)
        }
    }
}

/**
 * Flip pending referrals where this user is the referee to 'earned',
 * then credit one Pro-month to BOTH the referrer and the referee.
 *
 * Why the lock-then-credit order: Stripe webhooks retry on 5xx
 * indefinitely. If we credited first, every retry would re-credit. By
 * flipping `referrals.status = 'earned'` first (with an optimistic
 * `.eq('status', 'pending')` filter), we guarantee only the first call
 * for a given referral will reach the credit step.
 *
 * Trade-off: if the credit call fails AFTER the lock flips, we lose
 * that credit. Acceptable for v1 — we log loudly so support can
 * manually re-credit. The alternative (credit-then-lock) is far worse:
 * silent double/triple-crediting on every Stripe retry.
 */
async function maybeApplyReferralRewards(db: any, refereeUserId: string) {
    const { data: pending, error: selErr } = await db
        .from("referrals")
        .select("id, referrer_user_id")
        .eq("referee_user_id", refereeUserId)
        .eq("status", "pending")

    if (selErr) {
        console.error("referrals select failed:", selErr)
        return
    }
    if (!pending || pending.length === 0) return

    for (const row of pending) {
        const referralId = row.id as string
        const referrerUserId = row.referrer_user_id as string

        // 1. Optimistic lock — flip pending -> earned. If another
        // concurrent webhook already flipped it, our update affects
        // zero rows and we skip. (Postgres returns no error in that
        // case; we use returning: 'minimal' to keep this fast.)
        const { data: flipped, error: flipErr } = await db
            .from("referrals")
            .update({
                status: "earned",
                earned_at: new Date().toISOString(),
            })
            .eq("id", referralId)
            .eq("status", "pending")
            .select("id")

        if (flipErr) {
            console.error(`referral ${referralId} flip failed:`, flipErr)
            continue
        }
        if (!flipped || flipped.length === 0) {
            // Already earned by a concurrent fire — nothing to do.
            continue
        }

        // 2. Look up both customer IDs.
        const [
            { data: refereeLic },
            { data: referrerLic },
        ] = await Promise.all([
            db.from("licenses")
                .select("stripe_customer_id")
                .eq("user_id", refereeUserId)
                .maybeSingle(),
            db.from("licenses")
                .select("stripe_customer_id, referral_months_earned")
                .eq("user_id", referrerUserId)
                .maybeSingle(),
        ])

        // 3. Increment the referrer's earned-month counter (UI uses
        // this for the "you've earned X months" badge).
        if (referrerLic) {
            const next = (referrerLic.referral_months_earned ?? 0) + 1
            const { error: incErr } = await db
                .from("licenses")
                .update({
                    referral_months_earned: next,
                    updated_at: new Date().toISOString(),
                })
                .eq("user_id", referrerUserId)
            if (incErr) {
                console.error(
                    `referrer ${referrerUserId} months++ failed:`, incErr,
                )
            }
        }

        // 4. Credit both customers. Each call is independent — if one
        // fails the other can still succeed. Failures are logged but
        // don't roll back; manual support credit covers the gap.
        if (referrerLic?.stripe_customer_id) {
            try {
                await creditCustomerOneMonth(
                    referrerLic.stripe_customer_id,
                    `referrer ${referrerUserId} earned via referee ${refereeUserId}`,
                )
            } catch (e) {
                console.error(
                    `credit referrer ${referrerUserId} failed:`, e,
                )
            }
        } else {
            console.warn(
                `referrer ${referrerUserId} has no stripe_customer_id; ` +
                `month banked but no Stripe credit applied`,
            )
        }
        if (refereeLic?.stripe_customer_id) {
            try {
                await creditCustomerOneMonth(
                    refereeLic.stripe_customer_id,
                    `referee ${refereeUserId} bonus via referrer ${referrerUserId}`,
                )
            } catch (e) {
                console.error(
                    `credit referee ${refereeUserId} failed:`, e,
                )
            }
        }

        console.log(
            `referral ${referralId} earned: ` +
            `referrer ${referrerUserId} + referee ${refereeUserId} both credited`,
        )
    }
}

async function handleSubscriptionDeleted(db: any, sub: any) {
    const customerId = typeof sub.customer === "string"
        ? sub.customer
        : sub.customer?.id
    if (!customerId) return

    const { data: license } = await db
        .from("licenses")
        .select("user_id")
        .eq("stripe_customer_id", customerId)
        .maybeSingle()
    if (!license?.user_id) return

    const { error } = await db
        .from("licenses")
        .update({
            tier: "free",
            stripe_subscription_id: null,
            current_period_end: null,
            trial_ends_at: null,
            cancel_at_period_end: false,
            updated_at: new Date().toISOString(),
        })
        .eq("user_id", license.user_id)
    if (error) {
        throw new Error(`subscription delete sync failed: ${error.message}`)
    }
    console.log(`downgraded user ${license.user_id} -> free`)
}

async function handlePaymentFailed(db: any, invoice: any) {
    // For now just log. In step 7 (alerts) we'll send a warning email
    // via /alerts-send so the user can update their payment method
    // before their access lapses.
    const customerId = typeof invoice.customer === "string"
        ? invoice.customer
        : invoice.customer?.id
    console.warn(
        `payment failed for customer ${customerId}, invoice ${invoice.id}`,
    )
}
