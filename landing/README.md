# Bullseye landing page

Static site, deployed to Cloudflare Pages at `bullseye.app`. Pure HTML
+ CSS, no build step. The only JS is on `pricing.html` and
`upgrade-success.html` (supabase-js loaded from a CDN, used to do
Google OAuth and call the `checkout-create` and `license` edge
functions).

## Pages

- `index.html` — hero, how it works, comparison table, "built for"
  personas, FAQ, footer
- `pricing.html` — Free vs Pro cards, Stripe Checkout via
  `checkout-create` edge function, billing FAQ
- `privacy.html` — placeholder copy until the Termly-generated
  policy is dropped in
- `terms.html` — same
- `upgrade-success.html` — landing after Stripe Checkout; polls
  `/license` for up to 30 seconds to confirm the webhook landed
- `_redirects` — Cloudflare Pages redirect rules
- `assets/styles.css` — single stylesheet, hand-rolled
- `assets/logo.svg` — wordmark icon (target rings), single-color via
  `currentColor`, used as favicon and in the header

## Deploy to Cloudflare Pages

One-time setup:

1. Go to **Cloudflare → Workers & Pages → Create application →
   Pages → Connect to Git** and authorize the repo.
2. Pick the `bullseye` repo.
3. **Project name**: `bullseye-landing`
4. **Production branch**: `main`
5. **Build command**: leave empty (no build step)
6. **Build output directory**: `landing/public`
7. Save and deploy. Cloudflare assigns a `*.pages.dev` URL.
8. **Custom domains** → add `bullseye.app`. Cloudflare wires the CNAME
   automatically because DNS is also on Cloudflare.
9. Add `www.bullseye.app` as a redirect to the apex.
10. **Settings → Builds & deployments → Preview deployments** →
    enable "All non-Production branches and pull requests" so PRs get
    preview URLs.

After that, every push to `main` redeploys; every PR gets a preview
URL automatically.

## TODOs to finish before public launch

- [ ] Drop in the real Termly-generated `privacy.html` and `terms.html`
  (current ones are plain-English placeholders that will be replaced
  wholesale; see the banner inside each page)
- [ ] Add the Plausible Analytics `<script>` tag to the `<head>` of
  `index.html`, `pricing.html`, and `upgrade-success.html`. The exact
  snippet (search for `TODO: Plausible`) is:
  ```html
  <script defer data-domain="bullseye.app" src="https://plausible.io/js/script.js"></script>
  ```
- [ ] Replace the Stripe Checkout success URL placeholder portal link
  in `upgrade-success.html` with the real customer-portal login link
  once the Stripe customer portal is configured (Settings → Customer
  portal → Login link)
- [ ] Drop a 30-second demo GIF at `assets/demo.gif` and uncomment
  the `<img>` tag in `index.html` (search for `TODO: 30-second demo`)
- [ ] Once the desktop app registers the `bullseye://` protocol
  handler, the deep link on `upgrade-success.html` will start working
  — no code change needed here

## Local preview

Any static-file server works. From the repo root:

```bash
cd landing/public && python -m http.server 8000
# open http://localhost:8000
```

Note that `_redirects` is a Cloudflare-Pages-only feature; it has no
effect when serving locally.

## Constraints we deliberately keep

- No CSS framework (Tailwind, Bootstrap, etc.) — handcoded CSS only,
  one file
- No build step
- No server-side templating (pure HTML)
- Total uncompressed weight under 200 KB without images
- Third-party scripts only via CDN tags, never bundled
