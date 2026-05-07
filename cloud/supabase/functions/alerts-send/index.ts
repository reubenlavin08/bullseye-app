// /alerts-send — render + send email via Resend, with tier rules.
//
// Tier behavior:
//   free: at most 1 digest/day per user. Idempotency enforced by the
//         partial unique index `uq_one_digest_per_day`. If today's
//         digest already sent, push the matches to email_queue for
//         tomorrow's 8am-local slot.
//   paid/trial: instant emails; the desktop side does the 60s batching
//         hold. This function just sends what it's given.
//
// Request:
//   POST /alerts-send
//     { type: 'digest'|'instant', matches: DigestMatch[] }
//   ->
//     { sent: bool, queued: bool, count: int }
//
// On missing RESEND_API_KEY: return 503 "email service not configured"
// rather than 500. The caller treats this as a soft fail and the
// listing stays un-notified for next tick.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import {
    sendEmail,
    renderDigest,
    renderInstant,
    type DigestMatch,
} from "../_shared/resend.ts"

interface Body {
    type?: "digest" | "instant"
    matches?: DigestMatch[]
}

interface License {
    tier: "free" | "paid" | "trial"
}

// Tomorrow at 08:00 UTC. We don't have the user's IANA timezone server-
// side (would need to add a column to user profile), so the cron-based
// digest sender uses 08:00 UTC as a coarse default. The desktop app's
// digest scheduler runs at 08:00 LOCAL and posts type='digest'; this
// function just enforces "at most one per UTC day" for idempotency.
// If we later add per-user tz, swap this for a tz-aware computation.
function tomorrow8amUTC(): string {
    const d = new Date()
    d.setUTCDate(d.getUTCDate() + 1)
    d.setUTCHours(8, 0, 0, 0)
    return d.toISOString()
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
    } catch (_) {
        return errorResponse("invalid JSON body", 400)
    }

    const type = body.type
    if (type !== "digest" && type !== "instant") {
        return errorResponse("type must be 'digest' or 'instant'", 400)
    }
    const matches = Array.isArray(body.matches) ? body.matches : []
    if (matches.length === 0) {
        return jsonResponse({ sent: false, queued: false, count: 0 })
    }
    if (!user.email) {
        return errorResponse("user has no email address on file", 400)
    }

    const db = adminClient()

    // Tier lookup. Default to 'free' if the row's missing — same
    // self-heal philosophy as /license.
    const { data: lic, error: licErr } = await db
        .from("licenses")
        .select("tier")
        .eq("user_id", user.id)
        .maybeSingle<License>()
    if (licErr) {
        return errorResponse(`license lookup failed: ${licErr.message}`, 500)
    }
    const tier: "free" | "paid" | "trial" = lic?.tier ?? "free"

    // ----------------------- FREE: digest with idempotency ---------------
    if (tier === "free" && type === "digest") {
        // Reserve today's digest slot atomically. The partial unique
        // index uq_one_digest_per_day guarantees at most one row with
        // (user_id, sent_date=today, email_type='digest'). If we get
        // a duplicate-key error, today's digest is already sent —
        // queue this batch for tomorrow's slot instead.
        const reserve = await db
            .from("email_log")
            .insert({
                user_id: user.id,
                email_type: "digest",
                match_count: matches.length,
            })
            .select("id")
            .maybeSingle()

        // PostgREST returns code "23505" on unique-violation.
        const dupKey = reserve.error?.code === "23505" ||
            (reserve.error?.message ?? "").includes("uq_one_digest_per_day")

        if (reserve.error && !dupKey) {
            return errorResponse(
                `email_log insert failed: ${reserve.error.message}`,
                500,
            )
        }

        if (dupKey) {
            // Already sent today — drop. The original behavior queued
            // for tomorrow via email_queue + queue-worker, but queue-
            // worker is currently a stub (returns 501) so queued rows
            // would never drain. Tomorrow's regular cron run will pick
            // up the latest matches anyway, so dropping in-flight is
            // correct user-facing behavior. (Audit finding 2026-05-06.)
            return jsonResponse({
                sent: false,
                queued: false,
                already_sent_today: true,
                count: matches.length,
            })
        }

        // Slot reserved; render + send.
        const reservedId = reserve.data?.id
        const { subject, html, text } = renderDigest(matches)
        const result = await sendEmail({
            to: user.email,
            subject,
            html,
            text,
        })

        if (result.status === "missing_api_key") {
            // Roll back the reservation so tomorrow's send isn't blocked.
            if (reservedId !== undefined) {
                await db.from("email_log").delete().eq("id", reservedId)
            }
            return errorResponse("email service not configured", 503)
        }

        if (!result.ok) {
            // Network or 4xx/5xx from Resend. Roll back so retry works.
            if (reservedId !== undefined) {
                await db.from("email_log").delete().eq("id", reservedId)
            }
            return errorResponse(
                `email send failed: ${result.status}`,
                502,
            )
        }

        // Stamp the message id for future delivery-tracking webhooks.
        if (reservedId !== undefined && result.id) {
            await db.from("email_log")
                .update({ resend_message_id: result.id })
                .eq("id", reservedId)
        }

        return jsonResponse({
            sent: true,
            queued: false,
            count: matches.length,
        })
    }

    // -------------------- PAID/TRIAL: instant; or free instant -----------
    // Free-tier 'instant' requests get coerced into a digest path is too
    // generous — the desktop side gates by tier before posting, so a
    // free-tier 'instant' POST means a misuse. Reject for clarity.
    if (tier === "free" && type === "instant") {
        return errorResponse(
            "instant emails require a paid or trial license",
            403,
        )
    }

    // Paid/trial path — send directly. No daily idempotency; we DO log
    // every send for budget tracking + future delivery webhooks.
    const { subject, html, text } = type === "instant"
        ? renderInstant(matches[0])
        : renderDigest(matches)
    const result = await sendEmail({
        to: user.email,
        subject,
        html,
        text,
    })

    if (result.status === "missing_api_key") {
        return errorResponse("email service not configured", 503)
    }
    if (!result.ok) {
        return errorResponse(
            `email send failed: ${result.status}`,
            502,
        )
    }

    // Record the send for budget tracking. Failure here is non-fatal —
    // the email already went out.
    const logIns = await db.from("email_log").insert({
        user_id: user.id,
        email_type: type,
        match_count: matches.length,
        resend_message_id: result.id || null,
    })
    if (logIns.error) {
        console.warn("email_log insert (paid path) failed:", logIns.error.message)
    }

    return jsonResponse({
        sent: true,
        queued: false,
        count: matches.length,
    })
})
