// Shared auth + response helpers used by every Edge Function.
//
// requireUser validates the bearer token in the Authorization header
// against Supabase Auth and returns the user. Throws a Response (not
// an Error) on failure so callers can `try { ... } catch (r) { return r }`.
//
// jsonResponse, errorResponse, corsHeaders are convenience wrappers
// to keep route handlers tight.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0"

export interface AuthedUser {
    id: string
    email: string
}

// CORS — keep `*` for the truly public anon endpoints (test appraiser,
// public landing) but lock down PII endpoints to the trusted origins.
// The desktop app sends a `null` Origin header (file:// + pywebview),
// so we always allow null + 127.0.0.1 + the production domain.
const ALLOWED_ORIGINS = [
    "https://getbullseye.app",
    "https://www.getbullseye.app",
    "http://127.0.0.1",          // pywebview dev
    "http://localhost",
]

const CORS_PUBLIC = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers":
        "authorization, x-client-info, apikey, content-type, x-app-version",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Vary": "Origin",
}

function corsForOrigin(origin: string | null): Record<string, string> {
    // Match by prefix so 127.0.0.1:<port> + localhost:<port> work.
    if (!origin) {
        return {
            "Access-Control-Allow-Origin": "https://getbullseye.app",
            "Access-Control-Allow-Headers":
                "authorization, x-client-info, apikey, content-type, x-app-version",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Vary": "Origin",
        }
    }
    const allowed = ALLOWED_ORIGINS.some(o =>
        origin === o || origin.startsWith(o + ":") || origin.startsWith(o + "/")
    )
    return {
        "Access-Control-Allow-Origin": allowed ? origin : "https://getbullseye.app",
        "Access-Control-Allow-Headers":
            "authorization, x-client-info, apikey, content-type, x-app-version",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Vary": "Origin",
    }
}

/**
 * CORS headers. Default form is the wide-open `*` set used by the
 * anonymous endpoints (`/comps`, `/telemetry`). PII-bearing endpoints
 * (`/license`, `/account-export`, `/billing-portal`, `/referral-info`,
 * etc.) MUST pass `req` so we can lock the Allow-Origin to the trusted
 * list. (Audit finding 2026-05-06.)
 *
 * Usage:
 *     // anonymous public:   corsHeaders()
 *     // user-PII bearing:   corsHeaders(req)
 */
export function corsHeaders(req?: Request): HeadersInit {
    if (!req) return CORS_PUBLIC
    const origin = req.headers.get("Origin")
    return corsForOrigin(origin)
}

export function jsonResponse(data: unknown, status = 200): Response {
    return new Response(JSON.stringify(data), {
        status,
        headers: {
            "Content-Type": "application/json",
            ...CORS_PUBLIC,
        },
    })
}

export function errorResponse(message: string, status = 400): Response {
    return jsonResponse({ error: message }, status)
}

/**
 * Extract + validate the Authorization bearer token. Returns the
 * authenticated user on success.
 *
 * Throws a Response object (not a real Error) so route handlers can
 * just `return await requireUser(req)` and let unhandled throws bubble
 * up to a top-level catch. Pattern:
 *
 *     try {
 *         const user = await requireUser(req)
 *         // ...
 *     } catch (r) {
 *         if (r instanceof Response) return r
 *         throw r
 *     }
 */
export async function requireUser(req: Request): Promise<AuthedUser> {
    const auth = req.headers.get("Authorization") ?? ""
    if (!auth.startsWith("Bearer ")) {
        throw errorResponse("missing bearer token", 401)
    }
    const token = auth.slice(7)

    // Build a Supabase client scoped to the service role for the
    // server-side getUser call. We use service role here only to
    // validate the JWT — we don't act on the database with it from
    // this helper.
    const supabaseUrl = Deno.env.get("SUPABASE_URL")
    const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")
    if (!supabaseUrl || !serviceKey) {
        throw errorResponse("server misconfigured", 500)
    }
    const admin = createClient(supabaseUrl, serviceKey, {
        auth: { persistSession: false },
    })

    const { data, error } = await admin.auth.getUser(token)
    if (error || !data?.user) {
        throw errorResponse("invalid or expired token", 401)
    }
    return {
        id: data.user.id,
        email: data.user.email ?? "",
    }
}

/**
 * Build a per-request Supabase client that respects RLS by passing
 * through the user's JWT. Use this for any reads/writes that should
 * be subject to RLS policies. For admin operations (Stripe webhook,
 * trigger work) use the service-role admin client instead.
 */
export function userClient(req: Request) {
    const auth = req.headers.get("Authorization") ?? ""
    return createClient(
        Deno.env.get("SUPABASE_URL")!,
        Deno.env.get("SUPABASE_ANON_KEY")!,
        {
            global: { headers: { Authorization: auth } },
            auth: { persistSession: false },
        },
    )
}

/** Service-role admin client. Bypasses RLS — use sparingly. */
export function adminClient() {
    return createClient(
        Deno.env.get("SUPABASE_URL")!,
        Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
        { auth: { persistSession: false } },
    )
}
