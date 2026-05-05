// /license — return user's tier + limits + kill switch.
//
// Flow:
//   1. requireUser
//   2. SELECT * FROM licenses WHERE user_id = auth.uid()
//   3. If tier='paid' AND current_period_end < NOW(): downgrade -> free
//      (UPDATE in this same call, then return the new tier)
//   4. If tier='trial' AND trial_ends_at < NOW(): downgrade -> free
//      (UPDATE in this same call, then return the new tier)
//   5. Return:
//       {
//         tier, watches_limit, poll_interval_min,
//         expires_at, trial_ends_at,
//         min_supported_version    <-- KILL SWITCH
//       }
//
// `watches_limit`: 3 for free, null (unlimited) for paid/trial.
// `poll_interval_min`: 30 for free, 5 for paid/trial.
//
// Note on trial expiry: we do the downgrade synchronously inside this
// endpoint instead of via a pg_cron job. pg_cron isn't enabled by
// default on Supabase Free projects, and there's no real benefit to
// sweeping in the background — the desktop app calls /license every
// hour, so any user whose trial expired will get downgraded the next
// time they're online (which is when it actually matters anyway,
// since an offline user can't use Pro features).

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import { currentMinSupportedVersion } from "../_shared/normalize.ts"

interface License {
    user_id: string
    tier: "free" | "paid" | "trial"
    current_period_end: string | null
    trial_ends_at: string | null
    cancel_at_period_end: boolean
    stripe_customer_id: string | null
    stripe_subscription_id: string | null
}

const FREE_LIMITS = { watches_limit: 3, poll_interval_min: 30 }
const PAID_LIMITS = { watches_limit: null, poll_interval_min: 5 }

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

    // 1. Fetch the row. If somehow missing (signup trigger raced or
    //    failed), create a default 'free' license and proceed.
    let { data: license, error: selErr } = await db
        .from("licenses")
        .select("*")
        .eq("user_id", user.id)
        .maybeSingle<License>()

    if (selErr) {
        return errorResponse(`db read failed: ${selErr.message}`, 500)
    }

    if (!license) {
        // Self-heal: row missing despite the on-signup trigger.
        const { data: created, error: insErr } = await db
            .from("licenses")
            .insert({ user_id: user.id, tier: "free" })
            .select("*")
            .single<License>()
        if (insErr) {
            return errorResponse(`db insert failed: ${insErr.message}`, 500)
        }
        license = created
    }

    // 2. Downgrade-on-read for expired trials/subscriptions. Done
    //    synchronously so the response always reflects the truth.
    const now = new Date()
    let needsDowngrade = false
    let downgradeReason = ""

    if (license.tier === "trial" && license.trial_ends_at) {
        if (new Date(license.trial_ends_at) < now) {
            needsDowngrade = true
            downgradeReason = "trial expired"
        }
    }
    if (license.tier === "paid" && license.current_period_end) {
        if (new Date(license.current_period_end) < now) {
            needsDowngrade = true
            downgradeReason = "subscription period ended"
        }
    }

    if (needsDowngrade) {
        const { error: updErr } = await db
            .from("licenses")
            .update({ tier: "free", updated_at: new Date().toISOString() })
            .eq("user_id", user.id)
        if (updErr) {
            console.warn("downgrade failed:", updErr.message)
            // Fall through and return the OLD tier — better than 500ing.
        } else {
            license.tier = "free"
            console.log(`downgraded ${user.id}: ${downgradeReason}`)
        }
    }

    const limits = license.tier === "free" ? FREE_LIMITS : PAID_LIMITS

    return jsonResponse({
        tier: license.tier,
        ...limits,
        expires_at: license.current_period_end,
        trial_ends_at: license.trial_ends_at,
        cancel_at_period_end: license.cancel_at_period_end,
        // Kill switch — if the desktop app's version is older than
        // this, it shows a "please update" hard-stop and stops polling.
        // Bump via `supabase secrets set MIN_SUPPORTED_VERSION=0.x.y`
        // when shipping a breaking change.
        min_supported_version: currentMinSupportedVersion(),
    })
})
