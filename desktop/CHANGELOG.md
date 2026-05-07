# Changelog

All notable changes to Bullseye desktop. Format: Keep a Changelog 1.1.0.

## [Unreleased]

### Added
- **Test Appraiser rewrite** — search Marketplace, pick a real listing, click Appraise per-card. Comp data stays internal (eBay), per-listing scores show inline.
- **Lifetime savings counter** on Home: "$X saved across N deals" + cultural-comparison flex ("That's enough to buy a MacBook").
- **Sidebar trial countdown** while on trial — replaces banked-Pro-days pill with days-remaining + progress bar + Upgrade CTA.
- **Insights tab** — 90-day deal heatmap + personal-best leaderboard.
- **Granular notification toggles** in Settings (milestone toasts, first-deal-of-the-day, kill-switch banner).
- **Stripe customer portal** wired in Settings → Account → Manage / cancel.
- **No-credit-card-required trial** — `payment_method_collection: if_required`. Trial expires silently if no card is added.
- **14-day trial** (was 7) — twice the runway.
- **Wispr Flow-style shell** with sidebar + tabs (Home / Watches / Activity / Insights / Test / Stats / Settings).
- **Logo B**: black concentric circles + red center + black arrow.

### Fixed
- Google OAuth token delivery: switched from POST-fetch to GET-redirect (works reliably on pywebview's WebView2).
- File-based token fallback (`%APPDATA%\Bullseye\tokens.json`) so keychain failures don't lock users out.
- Diagnostic OAuth log at `%APPDATA%\Bullseye\bullseye.log`.
- Stripe `success_url` corrected to `getbullseye.app` (was bouncing to a parking page).

## [0.1.0] — initial release

Personal tool baseline ported into a freemium SaaS shell. Local FB
scraper, cloud Supabase backend, Stripe + Resend wired, Inno Setup
Windows installer.
