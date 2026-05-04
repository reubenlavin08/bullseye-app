// /comps — eBay proxy with shared cache.
//
// Flow:
//   1. requireUser
//   2. Read { search_term, region } from body
//   3. Normalize search_term -> cache key
//   4. Check comps_cache; if fresh (< 12h), return it
//   5. Cache miss: call searchEbay(), computeStats(), upsert into cache
//   6. Return { stats, source: 'cache'|'fresh', age_seconds }
//
// Future cost ceiling: reject calls when user has hit a per-day quota
// (track via telemetry_events or a dedicated comps_call_log table).
// Not enforced by default — wait until abuse appears.

// import { requireUser, jsonResponse, corsHeaders } from '../_shared/auth.ts'
// import { searchEbay, computeStats, normalizeSearchTerm } from '../_shared/ebay.ts'

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        // return new Response(null, { headers: corsHeaders() })
    }
    // const user = await requireUser(req)
    // const { search_term, region = 'EBAY-ENCA' } = await req.json()
    // const key = normalizeSearchTerm(search_term)
    // ... cache check + fetch + upsert ...
    // return jsonResponse({ stats, source, age_seconds })
    return new Response("not implemented", { status: 501 })
})
