// /trial-start — flip the calling user's licenses row to a 14-day Pro
// trial WITHOUT going through Stripe.
//
// Why this exists:
//   The trial is no-credit-card. Going through Stripe Checkout for a
//   no-card trial round-trips the user out of the app to a hosted
//   Stripe page and then to a website success page. For a desktop
//   user, that's a confusing journey for a trial that doesn't even
//   need payment info. This function just sets tier='trial' +
//   trial_ends_at=NOW()+14d and returns. The user never leaves the
//   app.
//
// Stripe still owns paid conversions — when the trial ends and the
// user wants to keep Pro, they go through /checkout-create which DOES
// route through Stripe. But the trial itself is now a one-DB-write
// affair.
//
// Body: {} (identity from JWT)
// Returns: { ok, tier, trial_ends_at } | { ok: false, error }
//
// Guards:
//   - tier already 'paid' or 'trial' (active) => 409
//   - previously had a trial (trial_ends_at not null) => 409
//     (this stops the same user from re-triggering the trial after
//     it ends; matches the same guard in checkout-create)

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"

const TRIAL_DAYS = 14

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

    const db = adminClient()
    const { data: existing } = await db
        .from("licenses")
        .select("tier, trial_ends_at, current_period_end")
        .eq("user_id", user.id)
        .maybeSingle<{
            tier: "free" | "trial" | "paid"
            trial_ends_at: string | null
            current_period_end: string | null
        }>()

    if (!existing) {
        return errorResponse("license row missing; sign out and sign back in", 500)
    }

    if (existing.tier === "paid" && existing.current_period_end) {
        const stillValid = new Date(existing.current_period_end) > new Date()
        if (stillValid) {
            return errorResponse(
                "already on Pro; manage subscription in Settings",
                409,
            )
        }
    }
    if (existing.tier === "trial") {
        return errorResponse(
            "trial already active",
            409,
        )
    }
    if (existing.trial_ends_at) {
        return errorResponse(
            "trial already used on this account; subscribe to keep Pro",
            409,
        )
    }

    // Email-alias dedupe — prevents the obvious trial-farming attack
    // where one person creates many Supabase accounts with Gmail/
    // Outlook aliases (`me+1@gmail.com`, `me+2@gmail.com`, dots in
    // local-part, etc.) and runs the 14-day trial repeatedly.
    //
    // Normalization: lowercase + strip `+suffix` and `.` from the
    // local-part of Gmail/Googlemail/Outlook/Hotmail/Live (the four
    // providers that treat dots and pluses as aliases). Other domains
    // pass through unchanged because alias rules differ.
    //
    // Compare the normalized form against every other auth.users row
    // (case-insensitive) and refuse if any other user_id has already
    // redeemed a trial. This is a SOFT block: a determined attacker
    // can use distinct domains, but it stops the casual exploit.
    function normalizeEmail(raw: string): string {
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
    try {
        const norm = normalizeEmail(user.email)
        // Find all other auth users whose email normalizes to the same
        // value AND have ever redeemed a trial. Doing this server-side
        // because we have admin access to auth.users via service role.
        const { data: priorUsers } = await db
            .schema("auth")
            .from("users")
            .select("id, email")
            .neq("id", user.id)
            .ilike("email", `%${norm.split("@")[1]}`)  // narrow by domain
        if (priorUsers && priorUsers.length > 0) {
            const sameInboxIds = priorUsers
                .filter(u => normalizeEmail(u.email || "") === norm)
                .map(u => u.id)
            if (sameInboxIds.length > 0) {
                const { count } = await db
                    .from("licenses")
                    .select("user_id", { count: "exact", head: true })
                    .in("user_id", sameInboxIds)
                    .not("trial_ends_at", "is", null)
                if (typeof count === "number" && count > 0) {
                    return errorResponse(
                        "A trial has already been used from this email " +
                        "address. Subscribe to keep Pro.",
                        409,
                    )
                }
            }
        }
    } catch (e) {
        // Fail open — better to grant a possibly-duplicate trial than
        // to block legitimate sign-ups when the dedupe query hiccups.
        console.warn(
            "trial alias-dedupe check failed (continuing): " +
            (e instanceof Error ? e.message : String(e)),
        )
    }

    const trialEnds = new Date(Date.now() + TRIAL_DAYS * 24 * 60 * 60 * 1000)
        .toISOString()

    const { error } = await db
        .from("licenses")
        .update({
            tier: "trial",
            trial_ends_at: trialEnds,
            updated_at: new Date().toISOString(),
        })
        .eq("user_id", user.id)

    if (error) {
        console.error("trial-start update failed:", error)
        return errorResponse(`db error: ${error.message}`, 500)
    }

    console.log(`user ${user.id} started 14-day trial; ends ${trialEnds}`)
    return jsonResponse({
        ok: true,
        tier: "trial",
        trial_ends_at: trialEnds,
    })
})
