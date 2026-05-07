// /referral-info — return the calling user's referral code, share link,
// earned-month count, and history of who they referred.
//
// Body: {} (no input — identity from JWT)
// Returns:
//   {
//     ok, code, link,
//     earned: int (months earned + applied),
//     pending: int (referrals not yet activated by referee),
//     referrals: [{ status, created_at, earned_at }]
//   }
//
// Code is generated lazily — first call mints a fresh 8-char base32
// code if the user's licenses row doesn't have one yet (covers users
// from before migration 012 if any slip through, plus future signup
// races).

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"

// Share link uses a query param (?ref=CODE) on the apex URL rather
// than a path (/r/CODE). Reason: GitHub Pages doesn't serve dynamic
// path segments without a file at that exact location, so /r/ABCD2345
// returned 404. Query params route to the existing index.html, which
// has the JS to stash the code in localStorage and surface a banner.
const SHARE_BASE = "https://getbullseye.app/?ref="

function generateCode(): string {
    // 8 chars, alphabet 23456789ABCDEFGHJKLMNPQRSTUVWXYZ (no 0/1/O/I).
    const alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    const bytes = new Uint8Array(8)
    crypto.getRandomValues(bytes)
    let out = ""
    for (let i = 0; i < 8; i++) {
        out += alphabet[bytes[i] % alphabet.length]
    }
    return out
}

async function ensureCode(db: any, userId: string): Promise<string> {
    const { data: license } = await db
        .from("licenses")
        .select("referral_code")
        .eq("user_id", userId)
        .maybeSingle()
    if (license?.referral_code) return license.referral_code
    // Mint one. Retry on the (extremely unlikely) collision.
    for (let i = 0; i < 5; i++) {
        const code = generateCode()
        const { error } = await db
            .from("licenses")
            .update({ referral_code: code })
            .eq("user_id", userId)
        if (!error) return code
    }
    throw new Error("could not mint referral code after 5 attempts")
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

    const db = adminClient()
    let code: string
    try {
        code = await ensureCode(db, user.id)
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        return errorResponse(`code mint failed: ${msg}`, 500)
    }

    const { data: rows } = await db
        .from("referrals")
        .select("status, created_at, earned_at")
        .eq("referrer_user_id", user.id)
        .order("created_at", { ascending: false })

    const referrals = (rows ?? []) as Array<{
        status: string
        created_at: string
        earned_at: string | null
    }>
    const earned = referrals.filter((r) => r.status === "earned").length
    const pending = referrals.filter((r) => r.status === "pending").length

    return jsonResponse({
        ok: true,
        code,
        link: `${SHARE_BASE}${code}`,
        earned,
        pending,
        referrals,
    })
})
