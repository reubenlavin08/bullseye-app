// /account-delete — GDPR/PIPEDA cascade delete.
//
// Flow:
//   1. requireUser  (authenticated as the account being deleted — we
//      never delete by id supplied in the body; that would let any
//      authed user nuke another's account)
//   2. Cancel active Stripe subscription if one exists. Failure here
//      is logged but doesn't block deletion — the user has already
//      consented to the irreversible action and Stripe customer-
//      portal cancellation is a separate path.
//   3. Mark the licenses row deleted (don't actually rely on the FK
//      cascade for billing data — keep the canceled-subscription
//      record around for accounting but null out the user link).
//   4. DELETE FROM auth.users WHERE id = user_id. Postgres FK
//      ON DELETE CASCADE then wipes user_watches, email_log,
//      email_queue, telemetry_events (those have CASCADE on user_id),
//      user_streaks, user_unlocks. licenses uses ON DELETE CASCADE
//      too — so the row goes too.
//   5. Return { ok: true, deleted_at }
//
// The desktop client follows this with token_store.clear() + a local
// SQLite wipe, then exits.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import { getStripe } from "../_shared/stripe.ts"

interface LicenseRow {
    stripe_customer_id: string | null
    stripe_subscription_id: string | null
}

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST" && req.method !== "DELETE") {
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

    // 1. Look up Stripe linkage so we can cancel the subscription
    //    BEFORE the DB cascade wipes the row.
    const { data: license } = await db
        .from("licenses")
        .select("stripe_customer_id, stripe_subscription_id")
        .eq("user_id", user.id)
        .maybeSingle<LicenseRow>()

    // 2. Best-effort Stripe cancellation. We swallow errors here so a
    //    Stripe outage can't strand the user with an orphaned account
    //    they can't delete. Worst case: subscription continues until
    //    period end, gets refunded by support if user complains.
    if (license?.stripe_subscription_id) {
        try {
            const stripe = getStripe()
            await stripe.subscriptions.cancel(license.stripe_subscription_id)
            console.log(
                `cancelled subscription ${license.stripe_subscription_id} ` +
                `for deleted user ${user.id}`,
            )
        } catch (e) {
            const msg = e instanceof Error ? e.message : String(e)
            console.warn(
                `stripe cancel failed for user ${user.id}: ${msg} ` +
                `(continuing with delete)`,
            )
        }
    }

    // 3. Delete the auth.users row. FK CASCADE wipes all user-owned
    //    rows across the schema. We use admin (service-role) so RLS
    //    can't block this.
    //
    // Supabase's auth admin API exposes deleteUser via the gotrue
    // admin endpoints. We call it via the supabase-js admin namespace.
    const { error: delErr } = await db.auth.admin.deleteUser(user.id)
    if (delErr) {
        return errorResponse(
            `account delete failed: ${delErr.message}`,
            500,
        )
    }

    return jsonResponse({
        ok: true,
        user_id: user.id,
        deleted_at: new Date().toISOString(),
    })
})
