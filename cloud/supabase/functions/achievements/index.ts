// /achievements — return the master list of achievements joined with
// the user's unlock state, for rendering the gallery in the desktop
// app.
//
// Response shape:
//   {
//     pro_days_banked: number,
//     pro_days_lifetime: number,
//     unlocked_count: number,
//     total_count: number,
//     achievements: [
//       {
//         id, name, description, pro_days, icon, family, hint,
//         unlocked: boolean,
//         unlocked_at: string | null,
//       }
//     ]
//   }
//
// Streak achievements show as locked until the user_unlocks row from
// /streak's milestone-award path appears. Action achievements show as
// locked until /award-action grants them.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import { ACHIEVEMENTS } from "../_shared/achievements.ts"

interface UnlockRow {
    milestone: string
    earned_at: string
}

interface StreakRow {
    pro_days_banked: number
    pro_days_lifetime: number
}

Deno.serve(async (req: Request) => {
    if (req.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() })
    }
    if (req.method !== "GET" && req.method !== "POST") {
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

    // Pull all unlock rows for the user. Single round-trip; gallery
    // is small enough that a per-achievement check would be wasteful.
    const { data: unlocks, error: unlockErr } = await db
        .from("user_unlocks")
        .select("milestone, earned_at")
        .eq("user_id", user.id)
    if (unlockErr) {
        return errorResponse(
            `user_unlocks read failed: ${unlockErr.message}`,
            500,
        )
    }

    const unlockMap = new Map<string, string>()
    for (const u of (unlocks ?? []) as UnlockRow[]) {
        unlockMap.set(u.milestone, u.earned_at)
    }

    // Streak row for the gallery header (banked + lifetime). May not
    // exist yet for users who haven't ticked /streak.
    const { data: streak } = await db
        .from("user_streaks")
        .select("pro_days_banked, pro_days_lifetime")
        .eq("user_id", user.id)
        .maybeSingle<StreakRow>()

    let unlockedCount = 0
    const decorated = ACHIEVEMENTS.map(a => {
        const unlockedAt = unlockMap.get(a.id) ?? null
        if (unlockedAt) unlockedCount++
        return {
            id: a.id,
            name: a.name,
            description: a.description,
            pro_days: a.pro_days,
            icon: a.icon,
            family: a.family,
            hint: a.hint,
            // Insight unlock key (or undefined for Pro-day-only
            // achievements). Lets the desktop's tab_insights.js map
            // the user's unlock state to the widget renderer without
            // hard-coding the achievement→widget pairing in two places.
            unlocks: a.unlocks ?? null,
            unlocked: !!unlockedAt,
            unlocked_at: unlockedAt,
        }
    })

    return jsonResponse({
        pro_days_banked: streak?.pro_days_banked ?? 0,
        pro_days_lifetime: streak?.pro_days_lifetime ?? 0,
        unlocked_count: unlockedCount,
        total_count: ACHIEVEMENTS.length,
        achievements: decorated,
    })
})
