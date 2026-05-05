// /account-export — GDPR/PIPEDA data export.
//
// Returns a JSON blob with every row this user owns. Deliberately
// flat + verbose so the user can paste it into a script if they need
// to migrate, and so a privacy auditor can read it without decoding
// our schema.
//
// Tables included:
//   licenses         — billing tier + Stripe linkage (no card numbers)
//   user_watches     — saved searches (cloud-backup mirror)
//   email_log        — outbound email history (last 365 days)
//   email_queue      — pending sends
//   telemetry_events — anonymized usage events (last 90 days)
//   user_streaks     — streak counter + Pro days banked
//   user_unlocks     — milestone awards
//
// Auth.users metadata (email, created_at) goes in `account` at the top.
// Stripe payment-method records are NOT in our database (Stripe holds
// them); user can export those from billing.stripe.com directly.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"

const EMAIL_LOG_DAYS = 365
const TELEMETRY_DAYS = 90
const MAX_ROWS_PER_TABLE = 10000  // reasonable cap; alert if hit

function isoCutoff(daysAgo: number): string {
    const d = new Date()
    d.setUTCDate(d.getUTCDate() - daysAgo)
    return d.toISOString()
}

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "POST" && req.method !== "GET") {
        return errorResponse("method not allowed", 405)
    }

    let user
    try {
        user = await requireUser(req)
    } catch (r) {
        if (r instanceof Response) return r
        throw r
    }

    const db = adminClient()

    // Helper: fetch and report truncation. Service-role bypasses RLS
    // (so the export sees everything we hold for this user, even rows
    // RLS would normally hide from the client).
    async function fetchTable(
        table: string,
        select: string,
        opts: { whereCol?: string; sinceIsoCol?: string; sinceDays?: number } = {},
    ): Promise<{ rows: unknown[]; truncated: boolean }> {
        let q = db.from(table).select(select).limit(MAX_ROWS_PER_TABLE + 1)
        // user_id filter — every user-owned table has it.
        const userCol = opts.whereCol ?? "user_id"
        q = q.eq(userCol, user.id)
        if (opts.sinceIsoCol && opts.sinceDays) {
            q = q.gte(opts.sinceIsoCol, isoCutoff(opts.sinceDays))
        }
        const { data, error } = await q
        if (error) {
            console.warn(`export ${table} failed: ${error.message}`)
            return { rows: [], truncated: false }
        }
        const rows = data ?? []
        const truncated = rows.length > MAX_ROWS_PER_TABLE
        return {
            rows: truncated ? rows.slice(0, MAX_ROWS_PER_TABLE) : rows,
            truncated,
        }
    }

    const [licenses, watches, emailLog, emailQueue,
           telemetry, streaks, unlocks] = await Promise.all([
        fetchTable("licenses",
            "tier, stripe_customer_id, stripe_subscription_id, " +
            "current_period_end, trial_ends_at, cancel_at_period_end, " +
            "created_at, updated_at"),
        fetchTable("user_watches",
            "id, keyword, must_include, must_exclude, latitude, longitude, " +
            "radius_km, price_min, price_max, active, created_at, updated_at"),
        fetchTable("email_log",
            "id, email_type, sent_at, sent_date, match_count",
            { sinceIsoCol: "sent_at", sinceDays: EMAIL_LOG_DAYS }),
        fetchTable("email_queue",
            "id, email_type, scheduled_for, sent, sent_at, created_at"),
        fetchTable("telemetry_events",
            "id, event_name, properties, app_version, created_at",
            { sinceIsoCol: "created_at", sinceDays: TELEMETRY_DAYS }),
        fetchTable("user_streaks",
            "current_streak, longest_streak, last_active_date, " +
            "freeze_used_month, pro_days_banked, pro_days_lifetime, " +
            "created_at, updated_at"),
        fetchTable("user_unlocks",
            "id, milestone, earned_at, pro_days_awarded"),
    ])

    return jsonResponse({
        ok: true,
        format_version: "1",
        exported_at: new Date().toISOString(),
        account: {
            user_id: user.id,
            email: user.email,
        },
        retention_windows: {
            email_log_days: EMAIL_LOG_DAYS,
            telemetry_days: TELEMETRY_DAYS,
        },
        notes: [
            "Stripe payment methods are not in this export. Export those at billing.stripe.com.",
            "Anonymous pre-login telemetry is keyed by install_id, not user_id, and is not included here.",
        ],
        data: {
            licenses: licenses.rows,
            user_watches: watches.rows,
            email_log: emailLog.rows,
            email_queue: emailQueue.rows,
            telemetry_events: telemetry.rows,
            user_streaks: streaks.rows,
            user_unlocks: unlocks.rows,
        },
        truncation_warnings: {
            licenses: licenses.truncated,
            user_watches: watches.truncated,
            email_log: emailLog.truncated,
            email_queue: emailQueue.truncated,
            telemetry_events: telemetry.truncated,
            user_streaks: streaks.truncated,
            user_unlocks: unlocks.truncated,
        },
    })
})
