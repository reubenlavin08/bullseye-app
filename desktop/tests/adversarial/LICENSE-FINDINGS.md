# License & Tier Enforcement — Adversarial Findings

Probed: `desktop/src/deal_finder/license/manager.py`, `desktop/src/webapp/app.py`, `cloud/supabase/functions/license/index.ts`. Tests in `test_license_bypass.py`. All 24 tests now pass after the fixes below; the 2 marked **FIXED** were genuine breaches caught here first.

| ID  | Attack                                               | Severity | Status        |
| --- | ---------------------------------------------------- | -------- | ------------- |
| L1  | Race on `watches_limit` (TOCTOU)                     | HIGH     | **FIXED**     |
| L2  | Bulk endpoint cap (10 kw → 3 created)                | -        | clean         |
| L3  | Pause-create-unpause cycle (PATCH active=true)       | MEDIUM   | **FIXED**     |
| L4  | Soft-delete reactivation                             | -        | clean (hard)  |
| L5  | Kill-switch sticky on offline                        | -        | clean         |
| L6  | Trial re-redemption via re-signup                    | LOW      | policy gap    |
| L7  | Concurrent `LicenseManager.get()`                    | -        | clean         |
| L8  | Garbage `min_supported_version` (`'1.x.0'`)          | MEDIUM   | **FIXED**     |
| L9  | Free-user paid endpoints                             | -        | clean         |
| L10 | Direct cloud `/license` as free                      | -        | clean         |
| L11 | Trial expiry auto-downgrade                          | -        | clean         |
| L12 | Bulk-add corner cases                                | -        | clean         |

---

## L1 — TOCTOU race on `watches_limit` *(HIGH, FIXED)*

**Breach.** Two concurrent `POST /api/watches` from a 2-watch free user (cap=3) both passed the SELECT-then-INSERT gate in the original code: I observed `4` active rows and two `200` responses with the test client. Repro at `test_L1_race_watches_limit_concurrent_post`.

**Why.** The original handler did the COUNT in one `with get_conn()` context and the INSERT in a second context — no shared transaction, no upfront write lock. Per-thread connections + `isolation_level=None` autocommit mean the two threads were interleaving freely.

**Fix.** `desktop/src/webapp/app.py` `api_watches_create`: COUNT and INSERT now run inside a single `BEGIN IMMEDIATE` … `COMMIT` block. SQLite's RESERVED write lock serializes the two POSTs; the second thread sees the first thread's row in its COUNT and gets the 403.

## L3 — Pause → create → unpause exceeds cap *(MEDIUM, FIXED)*

**Breach.** With cap=3 and 3 active watches: pause one (2 active, 1 paused), POST a fresh watch (3 active, 1 paused), then `PATCH active=true` on the paused row → 4 active. The PATCH endpoint had no cap re-check.

**Fix.** `api_watches_patch` now calls `license_manager.watches_limit()` whenever a request flips `active` from 0 to 1 and rejects with the same `watches_limit_reached` 403 the POST handler uses. A non-flip update (e.g. just changing `score_threshold`) skips the gate.

## L8 — `'1.x.0'` wedges the user *(MEDIUM, FIXED)*

**Breach.** Original `_semver_less` parser silently coerced unparseable components to 0. So `'1.x.0'` became `(1, 0, 0)`, which is `>` than our `__version__ = '0.1.0'`, and `is_kill_switched()` returned True — locking out every user on a typo-level cloud config glitch.

**Fix.** `desktop/src/deal_finder/license/manager.py` `_semver_less` now rejects any non-digit component outright (returns `None` from `parse`), and the comparison fails open (`return False`) on either side being `None`. Verified across `'not.a.version'`, `''`, `None`, `'abc.def.ghi'`, `'1.x.0'`, `'v0.1.0'`, `'  '` — all now fail open.

## L6 — Trial re-redemption *(LOW, policy gap, no fix)*

A user can: (a) burn their trial, (b) `DELETE /api/account/delete`, (c) re-sign-up with the same email. Cloud `/license` keys off `auth.users.id`; a fresh signup creates a fresh `licenses` row with a fresh trial window. There is no `trial_redemption_history` table or email-based throttle.

For a $9.99/mo product with email/name signup friction this is acceptable — the signup-trigger creates a default `tier='free'` row, and trials are gated by Stripe checkout, not /license. Documented as a policy choice rather than a flaw. Mitigation if abuse appears: add a `prior_trial_emails` table and have the auth signup hook check it.

## Findings that came back clean

- **L2 / L12** Bulk endpoint correctly truncates at the cap, sets `truncated_at_limit=true`, and counts dedupe-reactivation separately from new creates.
- **L4** `DELETE /api/watches/<id>` is a hard delete (`DELETE … RETURNING keyword`); subsequent PATCH yields 404. No soft-delete revival vector.
- **L5** Kill-switch is sticky offline. Once `is_kill_switched()` returns True, a `CloudUnavailable` error keeps the cached True value (in-memory > SQLite > default). Verified across `force_refresh=True` while cloud is down.
- **L7** `LicenseManager._lock` (RLock) serializes the cloud round-trip — two concurrent `get()`s yield exactly one `client.post` call and identical dicts. SQLite mirror is single-write, parseable.
- **L9** All three paid endpoints (`/api/dashboard/breakdown/<id>`, `/per-watch`, `/score-histogram`) return `403 upgrade_required` for free, `200` for paid/trial.
- **L10** Code-trace of `cloud/supabase/functions/license/index.ts`: only writes are (a) self-heal `INSERT … tier='free'` and (b) `UPDATE … tier='free'` on expiry. There is no codepath in `/license` that promotes a user to `paid` — that's owned by `stripe_webhook` in a separate function.
- **L11** Same source: expired trials are downgraded synchronously inside `/license` (`license.tier === "trial" && new Date(license.trial_ends_at) < now → UPDATE tier='free'`). Desktop-side `trial_days_remaining()` returns `0`, not negative, in the rare race window before the next /license refresh.

## Files touched by fixes

- `desktop/src/webapp/app.py` — `api_watches_create` (L1) and `api_watches_patch` (L3).
- `desktop/src/deal_finder/license/manager.py` — `_semver_less` (L8).
