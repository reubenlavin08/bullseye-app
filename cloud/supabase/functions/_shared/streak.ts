// Pure helpers for streak math + milestone awards.
//
// Lives in _shared/ so /streak (which writes the row) and /license
// (which reads pro_days_banked to decide whether to expose
// can_redeem_trial) can share the same constants. Anyone editing
// milestone tunings must touch only this file.
//
// Design notes:
//   - Functions here are PURE: no I/O, no Date.now() side-effects,
//     no Supabase calls. Makes them trivially unit-testable from
//     either Deno or the desktop Python tests via a `deno run`
//     wrapper if we add one later.
//   - Date math uses UTC days. We deliberately don't try to honor
//     the user's local timezone — a user travelling east/west could
//     get an extra streak day, but the ROI of a per-user IANA
//     timezone column isn't worth the complexity for v1.1.

/** Cap on simultaneously-banked Pro days. Two redemptions' worth.
 *  Prevents a power user from amassing months of free Pro by
 *  hitting every milestone. */
export const PRO_DAYS_CAP = 14;

/** Pro days deducted from the bank for one trial redemption. */
export const REDEEM_COST = 7;

/** Length of the trial granted on redeem. Matches REDEEM_COST so
 *  the user effectively trades 7 banked days for a 7-day Pro trial. */
export const TRIAL_DAYS = 7;

/**
 * Pure: which milestones does this exact streak length unlock?
 *
 * Returns an array because future tunings might unlock multiple at
 * once (e.g. a "first week" cosmetic badge alongside the Pro-day
 * award). For now each length maps to at most one milestone.
 *
 * Tuning rationale:
 *   - first_streak (1): no Pro days, just an emoji ack — onboarding signal.
 *   - week_warrior (7): +1 day. First taste of Pro for free; cheap to give.
 *   - month_master (30): +3 days. Real commitment, real reward.
 *   - century_club (100): +7 days. A full free trial — this is the
 *     conversion lever. By day 100 a user is *demonstrably* sticky
 *     and a free Pro week has near-zero opportunity cost for us.
 */
export function computeMilestone(currentStreak: number): string[] {
    const out: string[] = [];
    if (currentStreak === 1) out.push("first_streak");
    if (currentStreak === 7) out.push("week_warrior");
    if (currentStreak === 30) out.push("month_master");
    if (currentStreak === 100) {
        out.push("century_club");
        out.push("year_in_review_unlocked");
    }
    return out;
}

/**
 * Pure: how many Pro days does this milestone award?
 * Unknown milestones return 0 (defensive — keeps callers from awarding
 * accidentally if we add a cosmetic-only milestone later).
 */
export function awardProDays(milestone: string): number {
    switch (milestone) {
        case "week_warrior":
            return 1;
        case "month_master":
            return 3;
        case "century_club":
            return 7;
        // first_streak, year_in_review_unlocked: no Pro-day reward.
        default:
            return 0;
    }
}

/**
 * Compute today's UTC date (YYYY-MM-DD) — extracted so tests can
 * inject a fake "today" by mocking Date if needed. We don't expose
 * a parameter because callers passing the wrong tz silently is the
 * exact bug we want to avoid.
 */
export function todayUTC(now: Date = new Date()): string {
    return now.toISOString().slice(0, 10);
}

/**
 * Return the diff in days between two YYYY-MM-DD strings (b - a).
 * Used to classify "same day / yesterday / older". Pure — no tz.
 */
export function daysBetween(a: string, b: string): number {
    const ms = Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z");
    return Math.round(ms / 86_400_000);
}

/**
 * 'YYYY-MM' for the freeze-used-month tracking. A new month means a
 * new freeze is automatically available.
 */
export function monthKey(now: Date = new Date()): string {
    return now.toISOString().slice(0, 7);
}

/**
 * Pure streak transition. Given the previous state + today's date,
 * return what the new streak should be plus whether a freeze was
 * consumed and whether this is a no-op (same-day re-call).
 *
 * Cases:
 *   - no prior activity → start at 1
 *   - same UTC day → no-op (caller should not award milestones)
 *   - exactly one day later → +1
 *   - 2+ days later, freeze available → use freeze (treat as +1)
 *   - 2+ days later, freeze unavailable → reset to 1
 */
export interface StreakTransition {
    newStreak: number;
    sameDay: boolean;
    usedFreeze: boolean;
}

export function transitionStreak(args: {
    currentStreak: number;
    lastActiveDate: string | null;
    today: string;
    freezeAvailable: boolean;
}): StreakTransition {
    const { currentStreak, lastActiveDate, today, freezeAvailable } = args;
    if (!lastActiveDate) {
        return { newStreak: 1, sameDay: false, usedFreeze: false };
    }
    const diff = daysBetween(lastActiveDate, today);
    if (diff <= 0) {
        // same day OR clock skew (treat as no-op rather than punish)
        return { newStreak: currentStreak, sameDay: true, usedFreeze: false };
    }
    if (diff === 1) {
        return { newStreak: currentStreak + 1, sameDay: false, usedFreeze: false };
    }
    // 2+ days gap
    if (freezeAvailable) {
        return { newStreak: currentStreak + 1, sameDay: false, usedFreeze: true };
    }
    return { newStreak: 1, sameDay: false, usedFreeze: false };
}
