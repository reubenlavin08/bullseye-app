# Handoff: Bullseye

## Overview
Bullseye is a Marketplace appraisal tool for resellers. It watches Facebook Marketplace, scores new listings against eBay sold comps from the last 90 days, and alerts the user when a listing is priced below market. It ships as a desktop app (Flask + PyWebView, localhost) and a marketing site.

This handoff covers:
- Marketing landing page
- Daily-digest email template
- Desktop app (Wispr-Flow-Hub-style sidebar layout)
- Interactive deal-score component with breakdown panel
- Logo system (Logo B is canonical)

## About the Design Files
The files in `files/` are **design references created in HTML/JSX** — prototypes showing intended look and behavior, not production code to copy directly. They use React via inline Babel + global window-attached components and a single shared `styles.css` for tokens.

Your task is to **recreate these designs in the target codebase's existing environment**, using its established framework, component library, and patterns. If no environment exists yet, pick the most appropriate stack for the project (Next.js + Tailwind is a good default for the marketing site; Tauri/Electron + React for the desktop app) and implement there. Do not ship the prototype HTML.

## Fidelity
**Hi-fi** for: logo, score component, landing page (`landing-v2.jsx`), daily-digest email, Hub-style desktop app shell, event tail, score breakdown panel. Pixel-perfect — recreate exactly using the tokens below.

**Lo-fi (wireframe)** for: the four hero direction explorations in `hero-variants.jsx`. These show structure only; the chosen direction (default: A · Live feed) is realized hi-fi in `landing-v2.jsx`.

---

## Design Tokens

All tokens live in `:root` in `files/styles.css`. Reproduce them as your framework's design tokens (Tailwind config, CSS variables, Theme provider, etc.).

### Colors
| Token | Hex | Use |
|---|---|---|
| `--bg` | `#fafaf7` | Page background, warm off-white |
| `--bg-elev` | `#ffffff` | Cards, elevated surfaces |
| `--bg-sunk` | `#f3efe7` | Subtle recessed areas, table headers |
| `--fg` | `#1a1614` | Primary text |
| `--muted` | `#6b5d52` | Secondary text |
| `--muted-2` | `#9a8b7e` | Tertiary text, meta |
| `--accent` | `#c2410c` | Primary accent (burnt orange) — CTAs, asking price, brand |
| `--accent-hover` | `#a8380a` | Hover state for accent |
| `--accent-soft` | `#f9ebe1` | Tinted backgrounds, alert highlights |
| `--accent-2` | `#5d7a4f` | Success, "good score", under-market savings |
| `--accent-2-soft` | `#e8eee2` | Tinted "good" backgrounds |
| `--warn` | `#b87f1f` | "OK" score, warnings |
| `--warn-soft` | `#f6ecd6` | Tinted warn backgrounds |
| `--border` | `#ebe2d4` | Default 1px borders |
| `--border-strong` | `#d8cdb9` | Hover/emphasis borders |

### Score-tier colors (paired)
- **Good (≥70)**: text `#3d5435` · border `#b8c8ae` · bg `#eef3e8`
- **OK (50–69)**: text `#8a5d18` · border `#e6cf9b` · bg `#faf2dc`
- **Meh (<50)**: text `#6b5d52` · border `#ebe2d4` · bg `#ffffff`

### Typography
- `--serif`: `Georgia, "Times New Roman", serif` — headings, scores, listing titles, prices when shown as numerals
- `--sans`: `-apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif` — body, UI controls
- `--mono`: `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace` — meta, kickers, receipts, log lines

Heading defaults: `font-weight: 400`, `letter-spacing: -0.01em`. Hero hero-headline goes to `-0.025em`.

Body base: 14px / line-height 1.5. Small meta: 11–12px.

### Radius
- `--radius`: `6px` (buttons, badges, inputs)
- `--radius-lg`: `10px` (cards)

### Shadows
- `--shadow-1`: `0 1px 2px rgba(26,22,20,0.04)` — default cards
- `--shadow-2`: `0 1px 2px rgba(26,22,20,0.04), 0 4px 16px rgba(26,22,20,0.04)` — elevated/floating

### Spacing
8-pt-ish but not strict. Common values used: 4, 6, 8, 10, 12, 14, 16, 18, 22, 24, 28, 32, 40, 56, 72, 80.

### "Kicker" pattern (reused everywhere)
Small monospace eyebrow label above titles:
```css
font-family: mono; font-size: 11px; letter-spacing: 0.06em;
text-transform: uppercase; color: var(--muted);
```

---

## Logo

**Logo B is canonical** — concentric target rings with an arrow embedded, paired with a Georgia wordmark. Source in `files/logo.jsx` (`Logo.B`).

SVG construction (24×24 viewBox shown at varied sizes):
- Outer ring: `circle cx=14 cy=18 r=13` stroke `#1a1614` 1.25px, no fill
- Inner ring: `circle cx=14 cy=18 r=8` stroke `#1a1614` 1.25px, no fill
- Bullseye dot: `circle cx=14 cy=18 r=3.5` fill `#c2410c`
- Arrow shaft: `line 22,10 → 14,18` stroke `#1a1614` 1.5px round
- Fletching: three lines from (22,10) to (26,6), (26,10), (22,6) — stroke 1.25px round

Wordmark: "bullseye" in Georgia, weight 400, letter-spacing -0.01em, font-size = `iconSize × 0.78`. Gap between mark and word: 10px.

App icon variant: dark fill `#1a1614` rounded square, white "b" Georgia 18px centered, orange dot `#c2410c` r=2 at (23,9).

---

## Screens

### 1. Marketing landing page
File: `files/landing-v2.jsx` → `<LandingV2/>`. Width: 1180.

**Sections, in order:**

1. **Top nav** (height ~64px, padding 20×40)
   - Left: Logo B at size 26
   - Right: links "How it works · Pricing · For resellers · Sign in" (13px muted, 28px gap), then primary "Download free" button (small)

2. **Hero** (padding 80×40 top, 70 bottom; 2-col grid 1.05fr / 1fr, 56px gap)
   - Left col:
     - Kicker `● new listing · oakland · 12s ago` (live red-orange dot)
     - H1 60px serif, line-height 1.04, letter-spacing -0.025em, two lines: "Resellers, stop / refreshing Marketplace."
     - Sub 17px muted, max-width 480, line-height 1.6
     - Buttons row: primary lg "Try Pro free for 7 days" + ghost lg "Download for Windows"
     - Caption 12px muted: "$9.99/mo Pro · free tier forever · 14-day money-back · no card required"
   - Right col: live appraisal feed card (`HeroDemo` in source) — 3 listing rows; first row tinted `--accent-soft`; footer with "next poll in 2:14" / "↗ 23 active watches"

3. **Reseller stats band** (padding 28×40, background `--accent-soft`, top+bottom border `#f1d9c5`)
   - 4-col grid; each cell: 28px serif accent number on top, 12px muted label below
   - Values: `< 5 min` / `23` / `$9.99` / `0`

4. **How it works** (padding 72×40)
   - Header row: H2 32px "From listing to alert in three steps." + kicker "how it works"
   - 3-col grid, 28px gap
   - Each step: mono accent "01"/"02"/"03" → H3 22px → 14px muted body line-height 1.6

5. **Free vs Pro pricing** (padding 40×40 80 bottom, top border, bg `--bg-sunk`)
   - Header row: H2 32px "Free or Pro." + kicker "pricing"
   - 2-col grid, 18px gap, max-width 920
   - Each plan card: padding 28; kicker tag; price 44px serif + sub muted; full-width CTA; feature list with `✓` accent-2 check or `—` muted-2 dash for omitted lines
   - Pro card highlighted: shadow-2, "RECOMMENDED" pill (mono uppercase, accent fill, white text, top-right 14/14)
   - Below: small caption "Compare to alternatives: most reseller scouts cost $19–$29/mo and lock the comp data behind a black-box score."

6. **"Built for"** (padding 72×40 90 bottom)
   - H2 "Built for people who already know what to buy."
   - 3-col grid of persona cards: kicker accent + 14px body
   - Personas: Resellers · Vintage hunters · Electronics flippers

7. **Footer** (padding 28×40, top border, bg `--bg-sunk`, 12px muted)
   - Logo B at 18 + "© 2026 Bullseye · Privacy · Terms · Contact"

### 2. Daily digest email
File: `files/email.jsx` → `<EmailDigest/>`. Width: 720.

- Outer "envelope" bg `#e8e1d3` with 28px padding (so the email floats on a tinted surface in mocks)
- Header bar: gmail-style "From / Today" mono 11px muted
- Email body card: bg `--bg`, 1px border, radius 6
  - Header: padding 24×28, bottom border. Logo B 20. Then H1 26px serif "3 new deals — May 4". Then sub "From your 23 active watches · highest score [accent]78"
  - Sections grouped by keyword: kicker title ("Cameras · 2 deals"), then deal cards
  - Each deal card: 88×88 photo placeholder · title (16px serif) + meta · prices line (mono, asking accent / fair muted / under accent-2) · score badge top-right + "open →" link bottom-right
  - Footer: bg `--bg-sunk`, top border, 11px muted: "Manage watches · Switch to instant (Pro) · Unsubscribe"

### 3. Desktop app — Hub-style shell (canonical)
File: `files/hub-app.jsx` → `<HubApp/>`. Width: 1180, height: 800.

**Layout: 232px sidebar + flexible main**

**Left sidebar** (`HubSidebar`):
- Padding 18×12
- Logo B at 22 in top padding 4×8×18
- Primary nav (1px gap between items): Home / Appraisal feed (5) / Watches (23) / Comp library / Alert rules / Insights
- Spacer
- Footer nav (top border, padding-top 12): Settings / Help
- Pro trial card (margin-top 12, padding 12, radius 8, bg `--accent-soft`, border `#f1d9c5`): serif 14 "Pro trial" → muted 11 "5 days left · $9.99/mo" → full-width primary "Upgrade"

Nav item (`HubNavItem`):
- Padding 8×10, radius 6, gap 10
- Active: bg `--bg-elev`, shadow-1, fg color, weight 500, icon in accent
- Inactive: transparent bg, muted color, icon in `--muted-2`
- 16px icon column, label flex 1, optional count pill on right (mono 10px muted, bg `--bg-sunk`, radius 3, padding 1×5)

**Top bar** (`HubTopbar`, 52px tall):
- Bottom border
- Padding 0×28, gap 14
- Global input (max-width 560) "Test a Marketplace URL or freetext (e.g. canon ae-1 $380)"
- Spacer
- Live chip: dot-live + "next poll · 2:14"
- Small button "+ New watch"
- 28px round avatar, accent fill, white serif "j"

**Home view** (`HubHome`, padding 36×40 60 bottom, max-width 1000):
- Kicker "tuesday · may 5"
- H1 38px serif "Good morning, Jordan." (greeting varies by hour)
- Sub 15px muted: "Bullseye scanned 1,487 listings overnight. Five scored above your threshold of 70."
- "top deal · just now" kicker → `<ScoreBigInteractive/>`
- 2-col 18px-gap grid:
  - "overnight summary" card with KV rows (listings scanned / appraised / alerts sent (accent) / estimated margin found (accent-2))
  - "watches working" card with HubMini rows: query (mono fg), alerts (mono accent if >0), top score badge
- "today's appraisals" feed: kicker + "view all 23 →" accent link, then card with 4 rows. Each row: 48px photo + title/meta + score + "Open" button

### 4. Score component (deal-score with breakdown)
File: `files/score-interactive.jsx` → `<ScoreBigInteractive/>`.

**Closed state** — single card padding 24:
- Left column (min-width 110, padding-right 22, right border):
  - Kicker "score"
  - 60px serif numeral in tier color (good/ok/meh per `--accent-2` / `--warn` / `--muted`)
  - Below: "of 100" mono 11 muted + "why?" pill (mono 10 accent, padding 1×5, radius 3, bg `--accent-soft`, border `#f1d9c5`). Pill toggles to "hide" when open.
- Right column:
  - Title 18px serif
  - Meta 12px muted: "Oakland · 4.2 mi · 12 min ago"
  - Stats row 32px gap: asking (accent, $) · fair (fg, ±confidence) · vs. median (accent-2 negative)

**Open state** — animates in (`bs-reveal`, 180ms ease):
- Top border separator, 22 padding-top
- Tabs: "How we got 78" / "23 comps" — 13px, active bottom-border 2px accent
- **Math tab**:
  - DistRail: distribution bar 250→750 range. IQR band tinted `--accent-2-soft`, median tick accent-2 with label, asking pin accent with `$380` label below
  - Reason rows: tag pill (`+18` good / `−6` bad / `±0` muted) + label 13 weight 500 + 12 muted body. Rows separated by 1px borders.
- **Comps tab**:
  - 12px muted intro paragraph
  - Each row (anchor): title + price (mono fg) on top, then full-width 4px height bar tinted `--accent-2` proportional to price, "Nd ago" mono right-aligned
  - 8 sample rows shown

### 5. Event tail (cleaned)
File: `files/hub-app.jsx` → `<EventTailClean/>`.

Card with header "event tail · 2s refresh" + streaming chip. Each row:
- Grid: 12px dot · 70px time · 70px kind · 1fr message · auto
- Status dot color by status: ok=`#a3c08a`, warn=`--warn`, hit=`--accent`
- Time: mono 11 muted
- Kind: mono 10 uppercase 500, color by kind (poll=muted-2, score=fg, alert=accent, comps=muted-2, verify=accent-2)
- Message: 13 fg with " · meta" portion in 12 muted
- Hits get full-row `--accent-soft` background

### 6. Hero variants (lo-fi reference only)
File: `files/hero-variants.jsx`. Four wireframe directions; A is canonical and is the one realized hi-fi in landing-v2. Keep the others around as design history.

---

## Components (atomic)

All in `files/styles.css`:

- `.btn` — 34h, 13px sans 500, 6 radius, 1px border `--border`, bg white. Hover: border `--border-strong`, bg pure white.
- `.btn-primary` — bg/border `--accent`, color white. Hover: `--accent-hover`.
- `.btn-ghost` — transparent. Hover: `rgba(26,22,20,0.04)` bg.
- `.btn-sm` — 28h, 12px, 10 padding-x.
- `.btn-lg` — 42h, 14px, 18 padding-x.
- `.input` — 34h, 13 sans, 12 padding-x, 6 radius, 1px border. Focus: border `--accent` + 3px `--accent-soft` ring.
- `.score` — inline-flex, baseline align. Serif tabular-nums numeral 16px, mono pct 10px 0.7 opacity. Padding 4×10, radius 6, 1px border. Three tier classes for color.
- `.score-clickable` — adds cursor pointer + hover shadow-1 + translateY(-1px).
- `.chip` — 22h pill, 11 muted, 6 gap, 1px border, 8 padding-x.
- `.dot` — 6×6 round. `.dot-live` accent-2 with 3px halo `rgba(93,122,79,.18)`.
- `.kicker` — see Typography above.
- `.card` — bg `--bg-elev`, 1px border, 10 radius, shadow-1.
- `.ph` — placeholder image, 135deg repeating-stripes `#ece4d4`/`#e3d9c5` 8px each, mono 11 muted "photo" label.

---

## Interactions & Behavior

- **Score numeral / "why?" pill** — single click toggles the breakdown panel. State is per-instance React `useState`. Animation: 180ms ease, opacity 0→1 + translateY -4px → 0.
- **Score tabs** — click switches between "math" and "comps". No URL state needed.
- **Hub sidebar** — clicking a nav item swaps the main content. Active item gets surface bg + shadow.
- **Hover transitions** — buttons 120ms; nav items 120ms; cards 150ms shadow.
- **Loading** — appraiser shows a 1.1s shimmer skeleton (see `ResultSkeleton` in original v1 `appraiser.jsx`). Animation: `bs-pulse` 1.4s ease-in-out infinite, opacity .55↔.25.
- **Drawer** — score breakdown was originally a slide-in drawer in v1; **v2 inlines it** into the score card. Use the inline approach.
- **Live polling chip** — animated dot `--accent-2` with 3px halo. Static in mocks; in app, tick down "next poll · M:SS".

---

## State Management

For the desktop app:
- `activeNav` — current sidebar selection (string id)
- `nextPollSeconds` — countdown timer, ticks every 1s, resets to poll interval on poll
- `watches[]` — user's saved query objects { id, query, polls, alerts, topScore, threshold, maxDistance, ceiling }
- `appraisals[]` — listing objects { id, title, asking, fair, confidence, score, location, distance, listedAt, photo, reasons[], comps[] }
- `events[]` — capped log buffer of `{t, kind, target, meta, status}` objects, push on every poll/score/alert/verify
- `proTrialDaysLeft` — int from license server
- `apiCounters` — { miniMaxCalls, miniMaxLimit, miniMaxSpend, ebayCalls, ebayLimit, ebayCacheHitRate }

For the score component:
- `open` (bool) — breakdown visibility
- `tab` ('math' | 'comps') — active tab in breakdown

For the landing site: stateless except for the live-feed demo on the right of the hero (rotates rows every ~6s in a fuller implementation).

---

## Assets

- All visuals are SVG or pure CSS — no raster assets shipped.
- Listing photos are `.ph` placeholders; real implementation should use the listing's first Marketplace image.
- Logo SVG is inline in `logo.jsx`.

---

## Files
- `files/styles.css` — design tokens + atomic components
- `files/logo.jsx` — Logo A/B/C/D + recommended sketch sheet (Logo.B is canonical)
- `files/score-interactive.jsx` — ScoreBigInteractive with inline breakdown (math + comps tabs)
- `files/hub-app.jsx` — Wispr-Hub-style desktop shell + cleaned EventTail
- `files/landing-v2.jsx` — marketing landing page (reseller positioning, Free vs Pro)
- `files/email.jsx` — daily digest email template
- `files/hero-variants.jsx` — lo-fi hero wireframe directions (A is canonical)
- `files/Bullseye Design v2.html` — wraps all of the above on a single design canvas; open in a browser to see all artboards together
