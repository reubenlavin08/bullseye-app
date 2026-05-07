// /license — return user's tier + limits + kill switch.
//
// Modes:
//   GET  or  POST {}                       — read current license
//   POST {action: "redeem_pro_days"}       — convert 7 banked Pro days
//                                            into a 7-day Pro trial
//
// Read flow:
//   1. requireUser
//   2. SELECT * FROM licenses WHERE user_id = auth.uid()
//   3. If tier='paid' AND current_period_end < NOW(): downgrade -> free
//      (UPDATE in this same call, then return the new tier)
//   4. If tier='trial' AND trial_ends_at < NOW(): downgrade -> free
//      (UPDATE in this same call, then return the new tier)
//   5. Read user_streaks for pro_days_banked (v1.1 retention).
//   6. Return:
//       {
//         tier, watches_limit, poll_interval_min,
//         expires_at, trial_ends_at,
//         pro_days_banked,
//         can_redeem_trial,        <-- true iff free + banked >= 7
//         min_supported_version    <-- KILL SWITCH
//       }
//
// Redeem flow:
//   - Reject if tier != 'free' (paid users don't need a free trial;
//     trial users are already mid-trial). Return 403.
//   - Reject if pro_days_banked < REDEEM_COST. Return 403.
//   - Atomically: decrement banked by 7, set tier='trial' +
//     trial_ends_at = NOW() + 7 days. Return the updated license.
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
import { REDEEM_COST, TRIAL_DAYS } from "../_shared/streak.ts"

interface License {
    user_id: string
    tier: "free" | "paid" | "trial"
    current_period_end: string | null
    trial_ends_at: string | null
    cancel_at_period_end: boolean
    stripe_customer_id: string | null
    stripe_subscription_id: string | null
}

interface StreakRow {
    pro_days_banked: number
}

const FREE_LIMITS = { watches_limit: 3, poll_interval_min: 30 }
const PAID_LIMITS = { watches_limit: null, poll_interval_min: 5 }

function buildLicensePayload(
    license: License,
    proDaysBanked: number,
    trialBlocklisted: boolean,
): Record<string, unknown> {
    const limits = license.tier === "free" ? FREE_LIMITS : PAID_LIMITS
    const canRedeem =
        license.tier === "free" && proDaysBanked >= REDEEM_COST
    return {
        tier: license.tier,
        ...limits,
        expires_at: license.current_period_end,
        trial_ends_at: license.trial_ends_at,
        cancel_at_period_end: license.cancel_at_period_end,
        pro_days_banked: proDaysBanked,
        can_redeem_trial: canRedeem,
        // True if this user's email is in the trial_email_history
        // blocklist — i.e. they've consumed a trial in the past from
        // this inbox (current or alias). The desktop UI uses this to
        // swap "Start free trial" -> "Subscribe to Pro" so it never
        // offers a CTA the cloud will refuse. Authoritative — survives
        // SQL resets of licenses.trial_ends_at because the blocklist
        // is keyed by email hash, not user_id.
        trial_blocklisted: trialBlocklisted,
        // Kill switch — if the desktop app's version is older than
        // this, it shows a "please update" hard-stop and stops polling.
        min_supported_version: currentMinSupportedVersion(),
    }
}

/* normalizeEmail mirrors trial-start/index.ts so the same hash is
   computed at /license read time as at /trial-start grant time. */
function normalizeEmailForLicense(raw: string): string {
    const lower = raw.toLowerCase().trim()
    const at = lower.lastIndexOf("@")
    if (at <= 0) return lower
    const local = lower.slice(0, at)
    const domain = lower.slice(at + 1)
    const aliasDomains = new Set([
        "gmail.com", "googlemail.com",
        "outlook.com", "hotmail.com", "live.com",
    ])
    if (!aliasDomains.has(domain)) return lower
    const stripped = local.split("+")[0].replaceAll(".", "")
    return `${stripped}@${domain}`
}

async function sha256HexLicense(s: string): Promise<string> {
    const data = new TextEncoder().encode(s)
    const buf = await crypto.subtle.digest("SHA-256", data)
    return Array.from(new Uint8Array(buf))
        .map(b => b.toString(16).padStart(2, "0"))
        .join("")
}

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

    // Parse body for action routing. Empty body / GET = read mode.
    let action: string | undefined
    if (req.method === "POST") {
        try {
            const text = await req.text()
            if (text) {
                const body = JSON.parse(text)
                action = body?.action
            }
        } catch {
            // Malformed body — fall through and treat as a read.
        }
    }

    const db = adminClient()

    // 1. Fetch the license row. If somehow missing (signup trigger
    //    raced or failed), create a default 'free' license and proceed.
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

    // 3. Pull pro_days_banked from user_streaks. The row may not exist
    //    yet (user has never called /streak) — treat as 0.
    const { data: streak, error: streakErr } = await db
        .from("user_streaks")
        .select("pro_days_banked")
        .eq("user_id", user.id)
        .maybeSingle<StreakRow>()
    if (streakErr) {
        // Non-fatal — fall through with 0. The license read shouldn't
        // 500 because the streak table hiccupped.
        console.warn("streak read failed:", streakErr.message)
    }
    const proDaysBanked = streak?.pro_days_banked ?? 0

    // Compute trial-blocklist state. Authoritative source for "has
    // this user used a free trial?" — survives SQL resets of
    // licenses.trial_ends_at because the blocklist table is keyed by
    // SHA-256 hash of the normalized email. Used by the desktop UI
    // to swap "Start free trial" -> "Subscribe to Pro" so we never
    // offer a CTA the cloud's /trial-start would refuse.
    let trialBlocklisted = false
    try {
        const norm = normalizeEmailForLicense(user.email)
        const emailHash = await sha256HexLicense(norm)
        const { data: hit } = await db
            .from("trial_email_history")
            .select("first_seen_at")
            .eq("email_hash", emailHash)
            .maybeSingle()
        if (hit) trialBlocklisted = true
        // Also count licenses.trial_ends_at as proof of prior trial,
        // since some legacy users may have trialed before the
        // blocklist table existed.
        if (!trialBlocklisted && license.trial_ends_at) {
            trialBlocklisted = true
        }
    } catch (e) {
        // Fail open — better to (rarely) re-offer a trial than to
        // refuse legitimate first-time users on a blocklist hiccup.
        console.warn(
            "trial_blocklisted lookup failed: " +
            (e instanceof Error ? e.message : String(e)),
        )
    }

    // 4. Action: redeem_pro_days.
    if (action === "redeem_pro_days") {
        if (license.tier !== "free") {
            return errorResponse(
                `cannot redeem on tier=${license.tier}`,
                403,
            )
        }
        if (proDaysBanked < REDEEM_COST) {
            return errorResponse(
                `insufficient banked days: have ${proDaysBanked}, need ${REDEEM_COST}`,
                403,
            )
        }

        // Decrement bank + flip license to trial. Two writes; do the
        // bank decrement first so a partial failure leaves the user
        // with banked days they can retry with rather than a phantom
        // trial that was never paid for.
        const { error: bankErr } = await db
            .from("user_streaks")
            .update({
                pro_days_banked: proDaysBanked - REDEEM_COST,
                updated_at: new Date().toISOString(),
            })
            .eq("user_id", user.id)
        if (bankErr) {
            return errorResponse(
                `failed to debit banked days: ${bankErr.message}`,
                500,
            )
        }

        const trialEnd = new Date(now)
        trialEnd.setUTCDate(trialEnd.getUTCDate() + TRIAL_DAYS)
        const { data: updatedLicense, error: licErr } = await db
            .from("licenses")
            .update({
                tier: "trial",
                trial_ends_at: trialEnd.toISOString(),
                updated_at: new Date().toISOString(),
            })
            .eq("user_id", user.id)
            .select("*")
            .single<License>()
        if (licErr || !updatedLicense) {
            // Best-effort refund: try to put the days back.
            await db
                .from("user_streaks")
                .update({ pro_days_banked: proDaysBanked })
                .eq("user_id", user.id)
            return errorResponse(
                `failed to start trial: ${licErr?.message ?? "unknown"}`,
                500,
            )
        }
        license = updatedLicense
        return jsonResponse(
            buildLicensePayload(license, proDaysBanked - REDEEM_COST, true),
        )
    }

    // 5. Default read path.
    return jsonResponse(buildLicensePayload(license, proDaysBanked, trialBlocklisted))
})
