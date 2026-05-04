// Shared auth helpers for Edge Functions.
//
// Every protected function calls `requireUser(req)` to validate the
// JWT in the Authorization header and return the user_id. Unauth'd
// requests get a 401 thrown immediately.

// import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

export interface AuthedUser {
    id: string
    email: string
}

export async function requireUser(req: Request): Promise<AuthedUser> {
    // TODO:
    //   1. Extract bearer token from Authorization header
    //   2. supabase.auth.getUser(token) -> AuthedUser
    //   3. Throw 401 Response if invalid
    throw new Error("not implemented")
}

export function jsonResponse(data: unknown, status = 200): Response {
    return new Response(JSON.stringify(data), {
        status,
        headers: { "Content-Type": "application/json" },
    })
}

export function corsHeaders(): HeadersInit {
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
    }
}
