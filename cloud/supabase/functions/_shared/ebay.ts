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

// Mirror of Python `_DEFAULT_EXCLUDE_TERMS`. Be conservative — only
// terms that are highly correlated with parts listings AND unlikely
// to appear in a real product's title.
const EXCLUDE_TERMS = [
    // English
    "parts", "part", "replacement", "replace",
    "accessory", "accessories",
    "kit", "kits", "lot", "spare",
    "filter", "filters", "brush", "brushes",
    "pads", "pad", "cover", "covers", "lid",
    "bag", "bags", "wheel", "wheels",
    "battery", "batteries", "charger",
    "cable", "cables", "adapter", "cord",
    "screen protector", "skin", "wrap",
    "decal", "sticker", "manual",
    "box only", "empty box", "broken", "repair",
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
function buildExclusionSuffix(searchTerm: string): string {
    const needle = searchTerm.toLowerCase()
    const parts: string[] = []
    for (const t of EXCLUDE_TERMS) {
        if (needle.includes(t.toLowerCase())) continue
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

export async function searchEbay(args: {
    keywords: string
    region?: string
    limit?: number
}): Promise<EbayItem[]> {
    const region = args.region ?? "EBAY-ENCA"
    const targetLimit = Math.min(Math.max(args.limit ?? 50, 1), 200)
    // Over-fetch by 3x to absorb post-filter losses; eBay Browse API
    // caps `limit` at 200, so we max out at 3x but never above 200.
    const fetchLimit = Math.min(targetLimit * 3, 200)
    const marketplace = GLOBAL_ID_TO_MARKETPLACE[region] ?? "EBAY_US"

    const token = await getOAuthToken()
    const q = args.keywords.trim() + buildExclusionSuffix(args.keywords)
    const url = new URL(BROWSE_SEARCH)
    url.searchParams.set("q", q)
    url.searchParams.set("limit", String(fetchLimit))

    const resp = await fetch(url, {
        headers: {
            Authorization: `Bearer ${token}`,
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
            Accept: "application/json",
        },
    })
    if (resp.status === 401 || resp.status === 403) {
        // Token may have just expired — drop cache and retry once.
        cachedToken = null
        const t2 = await getOAuthToken()
        const r2 = await fetch(url, {
            headers: {
                Authorization: `Bearer ${t2}`,
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                Accept: "application/json",
            },
        })
        if (!r2.ok) {
            throw new Error(`eBay Browse HTTP ${r2.status}`)
        }
        return parseAndFilter(await r2.json(), args.keywords, targetLimit)
    }
    if (!resp.ok) {
        const text = await resp.text()
        throw new Error(`eBay Browse HTTP ${resp.status}: ${text.slice(0, 300)}`)
    }
    return parseAndFilter(await resp.json(), args.keywords, targetLimit)
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
