// /appraise-normalize — batched MiniMax title-normalization.
//
// Takes a batch of raw Marketplace listings and returns each one's
// canonical product identifier (e.g. "iPhone 12 - Available in Good
// Condition" → "iPhone 12 64GB"), a coarse expected price range, a
// confidence level, a "is this listing scoreable at all?" flag, and
// any red flags the LLM surfaces (broken_screen, icloud_locked, etc).
//
// Why batched: MiniMax-Text-01 charges per-token. The system prompt
// is ~400 tokens. Sending one listing per request costs the system
// prompt N times. Sending 40 listings in one request costs the
// system prompt ONCE for all 40 → ~75% input-token reduction at N=40.
// The desktop side bundles up survivors after the cheap regex reject
// filters and ships them as a batch.
//
// Auth: requireUser. We're spending the user's quota implicitly here
// (cost gating happens upstream, but anon-callable would let anyone
// burn the MiniMax key). Service role behind requireUser.
//
// Flow:
//   1. requireUser
//   2. Read { items: [{listing_url, title, body, ask_price}, ...] } from body
//   3. For each item, look up cache by listing_url + current prompt_version
//   4. Send the cache-misses to MiniMax in one batched call
//   5. Upsert results into normalized_listings
//   6. Return enriched array, preserving input order
//
// IMPORTANT: this function is intentionally STATELESS w.r.t. the
// scraping path. It does not know or care WHERE the listings came
// from. Desktop scrapes (Facebook IP-rate-limit reasons), this
// normalizes. One job, done well.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"

// Bump when the system prompt or schema changes materially. Old rows
// stay in the table but are ignored on cache lookup, so the next
// pass re-normalizes with the new prompt.
//   v2 (2026-05-06): preserve year-make-model patterns ("2018 Honda
//                    Civic LX", "2023 Honda SCL500"). v1 was stripping
//                    years which destroyed comp accuracy for vehicles.
//   v3 (2026-05-06): add category_hint (motorcycle/car/phone/...).
//                    Drives eBay categoryId filter on /comps so
//                    helmets/parts/accessories physically can't appear
//                    in the result set. Fixes "Honda motorcycle returns
//                    50 helmets" failure mode.
//   v4 (2026-05-06): tighten category_hint rules. v3 was returning
//                    "other" too often for vehicles (the LLM treated
//                    "Bike" as ambiguous, and "Toyota Sienna" as a
//                    generic noun rather than a vehicle). v4 spells
//                    out year-make-model = vehicle and gives explicit
//                    examples per category. Concrete patterns beat
//                    abstract "pick the closest" guidance.
const PROMPT_VERSION = 4

const MINIMAX_MODEL = Deno.env.get("MINIMAX_MODEL") ?? "MiniMax-Text-01"
const MINIMAX_BASE_URL =
    Deno.env.get("MINIMAX_BASE_URL") ?? "https://api.minimax.io/v1"
const MAX_BATCH_SIZE = 50    // hard cap; over this we slice into chunks
const HTTP_TIMEOUT_MS = 45_000  // per request; batches of 50 take ~5-15s

interface InItem {
    listing_url: string
    title: string
    body?: string
    ask_price?: number | null
}
interface NormalizeResult {
    listing_url: string
    canonical_kind: string
    coarse_low: number
    coarse_high: number
    confidence: "low" | "medium" | "high"
    worth_deep: boolean
    category_hint: string
    red_flags: string[]
    reasoning?: string
    cache_hit: boolean
}
interface Body { items?: InItem[] }

const SYSTEM_PROMPT = `You are a Facebook Marketplace listing normalizer.

You receive a batch of raw listings (title + body + asking price). For each one, return:
- canonical_kind: a clean canonical product identifier suitable as an eBay search query. Strip seller phrases like "Available in Good Condition", "MUST GO!", emojis, prices, locations, contact info. Include model + capacity/size if known (e.g. "iPhone 12 64GB", "MacBook Pro 14 M2", "Aeron Size B"). PRESERVE year-make-model patterns when present — for vehicles, motorcycles, RVs, instruments, and any product where year is a price-driving spec, KEEP the year (e.g. "2018 Honda Civic LX", "2023 Honda SCL500", "1965 Fender Stratocaster"). Only strip year when it's clearly noise (e.g. "Bought in 2020 — selling now"). If the listing is unscoreable (services, WTB, no clear product), return an empty string.
- category_hint: ONE of these exact strings, picked using the rules below. NEVER invent new strings.
    Allowed: "motorcycle" | "car" | "truck" | "rv" | "atv" | "boat" | "phone" | "laptop" | "tablet" | "camera" | "tv" | "appliance" | "furniture" | "other".

    Classification rules — apply IN ORDER, first match wins:
    1. Vehicle detection: ANY year-make-model pattern (e.g. "2018 Honda Civic", "2009 Toyota Sienna", "2023 Honda SCL500", "1995 Ford F-150") → pick the matching vehicle category. Even one-word listings like "Bike", "Motorcycle", "Truck", "Car", "RV", "ATV", "Boat", "Sedan", "SUV", "Pickup", "Cruiser", "Sportbike" → use the matching vehicle category. The literal word "vehicle" → "car".
       - "motorcycle" for: any motorcycle / bike with engine — Honda CBR, Yamaha R1, Harley, Ducati, sportbike, cruiser, dual-sport, naked bike, scooter (gas or electric), moped, dirt bike, bike when used in a motorized context.
       - "car" for: sedan, coupe, hatchback, SUV, crossover, minivan, station wagon, sports car, "Civic", "Camry", "Sienna", anything Honda/Toyota/Ford/etc. that isn't explicitly a truck.
       - "truck" for: pickup, F-150, Silverado, Ram, Tacoma, work truck, flatbed, dump truck, anything explicitly described as a "truck".
       - "rv" for: motorhome, camper van, travel trailer, fifth wheel, Class A/B/C, RV, "Sprinter conversion".
       - "atv" for: quad, 4-wheeler, side-by-side, UTV, dirt quad, sport quad, ATV.
       - "boat" for: powerboat, sailboat, fishing boat, jet ski, watercraft, dinghy, canoe with motor, pontoon, anything aquatic with a hull.
    2. If listing names a phone/tablet/laptop/camera brand+model (e.g. "iPhone 12", "Galaxy S23", "MacBook Pro 14 M2", "iPad Air", "Sony A7", "Canon R5", "Nikon Z6") → use the matching electronics category.
    3. "tv" for any television (LG OLED, Samsung QLED, Sony Bravia, "55-inch TV").
    4. "appliance" for fridge, washer, dryer, dishwasher, microwave, oven, stove, range hood, vacuum, blender, mixer.
    5. "furniture" for couch, sofa, sectional, dining table, dresser, bookshelf, bed frame, mattress, desk, office chair, Aeron.
    6. None of the above → "other" (disables eBay category filter; relies on keyword + price band).

    Critical: when in doubt between a vehicle category and "other", PICK the vehicle category. The cost of mis-classifying a non-vehicle as "car" is small (slightly stricter eBay search); the cost of mis-classifying a vehicle as "other" is huge (eBay returns 50 floor mats and the appraisal is unusable).
- coarse_low / coarse_high: your rough expected used-price range in CAD. Used to gate expensive comp lookups — be wide rather than narrow.
- confidence: "low" | "medium" | "high". "high" if the listing names model + condition explicitly. "medium" if model is implied. "low" otherwise.
- worth_deep: true if this is a real product listing that's worth attempting to score. false if it's a buyer post (WTB / ISO / "looking for"), a service, off-topic, or so vague that no canonical_kind can be determined.
- red_flags: array of short strings flagging issues a buyer should know about. Use only these tokens: "broken_screen", "water_damage", "icloud_locked", "for_parts", "as_is", "bundle_of_items", "missing_pieces", "cosmetic_damage", "no_charger", "non_functional". Empty array if none apply.
- reasoning: one-sentence explanation, optional.

Return ONLY a JSON object with shape {"results": [{...one per input, same id...}]}. No prose, no markdown fences.`

/** MiniMax JSON output shape. Robust to extra fields. */
interface MMResult {
    id: string
    canonical_kind: string
    coarse_low: number
    coarse_high: number
    confidence: string
    worth_deep: boolean
    category_hint?: string
    red_flags?: string[]
    reasoning?: string
}

const ALLOWED_CATEGORY_HINTS = new Set([
    "motorcycle", "car", "truck", "rv", "atv", "boat",
    "phone", "laptop", "tablet", "camera", "tv",
    "appliance", "furniture", "other",
])

/** Validate the LLM's category_hint, defaulting to "other" on miss. */
function safeHint(raw: unknown): string {
    if (typeof raw !== "string") return "other"
    const v = raw.trim().toLowerCase()
    return ALLOWED_CATEGORY_HINTS.has(v) ? v : "other"
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
    const items = (body.items ?? []).slice(0, MAX_BATCH_SIZE)
        .filter((it) => it && typeof it.listing_url === "string"
            && typeof it.title === "string")
    if (items.length === 0) {
        return errorResponse("items[] required (with listing_url + title)", 400)
    }

    const db = adminClient()

    // 1. Cache lookup. Pull every row matching one of the URLs at
    //    the current prompt_version.
    const urls = items.map((it) => it.listing_url)
    const { data: cachedRows } = await db
        .from("normalized_listings")
        .select("listing_url, canonical_kind, coarse_low, coarse_high, " +
                "confidence, worth_deep, category_hint, red_flags, reasoning")
        .in("listing_url", urls)
        .eq("prompt_version", PROMPT_VERSION)

    const cacheMap = new Map<string, MMResult>()
    for (const r of (cachedRows ?? [])) {
        cacheMap.set(r.listing_url, {
            id: r.listing_url,
            canonical_kind: r.canonical_kind,
            coarse_low: r.coarse_low,
            coarse_high: r.coarse_high,
            confidence: r.confidence,
            worth_deep: r.worth_deep,
            category_hint: safeHint(r.category_hint),
            red_flags: r.red_flags ?? [],
            reasoning: r.reasoning ?? undefined,
        })
    }

    const misses = items.filter((it) => !cacheMap.has(it.listing_url))

    // 2. MiniMax call for the misses (if any).
    let llmResults: MMResult[] = []
    if (misses.length > 0) {
        try {
            llmResults = await callMiniMaxBatch(misses)
        } catch (e) {
            const msg = e instanceof Error ? e.message : String(e)
            console.error("MiniMax batch call failed:", msg)
            // Don't 5xx — return whatever we have from cache and let
            // the desktop fall back to raw-title comp lookup for the
            // misses. The desktop already handles missing/empty
            // canonical_kind gracefully.
            return jsonResponse({
                ok: true,
                results: items.map((it): NormalizeResult | null => {
                    const hit = cacheMap.get(it.listing_url)
                    if (!hit) return null
                    const c: "low" | "medium" | "high" =
                        hit.confidence === "high" ? "high"
                        : hit.confidence === "medium" ? "medium"
                        : "low"
                    return {
                        listing_url: it.listing_url,
                        canonical_kind: hit.canonical_kind,
                        coarse_low: hit.coarse_low,
                        coarse_high: hit.coarse_high,
                        confidence: c,
                        worth_deep: hit.worth_deep,
                        category_hint: safeHint(hit.category_hint),
                        red_flags: hit.red_flags ?? [],
                        reasoning: hit.reasoning,
                        cache_hit: true,
                    }
                }).filter((x): x is NormalizeResult => x !== null),
                llm_error: msg,
            })
        }

        // 3. Upsert MiniMax results into the cache. Each row keyed on
        //    listing_url + prompt_version (PK is just listing_url, but
        //    we filter by version on read).
        const rowsToInsert = misses.map((it) => {
            const r = llmResults.find((x) => x.id === it.listing_url)
            if (!r) return null
            return {
                listing_url: it.listing_url,
                canonical_kind: r.canonical_kind,
                coarse_low: clampInt(r.coarse_low, 0, 1_000_000),
                coarse_high: clampInt(r.coarse_high, 0, 1_000_000),
                confidence: ["low", "medium", "high"].includes(r.confidence)
                    ? r.confidence : "low",
                worth_deep: !!r.worth_deep,
                category_hint: safeHint(r.category_hint),
                red_flags: Array.isArray(r.red_flags) ? r.red_flags : [],
                reasoning: r.reasoning ?? null,
                prompt_version: PROMPT_VERSION,
                model: MINIMAX_MODEL,
                normalized_at: new Date().toISOString(),
                input_title: it.title.slice(0, 1000),
                input_body: (it.body ?? "").slice(0, 1000),
                input_ask: it.ask_price ?? null,
            }
        }).filter((x): x is NonNullable<typeof x> => x !== null)

        if (rowsToInsert.length > 0) {
            const { error: insertErr } = await db
                .from("normalized_listings")
                .upsert(rowsToInsert, { onConflict: "listing_url" })
            if (insertErr) {
                console.warn("normalized_listings upsert error:", insertErr.message)
                // Don't fail the request — return results to caller anyway.
            }
        }
    }

    // 4. Stitch cache hits + LLM results into the input order.
    const out: NormalizeResult[] = items.map((it) => {
        const fromLLM = llmResults.find((r) => r.id === it.listing_url)
        const fromCache = cacheMap.get(it.listing_url)
        const r = fromCache ?? fromLLM
        if (!r) {
            // LLM miss + cache miss = soft fallback (no normalization).
            // Mark as low-quality so the desktop UI can show the
            // "Couldn't appraise" badge.
            return {
                listing_url: it.listing_url,
                canonical_kind: "",
                coarse_low: 0,
                coarse_high: 0,
                confidence: "low",
                worth_deep: false,
                category_hint: "other",
                red_flags: [],
                reasoning: "normalization unavailable",
                cache_hit: false,
            }
        }
        const confidence: "low" | "medium" | "high" =
            r.confidence === "high" ? "high"
            : r.confidence === "medium" ? "medium"
            : "low"
        return {
            listing_url: it.listing_url,
            canonical_kind: r.canonical_kind,
            coarse_low: r.coarse_low,
            coarse_high: r.coarse_high,
            confidence,
            worth_deep: !!r.worth_deep,
            category_hint: safeHint(r.category_hint),
            red_flags: r.red_flags ?? [],
            reasoning: r.reasoning,
            cache_hit: !!fromCache,
        }
    })

    return jsonResponse({
        ok: true,
        results: out,
        cache_hits: out.filter((r) => r.cache_hit).length,
        llm_calls: misses.length,
    })
})


/**
 * One MiniMax HTTP call with the full batch in the user message.
 * Uses MiniMax's OpenAI-compatible /v1/chat/completions endpoint.
 *
 * The output JSON object's "results" array MUST be in the same order
 * and length as the input. We ask for that explicitly in the prompt
 * and tolerate the LLM dropping/reordering by keying on `id`.
 */
async function callMiniMaxBatch(items: InItem[]): Promise<MMResult[]> {
    const apiKey = Deno.env.get("MINIMAX_API_KEY")
    if (!apiKey) {
        throw new Error("MINIMAX_API_KEY not set in Supabase secrets")
    }

    // Build the user message. Truncate body to 1000 chars per item to
    // bound batch size; the model name + condition are almost always
    // in the first sentence anyway.
    const inputJson = JSON.stringify(
        items.map((it) => ({
            id: it.listing_url,
            title: it.title.slice(0, 200),
            body: (it.body ?? "").slice(0, 1000),
            ask_price: it.ask_price ?? null,
        })),
    )
    const userMessage =
        `Normalize this batch of ${items.length} listings. ` +
        `Each result must use the exact "id" from the input.\n\n` + inputJson

    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), HTTP_TIMEOUT_MS)
    let resp: Response
    try {
        resp = await fetch(`${MINIMAX_BASE_URL}/chat/completions`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${apiKey}`,
                "Content-Type": "application/json",
            },
            signal: ctrl.signal,
            body: JSON.stringify({
                model: MINIMAX_MODEL,
                messages: [
                    { role: "system", content: SYSTEM_PROMPT },
                    { role: "user", content: userMessage },
                ],
                temperature: 0.0,
                max_tokens: 4096,
            }),
        })
    } finally {
        clearTimeout(timer)
    }

    if (!resp.ok) {
        const text = await resp.text().catch(() => "")
        throw new Error(`MiniMax HTTP ${resp.status}: ${text.slice(0, 300)}`)
    }
    const json = await resp.json() as {
        choices?: Array<{ message?: { content?: string } }>
    }
    const text = json.choices?.[0]?.message?.content ?? ""
    if (!text) throw new Error("MiniMax returned empty content")

    const parsed = parseLooseJson(text)
    const results = (parsed?.results ?? []) as MMResult[]
    if (!Array.isArray(results)) {
        throw new Error("MiniMax response missing results[] array")
    }
    return results
}


/**
 * Tolerant JSON extractor — handles pure JSON, ```json fences,
 * and prose-prefixed JSON. Returns null on failure (caller treats
 * as empty results, which falls back to no-cache for those items).
 */
function parseLooseJson(text: string): { results?: unknown[] } | null {
    if (!text) return null
    text = text.trim()
    try { return JSON.parse(text) } catch { /* fall through */ }
    for (const fence of ["```json", "```"]) {
        if (text.startsWith(fence)) {
            const inner = text.slice(fence.length).split("```")[0].trim()
            try { return JSON.parse(inner) } catch { /* fall through */ }
        }
    }
    // Last resort: find the first '{' and the matching last '}'.
    const start = text.indexOf("{")
    const end = text.lastIndexOf("}")
    if (start >= 0 && end > start) {
        try { return JSON.parse(text.slice(start, end + 1)) } catch { /* fall through */ }
    }
    return null
}

function clampInt(n: unknown, lo: number, hi: number): number {
    const x = typeof n === "number" ? n : Number(n)
    if (!Number.isFinite(x)) return lo
    return Math.max(lo, Math.min(hi, Math.round(x)))
}
