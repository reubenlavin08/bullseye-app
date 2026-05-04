# Bullseye account setup guide

Step-by-step walkthrough of every external account the project depends
on, what each setting means, and why we need it. Order is roughly
"easiest first" — feel free to skip ahead and come back.

---

## Why we need any of this

The desktop app talks to a **cloud backend** (Supabase) for things that
shouldn't live on the user's machine: payment records, the shared eBay
comp cache, and email sending. The cloud backend talks to **paid third-
party services** (Stripe for billing, Resend for email, eBay for comps,
Sentry for crash reports) that each need an account + API keys.

You don't need all of these on day one. The order below is roughly
the order they're needed for development:

1. **Supabase** — needed before any cloud function works
2. **Stripe** — needed before billing works
3. **Resend** — needed before email sending works (also requires domain)
4. **Google OAuth** — needed before login works
5. **Sentry** — needed before crash reports work (low priority)
6. **Cloudflare** — needed before the landing page is on a real domain
7. **Termly** — generates legal docs (needed before launch)
8. **Plausible** — analytics (needed before launch)
9. **Inno Setup** — local tool to build Windows installers

---

## 1. Supabase (do this first)

### What it is
A managed Postgres database + auth provider + serverless functions
runtime + storage, all in one. Free tier is generous: 500MB database,
2GB bandwidth, 50k monthly active users. We use:
- **Postgres** for the cloud-side data (licenses, comp cache, telemetry)
- **Auth** as a wrapper around Google OAuth (so we don't have to
  implement OAuth ourselves)
- **Edge Functions** as the serverless backend the desktop app calls
- **pg_cron** to schedule the email-queue worker

### Steps

1. Go to **supabase.com**, click "Start your project", sign in with
   GitHub (the same account as `reubenlavin08`).

2. Click **"New project"**.
   - **Name**: `bullseye-prod` (use this for the real one; create a
     separate `bullseye-dev` if you want a sandbox)
   - **Database password**: click "Generate a strong password",
     copy it to your password manager. You'll never type it again
     unless you connect via SQL clients.
   - **Region**: pick the one closest to your users. For Canada,
     `us-west-1` (Oregon) is closest geographically. For paying
     users in CA + US, `us-east-1` (Virginia) is also fine.
   - **Pricing plan**: Free.

   Wait ~2 minutes for the project to provision.

3. Once it's up, you're on the project dashboard. Three things to
   grab and save:
   - **Settings → API → Project URL** — looks like
     `https://abcdefghijkl.supabase.co`. This goes in `.env` as
     `SUPABASE_URL` and gets baked into the desktop app.
   - **Settings → API → Project API keys → anon public** — a long
     `eyJ...` JWT. Safe to ship in the desktop binary because RLS
     (row-level security) prevents it from reading other users' data.
     Goes in `.env` as `SUPABASE_ANON_KEY`.
   - **Settings → API → Project API keys → service_role** — also
     `eyJ...`. **NEVER ship this**. Bypasses RLS. Goes in Supabase's
     own function-secret store via `supabase secrets set`. We'll need
     it for Stripe webhook handling and pg_cron internal calls.

4. Install the **Supabase CLI** (one-time, on your dev machine):
   ```
   npm install -g supabase
   supabase login        # opens browser for OAuth, paste the token back
   ```

5. Link the local repo to the project:
   ```
   cd "C:/Users/User/OneDrive/Desktop/Claude Project/bullseye/cloud"
   supabase link --project-ref <your-project-ref>
   # (NOT bullseye/cloud/supabase — one level too deep. Supabase CLI
   #  expects to find a `supabase/` subdirectory in its workdir.)
   ```
   The project-ref is the `abcdefghijkl` part of your Supabase URL.

6. Apply the migrations I've already written:
   ```
   supabase db push
   ```
   This runs `migrations/001_licenses.sql` through `008_pg_cron.sql`.
   Watch for errors; if any migration fails, fix it and re-run.

7. Set the function secrets (these are env vars available inside Edge
   Functions, NOT bundled into the desktop app):
   ```
   supabase secrets set MIN_SUPPORTED_VERSION=0.1.0        (the kill switch
                                                            — increment when
                                                            we ship a
                                                            breaking change)
   ```

   **Note**: do NOT try to set `SUPABASE_SERVICE_ROLE_KEY` (or any
   other `SUPABASE_*` name) yourself — Supabase auto-injects these
   into every Edge Function at runtime. The CLI rejects manually-set
   `SUPABASE_*` names with "Env name cannot start with SUPABASE_".

   We'll add eBay/Resend/Stripe secrets in their respective sections.

### Why we did each thing
- **Free tier**: zero cost until we have real users. At 1000 paying
  users we'll outgrow it (~$25/month for the Pro tier), but that's a
  good problem.
- **Anon key bundled in app**: RLS makes it safe. Without RLS this
  would be a security hole.
- **Service role key in function secrets only**: this key bypasses
  RLS and could read every user's data. Treat like a password.
- **pg_cron**: comes built-in with Supabase, no extra config. Lets us
  schedule the email-queue drain without running our own worker host.

---

## 2. Google OAuth (needed for Supabase Auth)

### What it is
Lets users "Sign in with Google" instead of creating yet another
password. Supabase Auth wraps the OAuth dance for us; we just need to
hand it credentials from a Google Cloud project.

### Steps

1. Go to **console.cloud.google.com**, sign in with the Google account
   you want to OWN the OAuth client (this account name appears nowhere
   user-facing — recommend using a dedicated `bullseye.dev@gmail.com`
   or similar, but your personal works fine for v1).

2. **Create a new project** named "Bullseye" (top-left dropdown →
   "New Project"). Wait 30s for provisioning.

3. **APIs & Services → OAuth consent screen**:
   - **User Type**: External (we'll have public users)
   - **App name**: Bullseye
   - **User support email**: `hello@bullseye.app` (or your personal
     for now)
   - **App logo**: optional, can add later
   - **Authorized domains**: `bullseye.app` (will warn if not yet
     verified — that's fine, we add it once domain DNS is live)
   - **Developer contact email**: your personal Gmail

   **Scopes**: just `email` and `profile`. Anything more triggers
   Google's lengthy verification process.

   **Test users**: add your own email so you can test before publishing.

   Save and continue. The status will say "Testing" — that's fine for
   development. Switching to "In production" requires the verification
   step, which for `email`+`profile` scope is instant.

4. **APIs & Services → Credentials → Create Credentials → OAuth client
   ID**:
   - **Application type**: Web application
   - **Name**: "Bullseye Supabase"
   - **Authorized redirect URIs**: add BOTH of these:
     - `https://YOUR_PROJECT.supabase.co/auth/v1/callback` (replace
       YOUR_PROJECT with your actual ref from §1 step 3)
     - `http://localhost:53682/auth/callback` (this is the desktop
       app's local OAuth callback)
   - Save. Copy the **Client ID** and **Client Secret**.

5. Back in Supabase: **Authentication → Providers → Google**:
   - Enable the toggle
   - Paste the Client ID and Client Secret
   - Save

6. (Optional) **Authentication → URL Configuration**:
   - Add `http://localhost:53682/auth/callback` to "Redirect URLs"
     allowlist. This is what the desktop app uses.

### Why we did each thing
- **External user type**: required for any non-Workspace users
- **email + profile scopes only**: anything else (Drive, Calendar,
  Gmail) requires a security audit that takes weeks
- **Two redirect URIs**: one for the cloud OAuth callback (Supabase's
  own), one for the desktop app's local listener
- **Test users + "Testing" status**: lets you sign in immediately;
  flip to production right before launch (instant for email/profile
  scopes)

---

## 3. Stripe (needed for billing)

### What it is
Payment processor. Charges users' cards, handles subscriptions,
emails receipts, deals with chargebacks. We use **Stripe Checkout**
(hosted page — Stripe handles the card form, PCI compliance, 3D
Secure) and **Stripe Billing** (recurring subscriptions).

### Steps

1. Go to **stripe.com**, click "Start now". Sign up as an **individual**
   (not a business — Canadian individuals can take payments without
   incorporating).

2. Provide the required info:
   - **Country**: Canada
   - **Legal name**: your real name
   - **Address**: your address (will appear on invoices — use a
     P.O. box if privacy matters)
   - **Date of birth**, **SIN** (last 4 digits), **bank account**
     for payouts (any Canadian bank)
   - **Phone number**

   Stripe runs identity verification — usually instant for Canadians.
   Sometimes takes a day. Until verified, you can use **test mode**
   for everything.

3. **Products → Add product**:
   - **Name**: Bullseye Pro
   - **Description**: "Unlimited watches, 5-min polling, instant
     alerts, full score breakdown"

   Add **two prices** to that product:
   - Price 1: **$9.99 USD** / month / Recurring. Save. Note the
     **Price ID** (starts with `price_...`) — goes in `.env` as
     `STRIPE_PRICE_MONTHLY`.
   - Price 2: **$99 USD** / year / Recurring. Save. Note the
     **Price ID** — goes in `.env` as `STRIPE_PRICE_YEARLY`.

4. **Settings → Customer portal → Activate**:
   - **Enable**: cancellation, subscription update, invoice history
   - **Cancellation reason**: optional checkbox-list (lets us learn
     why people cancel)
   - **Default return URL**: `https://bullseye.app/billing-return`

   The customer portal is what users hit when they click "Manage
   subscription" in the app — Stripe hosts it, we just link to it.

5. **Developers → API keys**:
   - **Secret key** — `sk_test_...` (test mode) or `sk_live_...`
     (live mode). Goes in Supabase function secrets:
     ```
     supabase secrets set STRIPE_SECRET_KEY=sk_test_...
     ```
   - **Publishable key** — `pk_...`. We don't strictly need this for
     v1 (Checkout is server-rendered) but copy it anyway.

6. **Developers → Webhooks → Add endpoint**:
   - **Endpoint URL**:
     `https://YOUR_PROJECT.supabase.co/functions/v1/stripe-webhook`
   - **Events to send**: select these five
     - `checkout.session.completed`
     - `customer.subscription.created`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_failed`
   - Save. Copy the **Signing secret** (`whsec_...`) — goes in
     Supabase function secrets:
     ```
     supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_...
     supabase secrets set STRIPE_PRICE_MONTHLY=price_...
     supabase secrets set STRIPE_PRICE_YEARLY=price_...
     ```

7. While developing, stay in **test mode**. Test cards: `4242 4242
   4242 4242` (success), `4000 0000 0000 0002` (decline). Any future
   expiry, any CVC. Stripe has a full list at
   `stripe.com/docs/testing`.

### Why we did each thing
- **Individual account, not business**: Canada lets you take payments
  as a sole proprietor without registering. Saves you weeks.
- **Two prices on one product**: lets the user pick monthly vs yearly
  on a single Checkout page. Same product = same feature set.
- **Customer portal**: required for $9.99 SaaS. Without it users will
  email you to cancel and you'll have to do it manually in Stripe.
- **Webhook signing secret**: every webhook event from Stripe is
  signed. Without verification, anyone could POST a fake "subscription
  created" and become a paid user. The signing secret prevents that.
- **Test mode**: zero risk of accidentally charging your own card
  during dev.

---

## 4. Resend (needed for email)

### What it is
Email-sending API. Free tier = 3000 emails/month + 100/day. Paid =
$20/month for 50k emails. We pay nothing until we have ~200 paying users.

### Steps (do this AFTER you have a domain)

1. Go to **resend.com**, sign up with the account you want to own
   email sending (your personal Gmail is fine).

2. **Domains → Add Domain**:
   - **Domain**: `bullseye.app` (or whatever you registered)
   - **Region**: pick same region as Supabase

3. Resend gives you 4 DNS records to add. Each one is
   `Type / Name / Value`. Add them via Cloudflare DNS (see §6
   Cloudflare):
   - **MX record** for return-path bounces
   - **TXT (SPF)** — says "Resend is allowed to send for this domain"
   - **TXT (DKIM)** — cryptographic signature so receiving servers
     can verify the email is really from us
   - **TXT (DMARC)** — policy that says "if neither SPF nor DKIM
     pass, reject the email"

   Add all four in Cloudflare, wait 5-30 min for propagation, then
   click "Verify" in Resend. Once green, you're ready to send.

4. **API Keys → Create API Key**:
   - **Name**: `bullseye-prod-send`
   - **Permission**: "Sending access"
   - **Domain**: bullseye.app

   Copy the key (starts with `re_...`). Goes in Supabase function
   secrets:
   ```
   supabase secrets set RESEND_API_KEY=re_...
   supabase secrets set RESEND_FROM_ADDRESS=alerts@bullseye.app
   supabase secrets set RESEND_REPLY_TO=hello@bullseye.app
   ```

5. **Recommended: warm up the domain before launch.** A new domain
   sending 100s of emails immediately gets flagged as spam. Send 5-10
   real emails to friends/family for two weeks before launch — that
   builds reputation with Gmail/Outlook spam filters.

### Why we did each thing
- **SPF/DKIM/DMARC**: without all three, your emails go to spam.
  This is the email industry standard, not Resend-specific.
- **Sending-only API key**: limits the blast radius if the desktop
  app's bundle ever leaks. The key can send but can't read other
  emails or modify settings.
- **Domain warmup**: the difference between 95% inbox delivery and
  20%. Worth the effort.

---

## 5. Sentry (low priority, do before launch)

### What it is
Crash reporter. When the desktop app throws an unhandled exception,
the Sentry SDK sends a stack trace + context to Sentry's servers, so
you find out about bugs without users having to email you. Free tier:
5k events/month.

### Steps

1. Go to **sentry.io**, sign up.
2. Create an organization "Bullseye".
3. Create a **project**:
   - **Platform**: Python
   - **Project name**: `bullseye-desktop`
4. Sentry shows you a DSN (looks like
   `https://abc123@o12345.ingest.sentry.io/67890`). Copy it.
5. Goes in `.env` as `SENTRY_DSN`. Gets baked into the desktop bundle.

### Why we did this
- Without it: you find out about bugs from angry user emails (best
  case) or one-star reviews (worst case)
- With it: you see "23 users hit a NullPointerException in
  scheduler/jobs.py:142" the morning after release

---

## 6. Cloudflare (needed for domain + email + landing page)

### What it is
Cloudflare does three things for us:
- **DNS hosting** (free, faster + better than Namecheap's)
- **Email Routing** (free — `hello@bullseye.app` → your Gmail)
- **Pages** (free static-site hosting — like GitHub Pages but
  faster and with a real custom domain)

### Steps

#### Move DNS to Cloudflare (after domain registration)

1. Sign up at **cloudflare.com**.
2. **Add a Site** → enter `bullseye.app`.
3. Free plan, click through.
4. Cloudflare scans your existing DNS records (mostly empty for a
   fresh domain) and lists them.
5. Cloudflare gives you 2 nameservers like `kate.ns.cloudflare.com`,
   `bob.ns.cloudflare.com`.
6. Go to **Namecheap → Domain List → Manage → Nameservers → Custom
   DNS**, paste the two Cloudflare nameservers. Save.
7. Wait 5 minutes to 48 hours for propagation. Cloudflare will email
   when active.

#### Email Routing

1. In Cloudflare dashboard for `bullseye.app` → **Email → Email
   Routing → Get started**.
2. Cloudflare adds the necessary MX/TXT records automatically.
3. **Routes** tab → "Create address":
   - **Custom address**: `hello`
   - **Action**: Send to existing address
   - **Destination**: your personal Gmail (verify via the email
     they send you)
4. Add a catch-all rule (any `*@bullseye.app` → your Gmail) so you
   don't lose mail to typos.

#### Pages (landing page hosting)

1. **Workers & Pages → Create application → Pages → Connect to Git**.
2. Authorize Cloudflare to read your GitHub.
3. Pick the `bullseye-app` repo.
4. **Project name**: `bullseye-landing`
5. **Production branch**: `main`
6. **Build command**: leave empty (static HTML, no build step)
7. **Build output directory**: `landing/public`
8. Save and deploy.
9. Cloudflare assigns a URL like `bullseye-landing.pages.dev`.
10. **Custom domains** → "Set up a custom domain" → `bullseye.app`.
    Cloudflare sets up the CNAME automatically (since DNS is also on
    Cloudflare).
11. Add `www.bullseye.app` redirect to the apex too.

### Why we did each thing
- **DNS at Cloudflare**: free, fast, lets us add records via API
  later if needed (Resend's automation), and Cloudflare's caching
  CDN comes free
- **Email Routing**: zero-cost forwarding, no need for Google
  Workspace ($6/user/month)
- **Pages**: zero-cost static hosting, automatic HTTPS, automatic
  deploys on git push

---

## 7. Termly (legal docs, before launch)

### What it is
Web tool that asks you ~30 questions about your app, then generates
a privacy policy + terms of service + cookie policy. Free tier
includes the basic policies.

### Steps

1. Go to **termly.io/signup-free**, sign up.
2. **Create policy → Privacy Policy**.
3. Answer the questions. Things to highlight:
   - **Country**: Canada
   - **Province**: British Columbia (or wherever you live)
   - **Data collected**: email, watch list, telemetry events,
     payment info (via Stripe — note that Stripe holds the card,
     not us)
   - **Third parties**: list Stripe, Resend, Supabase, Sentry,
     Google (OAuth), eBay, Facebook
   - **Cookies**: minimal — just session cookies for auth
   - **Children's data**: no, restrict to 13+
4. Generate. Termly gives you HTML + a hosted link.
5. Paste the HTML into `landing/public/privacy.html` (replacing the
   placeholder). Use the hosted link if you'd rather not maintain it.
6. Repeat for **Terms of Service**:
   - Refund policy: 14-day money-back after Pro trial
   - Acceptable use: no scraping our cache, no resale, etc.
   - Liability: standard "as-is, no warranty"
   - Governing law: British Columbia, Canada

### Why we did this
- **Required by Stripe**: their terms say you must have a privacy
  policy + ToS posted before they let you process live payments
- **Required by GDPR/PIPEDA/CCPA**: privacy laws apply to anyone
  collecting personal data from EU/Canadian/Californian users
- **Termly vs writing one yourself**: Termly is $0 vs $500-2000
  for a lawyer-drafted custom one. Lawyer-quality is worth it once
  you're at $50k MRR; until then, Termly is fine.

---

## 8. Plausible Analytics (low priority, before launch)

### What it is
Privacy-respecting analytics — like Google Analytics but no cookies,
no personal data, GDPR-compliant by default. $9/month for the
Starter plan (up to 10k pageviews).

### Steps

1. **plausible.io/register** → 30-day free trial.
2. Add site `bullseye.app`.
3. Plausible gives you a `<script>` tag. Paste it into the `<head>`
   of every HTML file in `landing/public/`.
4. After launch, watch the dashboard. You're looking for:
   - **Acquisition funnel**: visit → click "Download" → install
   - **Pricing-page conversion**: of users who land on `/pricing`,
     how many click "Start trial"?

### Why we did this
- Without analytics, you have no idea what's working. Marketing
  spend is guesswork.
- Plausible vs Google Analytics: Plausible is privacy-friendly (no
  cookie banner needed), simpler dashboard, better UX. Same data
  for our purposes.

---

## 9. Inno Setup (local dev tool)

### What it is
Free Windows installer compiler. Wraps your `.exe` in a polished
installer with shortcuts, uninstaller, etc. PyInstaller produces a
single `.exe`; Inno wraps it in a setup wizard.

### Steps

1. Download from **jrsoftware.org/isdl.php**.
2. Run the installer with default options. It installs to
   `C:\Program Files (x86)\Inno Setup 6\`.
3. The build script `desktop/build/build_windows.bat` already calls
   it from that path. Nothing else to configure.

### Why we did this
- Distribution channel for Windows is `.exe` installer or `.msi`.
  Inno produces `.exe` installers. Free, well-maintained, used by
  most open-source Windows apps.
- Alternative `.msi` builders (WiX) exist but have a much steeper
  learning curve. Inno is fine.

---

## Order of operations summary

If you want to set everything up in one sitting, here's the optimal
order (minimizes blocked-on-DNS time):

1. **Stripe** (~30 min) — no domain dependency
2. **Sentry** (~5 min) — no dependencies
3. **Inno Setup** install (~3 min) — local only
4. Wait for **domain** to arrive (1-2 days)
5. **Cloudflare DNS migration** (~10 min, then 5min-48h propagation wait)
6. **Cloudflare Email Routing** (~5 min, parallel with DNS)
7. **Resend domain + DNS records** (~10 min, then verify wait)
8. **Cloudflare Pages connection** (~5 min)
9. **Supabase** (~30 min)
10. **Google OAuth** (~15 min)
11. **Termly** policies (~30 min)
12. **Plausible** (~5 min)

Total: ~3 hours of clicking, plus DNS propagation waits you can do
other things during.

---

## What "I'll handle" means

For each setting above, the only things YOU do are:
- Sign up / verify identity
- Click around UIs to create projects/products/keys
- Paste DNS records into Cloudflare

Everything that goes in **code** (migrations, function deploys,
.env file population) — I'll handle once you give me the keys. As
keys arrive, paste them into `.env` (which is gitignored) and tell
me. I'll wire them in from there.
