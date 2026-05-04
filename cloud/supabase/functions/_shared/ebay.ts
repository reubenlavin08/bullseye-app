// eBay Browse API client (server-side).
//
// PHASE 1 PORT: TypeScript translation of the personal tool's
// `deal_finder/scraper/ebay.py`. Same behavior:
//   - OAuth client_credentials grant (App ID + Cert ID -> bearer token)
//   - Token cache with ~2h TTL, refresh 5 min before expiry
//   - Browse API search with parts/accessory exclusion
//   - Multilingual exclusion list (English, French, Spanish)
//
// Cert ID lives in EBAY_CERT_ID secret. Never returned to clients.

const BROWSE_OAUTH = "https://api.ebay.com/identity/v1/oauth2/token"
const BROWSE_SEARCH = "https://api.ebay.com/buy/browse/v1/item_summary/search"

const EXCLUDE_TERMS = [
    // English
    "parts", "part", "replacement", "accessory", "accessories",
    "kit", "lot", "spare", "filter", "filters", "brush", "brushes",
    "pads", "cover", "lid", "bag", "bags", "wheel", "battery",
    "charger", "cable", "adapter", "screen protector", "skin",
    "decal", "manual", "broken", "repair",
    // French
    "pièces", "remplacement", "filtre", "filtres", "brosse",
    "couvercle", "chiffon", "batterie", "accessoire",
    // Spanish
    "repuesto", "pieza", "filtro", "cepillo", "tapa",
]

export interface EbayItem {
    title: string
    price: number
    currency: string
    listing_url: string
    location?: string
}

export interface CompStats {
    sample_size: number
    median: number
    mean: number
    min: number
    max: number
    p10: number
    q1: number
    q3: number
    p90: number
    raw: EbayItem[]
}

export async function getOAuthToken(): Promise<string> {
    // TODO: client_credentials POST to BROWSE_OAUTH, cache token in
    // memory (Edge Function lifetime is short but tokens last 2h, so
    // stash in a module-scope variable).
    throw new Error("not implemented")
}

export async function searchEbay(
    keywords: string,
    options: { region?: string; limit?: number; excludeParts?: boolean } = {}
): Promise<EbayItem[]> {
    // TODO: build q-with-negation, GET BROWSE_SEARCH, post-filter titles
    throw new Error("not implemented")
}

export function computeStats(items: EbayItem[]): CompStats {
    // TODO: sort prices, compute median/percentiles/IQR/etc.
    throw new Error("not implemented")
}

export function normalizeSearchTerm(raw: string): string {
    // Lowercase, strip punctuation, sort tokens, dedupe.
    // Matches the desktop appraisal/normalizer.py output so cache
    // hit rate is maximized.
    return raw.toLowerCase().trim().replace(/[^\w\s]/g, " ").split(/\s+/).filter(Boolean).sort().join(" ")
}
