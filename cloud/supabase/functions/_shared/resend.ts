// Resend email client + HTML/text rendering for digest + instant emails.
//
// Why we render HTML inline (no <style> blocks): every major email
// client strips or scopes <style> tags unpredictably. Inline styles
// are the only cross-client way to guarantee the layout looks the way
// we drew it.
//
// Resend API: https://resend.com/docs/api-reference/emails/send-email
//
// API key: read from RESEND_API_KEY at call time (NOT module init —
// Supabase secrets are populated by the runtime, not at import). If
// the key is missing, sendEmail returns a sentinel result so the
// caller can return a clean 503; we do NOT crash the function.

const RESEND_API = "https://api.resend.com/emails"

const DEFAULT_FROM = "alerts@bullseye.app"
const DEFAULT_REPLY_TO = "hello@bullseye.app"

export interface SendEmailArgs {
    to: string
    subject: string
    html: string
    text: string
}

export interface SendEmailResult {
    ok: boolean
    /** Resend message id when ok. Empty when not. */
    id: string
    /** "missing_api_key" | "http_<code>" | "network_error" | "ok" */
    status: string
    message?: string
}

/**
 * POST one email through Resend. Returns a SendEmailResult — never
 * throws. The "missing_api_key" status is the signal callers use to
 * return a 503 "email service not configured" response.
 */
export async function sendEmail(args: SendEmailArgs): Promise<SendEmailResult> {
    const apiKey = Deno.env.get("RESEND_API_KEY")
    if (!apiKey) {
        console.warn("RESEND_API_KEY not set; refusing to send")
        return {
            ok: false,
            id: "",
            status: "missing_api_key",
            message: "email service not configured",
        }
    }

    const from = Deno.env.get("RESEND_FROM_ADDRESS") || DEFAULT_FROM
    const replyTo = Deno.env.get("RESEND_REPLY_TO") || DEFAULT_REPLY_TO

    let resp: Response
    try {
        resp = await fetch(RESEND_API, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${apiKey}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                from,
                to: args.to,
                subject: args.subject,
                html: args.html,
                text: args.text,
                reply_to: replyTo,
            }),
        })
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error("resend network error:", msg)
        return { ok: false, id: "", status: "network_error", message: msg }
    }

    if (!resp.ok) {
        const body = await resp.text().catch(() => "")
        console.error(`resend http ${resp.status}: ${body.slice(0, 300)}`)
        return {
            ok: false,
            id: "",
            status: `http_${resp.status}`,
            message: body.slice(0, 300),
        }
    }

    let id = ""
    try {
        const j = await resp.json()
        id = j?.id ?? ""
    } catch (_) {
        // ignore — successful 200 with no body still counts as sent
    }
    return { ok: true, id, status: "ok" }
}

// --- Rendering ----------------------------------------------------------

export interface DigestMatch {
    listing_id: string
    title: string
    asking_price: number | null
    fair_value: number | null
    deal_score: number
    confidence_label: string | null
    confidence_pm: number | null
    listing_url: string
    photo_url: string | null
    seller_location: string | null
    /** ISO-8601 string from the desktop side (or null). */
    listed_at: string | null
    keyword: string
}

const HTML_HEAD =
    `<!doctype html><html><body style="margin:0;background:#f3ead3;` +
    `font-family:ui-monospace,SFMono-Regular,Menlo,monospace;` +
    `color:#3f1718;line-height:1.55;">` +
    `<div style="max-width:560px;margin:0 auto;padding:32px 24px;">`

function escapeHtml(s: string): string {
    return s
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;")
}

/** Color-code the score badge: >=70 green, >=50 amber, else gray. */
export function scoreColor(score: number): string {
    if (score >= 70) return "#5d7a4f"
    if (score >= 50) return "#c98a3c"
    return "#9a8a7d"
}

function fmtPrice(p: number | null): string {
    if (p === null || p === undefined || Number.isNaN(p)) return "—"
    return `$${Math.round(p)}`
}

function fmtPosted(iso: string | null): string {
    if (!iso) return ""
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ""
    // "MMM D, h:MM AM/PM" — cross-platform manual format (avoids
    // locale fingerprinting and POSIX-only %-d/%-I quirks).
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    const day = d.getDate()
    let h = d.getHours()
    const ampm = h >= 12 ? "PM" : "AM"
    h = h % 12 || 12
    const m = String(d.getMinutes()).padStart(2, "0")
    return `${months[d.getMonth()]} ${day}, ${h}:${m} ${ampm}`
}

function renderMatchCard(m: DigestMatch): string {
    const color = scoreColor(m.deal_score)
    const asking = fmtPrice(m.asking_price)
    const fair = m.fair_value !== null ? fmtPrice(m.fair_value) : null
    const posted = fmtPosted(m.listed_at)

    const photoHtml = m.photo_url
        ? `<img src="${escapeHtml(m.photo_url)}" alt="" ` +
          `style="width:100%;max-width:480px;display:block;` +
          `border-radius:8px;margin-bottom:10px;">`
        : ""

    const detailBits: string[] = []
    if (m.seller_location) detailBits.push(escapeHtml(m.seller_location))
    if (posted) detailBits.push(`posted ${escapeHtml(posted)}`)
    if (fair) detailBits.push(`fair ${fair}`)
    if (m.confidence_label) {
        const cpm = m.confidence_pm ? ` ±${m.confidence_pm}` : ""
        detailBits.push(
            `${escapeHtml(m.confidence_label)} confidence${cpm}`,
        )
    }
    const detailLine = detailBits.join(" · ")
    const detailHtml = detailLine
        ? `<p style="margin:0;font-size:11px;color:#6b5d52;">${detailLine}</p>`
        : ""

    return (
        `<a href="${escapeHtml(m.listing_url)}" ` +
        `style="display:block;text-decoration:none;color:inherit;` +
        `border:1px solid #ebe2d4;border-radius:10px;padding:14px;` +
        `background:#fff;margin-bottom:12px;">` +
        photoHtml +
        `<div style="display:flex;align-items:baseline;gap:10px;` +
        `flex-wrap:wrap;">` +
        `<span style="font-size:24px;font-weight:700;color:${color};` +
        `font-family:Georgia,serif;letter-spacing:-0.02em;">${m.deal_score}</span>` +
        `<span style="font-size:10px;text-transform:uppercase;` +
        `letter-spacing:0.10em;color:#6b5d52;font-weight:600;">deal score</span>` +
        `<span style="font-size:14px;color:#1a1614;font-weight:600;` +
        `margin-left:auto;font-family:Georgia,serif;">${asking}</span>` +
        `</div>` +
        `<h3 style="margin:8px 0 4px;font-size:15px;font-weight:500;` +
        `color:#1a1614;">${escapeHtml(m.title)}</h3>` +
        detailHtml +
        `</a>`
    )
}

/** Render the multi-card digest body. */
export function renderDigestHtml(matches: DigestMatch[]): string {
    const n = matches.length
    const parts: string[] = [HTML_HEAD]
    parts.push(
        `<h1 style="font-family:Georgia,serif;font-weight:500;letter-spacing:-0.02em;` +
        `margin:0 0 18px;color:#1a1614;">` +
        `${n} new deal${n !== 1 ? "s" : ""} — bullseye</h1>`,
    )
    parts.push(
        `<p style="color:#6b5d52;margin:0 0 28px;font-size:14px;">` +
        `Sorted by deal score. Click any listing to open it on Marketplace.` +
        `</p>`,
    )

    const byKeyword: Record<string, DigestMatch[]> = {}
    for (const m of matches) {
        const k = m.keyword || ""
        if (!byKeyword[k]) byKeyword[k] = []
        byKeyword[k].push(m)
    }

    for (const kw of Object.keys(byKeyword).sort()) {
        parts.push(
            `<h2 style="font-family:Georgia,serif;font-style:italic;` +
            `font-weight:500;font-size:16px;color:#1a1614;` +
            `margin:24px 0 10px;letter-spacing:0.02em;` +
            `text-transform:lowercase;">` +
            `watch: ${escapeHtml(kw)}</h2>`,
        )
        for (const m of byKeyword[kw]) parts.push(renderMatchCard(m))
    }

    parts.push(
        `<p style="color:#9a8a7d;font-size:11px;margin-top:32px;` +
        `border-top:1px solid #ebe2d4;padding-top:14px;">` +
        `Don't want these? Reply with 'unsubscribe' or update your ` +
        `preferences on your bullseye dashboard.` +
        `</p>`,
    )
    parts.push("</div></body></html>")
    return parts.join("\n")
}

/** Plain-text fallback. */
export function renderDigestText(matches: DigestMatch[]): string {
    const lines: string[] = [
        `${matches.length} new deal(s) — bullseye`,
        "",
    ]
    const byKeyword: Record<string, DigestMatch[]> = {}
    for (const m of matches) {
        const k = m.keyword || ""
        if (!byKeyword[k]) byKeyword[k] = []
        byKeyword[k].push(m)
    }
    for (const kw of Object.keys(byKeyword).sort()) {
        lines.push(`=== ${kw} ===`)
        for (const m of byKeyword[kw]) {
            const asking = fmtPrice(m.asking_price)
            const fair = m.fair_value !== null
                ? `, fair ${fmtPrice(m.fair_value)}`
                : ""
            lines.push(`[${m.deal_score}] ${m.title}`)
            lines.push(`    ${asking}${fair}  ${m.seller_location ?? ""}`)
            lines.push(`    ${m.listing_url}`)
            lines.push("")
        }
    }
    return lines.join("\n")
}

/** Build (subject, html, text) for a digest email. */
export function renderDigest(matches: DigestMatch[]): {
    subject: string
    html: string
    text: string
} {
    const n = matches.length
    let subject: string
    if (n === 0) {
        subject = "bullseye: digest"
    } else {
        const top = matches[0]
        if (n === 1) {
            subject = `bullseye: ${top.deal_score}/100 — ${top.title.slice(0, 60)}`
        } else {
            subject = `bullseye: ${n} new deals (top: ${top.deal_score}/100 ${top.title.slice(0, 40)})`
        }
    }
    return {
        subject,
        html: renderDigestHtml(matches),
        text: renderDigestText(matches),
    }
}

/**
 * Render a single-match instant email. Same card style as digest but
 * without the per-keyword grouping (always one card). Subject leads
 * with the score so it's scannable in a notification banner.
 */
export function renderInstant(match: DigestMatch): {
    subject: string
    html: string
    text: string
} {
    const subject =
        `bullseye: ${match.deal_score}/100 — ${match.title.slice(0, 60)}`
    return {
        subject,
        html: renderDigestHtml([match]),
        text: renderDigestText([match]),
    }
}
