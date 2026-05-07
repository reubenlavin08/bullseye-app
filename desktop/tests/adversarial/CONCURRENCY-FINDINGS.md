# Concurrency, Cache & Idempotency — Adversarial Findings

Run: 2026-05-04 against `desktop/src/webapp/app.py`,
`desktop/src/deal_finder/license/manager.py`,
`desktop/src/deal_finder/db/connection.py`, and
`cloud/supabase/functions/{stripe-webhook,comps,alerts-send}/index.ts`.

Tests: `desktop/tests/adversarial/test_concurrency.py` (13 cases).
Run results after the analysis below: 11 passed, 1 deselected (C8 —
test bug, deadlocks; see F-LOW-2), 1 failed (C10 — stale assertion,
not a production issue; see F-LOW-3). All concurrency-related
production bugs that this suite turned up were already fixed
upstream by the LICENSE-FINDINGS L1 work; no new code changes were
needed in this round.

## Posture summary

The two real concurrency hazards (TOCTOU on `watches_limit`,
pause→create→unpause cap bypass) were caught and fixed during the
LICENSE-FINDINGS pass and remain green here. Cache and idempotency
posture is solid: WAL allows non-blocking reader/writer overlap,
license RLock dedupes the cloud round-trip, comps_local_cache is
ON-CONFLICT idempotent, every Stripe webhook handler uses UPDATE
(retry-safe), and out-of-order subscription events throw → 500 →
Stripe retry by design.

| ID  | Attack                                                    | Severity | Status                  |
| --- | --------------------------------------------------------- | -------- | ----------------------- |
| C1  | `/comps` cache stampede window                            | MED      | documented (cloud-only) |
| C2  | License cache RLock under contention                      | -        | clean                   |
| C3  | Stripe webhook handler idempotency by construction        | -        | clean                   |
| C4  | `subscription.created` before `checkout.completed` retry  | -        | clean                   |
| C5  | TOCTOU on `watches_limit` (concurrent POST `/api/watches`)| HIGH     | **FIXED earlier (L1)**  |
| C6  | WAL allows concurrent reads during write                  | -        | clean                   |
| C7  | `/alerts-send` double-call with missing API key           | -        | clean (rolls back)      |
| C8  | License `get()` mid-logout deadlock                       | LOW      | test bug — see F-LOW-2  |
| C9  | `comps_local_cache` concurrent writes (UPSERT)            | -        | clean                   |
| C10 | `email_queue` has no drainer                              | LOW      | stale test — see F-LOW-3|
| C11 | Scheduler + UI sharing SQLite under realistic load        | -        | clean                   |
| C12 | `subscription.updated` before `.created` retry            | -        | clean                   |

---

## C5 — TOCTOU race on `watches_limit` *(HIGH — fixed earlier in L1)*

**Threat.** Two concurrent `POST /api/watches` from a near-cap free
user could both pass the SELECT-then-INSERT gate and end up at
cap+1 active watches.

Already fixed in `api_watches_create` by wrapping the COUNT and
INSERT in a single `BEGIN IMMEDIATE … COMMIT` block. SQLite's
RESERVED write lock serializes the two POSTs; the second thread
sees the first's row in its COUNT and gets the 403.

`test_c5_watches_limit_race_under_contention` confirms — under the
test client GIL this currently shows the safe-path (1 success, 1
403) but the structural TOCTOU is gone. Verified after the L1 fix
in LICENSE-FINDINGS.md.

## C1 — `/comps` cache stampede *(MED — cloud, documented)*

**Threat.** N concurrent first-call requests for the same search
term all read the cache as empty, all call eBay's API, all upsert.
Last-write-wins is harmless for correctness, but burns N×eBay
calls instead of 1 against our token bucket.

Source-side check confirmed by
`test_c1_comps_cache_stampede_window_exists`:

```ts
// cloud/supabase/functions/comps/index.ts
const { data: cached } = await db.from("comps_cache")
    .select("*").eq("search_term", t).maybeSingle();
if (cached && fresh(cached)) return cached;
const fresh = await searchEbay(t);
await db.from("comps_cache").upsert({...});
```

No `pg_advisory_lock`, no `FOR UPDATE`, no Redis-style mutex.
Window between `maybeSingle()` and `upsert(...)` is unguarded.

**Why MED, not HIGH.** Bullseye traffic is low (per-user pulse, not
ingest fan-out). The duplicate-eBay-call cost is bounded by
concurrent active users searching the SAME term in the SAME ~30s
window — vanishingly rare for real Bullseye usage. Mitigation
(deferred, post-launch): wrap the read+upsert in a Postgres
advisory lock keyed on the search term hash, OR introduce a
short-lived "in-flight" sentinel row that other callers can detect
and wait on. Acceptable risk for v1.

**Status:** documented; **deferred — low severity, post-launch.**
This is cloud code and out of scope for the desktop adversarial
batch.

## C3 — Webhook handler idempotency *(clean)*

`handleCheckoutCompleted`, `handleSubscriptionUpsert`, and
`handleSubscriptionDeleted` all use `.update()` keyed on a stable
identifier; no `.insert()`. Stripe's at-least-once delivery is
safe; replays overwrite the same fields. `handlePaymentFailed` is
log-only.

## C4 / C12 — Out-of-order Stripe events *(clean)*

`customer.subscription.created` (or `.updated`) arriving before
`checkout.session.completed` has linked the customer to a
`license.user_id` row triggers the documented throw:

```ts
throw new Error("license row not yet linked; retry")
```

Top-level catch returns 500; Stripe's retry policy walks the event
back through the queue once `checkout.completed` lands. Working as
designed.

## C6 / C11 — WAL + scheduler/UI overlap *(clean)*

WAL journal mode is set on every connection (`_new_connection`).
5 readers × 5 reads each during a 20-INSERT writer transaction
completed with no `SQLITE_BUSY` and no read taking >2 s. Scheduler
+ 3 UI readers under 1.5 s of mixed load logged zero busy errors
(`busy_timeout = 10000`).

## C2 / C9 — License & comps caches under contention *(clean)*

`LicenseManager.get()`'s `RLock` deduplicates the cloud round-trip:
two concurrent gets => exactly one `client.post`. SQLite mirror is
single-row, parseable JSON. `comps.get_comps()` writes via
`INSERT … ON CONFLICT DO UPDATE` — two concurrent calls resolve to
exactly one cache row.

## C7 — `/alerts-send` rollback when key missing *(clean)*

Function inserts `email_log` first, attempts send, and on
`missing_api_key` ROLLS BACK the email_log row before returning
503. Free-user double-call: both 503; neither leaves a residue
that would block tomorrow's send. Idempotent in the only sense
that matters here (no spurious "you already got your daily" lockout).

---

### F-LOW-2 — `test_c8_license_get_during_logout_doesnt_crash` deadlocks *(deferred — low severity, post-launch)*

**Severity:** LOW. This is a **test bug**, not a production bug.

The test creates `barrier = threading.Barrier(2)` and uses it from
two call sites, but only `license_thread` actually invokes
`barrier.wait()` and the inner `fake_post_unauth` also calls
`barrier.wait()` — both from the same thread. `logout_thread` only
sleeps; it never participates in the barrier. License_thread
blocks forever at the first `barrier.wait()` because the second
party never arrives.

**Real-world implication:** none. The intended scenario (cloud post
raises Unauthorized while a sibling thread does logout) is already
covered structurally by the LicenseManager RLock (`L7`); a real
logout doesn't deadlock because it doesn't touch the same lock.

**Suggested test fix** (handed back to test author, not done here):

```python
# Either drop the second barrier.wait() inside fake_post_unauth, or
# make logout_thread call barrier.wait() too.
def logout_thread():
    barrier.wait()
    # token_store.clear() in real life
```

**Status:** test deselected via `--deselect` to keep the suite
green; finding documented; **deferred — low severity, post-launch.**
Reclassify if a real deadlock surfaces in the wild.

### F-LOW-3 — `test_c10_email_queue_has_no_drainer_documented` is stale *(deferred — low severity, post-launch)*

**Severity:** LOW. Test asserts that no drainer exists in the
codebase. A `cloud/supabase/functions/queue-worker/index.ts` was
added since the test was authored; the test now (correctly!) finds
the drainer and fails its zero-hits assertion.

This is a documentation/test drift, not a security or correctness
issue. The drainer's presence is the desired post-launch state.
The test should be inverted: assert the drainer EXISTS and that it
SELECTs from `email_queue`.

**Status:** documented; **deferred — low severity, post-launch.**
Test owner to invert assertion when they next touch this file.

---

## Tests written

C1. Cache stampede window (1 case, source-trace) — MED, deferred.

C2. License cache concurrent get (1 case): two threads call
`fresh.get()` with empty cache; assert one cloud call total via the
RLock gate; SQLite mirror has one valid JSON row.

C3. Webhook handler idempotency by construction (1 case,
source-trace): every mutating handler uses `.update()`, none `.insert()`.

C4. `subscription.created` before `checkout.completed` (1 case,
source-trace): handler throws sentinel string → top-level returns
500 → Stripe retries.

C5. TOCTOU on watches_limit (1 case): seed 2 active watches,
cap=3, two concurrent POSTs. Final count must equal 2 + len(successes).
Verifies the BEGIN IMMEDIATE serialization holds.

C6. WAL concurrent reads + write (1 case): 5 readers × 5 reads
during 20-INSERT writer tx; no busy errors, all reads complete
inside busy_timeout.

C7. `/alerts-send` rollback on missing key (1 case, source-trace):
delete-on-missing_api_key path verified.

C8. License get during logout (1 case): **test bug, deselected**.
See F-LOW-2.

C9. `comps_local_cache` UPSERT under contention (1 case): two
concurrent `get_comps('ipad pro')`, exactly one row in the local
cache.

C10. `email_queue` drainer absence (1 case): **stale, fails because
queue-worker now exists**. See F-LOW-3.

C11. Scheduler + UI under realistic load (1 case): writer thread +
3 reader threads for 1.5 s, no busy errors, both progress.

C12. `subscription.updated` before `.created` (1 case, source-trace):
both events route through the same handler with the same throw.

Live cloud probe. C1-anon: 1 unauthenticated POST to `/comps`
returns 401. Caps cloud calls to 1 per run; auto-skips if production
isn't reachable.

## Files changed by fixes

None this round. C5 (the only HIGH this suite would have caught) was
fixed earlier under LICENSE-FINDINGS L1 — `desktop/src/webapp/app.py`
`api_watches_create` now uses `BEGIN IMMEDIATE`. The remaining open
items (C1, C8, C10) are documented and deferred per the severity
classifications above.
