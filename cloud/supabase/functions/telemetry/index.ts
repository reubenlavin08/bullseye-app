// /telemetry — bulk-insert client telemetry events.
//
// Auth: JWT preferred (links events to user_id), but anonymous
// install_id-only events are also accepted (for pre-login app_open
// events). When the request comes in with the anon-key bearer (no
// real user JWT), we insert with user_id = NULL.
//
// Body:
//   { events: [{ event_name, properties, app_version, install_id }] }
//
// Caps:
//   events.length <= 100         (reject 400)
//   properties JSON <= 4 KB      (drop properties on overage; keep event)
//
// RLS: telemetry_events policy is `auth.uid() = user_id OR user_id IS NULL`.
// 010_telemetry_grant.sql adds INSERT to authenticated + anon so the
// anonymous-NULL path doesn't 403. We use the service-role admin
// client server-side regardless, but the GRANT keeps direct REST
// inserts from clients working too.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0"
import {
    jsonResponse,
    errorResponse,
    corsHeaders,
    adminClient,
} from "../_shared/auth.ts"

interface InboundEvent {
    event_name?: unknown
    properties?: unknown
    app_version?: unknown
    install_id?: unknown
}

interface Body {
    events?: InboundEvent[]
}

const MAX_EVENTS = 100
const MAX_PROPERTIES_BYTES = 4096

function sizeOfJson(value: unknown): number {
    try {
        return new TextEncoder().encode(JSON.stringify(value)).length
    } catch (_) {
        return Number.POSITIVE_INFINITY
    }
}

/**
 * Resolve the calling user's id from the Authorization header.
 *
 * Returns null in two cases:
 *   - no bearer header at all
 *   - the bearer is the anon key (or any other non-user JWT)
 * Both are valid here — we accept anonymous events keyed only by
 * install_id, e.g. for pre-login app_open events.
 *
 * Real auth errors (malformed JWT, expired user JWT) ALSO fall
 * through to null rather than 401: the desktop client always sends
 * the anon key as a fallback, so we can't distinguish "tried to log
 * in and failed" from "sent anon-only on purpose" at this layer.
 * The downside is small — the worst case is a logged-out client
 * still gets its events through anonymously.
 */
async function maybeUserId(req: Request): Promise<string | null> {
    const auth = req.headers.get("Authorization") ?? ""
    if (!auth.startsWith("Bearer ")) return null
    const token = auth.slice(7)
    const supabaseUrl = Deno.env.get("SUPABASE_URL")
    const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")
    if (!supabaseUrl || !serviceKey) return null

    const admin = createClient(supabaseUrl, serviceKey, {
        auth: { persistSession: false },
    })
    try {
        const { data, error } = await admin.auth.getUser(token)
        if (error || !data?.user) return null
        return data.user.id
    } catch (_) {
        return null
    }
}

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST") {
        return errorResponse("method not allowed", 405)
    }

    let body: Body
    try {
        body = await req.json()
    } catch (_) {
        return errorResponse("invalid JSON body", 400)
    }

    const events = Array.isArray(body.events) ? body.events : null
    if (!events) {
        return errorResponse("events must be an array", 400)
    }
    if (events.length === 0) {
        return jsonResponse({ inserted: 0 })
    }
    if (events.length > MAX_EVENTS) {
        return errorResponse(
            `too many events: ${events.length} > ${MAX_EVENTS}`,
            400,
        )
    }

    // Anonymous OK; user_id stays null in that case.
    const userId = await maybeUserId(req)

    // Build the insert rows. Skip events with no event_name (not worth
    // a whole-batch reject). Truncate oversized properties rather than
    // failing the event — the desktop side already does this client-
    // side, but defense in depth is cheap.
    const rows: Array<Record<string, unknown>> = []
    for (const e of events) {
        const name = typeof e?.event_name === "string"
            ? e.event_name.slice(0, 64)
            : null
        if (!name) continue
        const installId = typeof e?.install_id === "string"
            ? e.install_id.slice(0, 64)
            : null
        const appVersion = typeof e?.app_version === "string"
            ? e.app_version.slice(0, 32)
            : null
        let props = (e?.properties && typeof e.properties === "object")
            ? e.properties as Record<string, unknown>
            : null
        if (props && sizeOfJson(props) > MAX_PROPERTIES_BYTES) {
            props = { _telemetry_error: "properties_too_large" }
        }
        rows.push({
            user_id: userId,
            install_id: installId,
            event_name: name,
            properties: props,
            app_version: appVersion,
        })
    }

    if (rows.length === 0) {
        return jsonResponse({ inserted: 0 })
    }

    // Use the service-role client so we never get RLS-blocked. The
    // function itself is the trust boundary — we've already gated on
    // the JWT for user_id linkage; everything else is anonymous data
    // we're happy to accept.
    const db = adminClient()
    const { error } = await db.from("telemetry_events").insert(rows)
    if (error) {
        return errorResponse(
            `telemetry insert failed: ${error.message}`,
            500,
        )
    }

    return jsonResponse({ inserted: rows.length })
})
