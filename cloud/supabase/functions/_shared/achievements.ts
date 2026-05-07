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
// Pro-day reward sizing:
//   The Wispr-style retention loop only works if rewards visibly
//   accumulate. Small amounts (1-3 days) at frequent intervals beat
//   one big payout. Total earnable Pro days for a heavily-engaged
//   user during the first month should be ~25-35 days — enough to
//   effectively keep Pro alive for free if they're active, while
//   preserving the "subscribe for unlimited" path for casual users.

export interface Achievement {
    id: string                  // stable key for user_unlocks.milestone
    name: string                // user-visible name on the gallery
    description: string         // user-visible description
    pro_days: number            // reward when first earned
    icon: string                // single emoji or short glyph
    /** Family — groups related achievements in the gallery UI. */
    family: "streak" | "deals" | "savings" | "social" | "engagement"
    /** Hint shown while still locked (e.g. "Score your first 80+ deal"). */
    hint: string
}

export const ACHIEVEMENTS: ReadonlyArray<Achievement> = [
    // -------- Streak family (existing in user_unlocks via /streak) ----
    // We list them here so the gallery can render them with rich
    // metadata, but /streak still owns granting them.
    {
        id: "streak_3",
        name: "Three in a row",
        description: "Open Bullseye three days in a row.",
        pro_days: 1,
        icon: "🔥",
        family: "streak",
        hint: "Open the app three days in a row.",
    },
    {
        id: "streak_7",
        name: "Hot streak",
        description: "Seven-day streak — a free week of Pro is yours.",
        pro_days: 3,
        icon: "🔥",
        family: "streak",
        hint: "Open the app seven days in a row.",
    },
    {
        id: "streak_14",
        name: "Two weeks strong",
        description: "Fourteen-day streak. Now you're committed.",
        pro_days: 5,
        icon: "🔥",
        family: "streak",
        hint: "Open the app fourteen days in a row.",
    },
    {
        id: "streak_30",
        name: "A full month",
        description: "Thirty-day streak. We salute you.",
        pro_days: 10,
        icon: "🏆",
        family: "streak",
        hint: "Open the app thirty days in a row.",
    },

    // -------- Deals family ------------------------------------------
    {
        id: "first_deal_80",
        name: "First big find",
        description: "Score your first listing rated 80 or higher.",
        pro_days: 1,
        icon: "🎯",
        family: "deals",
        hint: "Find one listing that scores 80 or higher.",
    },
    {
        id: "five_deals_80",
        name: "Five-pack",
        description: "Score five listings rated 80 or higher.",
        pro_days: 2,
        icon: "🎯",
        family: "deals",
        hint: "Score five listings 80 or higher.",
    },
    {
        id: "twenty_five_deals_80",
        name: "Sharp eye",
        description: "Score twenty-five listings rated 80 or higher.",
        pro_days: 5,
        icon: "🎯",
        family: "deals",
        hint: "Score twenty-five listings 80 or higher.",
    },

    // -------- Savings family (lifetime tracked savings) -------------
    {
        id: "savings_100",
        name: "First hundred",
        description: "Track $100 in lifetime savings.",
        pro_days: 1,
        icon: "💰",
        family: "savings",
        hint: "Hit $100 in lifetime tracked savings.",
    },
    {
        id: "savings_500",
        name: "Half a grand",
        description: "Track $500 in lifetime savings.",
        pro_days: 3,
        icon: "💰",
        family: "savings",
        hint: "Hit $500 in lifetime tracked savings.",
    },
    {
        id: "savings_1000",
        name: "First grand",
        description: "Track $1,000 in lifetime savings.",
        pro_days: 5,
        icon: "💰",
        family: "savings",
        hint: "Hit $1,000 in lifetime tracked savings.",
    },
    {
        id: "savings_5000",
        name: "Five grand",
        description: "Track $5,000 in lifetime savings.",
        pro_days: 10,
        icon: "💎",
        family: "savings",
        hint: "Hit $5,000 in lifetime tracked savings.",
    },

    // -------- Social family -----------------------------------------
    {
        id: "first_referral_install",
        name: "First friend",
        description: "Get your first referred friend to install Bullseye.",
        pro_days: 1,
        icon: "🎁",
        family: "social",
        hint: "Share your invite link and get a friend to install.",
    },
    {
        id: "first_referral_paid",
        name: "Paid forward",
        description: "Get your first referred friend to subscribe.",
        pro_days: 30,
        icon: "🎁",
        family: "social",
        hint: "Get a friend to subscribe to Bullseye Pro.",
    },

    // -------- Engagement family -------------------------------------
    {
        id: "first_email_click",
        name: "Inbox in",
        description: "Click your first deal alert email.",
        pro_days: 0,  // 0.5 days handled on cloud side as half-credit; we
                      // round here. Frequent low-impact rewards are
                      // intentionally tiny.
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

/* Action-based achievements that the desktop app can self-report.
   Streak achievements are awarded server-side by /streak — clients
   shouldn't be able to claim them. Allowlist of client-grantable IDs: */
export const CLIENT_GRANTABLE: ReadonlySet<string> = new Set([
    "first_deal_80",
    "five_deals_80",
    "twenty_five_deals_80",
    "savings_100",
    "savings_500",
    "savings_1000",
    "savings_5000",
    "first_email_click",
    "first_watch_created",
])

export function findAchievement(id: string): Achievement | undefined {
    return ACHIEVEMENTS.find(a => a.id === id)
}
