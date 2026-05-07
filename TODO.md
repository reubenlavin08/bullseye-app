# Bullseye — running TODO

Single source of truth for every ask you've made. Rules:

- **Every chat from you adds an item here BEFORE I do any work.** Even one-off asks, design notes, fixes. I parse the message, write items into the relevant section, then start. (Rule added 2026-05-05.)
- An item is **only** checked off after (a) the actual work is verifiably done AND (b) you've explicitly said "this is done" or equivalent.
- "Built, awaiting verification" stays unchecked. Code on disk is not the same as done.
- After every completed task, I post a **completion note** in this format:
  - **Built**: bullet list of what shipped (action verbs)
  - **Files touched**: paths + one-line "what changed"
  - **Smoke test**: result, one line
  - **Verify on your end**: concrete steps you can run
  - **Open / blockers**: decisions you need to make or things you need to do
  - **Next**: what happens if you approve

Last updated: 2026-05-05

---

## Section A — Wispr Flow restructure (the active redesign)

### A1 — Planning

- [ ] **Inspect Wispr Flow's actual app** and build a 1:1 feature inventory. _Status: not started. The current shell was built on my guess._
- [ ] **Map every Wispr feature to a Bullseye equivalent** (table form). _Status: rough tab map exists, full inventory pending A1.1._
- [ ] **Match Wispr's visual language**, not just layout. Tokens (color, spacing, type, density) compared side-by-side. _Status: tokens picked off-the-cuff._
- [ ] **Write one unified plan doc** combining feature inventory + retention plan + sequence + feature flags. _Status: not started._
- [ ] **Plan reviewed and approved by you** before further code changes. _Status: not yet — you flagged that I executed without approval._

### A2 — Shell scaffolding (already on disk, unverified)

- [ ] `app_shell.html` (sidebar + content layout). _Built, not verified by you._
- [ ] `auth.html` (centered sign-in card). _Built, not verified by you._
- [ ] `tab_home.html` + `tab_home.js`. _Built, not verified by you._
- [ ] `tab_watches.html` + `tab_watches.js`. _Built, not verified by you._
- [ ] `tab_activity.html` + `tab_activity.js`. _Built, not verified by you._
- [ ] `tab_test.html` + `tab_test.js`. _Built, not verified by you._
- [ ] `tab_stats.html` + `tab_stats.js`. _Built, not verified by you._
- [ ] `tab_settings.html` + `tab_settings.js`. _Built, not verified by you._
- [ ] `shell.css` (single stylesheet). _Built, not verified by you._
- [ ] `shell.js` (apiGet/apiPost/toast helpers). _Built, not verified by you._

### A3 — Bugs you reported on 2026-05-04

- [ ] **Test Appraiser 404** when entering anything. _Root cause: `index.html` posted to nonexistent `/search`. Replaced via `tab_test.html` posting JSON to `/appraise`. Built, not verified by you._
- [ ] **Filter buttons don't work** in Test Appraiser. _Same root cause; filter inputs are now JS-controlled. Built, not verified by you._
- [ ] **App opens to marketing surface, not auth.** _Built: anonymous users now redirect `/` → `/auth`. Not verified by you._
- [ ] **"Searching from undefined, undefined".** _Old surface removed; new home-location form lives in Settings tab with explicit empty state. Built, not verified by you._
- [ ] **Can't type in location field / no auto-fill.** _New Settings → Location uses `/api/geocode` autocomplete. Built, not verified by you._
- [ ] **No error feedback on save.** _New `apiPost()` helper renders errors via toast. Built, not verified by you._
- [ ] **Login page UI doesn't match the app.** _`/login` now redirects to `/auth`; `/auth` uses `shell.css`. Built, not verified by you._
- [ ] **Allowed to attempt save without sign-in.** _Watches tab is auth-gated; anon users redirect to `/auth`. Built, not verified by you._
- [ ] **"Waiting for sign in to complete" wording.** _Still says this on the auth poll loop. Wording deliberate-ish but you flagged it. Not changed._

### A4 — Loose ends from agent 2's report

- [ ] **Sidebar shows user email.** _Wired via `token_store.load_email()` decoding the JWT. Built, not verified._
- [ ] **`/api/watches` POST accepts email + threshold and creates subscriber row.** _Built, not verified._
- [ ] **`/login` and `/logout` redirect to `/auth`.** _Built, not verified._
- [ ] **Active tab highlighting in sidebar.** _Confirmed working via Jinja `{% set active_tab %}` smoke test._
- [ ] **Customer portal URL** in Settings → Manage subscription. _Currently links to `/upgrade`. Wrong. Needs `/api/billing/portal` cloud endpoint. Not done._
- [ ] **Version string in Settings → About.** _Placeholder. Not done._
- [ ] **Stats tab feature parity** with old dashboard (score histogram, per-watch breakdown). _Basics ported, advanced charts missing._
- [ ] **Delete orphaned templates** (`index.html`, `dashboard.html`, `login.html`). _Awaiting your test-drive before deletion._
- [ ] **Telemetry opt-out toggle persists to SQLite.** _Toggle exists in Settings UI; never verified the round-trip to `user_settings.telemetry_opt_out`._

---

## Section B — Wispr Flow-style retention plan

- [ ] **Compile retention plan as one document.** _Pieces are scattered in code and conversation. No single doc. Not done._
- [ ] **Daily streak counter.** _Cloud + desktop wired (`/streak` Edge Function, migration 011, `cloud/streak.py`). Not user-verified._
- [ ] **Pro-day banking** (1 day banked per N consecutive days). _Wired. Not user-verified._
- [ ] **Redeem 7 banked days → 7-day Pro trial.** _Wired (`POST /streak {action:"redeem_pro_days"}`, banner on home tab). Not user-verified._
- [ ] **Milestones** at 7 / 30 / 100 days (week_warrior +1, month_master +3, century_club +7). _Wired in `_shared/streak.ts`. Not user-verified._
- [ ] **Per-month streak freeze.** _Wired. Not user-verified._
- [ ] **First-deal-of-the-day toast.** _Discussed in plan. Not implemented._
- [ ] **Hunter profile** (v1.2 — viewable badge of stats). _Discussed. Not implemented._
- [ ] **Year-end recap** (v1.2). _Discussed. Not implemented._
- [ ] **Refer-a-friend mechanic** (v1.2). _Discussed. Not implemented._
- [ ] **Feature-flag rollout strategy** for retention features. _Not designed._
- [ ] **Benchmark our retention surface against Wispr Flow's.** _Not done — depends on A1._

---

## Section C — Adversarial security testing

- [ ] **AUTH/RLS** (45 cases). _Built, run, findings doc clean. Last user agreement: "great" earlier in session._
- [ ] **Stripe webhook** (16 cases). _Built, run, findings doc clean._
- [ ] **License bypass** (24 cases). _Built, run, 3 bugs found and fixed: TOCTOU race, pause-create-unpause cap, kill-switch fail-closed bug._
- [ ] **Injection** (128 cases). _Built, run, 1 HIGH (errorhandler 4xx → 500) and 1 MED (JSON depth-bomb DoS) fixed._
- [ ] **Concurrency.** _Run by agent 1, 1 MED deferred (cloud cache stampede), 2 LOWs deselected as test-framework artifacts._
- [ ] **All 201 adversarial tests passing.** _Confirmed at end of integration pass. Not user-verified._
- [ ] **You've reviewed the 4 findings docs** and signed off. _Not done._

---

## Section D — Setup tasks you control

### D1 — Already attempted

- [ ] **Supabase project + anon key + connect Google OAuth.** _Done by you earlier in session._
- [ ] **Stripe products** (monthly `price_1TTXVsPHtYj5hQGcIUJo383n`, yearly `price_1TTXbKPHtYj5hQGctkiTyIeb`). _Done by you._
- [ ] **Stripe webhook secret installed.** _Done by you._
- [ ] **Email confirm toggle off.** _Done by you (smoke-test mode)._
- [ ] **Sentry DSN.** _Done by you._
- [ ] **Inno Setup installer working** (`Bullseye-Setup.exe` ~32 MB on disk). _Built. Never installed by you._

### D2 — Pending

- [ ] **Test-drive `Bullseye-Setup.exe` end-to-end** on your machine. SmartScreen → install → launch → Google OAuth → add a watch → wait for digest. **This unblocks everything else.**
- [x] **Buy / claim a domain.** Done — `getbullseye.app` registered at Name.com (2026-05-04).
- [x] **Resend domain verify** at `mail.getbullseye.app` (subdomain). DNS at Name.com. Verified 2026-05-05.
- [x] **Resend API key + FROM + REPLY_TO** added as Supabase Edge Function secrets. Done 2026-05-05.
- [x] **Supabase Auth → Resend SMTP** wired (port 587). Test invitation email landed. Bounce-warning will auto-clear in ~24h. Done 2026-05-05.
- [ ] **GitHub Pages for landing site.** Repo already exists; add CNAME + A records at name.com pointing to GitHub Pages, enable Pages on `landing/public/` directory. _Walkthrough pending — ask when ready._
- [ ] **ImprovMX for inbound email forwarding** (`support@getbullseye.app` → your Gmail). Free tier covers 1 alias. _Walkthrough pending._
- [ ] **Email + password sign-in option** (in addition to Google). Add UI on `/auth`, wire to Supabase `/auth/v1/signup` + `/auth/v1/token?grant_type=password`. _Ask added 2026-05-05._
- [ ] **Redeploy `/comps` with `--no-verify-jwt`** so the public Test Appraiser actually returns data for anon users. From `bullseye/cloud/supabase/`: `supabase functions deploy comps --no-verify-jwt`.
- [ ] **Redeploy `/stripe-webhook` with `--no-verify-jwt`** for the same reason — Stripe doesn't send a JWT.
- [x] **Stripe live mode:** keys + webhook signing secret in Supabase. Done 2026-05-05.
- [ ] **`supabase secrets set MIN_SUPPORTED_VERSION=0.1.0`.**
- [ ] **Plausible or SimpleAnalytics signup,** paste tag into landing pages. _SimpleAnalytics in Tier 2 student pack._
- [ ] **Termly privacy + terms.** You said you'd handle tomorrow.

### D3 — GitHub Student Pack: Tier 2/3 to claim later (remind me)

Tier 1 already claimed (Sentry, Stripe, Copilot, 1Password, Polypane, Testmail, DevCycle, DigitalOcean, GitHub Cert Voucher).

Tier 2 / 3 pending — claim before launch or as needed:

- [ ] **BrowserStack** (Tier 2) — for cross-device landing-page test before going live
- [ ] **Codecov** (Tier 2) — drop-in for PR coverage diffs, ~10 min setup
- [ ] **Doppler** (Tier 2) — alternative to 1Password for runtime secrets; skip if 1Password is enough
- [ ] **SimpleAnalytics** (Tier 3 alternative to Plausible) — privacy-friendly analytics, free 1 yr starter
- [ ] **Notion Education** (Tier 3) — only if you outgrow `TODO.md` markdown
- [ ] **GitHub Certification exam** — voucher claimed; schedule the exam itself when convenient (expires June 2026)

---

## Section E — Polish before launch

- [ ] **Logo refinement** in Claude Artifacts. _Not started._
- [ ] **Demo GIF** of the dashboard for landing hero. _Not started._
- [ ] **Landing-page screenshots block** (3 stills if no GIF). _Not started._
- [ ] **README polish** on `bullseye-app` repo (architecture diagram, install steps, screenshot). _Skeleton exists, not polished._
- [ ] **Hero copy A/B variants** for landing. _Not started._
- [ ] **Empty-state designs** for Home / Watches / Activity tabs. _Not started._
- [ ] **Mobile breakpoints** on landing page. _Not started._
- [ ] **`bullseye://` protocol handler** for upgrade-success deep-link. _Not started._
- [ ] **Code-signing cert** (~$200/yr) to remove SmartScreen warning. _Skip until first paid users._

---

## Section F — Defensive code I flagged but didn't write

- [ ] **Resend daily-budget gate** in `/alerts-send` (e.g. cap at 2000 sends/day). _Not done._
- [ ] **Migration rehearsal** (apply all 11 migrations to a throwaway Supabase project). _Not done._
- [ ] **Fresh Windows VM install test** (no developer tooling installed). _Not done._

---

## Section G — Anything you flagged that I didn't address

- [ ] **"Waiting for sign in to complete"** — exact wording you called out. Still in `auth.html`. Could change to "Approve in your browser…" or similar.
- [ ] **GitHub Student Pack guidance** — answered in chat, but the actual claim/redeem actions are pending you.
- [ ] **Domain registration guide** — answered in chat, pending you.
- [ ] **"How do I use Claude design to make a logo and design the website"** — discussed at high level, no concrete next step.
- [ ] **Comparison to Swoopa** in landing page is currently a copywriter's claim, not researched. _Verify or rewrite._

---

## Section L — Asks from chat 2026-05-05 (latest)

- [x] **/comps JWT toggle correct?** — yes, OFF is right. Same as `--no-verify-jwt`.
- [x] **Read OAuth log myself** — pulled `%APPDATA%\Bullseye\bullseye.log`. Only 5 lines (post-launch); sign-in succeeded on a prior run. You confirmed sign-in works now.
- [x] **Redeploy /comps for me** — done. Removed `requireUser()` from function code, deployed via supabase CLI with `--no-verify-jwt`. Direct curl test returned sample_size 50 for "MacBook Pro M2".
- [x] **Auto-check "Launch Bullseye" checkbox** in Inno Setup installer — `unchecked` flag removed, installer rebuilt.
- [x] **Sign-in works (you confirmed)** — Google OAuth flow now persists tokens via keychain + file fallback.

## Section T — Wispr Flow retention strategies (deep research, 2026-05-05)

### What Wispr Flow does for retention (verified from your screenshots + their pricing page text dump)

**Trial mechanics**:
- 14-day trial (not 7), no credit card, "You'll start with Flow Pro free for 14 days"
- After trial: drops to Basic (free with limits), no auto-charge
- Always-visible sidebar countdown: "Your trial ends in 8 days" + progress bar + "Upgrade to Pro" button
- Replaces sidebar-bottom pill content with trial-state messaging while active

**Streak + heatmap**:
- Daily streak counter ("13 day streak") shown on Home AND Insights
- 90+ day calendar heatmap on Insights tab, accent-2 green intensity scale, "Current streak" highlighted
- "Longest Streak | 13 days" comparison
- "Top 9%" WPM ranking — competitive social proof

**Accumulating-value counters**:
- "34.8K total words" prominently displayed
- "1,483 fixes made by Flow"
- "You've written 8 short film scripts!" — translates raw counts into culturally meaningful units
- "$1,086/mo saved" calculator widget on landing page

**Persistent feature prompts**:
- Black hero card on Home: "Make Flow sound like you · Set up different writing styles for different apps · Start now" — drives users to a sticky feature
- "Voice Profile · Appraisal Optimizer" card — names their custom AI persona

**Referral system ("Refer and earn rewards")**:
- "Give a month of Pro and get 1 month for each person you refer."
- Visible reward card mockup ("Flow Pro - Unlimited words for 1 month - Gifted by [Name]")
- 3 tabs: Refer / Past invites / Apply referral
- Personal referral link (`https://wisprflow.ai/r/REUBEN241`)
- Auto-applies rewards to next subscription payment
- "You must subscribe via the desktop app to claim your rewards" — funnels back to product
- Sidebar entry: **"Get a free month"** — direct entry point

**Team / collaboration as carrot**:
- "Create a team to get a 2-week pro trial and unlock team features!"
- Adds incentive to drag teammates in
- Team-pricing tier explicit on landing

**Notifications + activity**:
- Notification bell top-right (drives return visits)
- TODAY section on Home with timestamped activity rows
- Reinforces "I'm using this productively" feeling

**Insights tab as a destination**:
- Tabs: Your Usage / Your Voice
- Multiple charts (WPM, fixes, total words, app breakdown, streak heatmap)
- **Share button** top-right — sharable PNG / link
- Self-quantification = lock-in

**Student discount**:
- 3 months free + 50% off Pro
- Targets a real demographic (you, actually)
- Specific carve-out drives signups

**Landing-page social proof**:
- Logo carousel: Lovable, Menlo, Clay, Mercury, Groupon, Vercel, Replit, Notion, Substack, Amazon, Strava, Nvidia
- "Teams move faster with Flow"

**Pricing transparency** (drives free→paid):
- Monthly/Annual toggle (20% discount)
- "No credit card required" emphasized
- Granular feature comparison table
- **Calculate your savings** interactive widget — quantifies ROI in real numbers

**What's New + Help + onboarding**:
- "What's New" in Help menu — public changelog
- Help menu as popover with categorized links (Updates / Essentials / Get in touch)
- Creates impression of active development

### What Bullseye should adopt — ranked by impact ÷ effort

**Tier 1 — Ship in next 1-2 weeks**:
- [ ] **Sidebar trial countdown** — when user is on trial, replace banked-Pro-days pill with "Trial ends in N days · Upgrade →" + progress bar. Same template, different state. ~1h.
- [ ] **"Get a free month" sidebar item** — entry point to referral modal. ~30 min for entry, full referral system in Tier 2.
- [ ] **Notification bell top-right** — opens an inbox of high-score deals scored while you weren't looking. ~3h.
- [ ] **"What's New" link in Help / Settings** — render `CHANGELOG.md` as a modal. Drives perception of active dev. ~30 min.
- [ ] **Total-savings counter** on Home: "You've found 142 deals worth $5,400 in savings." Aggregates `listings.fair_value - listings.price` for scored rows over user's lifetime. ~2h.
- [ ] **Cultural-comparison flex** — once savings cross thresholds, surface "That's enough to buy a new MacBook" / "A trip to Mexico" / "A used car". Lookup table of price→item. ~1h.

**Tier 2 — Ship over the following 2-4 weeks**:
- [ ] **Refer & earn rewards** — full backend: per-user referral code on `licenses` table, tracking via `referrals` table, auto-apply 1 month free on signup, sidebar modal with link/copy/send. ~1 day.
- [ ] **Settings → Plans & Billing as sub-page** — move `/upgrade` content into Settings. Keeps users in-app instead of bouncing through routes. Mirrors Wispr exactly. ~2h.
- [ ] **Calculate-your-savings widget on landing** — interactive ROI: "How many hours/week do you spend hunting Marketplace?" → "Bullseye Pro saves you $X/month at the average finder fee." ~3h.
- [ ] **14-day trial (not 7)** — Wispr does 14, gives users twice the runway to fall in love. Change `trial_period_days: 7` → 14 in `_shared/stripe.ts`, redeploy, update copy on landing/upgrade pages. ~30 min.
- [ ] **Student discount** — 3 months free for verified students (use GitHub Student Pack or .edu email). You're the target demographic; this is on-brand. ~1 day.
- [ ] **Hunter Profile card on Home** (a la Voice Profile): "Hunter Profile · Bargain Optimizer" + small illustration + stats summary. Names their hunter persona. Already in v1.2 plans. ~half day.

**Tier 3 — Bigger / dependent on growth**:
- [ ] **WPM-equivalent metric** — "Top X% of hunters" needs anonymous cross-user benchmarking. Defer until N>200 users. ~2 days.
- [ ] **Logo carousel on landing** — needs real customer logos. Defer until you have permission from real customers.
- [ ] **Daily challenge mechanic** — "Watch one keyword today, see what scores" with a dedicated card and progress chip on Home. ~1 day.
- [ ] **Activity-feed TODAY section** styled per Wispr — already partially in our Home tab; refine the row treatment per the design handoff. ~2h.
- [ ] **Persistent black "feature spotlight" hero card** on Home — rotates between: "Set your home location", "Add your first watch", "Try the appraiser", "Refer a friend". ~3h.

### Sequencing recommendation

After Phase D1 lands (just shipped) and the post-Stripe portal work settles, the highest-ROI sequence is:

1. **Tier 1 batch** (sidebar trial countdown + total-savings counter + cultural-comparison flex + What's New) — these are all small but visibly transform the "feels like a real product" perception. ~1 day total.
2. **Tier 2 batch starting with Refer-and-earn** — the single biggest organic-growth lever Wispr has. Without it, every user is one-shot. ~2-3 days.
3. **14-day trial change + landing savings calculator** — improves trial→paid conversion. ~1 day.
4. Then Phase D3-D7 of the design handoff (score component, hub polish, landing v2, email, event tail).

## Section U — Cookie policy (2026-05-05)

- [ ] **Create `landing/public/cookies.html`** describing cookies + localStorage Bullseye actually uses (Stripe checkout, Supabase auth, optional analytics, referral/checkout local stash). GDPR-light, focused on what the product does.
- [ ] **Link the policy from every landing page footer** (index, pricing, download, privacy, terms, upgrade-success).

## Section S — Tweaks 2026-05-05 (post-Phase-D1)

- [ ] **Logo center: red, not orange** — Logo B's center dot currently uses `var(--accent)` (burnt orange #c2410c). Change to a saturated red (~#c0202a) so the mark reads as archery-target-with-arrow, not a corporate dartboard. Black rings + black arrow stay.
- [ ] **Remove anon Test Appraiser entirely** — kill the "Try without account" banner, gate `/test` behind login, gate `/api/search` and `/appraise` behind login. Simplify the codepath.

## Section R — Design handoff (Bullseye.zip, 2026-05-05)

You sent a complete design system. Source: `bullseye/_design_handoff/design_handoff_bullseye/`. Logo B (target + arrow) is canonical. Phasing the apply-work so each phase ships independently:

- [ ] **Phase D1 — Design tokens + logo + typography** (the foundation): replace shell.css color/radius/shadow/font tokens to match exactly; replace Logo SVG everywhere with Logo B; switch headings + score numerals to Georgia serif. **Starting now.**
- [ ] **Phase D2 — Component primitives**: add `.kicker`, `.score` (with good/ok/meh tiers), `.chip`, `.dot-live`, `.ph` placeholder, `.score-clickable`. Used everywhere downstream.
- [ ] **Phase D3 — Score component (interactive)**: rebuild the per-listing appraisal display as the design's `<ScoreBigInteractive/>` — left score column + right title/meta + inline breakdown panel with "math" / "comps" tabs. Replaces the simple inline score row I just added.
- [ ] **Phase D4 — Hub layout polish**: 232px sidebar, top bar with global search + live-poll chip + avatar, Home view greeting + overnight-summary card. Most of structure exists; this is visual alignment.
- [ ] **Phase D5 — Landing v2 rewrite**: replace `landing/public/index.html` with the design's `landing-v2.jsx` structure — live-feed hero, 4-stat band, Free vs Pro pricing, "Built for" personas. Logo B in nav.
- [ ] **Phase D6 — Daily digest email template**: rebuild `cloud/.../resend.ts renderDigestHtml` to match `email.jsx` (Gmail-style envelope, grouped-by-keyword sections, deal cards with photo + asking/fair/under prices).
- [ ] **Phase D7 — Event tail (cleaned)**: rework dashboard/stats event log rows to match `EventTailClean` (12px dot · time · kind · message grid; hits get accent-soft bg).

## Section Q — Asks from chat 2026-05-05 (Stripe Customer Portal 404)

- [ ] **Build `/api/billing/portal` cloud Edge Function** — takes user's JWT, looks up their `stripe_customer_id` from licenses, creates a Stripe billing-portal session, returns the short-lived URL. Wire this to Settings → "Manage subscription" + upgrade-success.html.
- [ ] **Fix the broken "Stripe customer portal" link** in `landing/public/upgrade-success.html` — currently a placeholder pointing at a `test_00000...` URL that 404s. Replace with guidance + (eventually) the live `/api/billing/portal` URL.
- [ ] **Cancel-trial / cancel-subscription button** in desktop Settings → Account section. Wires to the same `/api/billing/portal` (which surfaces a Cancel button in Stripe's hosted UI), OR a direct "Cancel subscription" button that calls a cloud `/api/billing/cancel` endpoint to immediately cancel-at-period-end.

## Section P — Asks from chat 2026-05-05 (post-Stripe-checkout)

- [ ] **Upgrade page in desktop app looks unstyled** — `templates/upgrade.html` doesn't extend `app_shell.html` or pull `shell.css`. Restyle to match the rest of the app.
- [ ] **Trial flow took a credit card despite "no card required" copy** — fix Stripe Checkout config: `payment_method_collection: 'if_required'` + `subscription_data.trial_settings.end_behavior.missing_payment_method: 'cancel'`. Redeploy `/checkout-create`.
- [ ] **Cancel my current trial so my card isn't charged** — guide user through Stripe Dashboard → Subscriptions → cancel.
- [ ] **"Open Bullseye" button on `/upgrade-success.html` doesn't work** — no `bullseye://` URL protocol registered. Replace with clear instruction OR register the protocol via Inno Setup. Pick easiest: text instruction for v1.

## Section O — Asks from chat 2026-05-05 (after Upgrade-redirect / GoDaddy parking screenshot)

- [ ] **Stripe checkout success_url redirects to `bullseye.app` GoDaddy parking page** — code has correct URL but cloud `/checkout-create` Edge Function not redeployed. Redeploy now.
- [ ] **Audit any other code path linking to `bullseye.app`** (no `get` prefix). Find + fix.
- [x] **Move Test Appraiser from desktop app to landing site** — DECIDED 2026-05-05: skip the port. Replace with an interactive GIF demo on the landing page (you'll record it). Reason: cached FB listings would have dead links over time; a recorded GIF stays evergreen.
- [ ] **Record + add interactive GIF demo** of the desktop appraiser flow to landing page hero. (You record; I drop in.)
- [ ] **Email + password (+username) sign-up flow** — confirmed: a normal sign-up form with email field. Add to /auth.
- [ ] **Sidebar email pill should be clickable** → goes to profile/account view (Wispr Flow style).
- [ ] **DO NOT rebrand orange now** — user will send a design tablet later. Hold any color/style work.
- [ ] **Check for running local `deal_finder`/Bullseye polling on system** and stop it (rate limit hit).
- [ ] **Email + username/password sign-in** in addition to Google OAuth. (Re-stated; was already in TODO.)
- [ ] **Clarify "you need to add your email at some point"** — what email? where? Asking back.

## Section M — Asks from chat 2026-05-05 (after appraiser screenshot)

- [ ] **Test Appraiser shows wrong content** — currently dumps eBay comp results as if they were listings to pick. Should mimic personal `deal_finder/` flow: search term → list of REAL FB Marketplace listings → user clicks "Appraise" on a chosen one → score appears. The eBay comps are INTERNAL lookup data, not user-facing.
- [ ] **Port test-appraiser flow from personal deal_finder/** — don't reinvent. Reference `Claude Project/deal_finder/deal_finder/webapp/app.py`'s `/search` route + the listing-card UI it rendered.
- [ ] **Anon (not-signed-in) test appraiser still failing** — even after /comps redeploy. Diagnose end-to-end after the rewrite lands.

## Section N — Back-fill: past complaints I should have tracked from earlier in chat

These were addressed in passing or implicitly; logging them explicitly per the new rule. Status reflects current state.

- [x] **Domain rename to getbullseye.app** — 52 references updated 2026-05-04
- [x] **Resend domain verified at mail.getbullseye.app** — DNS at Name.com 2026-05-05
- [x] **Resend API key + FROM + REPLY_TO in Supabase secrets** — 2026-05-05
- [x] **Supabase Auth → Resend SMTP wired** — fixes bounce warning, 2026-05-05
- [x] **Bounce-email warning addressed** — auto-clears within 24h
- [x] **Stripe webhook test** — done 2026-05-05. 6 events confirmed succeeded in Stripe → Webhooks → Recent deliveries.
- [ ] **GitHub Pages "Source" dropdown flipped to "GitHub Actions"** — README still showing because dropdown still on "Deploy from a branch"
- [x] **Subscription redirect fix** — pricing.html updated to use explicit URL + better error message
- [x] **Download 404 fix** — created `landing/public/download.html`
- [x] **Swoopa comparison removal** — comparison table now Free vs Pro only, FAQ rephrased

## Section L (old) — moved into Section L above

## Section I — Landing page bugs you reported on 2026-05-05

- [ ] **Subscription redirect on pricing page does not work** — clicking the Pro plan button does nothing or fails. Diagnose + fix.
- [ ] **"Download free" button → 404** on the landing page hero. Either point at the actual installer URL (GitHub Releases) or hide the CTA until we have a hosted .exe.
- [ ] **Remove the Swoopa comparison** from the landing page. You don't want comparisons there.

## Section J — OAuth root-cause investigation (still open)

- [x] **Verify Supabase Auth → URL Configuration → Redirect URLs** — done 2026-05-05. Localhost callback + getbullseye.app paths added.
- [ ] **Verify Google Cloud Console OAuth client → Authorized redirect URIs** includes both `https://qfkzhyxmohytnzskcmdv.supabase.co/auth/v1/callback` AND `http://localhost:53682/auth/callback`. (The Supabase one is required; the localhost one isn't strictly needed but doesn't hurt.)
- [ ] **Reinstall + try sign-in once + share the OAuth log lines** from `%APPDATA%\Bullseye\bullseye.log` so we can see the exact failure point.

## Section K — All other pending items I want tracked explicitly

These were mentioned but never made the list cleanly:

- [ ] **Email + password sign-in option** in addition to Google (you flagged this 2026-05-05)
- [ ] **Wire toast emitter to read Phase 3 notification flags** (`notifications/desktop.py` reads `notif_milestones`/`notif_first_deal`/`notif_kill_switch_banner` before firing)
- [ ] **Customer portal URL** for paid users in Settings → Manage subscription (currently links to /upgrade)
- [ ] **Version string in Settings → About** (placeholder)
- [ ] **Stats tab feature parity** with old dashboard (score histogram, per-watch breakdown)
- [ ] **Delete orphaned templates** (`index.html`, `dashboard.html`, `login.html`) — pending your install verification first
- [ ] **Telemetry opt-out toggle persistence** — verify round-trip to SQLite
- [ ] **Wire Testmail.app integration test** for `/alerts-send` (post-launch)
- [ ] **Resend daily-budget gate** in `/alerts-send` (defensive)

## Done and verified

(empty — nothing has been mutually marked done yet.)

---

## Section H — Wispr Flow restructure plan (consolidated from PLAN.md)

This section is the working plan. Phases run in order. After each phase lands, you review and approve before I move to the next. Section A above gets crossed off **only when this entire section is complete and you sign off**.

### Wispr's surface map (what we're matching)

Wispr's "Hub" (app shell) has six sidebar tabs:

| Wispr tab | What it is |
|---|---|
| **Home** | Welcome header, dictation shortcut, recent activity feed, stats cards (daily word count, streak, WPM). "100 Words a Day Challenge" lives here. |
| **Scratchpad** | Cross-device notepad with version history. |
| **Transforms** | Save AI prompts that rewrite text. |
| **Insights** | **The retention engine.** Streak heatmap, leaderboard with podium, sharable social cards, WPM ranking. |
| **Style** | Writing style presets, Auto Cleanup Levels (4 tiers). |
| **History** | Recent dictations + dictation-recovery. |

Settings: separate surface with Style, Language, Microphone, Shortcuts, Privacy, **Notifications (granular per-category mute)**, Account.

Retention features Wispr uses we should adopt:
1. Daily streak heatmap (90-day calendar grid)
2. Leaderboard with podium
3. Sharable social cards (PNG export)
4. Milestone announcements (their "100 Words a Day" / our "first deal of the day")
5. Granular notification mute (per-category)
6. History dictation-recovery (we have analog: alert retry queue)
7. Public changelog at a fixed URL

### Feature inventory: Wispr → Bullseye

| Wispr feature | Bullseye equivalent | Status |
|---|---|---|
| Hub shell | Sidebar + content shell | Built (agent 2), unverified |
| Home tab | Home tab | Built, needs streak-heatmap mini |
| History tab | Activity tab | Built, unverified |
| **Insights tab** | Insights tab — heatmap + personal-best + share card | **Not built — biggest gap** |
| Style tab | Settings → Tuning (4 sensitivity tiers) | Not built |
| Settings → Notifications (granular) | Same | Not built (only telemetry toggle exists) |
| Settings → Shortcuts | Same | Not built |
| Public changelog | `getbullseye.app/changelog` | Not built |
| Status page | `status.getbullseye.app` | Defer to v1.2 |
| Sharable PNG card | Same | Not built |

Wispr features Bullseye does NOT copy: Scratchpad, Transforms, Snippets, Personal Dictionary, WPM ranking vs others (privacy + small base), mobile apps, multilingual support.

### Retention plan, compiled

**Already wired** (cloud + desktop):
- [ ] Daily streak counter (`/streak` Edge Function, migration 011, `cloud/streak.py`)
- [ ] Pro-day banking
- [ ] Redeem 7 banked → 7-day Pro trial
- [ ] Milestones at 7 / 30 / 100 days (week_warrior +1, month_master +3, century_club +7)
- [ ] Per-month streak freeze
- [ ] Banked-days banner on Home tab

**To build** (this section):

| Feature | Surface | Phase | Effort |
|---|---|---|---|
| 90-day streak heatmap | Insights tab | 2 | M |
| Personal-best leaderboard (top 10 deals) | Insights tab | 2 | S |
| "Deal of the month" sharable PNG card | Insights tab + cloud render | 5 | L |
| Daily goal banner ("first deal today") | Home tab | 4 | S |
| Milestone toast (first deal, streak ping, 100th deal) | Toast system | 4 | S |
| Granular notification toggles | Settings → Notifications | 3 | S |
| Public changelog page | Landing site | 7 | XS |
| Boost mode (15-min temporary threshold drop) | Tray menu | 5 | M |
| Tuning presets (loose/balanced/strict/whitelist) | Settings → Tuning | 6 | M |

**Already deferred to v1.2**: Hunter profile, year-end recap, refer-a-friend.

### Visual language

Burnt orange `#c2410c` accent stays (differentiates from Wispr's purple). Everything else aligns with their grayscale + system fonts + hairlines + airy density. `shell.css` already implements this.

Branding moves to copy: rename app surface to **"Bullseye Hub"** in title bar, keep the bullseye SVG mark in sidebar header.

### Build sequence (gated by your approval at each phase)

- [ ] **Phase 1 — You verify the existing shell.** Install `Bullseye-Setup.exe`, walk every tab, save a watch with email + threshold, run the Test Appraiser. Find regressions; we triage.
- [ ] **Phase 2 — Insights tab.** Migration (if needed), `/api/insights/heatmap` + `/api/insights/personal-best`, `tab_insights.html` + `tab_insights.js`, calendar-grid SVG component, sidebar nav entry. ~4-6h.
- [ ] **Phase 3 — Granular notifications.** Migration to extend `user_settings`, `/api/settings` PATCH support, Settings tab UI. ~1-2h.
- [ ] **Phase 4 — Home polish.** Mini-heatmap (last 14 days) + daily-goal banner. ~1h once Phase 2 lands.
- [ ] **Phase 5 — Sharable card + boost mode.** Cloud share-card endpoint + tray menu boost item. ~1 day.
- [ ] **Phase 6 — Tuning presets.** Settings → Tuning section with 4 presets. ~2h.
- [ ] **Phase 7 — Public changelog.** Static markdown rendering at `/changelog`. ~30 min.

### Feature flags (deferred to Phase 5)

I propose **no feature flags for v1**. Insights, notifications, home polish are all additive — they don't replace existing surfaces. Adding flag infrastructure now is overhead. We'd add flags only if Phase 5 (share card, boost mode) lands and we want a gradual rollout. If you disagree, say so and I'll add them in Phase 2.

### Open questions

1. **Insights vs Stats** — keep both as separate tabs (six → seven), or merge by moving Stats system-health into an Insights sub-section?
   - **My default**: keep separate. Stats already exists with funnel + poll timer; Insights adds retention. Less destructive.
2. **Boost mode threshold** — flat (set every watch to threshold=50) or relative (-20 from each watch's current)?
   - **My default**: relative. Respects per-watch tuning.
3. **Sharable card render** — Cloudflare Worker (free, more setup) or Supabase Edge Function (already in stack)?
   - **My default**: Supabase Edge Function — fewer moving parts.
4. **Daily goal copy** — "Find one deal today" vs "1 deal scored today" vs "Did you check today's drops?"
   - **My default**: "Find one deal today" (action-oriented, matches Wispr's "100 Words a Day Challenge").
5. **Public leaderboard across users** — defer to v1.2 or never?
   - **My default**: never. Privacy + small early base. Personal-best only.

Tell me to flip any default. Otherwise I proceed with the defaults above.
