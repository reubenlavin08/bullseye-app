// Resend email client.
// API: https://resend.com/docs/api-reference/emails/send-email

const RESEND_API = "https://api.resend.com/emails"

export interface SendEmailArgs {
    to: string
    subject: string
    html: string
    text: string
}

export async function sendEmail(args: SendEmailArgs): Promise<{ id: string }> {
    // TODO: POST to RESEND_API with API key, return message id
    throw new Error("not implemented")
}

export function renderDigestHtml(matches: any[]): string {
    // TODO: port from desktop alerts/digest.py _render_html()
    throw new Error("not implemented")
}

export function renderDigestText(matches: any[]): string {
    // TODO: port from desktop alerts/digest.py _render_text()
    throw new Error("not implemented")
}

export function renderInstantHtml(match: any): string {
    // Single-match version — bigger photo, more prominent score.
    throw new Error("not implemented")
}
