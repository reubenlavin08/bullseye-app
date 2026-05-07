// /referral-claim — link the calling user to a referrer via their code.
//
// Body: { code: string }
// Returns: { ok: true } | { ok: false, error: ... }
//
// Idempotent — calling twice with the same code is a no-op (UNIQUE
// (referee_user_id) constraint blocks the second insert and we treat
// the duplicate-key error as success).
//
// Failure modes:
//   - code unknown          → 404 "no such referral code"
//   - self-referral         → 400 "cannot refer yourself"
//   - already referred      → 200 (idempotent — first attribution wins)

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"

interface Body {
    code?: string
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

    const code = (body.code ?? "").trim().toUpperCase()
    if (!code || code.length < 4 || code.length > 32) {
        return errorResponse("code missing or wrong length", 400)
    }

    const db = adminClient()

    // 1. Look up the referrer.
    const { data: ref } = await db
        .from("licenses")
        .select("user_id")
        .eq("referral_code", code)
        .maybeSingle<{ user_id: string }>()

    if (!ref) {
        return errorResponse("no such referral code", 404)
    }
    if (ref.user_id === user.id) {
        return errorResponse("cannot refer yourself", 400)
    }

    // 2. Insert the referral row. UNIQUE (referee_user_id) + UNIQUE
    // (referrer, referee) protect against duplicates; we treat the
    // 23505 unique-violation as success so the call is idempotent.
    const { error } = await db
        .from("referrals")
        .insert({
            referrer_user_id: ref.user_id,
            referee_user_id: user.id,
            status: "pending",
        })

    if (error && (error as { code?: string }).code !== "23505") {
        console.error("referral insert failed:", error)
        return errorResponse(`db error: ${error.message}`, 500)
    }

    // 3. Set referred_by_user_id on the new user's licenses row for
    // the stripe-webhook coupon logic to find later.
    await db
        .from("licenses")
        .update({ referred_by_user_id: ref.user_id })
        .eq("user_id", user.id)
        .is("referred_by_user_id", null)

    return jsonResponse({ ok: true })
})
