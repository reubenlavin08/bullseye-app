# Adversarial Auth + RLS — Findings

Run: 2026-05-04 against production Supabase project `qfkzhyxmohytnzskcmdv`.

Tests: `desktop/tests/adversarial/test_auth_rls.py` (45 cases, all passing).
Cloud calls used: ~30 of the 50-call budget (2 signups + 2-4 calls per
attack vector).

## Posture summary

The auth + row-level-security model held up under every attack we
tried. No HIGH or CRITICAL issues found. One LOW behavioral
discrepancy was observed (documented below). The model is in good
shape for a public release on the desktop side.

Strengths confirmed:

  * RLS predicates `auth.uid() = user_id` correctly isolate rows
    across all five user-owned tables (`licenses`, `user_watches`,
    `email_log`, `email_queue`, `telemetry_events`).
  * `licenses` has no client-facing UPDATE/INSERT policy — users
    cannot self-promote to `tier='paid'` or hijack a
    `stripe_customer_id`. Default-deny is doing the right thing.
  * `comps_cache` is read-only for authenticated clients; cache
    poisoning via PostgREST is blocked.
  * Edge Functions enforce JWT validity via
    `supabase.auth.getUser(token)`. Tampered, expired, and forged
    tokens all return 401.
  * The Flask webapp's `@login_required_api` decorator returns 401
    on every `/api/*` route when `token_store.is_logged_in()` is
    False. Paid-only routes correctly 403 for free-tier users. The
    HTML `/dashboard` redirects free users to `/upgrade`.

## Findings

| ID       | Severity | Title                                                       | Status     |
|----------|----------|-------------------------------------------------------------|------------|
| F-LOW-1  | LOW      | Telemetry insert with explicit `user_id=NULL` rejected      | Documented |

---

### F-LOW-1 — Telemetry insert with explicit `user_id=NULL` rejected

**Severity:** LOW (no security impact; over-restriction, not under)

**Repro:**

  1. Sign up as user B; capture JWT.
  2. POST `/rest/v1/telemetry_events` with body
     `{"event_name": "x", "install_id": "y"}` — no `user_id` field.
  3. Server returns 403 with PostgREST code `42501`:
     `new row violates row-level security policy for table "telemetry_events"`.

**Expected per policy:**

```sql
CREATE POLICY "Users insert own telemetry"
    ON telemetry_events FOR INSERT
    WITH CHECK (auth.uid() = user_id OR user_id IS NULL);
```

The OR-NULL branch should allow this insert (so pre-login telemetry
can be queued during the auth handshake). In practice the insert is
rejected.

**Likely cause:** the `authenticated` role lacks an `INSERT` table
grant on `telemetry_events`, OR Supabase's PostgREST omits NULL
columns in a way that means the policy never sees the NULL branch.
The system ends up MORE restrictive than the policy text — which is
why this is LOW: there is no exfiltration or escalation path here.

**Recommended fix (optional, not urgent):**

If pre-login telemetry is a real product need, explicitly grant
INSERT and re-test:

```sql
GRANT INSERT ON public.telemetry_events TO authenticated, anon;
```

Otherwise, drop the `OR user_id IS NULL` branch and emit telemetry
only after a JWT is in hand. The current desktop client appears to
do that already (it sets `user_id` once login completes), so the
broken branch is unused in practice.

**Test reference:**
`test_a9_telemetry_null_user_id_observed_behavior`

---

## Tests written

A1. Cross-tenant RLS leak — 5 tables × 1 case = 5 cases. All return
empty arrays as expected.

A2. Anon-only writes — 2 cases (`licenses`, `user_watches`). Both
rejected (401/403/4xx).

A3. JWT tampering — re-encode user A's JWT with `sub` set to all
zeros, send to `/functions/v1/comps`. 401 returned.

A4. Expired/forged JWT — hardcoded `exp=1` token. 401 returned.

A5. Missing apikey header — verified production rejects or returns
no rows when `apikey` is omitted but `Authorization` is present.

A6. Webapp auth bypass — 23 protected `/api/*` routes return 401
when `token_store.is_logged_in()` is False. 3 paid-only routes
return 403 for free users. `/dashboard` redirects free users to
`/upgrade`. (26 cases.)

A7. Self-license forgery — UPDATE and INSERT on own `licenses` row
both blocked (no policy exists for those operations).

A8. Stripe-customer-id hijack — UPDATE rejected (covered by A7's
no-UPDATE-policy default-deny).

A9. Telemetry user_id spoof — user B inserting with
`user_id=<user A's id>` rejected (RLS WITH CHECK).

A10. comps_cache write protection — SELECT works for authenticated
users; INSERT and UPDATE are both blocked.

## Migration added

None — no security-impacting fix was required. F-LOW-1 is a
non-security behavioral note; if the team decides to enable the
NULL-user_id telemetry path later, that's a separate product change.
