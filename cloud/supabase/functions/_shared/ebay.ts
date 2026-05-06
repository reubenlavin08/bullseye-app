// eBay Browse API client for Edge Functions.
//
// TypeScript port of `deal_finder/src/deal_finder/scraper/ebay.py`.
// Same behavior:
//   - OAuth client_credentials grant (App ID + Cert ID -> bearer token)
//   - Token cached in module scope (Edge Function runtime is short but
//     tokens last 2h; cache survives within a single function instance)
//   - Browse API search with inline `-keyword` parts/accessory exclusion
//   - Multilingual exclusion list (English, French, Spanish)
//   - Post-filter via regex catches non-English part listings that slip
//     past keyword negation
//   - Compute statistics (median, mean, IQR, percentiles) for caching

const BROWSE_OAUTH = "https://api.ebay.com/identity/v1/oauth2/token"
const BROWSE_SEARCH = "https://api.ebay.com/buy/browse/v1/item_summary/search"

const GLOBAL_ID_TO_MARKETPLACE: Record<string, string> = {
    "EBAY-US": "EBAY_US",
    "EBAY-ENCA": "EBAY_CA",
    "EBAY-GB": "EBAY_GB",
    "EBAY-DE": "EBAY_DE",
    "EBAY-AU": "EBAY_AU",
    "EBAY-FR": "EBAY_FR",
    "EBAY-IT": "EBAY_IT",
    "EBAY-ES": "EBAY_ES",
}

// VEHICLE-SPECIFIC parts blocklist. Used IN ADDITION to the general
// EXCLUDE_TERMS when a vehicle category is being searched OR when the
// widening fallback drops the categoryId. eBay Browse's `q -term`
// suffix is the only path that catches the long-tail vehicle parts
// the categoryId filter misses (and there's a lot — door panels,
// instrument clusters, alternators, hoods, every body part).
//
// These terms are too vehicle-specific to put in the global list
// (would over-filter laptop / appliance / furniture searches).
const VEHICLE_PARTS_EXCLUDE = [
    // Body panels & exterior
    "door panel", "door panels", "door handle", "door handles",
    "hood", "trunk lid", "tailgate", "fender", "fenders",
    "bumper", "bumpers", "grille", "grilles", "fascia",
    "fender flare", "rocker panel", "quarter panel",
    "spoiler", "splash guard", "mud flap", "mud flaps",
    "running board", "running boards",
    // Lights
    "headlight", "headlights", "taillight", "taillights",
    "fog light", "fog lights", "turn signal", "side marker",
    "lens", "lenses", "reflector",
    // Mirrors
    "mirror", "mirrors", "side mirror", "side mirrors",
    "mirror cap", "mirror cover", "mirror glass",
    // Glass
    "windshield", "window regulator", "window motor", "sunroof",
    // Wheels & brakes (often listed without "for")
    "wheel hub", "wheel hubs", "wheel bearing", "wheel bearings",
    "rim", "rims", "rotor", "rotors", "brake pad", "brake pads",
    "brake disc", "brake caliper", "brake line", "brake hose",
    "lug nut", "lug nuts", "wheel stud",
    // Suspension & steering
    "control arm", "ball joint", "tie rod", "sway bar",
    "shock absorber", "strut", "struts", "spring", "coilover",
    "steering rack", "steering wheel", "power steering pump",
    // Engine & drivetrain
    "engine", "transmission", "gearbox", "differential",
    "axle", "axles", "drive shaft", "cv axle", "cv joint",
    "alternator", "starter", "starter motor",
    "fuel pump", "fuel injector", "fuel filter", "fuel tank",
    "spark plug", "spark plugs", "ignition coil", "coil pack",
    "timing belt", "timing chain", "serpentine belt",
    "water pump", "thermostat", "radiator", "intercooler",
    "turbo", "turbocharger", "supercharger",
    "exhaust", "muffler", "mufflers", "catalytic converter",
    "manifold", "header", "headers", "downpipe",
    "oxygen sensor", "o2 sensor", "maf sensor", "map sensor",
    "throttle body", "throttle position",
    "ecu", "ecm", "pcm", "tcm", "engine computer", "module",
    // Interior
    "seat", "seats", "seat cover", "seat covers",
    "steering wheel cover", "shift knob", "shift boot",
    "floor console", "center console", "console", "armrest",
    "dashboard", "dash", "dash cover", "dash pad",
    "instrument cluster", "speedometer", "tachometer",
    "headliner", "sun visor", "sun visors",
    "carpet", "carpets", "floor mat", "floor mats", "floor liner",
    "door card", "door cards", "trim panel",
    "cup holder", "ash tray",
    // Electrical
    "battery", "alternator harness", "wiring harness", "harness",
    "fuse box", "relay", "switch", "sensor", "sensors",
    "stereo", "head unit", "amplifier", "amp", "speaker", "speakers",
    "subwoofer", "antenna",
    // Keys / FOBs
    "key", "keys", "key fob", "fob", "fobs", "key chain", "keychain",
    "lanyard",
    // Branding-only items
    "emblem", "emblems", "badge", "badges", "logo", "decal", "decals",
    "sticker", "stickers", "license plate", "license plate frame",
    "license plate cover",
    // Generic giveaways
    "service manual", "owners manual", "repair manual",
    "wiring diagram", "shop manual",
    "for parts", "parts only", "broken", "salvage",
    "rebuilt", "core",
    // --- Motorcycle-specific accessories (eBay's Motorcycles 6024
    //     category sometimes includes these; the category filter
    //     alone isn't enough). ---
    "helmet", "helmets",
    "gloves", "glove",
    "jacket", "jackets", "leathers", "riding gear", "riding suit",
    "boot", "boots", "riding boots",
    "saddlebag", "saddlebags", "tank bag", "tank bags",
    "fairing", "fairings", "windscreen", "windshield",
    "exhaust", "slip-on", "slip on", "exhaust pipe", "exhaust pipes",
    "header", "headers", "muffler", "mufflers",
    "sprocket", "sprockets", "chain", "chains", "drive chain",
    "sissy bar", "sissy", "luggage rack",
    "seat", "seats", "seat cover",
    "grip", "grips", "bar end", "bar ends",
    "lever", "levers", "brake lever", "clutch lever",
    "foot peg", "foot pegs", "footpeg", "footpegs",
    "kickstand", "side stand",
    "tire", "tires", "tyre", "tyres",
    "fender", "fenders", "rear fender",
    "tank cover", "tank pad",
    "instrument", "speedo", "tachometer",
    "headlight assembly", "tail light",
    "led light", "led kit",
    "phone mount", "phone holder",
    "stand", "paddock stand", "rear stand", "front stand",
    "tool kit",
    "jersey", "pants", "race suit",
    "goggles", "tinted visor", "visor",
]

// Mirror of Python `_DEFAULT_EXCLUDE_TERMS`. Be conservative — only
// terms that are highly correlated with parts listings AND unlikely
// to appear in a real product's title.
const EXCLUDE_TERMS = [
    // English — generic
    "parts", "part", "replacement", "replace",
    "accessory", "accessories",
    "kit", "kits", "lot", "spare",
    "filter", "filters", "brush", "brushes",
    "pads", "pad", "cover", "covers", "lid",
    "bag", "bags",
    "battery", "batteries", "charger",
    "cable", "cables", "adapter", "cord",
    "screen protector", "skin", "wrap",
    "decal", "decals", "sticker", "stickers", "manual",
    "box only", "empty box", "broken", "repair",
    // English — automotive parts (the original miss: searching
    // "Honda Civic" returned mostly mats, mirrors, and emblems with
    // prices in the $5-$80 range, dragging the median to ~$35).
    "mat", "mats", "floor mat",
    "mirror", "mirrors", "side mirror",
    "headlight", "headlights", "taillight", "taillights",
    "bumper", "bumpers", "fender", "fenders",
    "grille", "grilles", "emblem", "emblems", "badge", "badges",
    "lens", "lenses", "antenna", "antennas",
    "key", "keys", "fob", "fobs",
    "wheel", "wheels", "rim", "rims",
    "rotor", "rotors", "brake", "brakes", "pad", "pads",
    "ecu", "ecm", "computer", "module",
    "shift knob", "spark plug", "spark plugs",
    "seat cover", "seat covers", "armrest",
    "service manual", "owners manual",
    "key chain", "keychain", "lanyard",
    // French
    "pièces", "pieces", "remplacement", "filtre", "filtres",
    "brosse", "brosses", "couvercle",
    "chiffon", "chiffons", "batterie",
    "accessoire", "accessoires",
    // Spanish
    "repuesto", "repuestos", "pieza", "piezas",
    "filtro", "filtros", "cepillo", "cepillos", "tapa",
]

export interface EbayItem {
    item_id: string
    title: string
    price: number
    currency: string
    listing_url: string
    location: string | null
}

// Semantic category hint (output by the LLM normalize step) → eBay
// Browse API leaf categoryId. The hint vocabulary is intentionally
// small — ~14 entries covers >95% of consumer-deal-finding listings.
// New entries cost one row each; the LLM picks from a fixed enum
// in the system prompt.
//
// IDs verified against ebay.com/sch/allcategories — these are leaf-
// level so eBay's "include sub-categories" default still gives us
// the full tree (e.g. "Cars & Trucks" 6001 includes every make/model).
//
// "other" → null  ⇒ skip the category filter entirely; rely on
//                    coarse-range price band + EXCLUDE_TERMS.
export const HINT_TO_EBAY_CAT: Record<string, string | null> = {
    motorcycle: "6024",
    car:        "6001",
    truck:      "6001",
    rv:         "50054",
    atv:        "6723",
    boat:       "26429",
    phone:      "9355",
    laptop:     "177",
    tablet:     "171485",
    camera:     "625",
    tv:         "11071",
    appliance:  "20710",
    furniture:  "3197",
    other:      null,
}

export interface CompStats {
    sample_size: number
    median: number
    mean: number
    minimum: number
    maximum: number
    p10: number
    q1: number
    q3: number
    p90: number
    iqr: number
    iqr_ratio: number     // IQR / median; > 1.0 = data quality poor
}

// --- Token cache (module-scope) -----------------------------------------

let cachedToken: { value: string; expiresAt: number } | null = null

async function getOAuthToken(): Promise<string> {
    const now = Date.now() / 1000
    // Refresh 5 min before expiry to avoid race conditions
    if (cachedToken && now < cachedToken.expiresAt - 300) {
        return cachedToken.value
    }
    const appId = Deno.env.get("EBAY_APP_ID")
    const certId = Deno.env.get("EBAY_CERT_ID")
    if (!appId || !certId) {
        throw new Error(
            "EBAY_APP_ID + EBAY_CERT_ID must be set via supabase secrets set",
        )
    }
    const basic = btoa(`${appId}:${certId}`)
    const resp = await fetch(BROWSE_OAUTH, {
        method: "POST",
        headers: {
            Authorization: `Basic ${basic}`,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
            grant_type: "client_credentials",
            scope: "https://api.ebay.com/oauth/api_scope",
        }),
    })
    if (!resp.ok) {
        const text = await resp.text()
        throw new Error(`eBay OAuth failed (${resp.status}): ${text.slice(0, 300)}`)
    }
    const body = await resp.json()
    if (!body.access_token) {
        throw new Error(`eBay OAuth response missing access_token`)
    }
    cachedToken = {
        value: body.access_token,
        expiresAt: now + (body.expires_in ?? 7200),
    }
    return cachedToken.value
}

// --- Exclusion helpers --------------------------------------------------

/** Build ` -term1 -"two words"` suffix; skips terms already in the search. */
function buildExclusionSuffix(searchTerm: string, extra: string[] = []): string {
    const needle = searchTerm.toLowerCase()
    const parts: string[] = []
    const seen = new Set<string>()
    for (const t of [...EXCLUDE_TERMS, ...extra]) {
        const key = t.toLowerCase()
        if (seen.has(key)) continue
        seen.add(key)
        if (needle.includes(key)) continue
        parts.push(t.includes(" ") ? `-"${t}"` : `-${t}`)
    }
    return parts.length ? " " + parts.join(" ") : ""
}

/** Compile a regex matching exclusion terms not present in the user's term. */
function compileExclusionRegex(searchTerm: string): RegExp | null {
    const needle = searchTerm.toLowerCase()
    const tokens = EXCLUDE_TERMS
        .filter((t) => !needle.includes(t.toLowerCase()))
        .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    if (tokens.length === 0) return null
    return new RegExp(`\\b(?:${tokens.join("|")})\\b`, "i")
}

// --- Search -------------------------------------------------------------

export interface SearchEbayArgs {
    keywords: string
    region?: string
    limit?: number
    /**
     * eBay Browse API leaf categoryId. When set, the upstream search
     * is constrained to that taxonomy node — accessories and parts
     * physically cannot appear in the result set (they live in
     * different categoryIds). This is the PRIMARY guard against the
     * "Honda motorcycle returns helmets" failure mode.
     *
     * Map a semantic hint (motorcycle/car/phone/...) to the categoryId
     * via HINT_TO_EBAY_CAT before calling.
     */
    categoryId?: string | null
    /**
     * Optional inclusive price filter applied at the eBay Browse API
     * level (filter=price:[min..max],priceCurrency:CAD). Used as the
     * SECONDARY guard once category is set: e.g. for vehicles, set
     * min = coarse_low * 0.20 and max = coarse_high * 5.0 to drop
     * cheap-accessory and absurdly-priced outliers before they reach
     * the stats pipeline. Pass null to skip on either side.
     *
     * Sanity: if the band is degenerate (max < min, min < 0, etc.) we
     * silently skip it rather than 4xx the upstream request.
     */
    minPrice?: number | null
    maxPrice?: number | null
}

export async function searchEbay(args: SearchEbayArgs): Promise<EbayItem[]> {
    const region = args.region ?? "EBAY-ENCA"
    const targetLimit = Math.min(Math.max(args.limit ?? 50, 1), 200)
    const fetchLimit = Math.min(targetLimit * 3, 200)
    const marketplace = GLOBAL_ID_TO_MARKETPLACE[region] ?? "EBAY_US"
    const token = await getOAuthToken()

    // Vehicle searches get the EXTRA long-tail vehicle-parts blocklist
    // appended to the eBay -term suffix. The general EXCLUDE_TERMS only
    // catches obvious accessory words; vehicle parts have hundreds of
    // long-tail names (door panel, instrument cluster, fuel injector,
    // etc.) that the categoryId filter alone misses for sellers who
    // list their parts in the wrong category.
    const VEHICLE_CATEGORIES = new Set([
        "6001",   // Cars & Trucks
        "6024",   // Motorcycles
        "26429",  // Boats
        "50054",  // RVs & Campers
        "6723",   // ATVs
    ])
    const isVehicle = args.categoryId
        ? VEHICLE_CATEGORIES.has(args.categoryId) : false
    const extraExcl = isVehicle ? VEHICLE_PARTS_EXCLUDE : []
    const q = args.keywords.trim() + buildExclusionSuffix(args.keywords, extraExcl)

    function buildUrl(opts: {
        category_id: string | null
        min_price: number | null
        max_price: number | null
    }): URL {
        const url = new URL(BROWSE_SEARCH)
        url.searchParams.set("q", q)
        url.searchParams.set("limit", String(fetchLimit))
        if (opts.category_id) {
            url.searchParams.set("category_ids", opts.category_id)
        }
        // eBay Browse API expects price+currency as a comma-joined
        // filter string. Build only when at least one bound is sane.
        const okMin = (opts.min_price != null && opts.min_price > 0
            && Number.isFinite(opts.min_price))
        const okMax = (opts.max_price != null && opts.max_price > 0
            && Number.isFinite(opts.max_price))
        const sane = okMin || okMax
        if (sane && (!okMin || !okMax || (opts.max_price! >= opts.min_price! * 1.1))) {
            const lo = okMin ? Math.floor(opts.min_price!) : ""
            const hi = okMax ? Math.ceil(opts.max_price!)  : ""
            // CAD because /comps default region is EBAY-ENCA. If
            // region != ENCA we omit the currency token; eBay falls
            // back to the marketplace's default currency.
            const currency = (region === "EBAY-ENCA") ? "CAD"
                : region === "EBAY-US" ? "USD" : ""
            const priceFilter = `price:[${lo}..${hi}]`
            const filterParts = [priceFilter]
            if (currency) filterParts.push(`priceCurrency:${currency}`)
            url.searchParams.set("filter", filterParts.join(","))
        }
        return url
    }

    async function doFetch(url: URL): Promise<Response> {
        let resp = await fetch(url, {
            headers: {
                Authorization: `Bearer ${token}`,
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                Accept: "application/json",
            },
        })
        if (resp.status === 401 || resp.status === 403) {
            cachedToken = null
            const t2 = await getOAuthToken()
            resp = await fetch(url, {
                headers: {
                    Authorization: `Bearer ${t2}`,
                    "X-EBAY-C-MARKETPLACE-ID": marketplace,
                    Accept: "application/json",
                },
            })
        }
        return resp
    }

    // First pass: full constraints (category + price band).
    const url1 = buildUrl({
        category_id: args.categoryId ?? null,
        min_price: args.minPrice ?? null,
        max_price: args.maxPrice ?? null,
    })
    let resp = await doFetch(url1)
    if (!resp.ok) {
        const text = await resp.text()
        throw new Error(`eBay Browse HTTP ${resp.status}: ${text.slice(0, 300)}`)
    }
    let items = parseAndFilter(await resp.json(), args.keywords, targetLimit)

    // Fallback widening: if we have a category filter and the result
    // set is too thin, drop the categoryId and keep the price band.
    // For VEHICLES we still widen — eBay's Cars & Trucks 6001 is
    // sparse (most cars are sold via Craigslist/Marketplace, not eBay)
    // — but on the widened pass we pile on the VEHICLE_PARTS_EXCLUDE
    // suffix so floor mats, mufflers, door panels, etc. don't flood
    // back. (Vehicle widen kept the suffix from the first pass via
    // `q` above; the widened URL re-uses the same `q`.)
    //
    // Threshold: 3 for vehicles (very thin tolerance — even 3 actual
    // cars + the long-tail parts blocklist usually beats 0). 8 for
    // non-vehicles (the original).
    const FALLBACK_THRESHOLD = isVehicle ? 3 : 8
    if (items.length < FALLBACK_THRESHOLD && args.categoryId) {
        console.log(
            `searchEbay: only ${items.length} results with categoryId=` +
            `${args.categoryId}; widening to no-category + price band`,
        )
        const url2 = buildUrl({
            category_id: null,
            min_price: args.minPrice ?? null,
            max_price: args.maxPrice ?? null,
        })
        const r2 = await doFetch(url2)
        if (r2.ok) {
            const widened = parseAndFilter(
                await r2.json(), args.keywords, targetLimit,
            )
            // Use the widened set if it's actually larger; otherwise
            // keep the narrower-but-cleaner first pass.
            if (widened.length > items.length) {
                items = widened
            }
        }
    }

    return items
}

function parseAndFilter(
    body: any,
    keywords: string,
    targetLimit: number,
): EbayItem[] {
    const summaries: any[] = body?.itemSummaries ?? []
    const raw: EbayItem[] = []
    for (const it of summaries) {
        const priceStr = it?.price?.value
        const price = typeof priceStr === "string" ? parseFloat(priceStr) : NaN
        if (!Number.isFinite(price) || price <= 0) continue
        raw.push({
            item_id: String(it?.itemId ?? ""),
            title: String(it?.title ?? ""),
            price,
            currency: String(it?.price?.currency ?? "USD"),
            listing_url: String(it?.itemWebUrl ?? ""),
            location: it?.itemLocation?.country
                ? `${it.itemLocation.city ?? ""} ${it.itemLocation.country}`.trim()
                : null,
        })
    }
    const pat = compileExclusionRegex(keywords)
    const filtered = pat ? raw.filter((r) => !pat.test(r.title)) : raw
    return filtered.slice(0, targetLimit)
}

// --- Stats --------------------------------------------------------------

function quantile(sorted: number[], p: number): number {
    if (sorted.length === 0) return NaN
    if (sorted.length === 1) return sorted[0]
    const idx = (sorted.length - 1) * p
    const lo = Math.floor(idx)
    const hi = Math.ceil(idx)
    if (lo === hi) return sorted[lo]
    const w = idx - lo
    return sorted[lo] * (1 - w) + sorted[hi] * w
}

export function computeStats(items: EbayItem[]): CompStats {
    const prices = items.map((i) => i.price).sort((a, b) => a - b)
    const n = prices.length
    if (n === 0) {
        return {
            sample_size: 0, median: 0, mean: 0, minimum: 0, maximum: 0,
            p10: 0, q1: 0, q3: 0, p90: 0, iqr: 0, iqr_ratio: 0,
        }
    }
    const mean = prices.reduce((s, x) => s + x, 0) / n
    const median = quantile(prices, 0.5)
    const q1 = quantile(prices, 0.25)
    const q3 = quantile(prices, 0.75)
    const iqr = q3 - q1
    return {
        sample_size: n,
        median,
        mean,
        minimum: prices[0],
        maximum: prices[n - 1],
        p10: quantile(prices, 0.1),
        q1,
        q3,
        p90: quantile(prices, 0.9),
        iqr,
        iqr_ratio: median > 0 ? iqr / median : 0,
    }
}

// --- Search-term normalization (must match desktop side) ---------------

/**
 * Cache key for `comps_cache.search_term_normalized`. Same logic as
 * the desktop side's `appraisal/normalizer.normalize_title`'s output:
 * lowercase + strip punctuation + dedupe tokens + sort. Sorting is
 * essential for cache hit rate ("Roomba i5 iRobot" and "iRobot
 * Roomba i5" must hash to the same key).
 */
export function normalizeSearchTerm(raw: string): string {
    return raw
        .toLowerCase()
        .trim()
        .replace(/[^\w\s]/g, " ")
        .split(/\s+/)
        .filter(Boolean)
        .filter((tok, i, arr) => arr.indexOf(tok) === i)  // dedupe
        .sort()
        .join(" ")
}
