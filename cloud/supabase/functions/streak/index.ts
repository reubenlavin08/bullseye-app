// /streak — daily-active check + milestone award + Pro-day banking.
//
// POST /streak  body: {}   auth: requireUser
//
// Flow:
//   1. Fetch (or create) the user_streaks row.
//   2. Compare today's UTC date to last_active_date:
//        same day        → no-op
//        +1 day          → current_streak += 1
//        +2 days, freeze → use freeze, treat as +1
//        +2 days, no fz  → reset to 1
//   3. Update longest_streak if exceeded.
//   4. For each milestone unlocked at this exact length, INSERT into
//      user_unlocks ON CONFLICT DO NOTHING. If the insert took
//      (i.e. first time hitting it), award the Pro days — capped at
//      PRO_DAYS_CAP banked.
//   5. Persist state, return summary.
//
// Why a single endpoint instead of separate read/write:
//   The desktop app calls /streak once per app open. We don't need a
//   read-only path because the same call gives back the current state
//   anyway. /license still surfaces pro_days_banked + can_redeem_trial
//   for clients that want to render the upgrade affordance without
//   ticking the streak.
//
// Idempotent? Mostly. Same-day re-calls are no-ops. Milestone awards
// are idempotent via the user_unlocks UNIQUE constraint. The only
// non-idempotent piece is the freeze: calling /streak with a 2+ day
// gap consumes the freeze on the first call, which is correct.

import {
    requireUser,
    jsonResponse,
    errorResponse,
    adminClient,
    corsHeaders,
} from "../_shared/auth.ts"
import {
    awardProDays,
    computeMilestone,
    monthKey,
    PRO_DAYS_CAP,
    todayUTC,
    transitionStreak,
} from "../_shared/streak.ts"

interface StreakRow {
    user_id: string
    current_streak: number
    longest_streak: number
    last_active_date: string | null
    freeze_used_month: string | null
    pro_days_banked: number
    pro_days_lifetime: number
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

    const db = adminClient()

    // 1. Load or create the row. Two queries instead of an upsert
    //    because we need to know whether the row pre-existed to
    //    classify "first ever streak day".
    let { data: row, error: selErr } = await db
        .from("user_streaks")
        .select("*")
        .eq("user_id", user.id)
        .maybeSingle<StreakRow>()

    if (selErr) {
        return errorResponse(`db read failed: ${selErr.message}`, 500)
    }

    if (!row) {
        const { data: created, error: insErr } = await db
            .from("user_streaks")
            .insert({ user_id: user.id })
            .select("*")
            .single<StreakRow>()
        if (insErr) {
            return errorResponse(`db insert failed: ${insErr.message}`, 500)
        }
        row = created
    }

    // 2. Compute the transition.
    const today = todayUTC()
    const thisMonth = monthKey()
    const freezeAvailable = row.freeze_used_month !== thisMonth

    const transition = transitionStreak({
        currentStreak: row.current_streak,
        lastActiveDate: row.last_active_date,
        today,
        freezeAvailable,
    })

    // Same-day re-call: just echo current state, no writes, no awards.
    if (transition.sameDay) {
        return jsonResponse({
            current_streak: row.current_streak,
            longest_streak: row.longest_streak,
            freeze_available: freezeAvailable,
            pro_days_banked: row.pro_days_banked,
            milestones_earned_today: [],
        })
    }

    // 3. Decide the new state.
    const newStreak = transition.newStreak
    const newLongest = Math.max(row.longest_streak, newStreak)
    const newFreezeMonth = transition.usedFreeze ? thisMonth : row.freeze_used_month

    // 4. Milestone awards. Insert each ON CONFLICT DO NOTHING; only
    //    rows that *actually* inserted count toward the bank update.
    const milestones = computeMilestone(newStreak)
    let proDaysAwarded = 0
    const milestonesEarnedToday: string[] = []

    for (const milestone of milestones) {
        const award = awardProDays(milestone)
        // Insert and check whether it was a fresh insert. PostgREST
        // returns the inserted rows; an empty array means the
        // ON CONFLICT path was taken (already earned).
        const { data: inserted, error: unlockErr } = await db
            .from("user_unlocks")
            .upsert(
                { user_id: user.id, milestone, pro_days_awarded: award },
                { onConflict: "user_id,milestone", ignoreDuplicates: true },
            )
            .select("id")
        if (unlockErr) {
            // Don't bail on the whole streak update if a unlock write
            // hiccups — log and continue. Worst case the user retries
            // tomorrow at the same length and the unique constraint
            // still keeps things idempotent.
            console.warn("unlock insert failed:", unlockErr.message)
            continue
        }
        if (inserted && inserted.length > 0) {
            milestonesEarnedToday.push(milestone)
            proDaysAwarded += award
        }
    }

    // Cap the bank. We award only as many days as fit under the cap;
    // the rest are forfeit. Lifetime counter still reflects only
    // banked days (i.e. capped) so the analytics signal is honest.
    const roomLeft = Math.max(0, PRO_DAYS_CAP - row.pro_days_banked)
    const proDaysToBank = Math.min(proDaysAwarded, roomLeft)

    // 5. Persist.
    const updates = {
        current_streak: newStreak,
        longest_streak: newLongest,
        last_active_date: today,
        freeze_used_month: newFreezeMonth,
        pro_days_banked: row.pro_days_banked + proDaysToBank,
        pro_days_lifetime: row.pro_days_lifetime + proDaysToBank,
        updated_at: new Date().toISOString(),
    }
    const { error: updErr } = await db
        .from("user_streaks")
        .update(updates)
        .eq("user_id", user.id)
    if (updErr) {
        return errorResponse(`db update failed: ${updErr.message}`, 500)
    }

    // freeze_available reflects the state AFTER the call. If we just
    // consumed it, the user has no freeze left this month.
    const freezeAvailableAfter = newFreezeMonth !== thisMonth

    return jsonResponse({
        current_streak: newStreak,
        longest_streak: newLongest,
        freeze_available: freezeAvailableAfter,
        pro_days_banked: row.pro_days_banked + proDaysToBank,
        milestones_earned_today: milestonesEarnedToday,
    })
})
