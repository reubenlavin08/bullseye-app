// /comps — eBay proxy with shared cache.
//
// The desktop scheduler hits this for every appraisal. First user to
// search a term pays the eBay round-trip; everyone else hits the cache
// for 12 hours. The cache is the actual cost moat — without it, every
// user with the same keyword would burn one eBay quota point per
// appraisal.
//
// Flow:
//   1. requireUser
//   2. Read { search_term, region } from body
//   3. Normalize search_term -> cache key
//   4. SELECT comps_cache row; if fetched_at > NOW() - 12h, return it
//   5. Cache miss: searchEbay -> computeStats -> upsert -> return
//   6. Always return { stats, raw_comps, source: 'cache'|'fresh',
//                      age_seconds, search_term_normalized }
//
// Errors are returned as JSON with a clear message. The desktop side
// falls back to its local SQLite cache when this function 5xx's.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import {
    searchEbay,
    computeStats,
    normalizeSearchTerm,
    type CompStats,
    type EbayItem,
} from "../_shared/ebay.ts"

const CACHE_TTL_SECONDS = 12 * 60 * 60  // 12 hours

interface Body {
    search_term?: string
    region?: string
    force_refresh?: boolean
}

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST") {
        return errorResponse("method not allowed", 405)
    }

    try {
        await requireUser(req)
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
    const rawTerm = (body.search_term ?? "").trim()
    if (!rawTerm) {
        return errorResponse("search_term required", 400)
    }
    const region = body.region ?? "EBAY-ENCA"
    const forceRefresh = !!body.force_refresh
    const normalized = normalizeSearchTerm(rawTerm)
    if (!normalized) {
        return errorResponse("search_term must contain at least one word", 400)
    }

    const db = adminClient()  // service-role; the cache is global

    // 1. Cache check (skip if force_refresh)
    if (!forceRefresh) {
        const { data: cached } = await db
            .from("comps_cache")
            .select("stats_json, raw_comps_json, fetched_at")
            .eq("search_term_normalized", normalized)
            .eq("region", region)
            .maybeSingle()

        if (cached) {
            const ageMs = Date.now() - new Date(cached.fetched_at).getTime()
            const ageSeconds = Math.floor(ageMs / 1000)
            if (ageSeconds < CACHE_TTL_SECONDS) {
                return jsonResponse({
                    stats: cached.stats_json,
                    raw_comps: cached.raw_comps_json ?? [],
                    source: "cache",
                    age_seconds: ageSeconds,
                    search_term_normalized: normalized,
                    region,
                })
            }
        }
    }

    // 2. Cache miss — fetch fresh from eBay
    let items: EbayItem[]
    let stats: CompStats
    try {
        items = await searchEbay({
            keywords: rawTerm,
            region,
            limit: 50,
        })
        stats = computeStats(items)
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        return errorResponse(`ebay fetch failed: ${msg}`, 502)
    }

    // 3. Upsert into shared cache. Use service-role since this is a
    //    cross-user write that must succeed regardless of RLS.
    const upsertRow = {
        search_term_normalized: normalized,
        region,
        stats_json: stats,
        raw_comps_json: items,
        fetched_at: new Date().toISOString(),
    }
    const { error: upsertErr } = await db
        .from("comps_cache")
        .upsert(upsertRow, { onConflict: "search_term_normalized,region" })

    if (upsertErr) {
        // Don't fail the response — return the fresh data anyway. The
        // next request will retry the cache write. Log so we notice if
        // this becomes a pattern.
        console.warn("comps_cache upsert failed:", upsertErr.message)
    }

    return jsonResponse({
        stats,
        raw_comps: items,
        source: "fresh",
        age_seconds: 0,
        search_term_normalized: normalized,
        region,
    })
})
