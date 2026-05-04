# Bullseye

Confidence-scored deal finder for Facebook Marketplace.

This repo is the **productized v1**: hybrid local desktop app + Supabase cloud
backend, sold as a freemium SaaS. The personal tool that this evolved from
lives at `../deal_finder/`.

## Sub-projects

- **`desktop/`** — Python desktop app (PyWebView shell, SQLite, scheduler,
  scraper). Ships as a Windows installer.
- **`cloud/`** — Supabase migrations + Edge Functions. Comp data proxy,
  license/billing, email sending, telemetry.
- **`landing/`** — Static landing page on Cloudflare Pages.

## Status

Phase 0 of `BULLSEYE-BLUEPRINT.md`: scaffolding complete, no real
implementation yet. Each file has a docstring explaining intent + TODO
comments marking where real code goes. Recursive flesh-out happens
phase by phase.

## What you need to drop in

See `.env.example` for the full list. Short version: Supabase project URL +
keys, Stripe keys + price IDs, Resend API key, eBay App ID + Cert ID,
Google OAuth client ID, Sentry DSN.

## Get going

```bash
cd desktop && pip install -r requirements.txt && python -m src.main
```

(Won't actually run yet — most modules raise NotImplementedError.)
