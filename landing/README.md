# Bullseye landing page

Static site, deployed to Cloudflare Pages at `bullseye.app`.

## Pages

- `index.html` — hero + how it works + comparison + FAQ + footer
- `pricing.html` — Free vs Pro cards with Stripe Checkout buttons
- `privacy.html` — privacy policy (Termly-generated, edit to taste)
- `terms.html` — terms of service (Termly-generated)
- `upgrade-success.html` — post-Checkout return URL

## Deploy

1. Push to GitHub
2. Cloudflare Pages → Connect to GitHub repo
3. Build command: (none)
4. Build output: `public`
5. Custom domain: `bullseye.app`

## Things to fill in before launch

- [ ] Demo GIF in `public/assets/demo.gif`
- [ ] Logo SVG in `public/assets/logo.svg`
- [ ] Real privacy policy + terms (currently placeholders)
- [ ] Real Stripe public keys + Supabase URL in pricing.html JS
- [ ] Plausible Analytics script in `<head>` of every page
