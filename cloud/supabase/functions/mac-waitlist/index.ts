// /mac-waitlist — public endpoint, no auth required.
//
// Body: { email: string, source?: string }
// Returns:
//   200 { ok: true }                       — added (or already present)
//   400 { ok: false, error: "valid email required" }
//   429 { ok: false, error: "rate limited" }
//
// Why no auth: the whole point is to capture interest from visitors who
// don't yet have a Bullseye account. The trade-off is we have to defend
// against bots / scrape attacks — see rate limit + email shape below.
//
// Idempotency: ON CONFLICT (email) DO NOTHING. Re-submitting the same
// email is a silent no-op (the user sees "you're on the list" either
// way; we don't reveal whether they're new or returning).
//
// Rate limit: 1 request per IP per 10 seconds, in-memory. Crude but
// sufficient — anything heavier and we'd reach for the comps_rate_limit
// table model. This isn't a high-value endpoint to brute-force.

import {
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"

interface Body {
    email?: string
    source?: string
}

const MAX_EMAIL_LENGTH = 320  // RFC 5321
const ALLOWED_SOURCES = new Set(["download", "landing", "footer", "other"])

// Per-IP throttle: in-memory ring of recent submissions. Cleared on
// edge-function cold-start, which is fine — the goal is to slow human
// spammers, not to enforce a hard quota.
const RATE_WINDOW_MS = 10_000
const recentSubmits = new Map<string, number>()

function tooSoon(ip: string): boolean {
    const last = recentSubmits.get(ip)
    if (last && Date.now() - last < RATE_WINDOW_MS) {
        return true
    }
    recentSubmits.set(ip, Date.now())
    // Light cleanup so the map doesn't grow unbounded — drop entries
    // older than the window every ~100 calls.
    if (recentSubmits.size > 100) {
        const cutoff = Date.now() - RATE_WINDOW_MS
        for (const [k, t] of recentSubmits) {
            if (t < cutoff) recentSubmits.delete(k)
        }
    }
    return false
}

function isValidEmail(s: string): boolean {
    if (!s || s.length > MAX_EMAIL_LENGTH) return false
    // Permissive but defensive: requires exactly one @, at least one
    // dot in the domain, no whitespace. We don't try to be RFC-perfect
    // — Supabase Auth will be the gate of record if/when this person
    // signs up later.
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s)
}

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST") {
        return errorResponse("method not allowed", 405)
    }

    // IP for rate limit. Cloudflare / Supabase sets x-real-ip; fall
    // back to forwarded-for. If neither exists (local dev), use a
    // sentinel so the limit still applies.
    const ip =
        req.headers.get("x-real-ip") ||
        (req.headers.get("x-forwarded-for") || "").split(",")[0].trim() ||
        "unknown"

    if (tooSoon(ip)) {
        return errorResponse("rate limited", 429)
    }

    let body: Body
    try {
        body = await req.json()
    } catch {
        return errorResponse("invalid JSON body", 400)
    }

    const email = (body.email ?? "").trim().toLowerCase()
    if (!isValidEmail(email)) {
        return errorResponse("valid email required", 400)
    }

    const sourceRaw = (body.source ?? "").trim().toLowerCase()
    const source = ALLOWED_SOURCES.has(sourceRaw) ? sourceRaw : "other"

    const userAgent = (req.headers.get("user-agent") || "").slice(0, 256)

    const db = adminClient()

    // Upsert with ignoreDuplicates so re-submits are silent no-ops.
    // We don't reveal whether the email was already on the list — the
    // user sees "you're on the list" either way.
    const { error } = await db
        .from("mac_waitlist")
        .upsert(
            {
                email,
                source,
                ip_address: ip === "unknown" ? null : ip,
                user_agent: userAgent || null,
            },
            { onConflict: "email", ignoreDuplicates: true },
        )

    if (error) {
        // Don't leak DB error details to the client. Log server-side.
        console.error("mac_waitlist insert failed:", error.message)
        return errorResponse("could not save", 500)
    }

    return jsonResponse({ ok: true })
})
