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

// Sending happens from the `mail.` subdomain so the apex's reputation
// stays clean (industry-standard transactional setup). Reply-To stays
// on the apex — customer replies land at `hello@getbullseye.app`,
// which gets forwarded to your Gmail once ImprovMX is wired.
const DEFAULT_FROM = "alerts@mail.getbullseye.app"
const DEFAULT_REPLY_TO = "hello@getbullseye.app"

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

// D6 — Gmail-style envelope. Clean light-gray page background with a
// white envelope card centered on top. Sans-serif body (system stack)
// for readability across every mail client; Georgia kept for headings
// so the brand voice stays consistent with the desktop app and landing.
//
// Inline styles only (no <style> blocks) — Gmail/Outlook strip class-
// based styles from <head>. Tested against Apple Mail, Gmail web,
// Outlook desktop, iOS Mail.
const HTML_HEAD =
    `<!doctype html><html><body style="margin:0;background:#f1ece1;` +
    `font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,` +
    `Arial,sans-serif;color:#1a1614;line-height:1.55;` +
    `-webkit-font-smoothing:antialiased;">` +
    // Outer padding so the envelope floats on the bg color
    `<div style="padding:24px 12px;">` +
    // The "envelope" — white card, hairline border, soft shadow
    `<div style="max-width:560px;margin:0 auto;background:#ffffff;` +
    `border:1px solid #ebe2d4;border-radius:12px;` +
    `box-shadow:0 1px 2px rgba(26,22,20,0.04),0 4px 16px rgba(26,22,20,0.06);` +
    `padding:24px 26px;">`

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

/** Compute "under fair value" savings, or null if not applicable. */
function savingsAmount(m: DigestMatch): number | null {
    if (m.asking_price == null || m.fair_value == null) return null
    if (m.asking_price >= m.fair_value) return null
    return Math.round(m.fair_value - m.asking_price)
}

/**
 * D6 match card. Restructured for clarity:
 *   row 1  photo (full-bleed, hidden if absent)
 *   row 2  big score · asking price · "save $X" pill
 *   row 3  title (sans, weight 600)
 *   row 4  meta (location · posted · fair · confidence) muted
 *
 * The "save $X" pill is the new flex moment — turns dry numbers into a
 * scannable feel-good signal that survives the Gmail preview-pane crop.
 */
function renderMatchCard(m: DigestMatch): string {
    const color = scoreColor(m.deal_score)
    const asking = fmtPrice(m.asking_price)
    const fair = m.fair_value !== null ? fmtPrice(m.fair_value) : null
    const posted = fmtPosted(m.listed_at)
    const savings = savingsAmount(m)

    const photoHtml = m.photo_url
        ? `<img src="${escapeHtml(m.photo_url)}" alt="" ` +
          `style="width:100%;max-width:512px;display:block;` +
          `border-radius:8px;margin-bottom:12px;` +
          `border:1px solid #ebe2d4;">`
        : ""

    // savings pill — green chip in the same accent-2 family the
    // desktop app uses on the Home hero. Signals "good deal" instantly.
    const savingsPill = savings != null
        ? `<span style="display:inline-block;font-size:11px;color:#5d7a4f;` +
          `background:#e8eee2;padding:3px 9px;border-radius:999px;` +
          `font-weight:600;margin-left:auto;white-space:nowrap;">` +
          `save $${savings}</span>`
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
        ? `<p style="margin:6px 0 0;font-size:11px;color:#6b5d52;` +
          `letter-spacing:0.01em;">${detailLine}</p>`
        : ""

    return (
        `<a href="${escapeHtml(m.listing_url)}" ` +
        `style="display:block;text-decoration:none;color:inherit;` +
        `border:1px solid #ebe2d4;border-radius:10px;padding:14px 14px 12px;` +
        `background:#fafaf7;margin-bottom:12px;">` +
        photoHtml +
        `<div style="display:flex;align-items:baseline;gap:10px;` +
        `flex-wrap:wrap;">` +
        `<span style="font-size:30px;font-weight:600;color:${color};` +
        `font-family:Georgia,serif;letter-spacing:-0.02em;line-height:1;">${m.deal_score}</span>` +
        `<span style="font-size:10px;text-transform:uppercase;` +
        `letter-spacing:0.08em;color:#6b5d52;font-weight:600;">/100</span>` +
        `<span style="font-size:15px;color:#1a1614;font-weight:600;` +
        `font-family:Georgia,serif;font-variant-numeric:tabular-nums;">${asking}</span>` +
        savingsPill +
        `</div>` +
        `<h3 style="margin:10px 0 0;font-size:15px;font-weight:600;` +
        `color:#1a1614;line-height:1.4;">${escapeHtml(m.title)}</h3>` +
        detailHtml +
        `</a>`
    )
}

/**
 * D6 digest layout:
 *   ┌──────────────────────────────────────┐
 *   │  ◎ bullseye               May 5      │  envelope header
 *   ├──────────────────────────────────────┤
 *   │  3 new deals                          │  hero count
 *   │  Top: 87/100 · save $215              │  preview line (Gmail-friendly)
 *   ├──────────────────────────────────────┤
 *   │  watch: macbook pro 14                │  per-keyword section
 *   │  ┌──────────────────────────────┐     │
 *   │  │ photo · 87 /100 $1200 [save..] │   │
 *   │  │ MacBook Pro 14" M2 Pro 16GB    │   │
 *   │  │ Vancouver · posted May 5...    │   │
 *   │  └──────────────────────────────┘     │
 *   ├──────────────────────────────────────┤
 *   │  Manage preferences · Unsubscribe     │  footer
 *   └──────────────────────────────────────┘
 *
 * The TLDR preview line ("Top: 87/100 · save $215") is critical for
 * Gmail's preview-pane crop — many users skim the first 60 chars and
 * decide whether to open. Lead with the best deal, not boilerplate.
 */
export function renderDigestHtml(matches: DigestMatch[]): string {
    const n = matches.length
    const parts: string[] = [HTML_HEAD]

    // Envelope header — small inline logo + brand on the left, date on
    // the right. Mimics Gmail's transactional header pattern (e.g.
    // Stripe receipts), which users recognize as "this is a real email
    // from a real product."
    const today = new Date()
    const dateStr = today.toLocaleDateString("en-US", {
        month: "short", day: "numeric",
    })
    parts.push(
        `<table role="presentation" cellpadding="0" cellspacing="0" ` +
        `style="width:100%;border-bottom:1px solid #ebe2d4;` +
        `padding-bottom:14px;margin-bottom:18px;"><tr>` +
        `<td style="font-family:Georgia,serif;font-size:18px;` +
        `font-weight:500;letter-spacing:-0.01em;color:#1a1614;">` +
        // Inline SVG-as-text isn't reliable in mail clients; use a
        // simple bullet glyph paired with the wordmark instead.
        `<span style="display:inline-block;width:14px;height:14px;` +
        `border:2px solid #1a1614;border-radius:50%;background:#c0202a;` +
        `box-sizing:border-box;vertical-align:-2px;margin-right:8px;"></span>` +
        `Bullseye</td>` +
        `<td style="text-align:right;font-size:11px;color:#9a8a7d;` +
        `text-transform:uppercase;letter-spacing:0.08em;font-weight:500;">` +
        escapeHtml(dateStr) +
        `</td></tr></table>`,
    )

    // Hero count — Georgia, big, with TLDR preview line below
    const top = matches[0]
    const topSavings = top ? savingsAmount(top) : null
    const previewLine = top
        ? `Top: ${top.deal_score}/100` +
          (topSavings != null ? ` · save $${topSavings}` : "")
        : ""

    parts.push(
        `<h1 style="font-family:Georgia,serif;font-weight:400;` +
        `letter-spacing:-0.02em;margin:0 0 6px;color:#1a1614;font-size:26px;` +
        `line-height:1.15;">` +
        `${n} new deal${n !== 1 ? "s" : ""}` +
        `</h1>`,
    )
    if (previewLine) {
        parts.push(
            `<p style="color:#6b5d52;margin:0 0 22px;font-size:13px;">` +
            escapeHtml(previewLine) +
            `</p>`,
        )
    } else {
        parts.push(
            `<p style="color:#6b5d52;margin:0 0 22px;font-size:13px;">` +
            `Sorted by deal score. Click any listing to open it on Marketplace.` +
            `</p>`,
        )
    }

    // Group by watch keyword
    const byKeyword: Record<string, DigestMatch[]> = {}
    for (const m of matches) {
        const k = m.keyword || ""
        if (!byKeyword[k]) byKeyword[k] = []
        byKeyword[k].push(m)
    }

    for (const kw of Object.keys(byKeyword).sort()) {
        parts.push(
            `<div style="font-size:10px;text-transform:uppercase;` +
            `letter-spacing:0.10em;color:#9a8a7d;font-weight:600;` +
            `margin:18px 0 10px;">` +
            `watch &middot; ${escapeHtml(kw)}</div>`,
        )
        for (const m of byKeyword[kw]) parts.push(renderMatchCard(m))
    }

    // Footer — tighter than before, gives users an actual link rather
    // than the previous "reply with unsubscribe" instruction (which
    // most modern users find weird).
    parts.push(
        `<div style="margin-top:28px;padding-top:14px;` +
        `border-top:1px solid #ebe2d4;color:#9a8a7d;font-size:11px;` +
        `line-height:1.6;">` +
        `These are the deals your watches found in the last 24h. ` +
        `<a href="https://getbullseye.app/" style="color:#9a8a7d;` +
        `text-decoration:underline;">Manage watches</a> &middot; ` +
        `<a href="https://getbullseye.app/" style="color:#9a8a7d;` +
        `text-decoration:underline;">Unsubscribe</a>` +
        `</div>`,
    )

    parts.push("</div></div></body></html>")  // close envelope, outer div, body
    return parts.join("\n")
}

/** Plain-text fallback. */
export function renderDigestText(matches: DigestMatch[]): string {
    const lines: string[] = [
        `${matches.length} new deal(s) — Bullseye`,
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

/**
 * Build (subject, html, text) for a digest email.
 *
 * D6 subject pattern: lead with the savings/score, not the brand. Most
 * inbox apps truncate at ~50-60 chars — the score and dollar amount
 * MUST appear before the truncate cutoff. Examples:
 *   "Save $215 · 87/100 · MacBook Pro 14"     (single deal)
 *   "3 deals · top: save $215 · 87/100"        (multi)
 */
export function renderDigest(matches: DigestMatch[]): {
    subject: string
    html: string
    text: string
} {
    const n = matches.length
    let subject: string
    if (n === 0) {
        subject = "Bullseye · digest"
    } else {
        const top = matches[0]
        const sav = savingsAmount(top)
        const savStr = sav != null ? `Save $${sav} · ` : ""
        if (n === 1) {
            subject = `${savStr}${top.deal_score}/100 · ${top.title.slice(0, 50)}`
        } else {
            subject = `${n} deals · top: ${savStr}${top.deal_score}/100`
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
    const sav = savingsAmount(match)
    const savStr = sav != null ? `Save $${sav} · ` : ""
    const subject =
        `${savStr}${match.deal_score}/100 · ${match.title.slice(0, 50)}`
    return {
        subject,
        html: renderDigestHtml([match]),
        text: renderDigestText([match]),
    }
}
