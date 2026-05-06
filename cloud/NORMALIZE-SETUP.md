# LLM normalize layer — Supabase setup runbook

You only have to do this once. Three steps: secret, migration, deploy.

## 1. Set the MiniMax API key as a Supabase secret

The desktop never sees this key — it lives only in the edge-function
runtime. Set it once via the Supabase CLI:

```bash
cd "C:\Users\User\OneDrive\Desktop\Claude Project\bullseye\cloud"
supabase secrets set MINIMAX_API_KEY="<your sk-api-... key>"
```

Optional overrides (defaults are sane):

```bash
# Lightest current MiniMax chat model. Stick with the default.
supabase secrets set MINIMAX_MODEL="MiniMax-Text-01"

# International endpoint (default). For China region:
#   https://api.minimax.chat/v1
supabase secrets set MINIMAX_BASE_URL="https://api.minimax.io/v1"
```

Verify:
```bash
supabase secrets list
```

You should see `MINIMAX_API_KEY` in the list.

## 2. Apply the migration

Creates the `normalized_listings` cache table.

```bash
cd "C:\Users\User\OneDrive\Desktop\Claude Project\bullseye\cloud"
supabase db push
```

If `db push` complains about already-applied migrations, run only the
new one explicitly:

```bash
supabase db query --file supabase/migrations/013_normalized_listings.sql
```

## 3. Deploy the edge function

```bash
cd "C:\Users\User\OneDrive\Desktop\Claude Project\bullseye\cloud"
supabase functions deploy appraise-normalize
```

Output should end with:
```
Deployed Functions on project ...: appraise-normalize
```

## 4. Smoke test

From any machine with the Supabase CLI logged in (or an authed client):

```bash
curl -X POST "https://qfkzhyxmohytnzskcmdv.supabase.co/functions/v1/appraise-normalize" \
  -H "Authorization: Bearer <user-jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "listing_url": "https://www.facebook.com/marketplace/item/test-123",
        "title": "iPhone 12 - Available in Good Condition",
        "body": "64GB unlocked, original box.",
        "ask_price": 260
      }
    ]
  }'
```

Expected response shape:
```json
{
  "ok": true,
  "results": [
    {
      "listing_url": "https://www.facebook.com/marketplace/item/test-123",
      "canonical_kind": "iPhone 12 64GB",
      "coarse_low": 240,
      "coarse_high": 380,
      "confidence": "high",
      "worth_deep": true,
      "red_flags": [],
      "cache_hit": false
    }
  ],
  "cache_hits": 0,
  "llm_calls": 1
}
```

A second identical request should return `"cache_hits": 1, "llm_calls": 0`
— that's the cache working.

## What runs where

```
desktop                                supabase
─────────────────────────              ────────────────────
poll Marketplace (curl_cffi)
    │
    ▼
regex reject filter         ┐
    │                       │   one HTTP per batch
    │  (survivors batched)  ├──────────────────►   appraise-normalize
    │                       │   ≤50 items/req            │
    ▼                       ┘                            │  cache check
score + persist             ◄──────────────────  results │  (normalized_listings)
                                                         │  cache miss → MiniMax
                                                         │  upsert + return
```

Desktop scrapes (Facebook IP-rate-limit reasons). Supabase normalizes
(API key never leaves the cloud). The two never trade roles.

## Cost shape

- MiniMax-Text-01: $0.20/M input, $1.10/M output.
- Typical batch of 40 listings: ~5,200 input tokens + ~3,000 output ≈ $0.004.
- Per cache MISS at N=40 batched: ~$0.0001/listing.
- Cache hits are FREE.
- Realistic per-user: 200-500 unique new listings/day across all watches → ~$0.02-$0.05/day. Pads in well under any reasonable per-user margin.

## Bumping the prompt

Bump `PROMPT_VERSION` in `cloud/supabase/functions/appraise-normalize/index.ts`
when you materially change the system prompt or output schema. Old
cached rows stay in the table but stop being consulted (the cache
lookup filters on `prompt_version = current`). They re-normalize on
the next pass at the new version.

## When something breaks

The pipeline is fail-soft: every layer treats normalize as optional.

| Failure mode | What happens |
|---|---|
| `MINIMAX_API_KEY` not set | edge function 500s; desktop logs warning, falls back to raw-title comp lookup, no UI breakage |
| MiniMax HTTP 5xx / timeout | edge function returns cached results only + `llm_error` field; desktop treats misses as fallbacks |
| Edge function not deployed | desktop cloud_client raises CloudUnavailable; normalize_batch returns empty_fallback for all; raw-title comp lookup proceeds |
| Migration not run | edge function 500s on first cache write; redeploy after running migration |

Logs are in the Supabase dashboard → Functions → appraise-normalize
→ Logs.
