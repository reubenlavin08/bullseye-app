# Cloud — Supabase backend

## Layout

- **`supabase/migrations/`** — numbered SQL files. Apply via Supabase
  CLI (`supabase db push`) or paste into the SQL editor.
- **`supabase/functions/`** — Edge Functions in TypeScript. Deploy via
  `supabase functions deploy <name>`.

## Initial setup

```bash
# Install Supabase CLI
npm install -g supabase

# Link this directory to your project
supabase login
supabase link --project-ref YOUR_PROJECT_REF

# Apply migrations
supabase db push

# Set function secrets (one-time)
supabase secrets set EBAY_APP_ID=...
supabase secrets set EBAY_CERT_ID=...
supabase secrets set EBAY_GLOBAL_ID=EBAY-ENCA
supabase secrets set RESEND_API_KEY=...
supabase secrets set RESEND_FROM_ADDRESS=alerts@mail.getbullseye.app
supabase secrets set STRIPE_SECRET_KEY=...
supabase secrets set STRIPE_WEBHOOK_SECRET=...
supabase secrets set STRIPE_PRICE_MONTHLY=...
supabase secrets set STRIPE_PRICE_YEARLY=...

# Deploy all functions
for f in comps license alerts-send checkout-create stripe-webhook telemetry account-delete account-export queue-worker; do
  supabase functions deploy $f
done
```

## Functions

| Function | Purpose | Auth |
|---|---|---|
| `/comps` | eBay proxy + shared cache. Replaces direct eBay calls from desktop. | JWT |
| `/license` | Returns user's tier + limits + min_supported_version (kill switch). | JWT |
| `/alerts-send` | Sends digest/instant emails via Resend, applies tier-aware rules. | JWT |
| `/checkout-create` | Creates Stripe Checkout session (with optional 7-day trial). | JWT |
| `/stripe-webhook` | Receives Stripe events, syncs license tier + period_end. | Stripe sig |
| `/telemetry` | Bulk-inserts client telemetry events. | JWT or anon |
| `/account-delete` | GDPR/PIPEDA cascade-delete the user's data. | JWT |
| `/account-export` | GDPR/PIPEDA data export — returns user's data as JSON. | JWT |
| `/queue-worker` | pg_cron-triggered hourly. Drains email_queue. | service role |
