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

// Trial length — shortened from 14 -> 7 days (2026-05-07).
//
// Rationale: 7 days forces the monetization decision faster while
// engaged users can easily extend it for free via the streak +
// action-based earning loop (banked Pro days, referrals). Disengaged
// users who weren't going to convert at day-14 anyway are filtered
// out earlier, freeing the funnel.
const TRIAL_DAYS = 7

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

    // Email-alias dedupe — prevents trial-farming attacks where one
    // person redeems trials repeatedly via:
    //   (a) Gmail/Outlook aliases (`me+1@gmail.com`, dots, etc.)
    //   (b) account delete + re-signup with the same email
    //
    // Defense layer 1 — normalize the email (strip `+suffix` and `.`
    // for the four providers that treat them as aliases). Compare to
    // a permanent SHA-256 hash blocklist (`trial_email_history`) that
    // does NOT cascade with auth.users — so deleting + recreating the
    // account doesn't reset the dedup record.
    //
    // Defense layer 2 — also check live auth.users for any other
    // currently-existing user with the same normalized email who has
    // redeemed a trial. Catches the case where the history table
    // hasn't yet been seeded (migration just ran, etc.).
    //
    // Both layers fail open on internal errors — better to grant a
    // possibly-duplicate trial than to refuse a legitimate signup.
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

    async function sha256Hex(s: string): Promise<string> {
        const data = new TextEncoder().encode(s)
        const buf = await crypto.subtle.digest("SHA-256", data)
        return Array.from(new Uint8Array(buf))
            .map(b => b.toString(16).padStart(2, "0"))
            .join("")
    }

    const norm = normalizeEmail(user.email)
    const emailHash = await sha256Hex(norm)

    // Layer 1 — permanent hash blocklist (survives account deletion).
    try {
        const { data: blocklisted } = await db
            .from("trial_email_history")
            .select("first_seen_at")
            .eq("email_hash", emailHash)
            .maybeSingle()
        if (blocklisted) {
            return errorResponse(
                "A trial has already been used from this email " +
                "address. Subscribe to keep Pro.",
                409,
            )
        }
    } catch (e) {
        // Fail open — table may not exist yet (pre-migration), or a
        // transient DB issue. Don't block legitimate signups on it.
        console.warn(
            "trial blocklist check failed (continuing): " +
            (e instanceof Error ? e.message : String(e)),
        )
    }

    // Layer 2 — current auth.users with the same normalized email
    // that have already redeemed a trial. Defensive: catches the case
    // where the blocklist row didn't get inserted last time.
    try {
        const { data: priorUsers } = await db
            .schema("auth")
            .from("users")
            .select("id, email")
            .neq("id", user.id)
            .ilike("email", `%${norm.split("@")[1]}`)
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

    // Stamp the permanent blocklist so a future delete-and-re-signup
    // with the same email won't get another trial. ON CONFLICT DO
    // NOTHING because the same hash may already exist (e.g. legitimate
    // re-trial attempt that we're letting through during fail-open).
    try {
        await db.from("trial_email_history").upsert({
            email_hash: emailHash,
            original_user_id: user.id,
        }, { onConflict: "email_hash", ignoreDuplicates: true })
    } catch (e) {
        // Non-fatal — trial was granted, blocklist insert is just
        // defense-in-depth for next time. Log and continue.
        console.warn(
            "trial blocklist insert failed (non-fatal): " +
            (e instanceof Error ? e.message : String(e)),
        )
    }

    console.log(`user ${user.id} started 14-day trial; ends ${trialEnds}`)
    return jsonResponse({
        ok: true,
        tier: "trial",
        trial_ends_at: trialEnds,
    })
})
