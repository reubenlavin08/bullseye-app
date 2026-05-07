// Master list of achievements (named "actions" elsewhere — same thing).
//
// Used by:
//   - /award-action  to validate the action_name + look up the reward
//   - /achievements  to render the locked/unlocked gallery on the
//                    desktop app
//
// Idempotency: each achievement is keyed by its `id` and stored in the
// user_unlocks table with a UNIQUE (user_id, milestone) constraint —
// awarding the same id twice is a no-op (ON CONFLICT DO NOTHING).
//
// Reward sizing (retuned 2026-05-07 — third pass):
//
//   Total Pro-days budget (excluding the 7-day trial that comes
//   separately at signup): ~8 days from non-referral achievements +
//   up to 6 days from referrals = ~14 days max for a heavily-engaged
//   user with three converted friend referrals. Combined with the
//   trial, a maximally-engaged user can stretch free Pro to roughly
//   3 weeks before they need to actually pay.
//
//   Design rule: every milestone awards EXACTLY 0 OR 1 Pro day. No
//   chunky 5-day or 10-day payouts. The "many small drips" pattern
//   (per user feedback after seeing Wispr Flow's reward economy)
//   keeps the carrot visible at every step without front-loading the
//   bank balance to "free Pro forever" before the user has felt the
//   value of paying.
//
//   The 7-day trial is the BIG single payout. Achievements are the
//   slow drip after that runs out. Together they give ~14-21 free
//   days, then the $9.99/mo subscription kicks in for power users.
//
//   NON-MONETARY unlocks (insights / charts) hold attention at the
//   intermediate milestones without spending Pro-day budget — see
//   the `unlocks` field on each achievement.

export interface Achievement {
    id: string                  // stable key for user_unlocks.milestone
    name: string                // user-visible name on the gallery
    description: string         // user-visible description
    pro_days: number            // reward when first earned
    icon: string                // single emoji or short glyph
    /**
     * Family — groups related achievements in the gallery UI.
     * `insight` family achievements unlock UI features (favorite
     * category, savings velocity chart, etc.) instead of Pro days.
     */
    family: "streak" | "deals" | "savings" | "social" | "engagement" | "insight"
    /** Hint shown while still locked (e.g. "Score your first 80+ deal"). */
    hint: string
    /**
     * For `insight` family rewards: a stable key the desktop app uses
     * to render the unlocked UI feature. Null for Pro-day-only rewards.
     */
    unlocks?: "favorite_category" | "favorite_term" | "savings_velocity"
        | "hunt_rhythm" | "score_distribution" | "lifetime_chart"
}

export const ACHIEVEMENTS: ReadonlyArray<Achievement> = [
    // ===================================================================
    // STREAK family (granted by /streak server-side; listed here for
    // gallery rendering only — clients can't self-report these).
    //
    // Each milestone gives at most 1 Pro day. The 30-day streak
    // intentionally doesn't pay more than the 7-day one — sustained
    // streaks already pay out via the BANKED Pro days the streak system
    // accrues separately. We don't double-dip.
    //
    // Family budget: 0 + 1 + 1 + 1 = 3 days
    // ===================================================================
    {
        id: "streak_3",
        name: "Three in a row",
        description: "Open Bullseye three days in a row. Welcome to the habit.",
        pro_days: 0,
        icon: "🔥",
        family: "streak",
        hint: "Open the app three days in a row.",
    },
    {
        id: "streak_7",
        name: "Hot streak",
        description: "Seven-day streak — your first free day of Pro.",
        pro_days: 1,
        icon: "🔥",
        family: "streak",
        hint: "Open the app seven days in a row.",
    },
    {
        id: "streak_14",
        name: "Two weeks strong",
        description: "Fourteen-day streak. Earn another day of Pro.",
        pro_days: 1,
        icon: "🔥",
        family: "streak",
        hint: "Open the app fourteen days in a row.",
    },
    {
        id: "streak_30",
        name: "A full month",
        description: "Thirty-day streak. One more day of Pro for the dedication.",
        pro_days: 1,
        icon: "🏆",
        family: "streak",
        hint: "Open the app thirty days in a row.",
    },

    // ===================================================================
    // DEALS family — finding 80+ scored listings.
    // Family budget: 0 + 1 + 1 = 2 days
    // ===================================================================
    {
        id: "first_deal_80",
        name: "First big find",
        description: "Score your first listing rated 80 or higher. (Unlocks the Lifetime Savings chart — your headline number from day one.)",
        pro_days: 0,
        icon: "🎯",
        family: "deals",
        hint: "Find one listing that scores 80 or higher.",
        unlocks: "lifetime_chart",
    },
    {
        id: "five_deals_80",
        name: "Five-pack",
        description: "Score five listings rated 80 or higher. Earn 1 Pro day. (Unlocks the Score Distribution histogram — needs at least a handful of scored deals to mean anything.)",
        pro_days: 1,
        icon: "🎯",
        family: "deals",
        hint: "Score five listings 80 or higher.",
        unlocks: "score_distribution",
    },
    {
        id: "twenty_five_deals_80",
        name: "Sharp eye",
        description: "Score twenty-five listings rated 80 or higher. Earn 1 Pro day.",
        pro_days: 1,
        icon: "🎯",
        family: "deals",
        hint: "Score twenty-five listings 80 or higher.",
    },

    // ===================================================================
    // SAVINGS family — lifetime tracked savings.
    //
    // RETUNED 2026-05-07 (third pass) — original thresholds ($100/$500/
    // $1k/$5k/$10k) were calibrated to feel "achievable" and ended up
    // trivially-unlockable in testing (user hit $1.5k saved within
    // minutes of running the app on stale test data). Real milestones
    // need to feel earned. New schedule: 5x across the lower tiers
    // and 10x at the upper end so $50k+ savings genuinely represents
    // months-to-years of dedicated flipping.
    //
    // IDs are renamed to match the new dollar amounts so
    // future-devs-reading-this don't get confused by a "savings_100"
    // ID that triggers at $500. This also gives existing test users a
    // clean reset: their stale savings_100/savings_500/etc. unlocks
    // become orphans (banked Pro days they earned remain in
    // user_streaks; just the badges disappear from the gallery).
    //
    // Family budget: 0 + 0 + 1 + 1 + 1 = 3 days
    // ===================================================================
    {
        id: "savings_500",
        name: "First $500",
        description: "Track $500 in lifetime savings. (Unlocks your Favorite Search Term insight.)",
        pro_days: 0,
        icon: "💰",
        family: "savings",
        hint: "Hit $500 in lifetime tracked savings.",
        unlocks: "favorite_term",
    },
    {
        id: "savings_2500",
        name: "Two and a half grand",
        description: "Track $2,500 in lifetime savings. (Unlocks the weekly Hunt Rhythm chart.)",
        pro_days: 0,
        icon: "💰",
        family: "savings",
        hint: "Hit $2,500 in lifetime tracked savings.",
        unlocks: "hunt_rhythm",
    },
    {
        id: "savings_10k",
        name: "Five figures",
        description: "Track $10,000 in lifetime savings. Earn 1 Pro day. (Unlocks the Hottest Category insight.)",
        pro_days: 1,
        icon: "💰",
        family: "savings",
        hint: "Hit $10,000 in lifetime tracked savings.",
        unlocks: "favorite_category",
    },
    {
        id: "savings_50k",
        name: "Career flipper",
        description: "Track $50,000 in lifetime savings. Earn 1 Pro day. (Unlocks the Savings Velocity chart — last-12-weeks trend.)",
        pro_days: 1,
        icon: "💎",
        family: "savings",
        hint: "Hit $50,000 in lifetime tracked savings.",
        unlocks: "savings_velocity",
    },
    {
        id: "savings_100k",
        name: "Six figures",
        description: "Track $100,000 in lifetime savings. Earn 1 Pro day — the final savings milestone.",
        pro_days: 1,
        icon: "💎",
        family: "savings",
        hint: "Hit $100,000 in lifetime tracked savings.",
    },

    // ===================================================================
    // SOCIAL family — referrals. Per-referral reward dropped from 4 to
    // 1 day to align with the 1-day-at-a-time philosophy and to
    // shrink the runaway "convert 3 friends → 12 free days" path.
    //
    // Family budget: 0 + 1 = 1 day per friend, capped at 3 friends
    // (so 3 days total across all converted referrals).
    // ===================================================================
    {
        id: "first_referral_install",
        name: "First friend",
        description: "Get your first referred friend to install Bullseye.",
        pro_days: 0,
        icon: "🎁",
        family: "social",
        hint: "Share your invite link and get a friend to install.",
    },
    {
        id: "first_referral_paid",
        name: "Friend joined Pro",
        description: "Your first referred friend started Pro. 1 Pro day per friend, up to 3 friends (3 days max from referrals).",
        pro_days: 1,
        icon: "🎁",
        family: "social",
        hint: "Get a friend to subscribe to Bullseye Pro.",
    },

    // ===================================================================
    // ENGAGEMENT family — first-time interactions.
    // Family budget: 0 days (gallery only).
    // ===================================================================
    {
        id: "first_email_click",
        name: "Inbox in",
        description: "Click your first deal alert email.",
        pro_days: 0,
        icon: "📬",
        family: "engagement",
        hint: "Click a deal alert link in your email.",
    },
    {
        id: "first_watch_created",
        name: "Set the table",
        description: "Create your first saved search.",
        pro_days: 0,
        icon: "🔭",
        family: "engagement",
        hint: "Add a saved search on the Saved searches tab.",
    },
] as const

// ===================================================================
// Total earnable Pro days across the entire achievement system:
//   streak  3 + deals 2 + savings 3 + social 1×3 + engagement 0
//   = 8 days from non-referral milestones
//   + up to 3 days from converted referrals (1/friend × 3)
//   = ~11 days lifetime cap from achievements alone.
//
// Combined with the 7-day Pro trial that comes with signup, a maxed-
// out engaged user gets ~18 days of free Pro before the subscription
// path kicks in. Wispr Flow Max benchmark is in the same ballpark.
//
// Every milestone awards EXACTLY 0 or 1 Pro day per the user's
// "many small drips, not chunky payouts" design rule. Power-user
// retention is held by the NON-MONETARY insights (Favorite Category,
// Hunt Rhythm chart, etc.) rather than by giving away more Pro days.
// ===================================================================

/* Action-based achievements that the desktop app can self-report.
   Streak achievements are awarded server-side by /streak — clients
   shouldn't be able to claim them. Allowlist of client-grantable IDs: */
export const CLIENT_GRANTABLE: ReadonlySet<string> = new Set([
    "first_deal_80",
    "five_deals_80",
    "twenty_five_deals_80",
    // New savings tiers (5-10x the original calibration). Old IDs
    // (savings_100/500/1000/5000/10000) intentionally NOT listed —
    // they're orphaned, so any lingering desktop build that tries
    // to grant them gets a 400 from /award-action.
    "savings_500",
    "savings_2500",
    "savings_10k",
    "savings_50k",
    "savings_100k",
    "first_email_click",
    "first_watch_created",
])

export function findAchievement(id: string): Achievement | undefined {
    return ACHIEVEMENTS.find(a => a.id === id)
}
