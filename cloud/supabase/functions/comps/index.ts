// /comps — eBay proxy with shared cache.
//
// The desktop scheduler hits this for every appraisal. First user to
// search a term pays the eBay round-trip; everyone else hits the cache
// for 12 hours. The cache is the actual cost moat — without it, every
// user with the same keyword would burn one eBay quota point per
// appraisal.
//
// Auth: ANON-CALLABLE (no requireUser). The Test Appraiser surface on
// the public landing/desktop must work without sign-in. The 12-hour
// cache plus the upstream eBay rate limits are the abuse mitigations —
// a malicious bot hammering random terms just fills the cache (cheap)
// and gets rate-limited at the eBay layer (~5k requests/day).
//
// Flow:
//   1. Read { search_term, region } from body
//   2. Normalize search_term -> cache key
//   3. SELECT comps_cache row; if fetched_at > NOW() - 12h, return it
//   4. Cache miss: searchEbay -> computeStats -> upsert -> return
//   5. Always return { stats, raw_comps, source: 'cache'|'fresh',
//                      age_seconds, search_term_normalized }
//
// Errors are returned as JSON with a clear message. The desktop side
// falls back to its local SQLite cache when this function 5xx's.
//
// IMPORTANT: this function must be deployed with `verify_jwt = false`
// in supabase/config.toml OR the dashboard "Verify JWT" toggle off.
// Otherwise Supabase's gateway 401s anon callers before our code runs.

import {
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import {
    searchEbay,
    computeStats,
    normalizeSearchTerm,
    HINT_TO_EBAY_CAT,
    type CompStats,
    type EbayItem,
} from "../_shared/ebay.ts"

const CACHE_TTL_SECONDS = 12 * 60 * 60  // 12 hours

// Price-band multipliers applied to LLM-output coarse range. Wide on
// purpose — drops accessories ($40 helmet vs $5000 motorcycle) but
// still admits damaged / discounted real items. Tukey trim handles
// within-band outliers downstream.
const PRICE_BAND_LOW_MULT  = 0.20
const PRICE_BAND_HIGH_MULT = 5.00

interface Body {
    search_term?: string
    region?: string
    force_refresh?: boolean
    /**
     * Semantic category hint from appraise-normalize ("motorcycle",
     * "phone", "furniture", "other"). Mapped server-side to an eBay
     * categoryId via HINT_TO_EBAY_CAT — this is the PRIMARY guard
     * against accessory contamination. "other"/missing → no category
     * filter; rely on price band + EXCLUDE_TERMS.
     */
    category_hint?: string | null
    /**
     * LLM-output rough expected price range in CAD. Used for the
     * SECONDARY guard: eBay query gets MinPrice = coarse_low * 0.20,
     * MaxPrice = coarse_high * 5.0. Drops cheap-accessory and
     * absurdly-expensive outliers before they reach the stats
     * pipeline. Sanity-checked: if coarse_high < coarse_low * 1.1 or
     * coarse_low <= 0, the band is silently skipped.
     */
    coarse_low?: number | null
    coarse_high?: number | null
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

    // Per-IP rate limit. The function is anonymous-callable by design,
    // and a single attacker can drain our daily eBay quota by hammering
    // unique terms (each one bypasses the cache and burns one quota
    // point). Cap at 60 distinct cache MISSES per IP per 10 minutes —
    // legitimate desktop polling never exceeds this. Hits to the cache
    // are not rate-limited, so the cost-of-attack stays asymmetric.
    // (Audit finding 2026-05-06.)
    const callerIp =
        req.headers.get("cf-connecting-ip")
        || req.headers.get("x-real-ip")
        || (req.headers.get("x-forwarded-for") || "").split(",")[0].trim()
        || "unknown"

    // Resolve category hint → eBay categoryId. Unknown hints fall back
    // to no-category-filter (same as "other"). This is graceful for
    // forward-compat: if the LLM ever emits a new hint we haven't
    // mapped, we just lose the primary guard for that one listing
    // instead of 4xx'ing the whole request.
    let categoryHint = (body.category_hint ?? "").trim().toLowerCase()

    // Belt-and-suspenders heuristic: if the LLM said "other" (or didn't
    // classify) but the search term looks like a vehicle, override the
    // hint to the right vehicle category. Year-make-model patterns are
    // the most common vehicle-misclassification failure mode and they
    // produce catastrophic comp pollution (50 mufflers / floor consoles)
    // when categoryId is null. Falling back to a regex sniff on the
    // search term itself catches the LLM's blind spots.
    if (!categoryHint || categoryHint === "other") {
        const t = rawTerm.toLowerCase()
        // Year + make is the strongest signal. Ranges 1900-2099 cover
        // every realistic listing.
        const yearMakeRe = /\b(19|20)\d{2}\s+(honda|toyota|ford|chevy|chevrolet|gmc|ram|dodge|jeep|nissan|hyundai|kia|mazda|subaru|volkswagen|vw|audi|bmw|mercedes|mercedes-benz|porsche|tesla|volvo|acura|infiniti|lexus|cadillac|chrysler|buick|lincoln|mitsubishi|fiat|alfa|land\s*rover|range\s*rover|jaguar|mini|saab|smart|scion|pontiac|saturn|oldsmobile|hummer|isuzu|suzuki|mercury)\b/
        const motorcycleMakeRe = /\b(harley|harley[\s-]davidson|ducati|kawasaki|yamaha\s+(yzf|r1|r6|fz|mt)|honda\s+(cbr|crf|cb|grom|rebel|shadow|africa|gold\s*wing|scl)|suzuki\s+(gsx|sv|dr|hayabusa|gsxr)|triumph|aprilia|ktm|bmw\s+(r|s|f|g)\d|indian|royal\s*enfield|piaggio|vespa)\b/
        const standaloneVehicleRe = /\b(motorcycle|motorbike|sportbike|cruiser|dirt\s*bike|moped|scooter|sidecar|sedan|coupe|hatchback|suv|crossover|minivan|pickup|f-?150|f-?250|silverado|tacoma|tundra|frontier|ranger|colorado)\b/
        const truckRe = /\b(pickup|f-?150|f-?250|f-?350|silverado|sierra|tacoma|tundra|frontier|ranger|colorado|titan)\b/
        const rvRe = /\b(motorhome|class\s+[abc]|camper\s*van|travel\s*trailer|fifth\s*wheel|sprinter\s+conversion|airstream|winnebago|jayco)\b/
        const atvRe = /\b(atv|quad|4-?wheeler|side[\s-]by[\s-]side|utv|polaris|can[\s-]am)\b/
        const boatRe = /\b(sailboat|powerboat|jet\s*ski|seadoo|sea[\s-]doo|waverunner|pontoon|fishing\s*boat|yacht|dinghy|kayak|canoe)\b/

        // Electronics regex — covers cases the LLM misclassifies
        // (especially future products like "iPhone 17" which the LLM
        // doesn't recognize but the regex catches by brand+model).
        const phoneRe = /\b(iphone(\s*\d+)?|galaxy\s*s\d+|pixel\s*\d+|oneplus|xiaomi|redmi|huawei\s*p\d+|samsung\s+galaxy)\b/
        const tabletRe = /\b(ipad(\s*(pro|air|mini))?|galaxy\s*tab|surface\s*pro|kindle\s*fire|pixel\s*tablet)\b/
        const laptopRe = /\b(macbook|thinkpad|elitebook|chromebook|surface\s*laptop|dell\s*xps|hp\s*pavilion|lenovo\s*yoga|razer\s*blade|asus\s*zenbook|acer\s*aspire)\b/
        const cameraRe = /\b(canon\s*(eos|r\d+|m\d+)|nikon\s*(d\d+|z\d+)|sony\s*(a\d+|alpha)|fujifilm\s*x|gopro\s*hero|dji\s*(osmo|pocket|action))\b/
        const tvRe = /\b(\d{2,3}[\s-]?inch\s*(tv|television)|oled\s*tv|qled\s*tv|smart\s*tv|samsung\s*\d{2}\b|lg\s*\d{2}\b|sony\s*bravia)\b/
        const applianceRe = /\b(refrigerator|fridge|washer|dryer|dishwasher|microwave|range\s*hood|stove|oven|coffee\s*maker|espresso\s*machine|kitchenaid|vitamix|blender|stand\s*mixer|vacuum\s*cleaner|robot\s*vacuum|roomba)\b/
        const furnitureRe = /\b(sofa|sectional|couch|loveseat|recliner|dining\s*table|coffee\s*table|side\s*table|dresser|bookshelf|bed\s*frame|mattress|nightstand|desk|office\s*chair|aeron|herman\s*miller|ikea)\b/

        let inferred: string | null = null
        if (motorcycleMakeRe.test(t) || /\b(motorcycle|motorbike|sportbike|cruiser|dirt\s*bike|moped|scooter)\b/.test(t)) {
            inferred = "motorcycle"
        } else if (truckRe.test(t)) {
            inferred = "truck"
        } else if (rvRe.test(t)) {
            inferred = "rv"
        } else if (atvRe.test(t)) {
            inferred = "atv"
        } else if (boatRe.test(t)) {
            inferred = "boat"
        } else if (yearMakeRe.test(t) || standaloneVehicleRe.test(t)) {
            inferred = "car"
        } else if (phoneRe.test(t)) {
            inferred = "phone"
        } else if (tabletRe.test(t)) {
            inferred = "tablet"
        } else if (laptopRe.test(t)) {
            inferred = "laptop"
        } else if (cameraRe.test(t)) {
            inferred = "camera"
        } else if (tvRe.test(t)) {
            inferred = "tv"
        } else if (applianceRe.test(t)) {
            inferred = "appliance"
        } else if (furnitureRe.test(t)) {
            inferred = "furniture"
        }

        if (inferred) {
            console.log(`category_hint regex fallback: "${rawTerm}" → ${inferred} (LLM said "${categoryHint || "(none)"}")`)
            categoryHint = inferred
        }
    }

    const categoryId = categoryHint
        ? (HINT_TO_EBAY_CAT[categoryHint] ?? null)
        : null

    console.log(
        `/comps: term="${rawTerm}" hint=${categoryHint || "(none)"} ` +
        `categoryId=${categoryId || "(null)"} ` +
        `coarse=[${body.coarse_low ?? "?"},${body.coarse_high ?? "?"}]`,
    )

    // Resolve coarse price band. Sanity check: skip if range is
    // degenerate or clearly bogus (LLM hallucinated zeroes / inverted
    // bounds). Without this we'd accidentally pass min=0 max=0 which
    // eBay treats as a hard "no results" filter.
    const coarseLow  = typeof body.coarse_low  === "number" ? body.coarse_low  : null
    const coarseHigh = typeof body.coarse_high === "number" ? body.coarse_high : null
    let minPrice: number | null = null
    let maxPrice: number | null = null
    if (coarseLow != null && coarseHigh != null
            && coarseLow > 0 && coarseHigh > coarseLow * 1.1) {
        minPrice = coarseLow  * PRICE_BAND_LOW_MULT
        maxPrice = coarseHigh * PRICE_BAND_HIGH_MULT
    }

    const db = adminClient()  // service-role; the cache is global

    // Cache key now includes category_hint so a "phone" search and a
    // "phone" search both for "iPhone 12 64GB" hit the same row, but
    // a future hypothetical hint change re-keys cleanly.
    const cacheKeyHint = categoryHint || "_none_"

    // 1. Cache check (skip if force_refresh)
    if (!forceRefresh) {
        const { data: cached } = await db
            .from("comps_cache")
            .select("stats_json, raw_comps_json, fetched_at, category_hint")
            .eq("search_term_normalized", normalized)
            .eq("region", region)
            .maybeSingle()

        // Only honor the cached row if its category_hint matches the
        // current request's hint. A pre-013 row with category_hint=null
        // also hits the cache for hint-less callers.
        const cachedHint = (cached as { category_hint?: string | null } | null)?.category_hint ?? "_none_"
        if (cached && cachedHint === cacheKeyHint) {
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
                    category_hint: categoryHint || null,
                    category_id: categoryId,
                    price_band: minPrice != null
                        ? { min: minPrice, max: maxPrice }
                        : null,
                })
            }
        }
    }

    // 2. Cache miss — about to burn an eBay quota point. Apply per-IP
    //    rate limit BEFORE the upstream call, so abuse is cheap to
    //    block. Track via comps_rate_limit table (created on first use,
    //    auto-pruned by 10-min window). Fail-open if the rate-limit
    //    table doesn't exist yet (legitimate during migration rollout).
    try {
        const tenMinAgo = new Date(Date.now() - 10 * 60 * 1000).toISOString()
        const { count, error: rlErr } = await db
            .from("comps_rate_limit")
            .select("id", { count: "exact", head: true })
            .eq("caller_ip", callerIp)
            .gte("created_at", tenMinAgo)
        if (!rlErr && typeof count === "number" && count >= 60) {
            return errorResponse("rate limited — too many comp lookups", 429)
        }
        await db.from("comps_rate_limit").insert({
            caller_ip: callerIp,
            search_term: normalized.slice(0, 200),
        })
    } catch (_) {
        // Fail-open during initial deployment.
    }

    let items: EbayItem[]
    let stats: CompStats
    try {
        items = await searchEbay({
            keywords: rawTerm,
            region,
            limit: 50,
            categoryId,
            minPrice,
            maxPrice,
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
        category_hint: categoryHint || null,
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
        category_hint: categoryHint || null,
        category_id: categoryId,
        price_band: minPrice != null ? { min: minPrice, max: maxPrice } : null,
    })
})
