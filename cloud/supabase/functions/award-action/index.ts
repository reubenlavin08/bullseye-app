// /award-action — client-grantable action-based achievement awards.
//
// Body: { action_id: string }
// Returns: {
//   awarded: boolean,
//   pro_days: number,         // days credited THIS call (0 if already had it)
//   pro_days_banked: number,  // total banked after award
//   already_unlocked: boolean,
// }
//
// Authoritative for action-based achievements like:
//   - first_deal_80          (first listing scored 80+)
//   - five_deals_80          (5 listings 80+)
//   - twenty_five_deals_80   (25 listings 80+)
//   - savings_100/500/1000/5000  (lifetime savings thresholds)
//   - first_watch_created    (first saved search)
//   - first_email_click      (first deal-alert click)
//
// Streak achievements (streak_3/7/14/30) are server-side only — clients
// can't claim them. The CLIENT_GRANTABLE allowlist in
// _shared/achievements.ts enforces that.
//
// Idempotency: user_unlocks has UNIQUE(user_id, milestone), so awarding
// the same achievement twice is a no-op. We detect "first time" by
// checking whether the upsert actually inserted a row.
//
// PRO_DAYS_CAP applies to the bank — if the user is already at cap, the
// award is logged but the bank doesn't grow. Lifetime counter still
// reflects the actual award for analytics honesty.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import {
    PRO_DAYS_CAP,
} from "../_shared/streak.ts"
import {
    CLIENT_GRANTABLE,
    findAchievement,
} from "../_shared/achievements.ts"

interface Body {
    action_id?: string
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

    const actionId = (body.action_id ?? "").trim()
    if (!actionId) {
        return errorResponse("action_id required", 400)
    }
    if (!CLIENT_GRANTABLE.has(actionId)) {
        return errorResponse(
            `action_id ${actionId} is not client-grantable`,
            400,
        )
    }
    const achievement = findAchievement(actionId)
    if (!achievement) {
        return errorResponse(`unknown action_id: ${actionId}`, 400)
    }

    const db = adminClient()

    // 1. Try to insert the unlock. ON CONFLICT DO NOTHING means the
    //    insert is a no-op if the user already has this achievement.
    //    The .select() returns the inserted row (empty if conflict).
    const { data: inserted, error: unlockErr } = await db
        .from("user_unlocks")
        .upsert(
            {
                user_id: user.id,
                milestone: actionId,
                pro_days_awarded: achievement.pro_days,
            },
            { onConflict: "user_id,milestone", ignoreDuplicates: true },
        )
        .select("id")
    if (unlockErr) {
        return errorResponse(
            `unlock insert failed: ${unlockErr.message}`,
            500,
        )
    }

    const isNew = inserted !== null && inserted.length > 0

    // 2. Read current banked. We need it for the response and for
    //    cap-check before crediting. Streak row may not exist yet.
    const { data: streak } = await db
        .from("user_streaks")
        .select("pro_days_banked, pro_days_lifetime")
        .eq("user_id", user.id)
        .maybeSingle<{ pro_days_banked: number; pro_days_lifetime: number }>()
    const currentBanked = streak?.pro_days_banked ?? 0
    const currentLifetime = streak?.pro_days_lifetime ?? 0

    if (!isNew) {
        // Already had it. Return the current banked total but no new
        // credit. Idempotent: clients can call this on every relevant
        // event without worrying about double-credits.
        return jsonResponse({
            awarded: false,
            already_unlocked: true,
            pro_days: 0,
            pro_days_banked: currentBanked,
        })
    }

    // 3. Credit the bank, capped. Only counts toward the lifetime
    //    counter if it actually banked (forfeit days don't show up
    //    in stats so the analytics signal is honest).
    const roomLeft = Math.max(0, PRO_DAYS_CAP - currentBanked)
    const daysToBank = Math.min(achievement.pro_days, roomLeft)

    if (!streak) {
        // No streak row yet — create one with the awarded days.
        const { error: insErr } = await db
            .from("user_streaks")
            .insert({
                user_id: user.id,
                pro_days_banked: daysToBank,
                pro_days_lifetime: daysToBank,
            })
        if (insErr) {
            return errorResponse(
                `streak insert failed: ${insErr.message}`,
                500,
            )
        }
    } else {
        const { error: updErr } = await db
            .from("user_streaks")
            .update({
                pro_days_banked: currentBanked + daysToBank,
                pro_days_lifetime: currentLifetime + daysToBank,
                updated_at: new Date().toISOString(),
            })
            .eq("user_id", user.id)
        if (updErr) {
            return errorResponse(
                `streak update failed: ${updErr.message}`,
                500,
            )
        }
    }

    return jsonResponse({
        awarded: true,
        already_unlocked: false,
        pro_days: daysToBank,
        pro_days_banked: currentBanked + daysToBank,
        achievement: {
            id: achievement.id,
            name: achievement.name,
            description: achievement.description,
            icon: achievement.icon,
        },
    })
})
