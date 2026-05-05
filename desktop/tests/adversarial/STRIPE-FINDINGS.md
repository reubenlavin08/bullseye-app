# Stripe webhook + checkout — adversarial findings

Probe date: 2026-05-04
Targets: `https://qfkzhyxmohytnzskcmdv.supabase.co/functions/v1/stripe-webhook`
and `/functions/v1/checkout-create`.
Tests: `desktop/tests/adversarial/test_stripe_webhook.py` (16 cases; 12
network + code-trace, 4 require a real user JWT in
`BULLSEYE_TEST_USER_JWT`).

## Summary

Posture is solid. Every attempt to forge an upgrade, bypass auth, or
slip past signature verification was rejected at the correct layer.
No CRITICAL findings. Two LOW-severity items documented for the backlog.

| ID  | Attack                                | Result                        | Severity |
| --- | ------------------------------------- | ----------------------------- | -------- |
| S1  | Unsigned webhook POST                 | 400 — header check first      | OK       |
| S2  | Wrong-signature webhook POST          | 400 — HMAC verify fails       | OK       |
| S3  | Forged `checkout.session.completed`   | 400 — never reaches handler   | OK       |
| S4  | Stale event (created 10h ago)         | 400 — HMAC fails before clock | OK       |
| S5  | Out-of-order `subscription.created`   | Throws -> 500 -> Stripe retry | OK       |
| S6  | Duplicate event (idempotency)         | All handlers UPDATE, no INSERT| OK       |
| S7  | `/checkout-create` body fuzz          | Auth-first, plan ternary safe | OK       |
| S8  | `/checkout-create` no JWT             | 401                           | OK       |
| S9  | Trial double-redemption               | Possible via fresh email      | LOW      |
| S10 | Checkout-URL reuse across users       | Originator wins, payer loses  | LOW      |

## S1 — Unsigned webhook

```
POST /functions/v1/stripe-webhook   (no stripe-signature)
-> 400  "missing stripe-signature header"
```

Handler short-circuits on the missing header before reading the body
(`stripe-webhook/index.ts:33-36`). Confirmed live.

## S2 — Wrong-signature webhook

```
stripe-signature: t=1,v1=deadbeef
-> 400  "signature verification failed: No signatures found matching ..."
```

`verifyWebhookSignature` runs `stripe.webhooks.constructEventAsync` on
the raw body bytes. The constant-time HMAC compare in the Stripe SDK
rejects the forged signature. (`_shared/stripe.ts:85-94`,
`stripe-webhook/index.ts:42-51`.)

## S3 — Forged `checkout.session.completed`

Body claimed `client_reference_id = 00000000-0000-0000-0000-000000000001`
and `customer = cus_attacker_owned`. Without `stripe-signature`,
processing stops at `index.ts:34`. Even with a bogus signature header,
HMAC verification fails. The handler `handleCheckoutCompleted` is
unreachable from outside Stripe.

## S4 — Stale event

Sent body with `created` 10 hours ago plus a fake signature with
`t=<10h ago>`. Returned 400 at HMAC verification — we never reached
Stripe's `Timestamp outside the tolerance zone` clock check. The
Stripe SDK enforces a default 5-minute tolerance on the `t=` header
inside `constructEventAsync`; the handler does not override this. So a
genuinely-signed-but-stale event would also be rejected (assuming the
attacker hasn't somehow gotten the signing secret, in which case the
game is already over). No action required.

## S5 — Out-of-order events

Code trace, `stripe-webhook/index.ts:116-135`:

```ts
const { data: license } = await db
    .from("licenses")
    .select("user_id")
    .eq("stripe_customer_id", customerId)
    .maybeSingle()
if (!license?.user_id) {
    console.warn(`no license row for customer ${customerId}; will retry`)
    throw new Error("license row not yet linked; retry")
}
```

If `customer.subscription.created` arrives before
`checkout.session.completed` has linked the customer to a user, the
handler throws. The top-level catch at `index.ts:76-82` returns 500,
and Stripe retries with exponential backoff. Correct.

## S6 — Idempotency

Each DB-mutating handler uses `.update()` keyed on a stable identifier:

| Handler                       | DB op                                                |
| ----------------------------- | ---------------------------------------------------- |
| `handleCheckoutCompleted`     | `update({stripe_customer_id, ...}).eq('user_id', x)` |
| `handleSubscriptionUpsert`    | `update({tier, period_end, ...}).eq('user_id', x)`   |
| `handleSubscriptionDeleted`   | `update({tier:'free', ...}).eq('user_id', x)`        |
| `handlePaymentFailed`         | logs only                                            |

No `.insert()` calls anywhere. Stripe at-least-once delivery is safe;
duplicate deliveries are harmless overwrites of the same fields.

## S7 — `/checkout-create` body fuzz

Order matters: `requireUser` runs BEFORE `req.json()`
(`checkout-create/index.ts:47-60`). All malformed-body cases without a
real user JWT return 401 before any input is even parsed. With anon-key
only (no user), `requireUser` rejects via `admin.auth.getUser(token)`
returning no user.

For the body-handling branches that require a real user JWT (skipped
unless `BULLSEYE_TEST_USER_JWT` is set), the code review confirms:

- `plan: "lifetime"` -> coerced to `"monthly"` by the ternary at line 61.
- `plan: "../../../etc"` -> same coercion; the raw string never reaches
  Stripe (Stripe gets a `priceId` from env, not the request body).
- `plan: ""` -> coerced to `"monthly"`.
- `{}` -> coerced to `"monthly"`.
- `body: null` -> `body.plan` throws on null deref; surfaces as 500.
  Minor: a strict null guard would be cleaner. Not exploitable; just
  noisier in logs.
- `body: "not json"` -> the `try/catch` at lines 56-60 returns 400
  `"invalid JSON body"`.
- `method: GET` -> 405 `"method not allowed"` (live-confirmed).

## S8 — No JWT

```
POST /checkout-create     (no Authorization header)
-> 401  {"code":"UNAUTHORIZED_NO_AUTH_HEADER",...}     (Supabase platform layer)

POST /checkout-create     (anon key as Bearer, no user JWT)
-> 401  {"error":"invalid or expired token"}            (our requireUser)
```

Both paths return 401. Confirmed live.

## S9 — Trial double-redemption (LOW)

`/checkout-create` accepts `trial=true` unconditionally
(`checkout-create/index.ts:62`). There is no server-side check against
`licenses.trial_ends_at` to block users who already burned their 7-day
trial. A determined user can:

1. Sign up with email A, start trial, cancel before billing.
2. Sign up with email B, start trial again.

Stripe does not de-duplicate trials per identity; only per Stripe
customer. We pass `customer_email` for first-time users, so a new email
mints a new Stripe customer and a fresh trial.

Mitigation (deferred): before creating the session, look up
`licenses.trial_ends_at IS NOT NULL` for the current user and force
`trial=false`, OR enforce uniqueness on a fingerprint
(payment_method.fingerprint, IP+device, etc.). Acceptable risk while
the product is in early-access.

## S10 — Checkout URL reuse across users (LOW)

`client_reference_id` is set to the originator's `user_id` at session
creation (`_shared/stripe.ts:57`) and is read back by the webhook
verbatim (`stripe-webhook/index.ts:95`). If user A creates a checkout
URL and shares it with user B who actually pays:

- A's `licenses` row is upgraded.
- B's payment method is charged.
- B has no record on our side.

This is a Stripe-platform behaviour, not a vulnerability. The mitigation
is product-side: the success page should display the upgraded account's
identity ("Upgraded as alice@x.com") so a payer notices the mismatch.
Documented; not currently mitigated.

## Posture

- Webhook signature enforcement: working, no bypass found.
- AuthN on `/checkout-create`: working at both Supabase platform and
  function layers.
- Idempotency: every DB-mutating webhook handler uses `.update()`,
  retry-safe.
- Out-of-order handling: correct (throw -> 500 -> Stripe retry).
- Two backlog items (S9, S10), both low-severity, both documented.

## Test artefacts

- `desktop/tests/adversarial/test_stripe_webhook.py` — 16 test cases.
  - Code-trace (S5, S6, S9, S10): always run, 4 passed.
  - Live (S1-S4, S7-no-JWT, S8, S7-GET): pass when
    `BULLSEYE_RUN_LIVE=1` is set, 8 passed.
  - JWT-gated (S7 fuzz happy paths): require
    `BULLSEYE_TEST_USER_JWT`, currently skipped.
- Total live HTTP calls used during this probe: ~10. Well under the
  ~30 cap.
