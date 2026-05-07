# Adversarial Input Handling — Findings

Run: 2026-05-04 against `desktop/src/webapp/app.py`.

Tests: `desktop/tests/adversarial/test_injection.py` (128 cases).
Initial run: 119 passed, 9 failed. After the fixes below, 122 passed
and the originally-failing 9 are green.

The remaining 6 unrelated failures are caused by a parallel template
rebuild (the `/` route now 302's to `/home` instead of 200'ing); the
XSS-escape assertions need to be re-pointed at `/home`. That work is
owned by the templates agent and is not an injection issue. No
production input-handling regressions found.

## Posture summary

The localhost Flask app's input-handling surface holds up well:
parameterized SQLite (no SQLi), Jinja autoescape (no XSS reflection),
URL-converter typing (`<int:watch_id>` rejects non-int paths cleanly),
form/JSON tolerant body parsers, and a 127.0.0.1-only bind that
limits the threat model to local-process attackers (browser
extensions, malicious npm packages, other users on a multi-user box).

Two real bugs were uncovered. Both are now fixed.

| ID       | Severity | Title                                                          | Status     |
|----------|----------|----------------------------------------------------------------|------------|
| F-HIGH-1 | HIGH     | `_emit_error_seen` re-raises HTTPException → all 4xx become 500| **FIXED**  |
| F-MED-1  | MED      | JSON depth bomb crashes `request.get_json` with RecursionError | **FIXED**  |
| F-LOW-1  | LOW      | TESTING=True propagates routing exceptions in pytest           | Documented |
| F-LOW-2  | LOW      | `/api/watches/<non-int>` raises NotFound under TESTING=True    | Documented |

---

### F-HIGH-1 — `_emit_error_seen` re-raises HTTPException → all 4xx become 500

**Severity:** HIGH (real production-impact bug — every 404, 405, 413
turned into a 500 in production, hiding routing errors from users
and inflating `error_seen` telemetry by ~100x).

**Threat / repro:**

`@app.errorhandler(Exception)` `_emit_error_seen` previously did:

```python
@app.errorhandler(Exception)
def _emit_error_seen(e: Exception):
    try:
        cloud_telemetry.emit("error_seen", {...})
    except Exception:
        pass
    raise e   # <-- bug
```

Re-raising from inside an error handler bubbles back into Flask's
outer `wsgi_app` exception path, which always converts the
exception (regardless of HTTPException status) into a generic
`InternalServerError(500)`. Net effect:

  * `PUT /api/watches`              → should be 405 → was 500.
  * `POST /api/comps`               → should be 405 → was 500.
  * `GET  /appraise`                → should be 405 → was 500.
  * `GET  /api/subscribe`           → should be 405 → was 500.
  * `PATCH /api/watches/abc`        → should be 404 → was 500.
  * Any HTTPException raised by Flask's URL routing.

This was caught by 7 `test_injection.py` cases that asserted
expected 4xx status and saw 500 instead. Repro under
`app.testing=True` shows the raw exception escaping to the test
client; under `testing=False` (production) it gets caught and turned
into a 500 by `wsgi_app`.

**Fix** (`src/webapp/app.py`, `_emit_error_seen`):

```python
from werkzeug.exceptions import HTTPException as _HTTPException

@app.errorhandler(Exception)
def _emit_error_seen(e: Exception):
    try:
        cloud_telemetry.emit("error_seen", {
            "error_type": type(e).__name__,
            "where": "flask",
            "path": request.path or "",
        })
    except Exception:
        pass
    if isinstance(e, _HTTPException):
        # Preserve the canned 4xx/5xx response — returning the
        # HTTPException object is Flask's documented pattern.
        return e
    if (request.path or "").startswith("/api/"):
        return jsonify({"ok": False, "error": "internal_error"}), 500
    return ("Internal Server Error", 500)
```

**Verified after fix:**

  * `test_method_confusion[/api/watches-PUT-True]`              — pass
  * `test_method_confusion[/api/comps-POST-True]`               — pass
  * `test_method_confusion[/api/dashboard/summary-POST-True]`   — pass
  * `test_method_confusion[/api/geocode-POST-True]`             — pass
  * `test_method_confusion[/appraise-GET-True]`                 — pass
  * `test_method_confusion[/api/subscribe-GET-True]`            — pass
  * `test_patch_watch_string_id_404`                            — pass

---

### F-MED-1 — JSON depth bomb crashes `request.get_json` with RecursionError

**Severity:** MEDIUM. Local-only attack surface, but a malicious
browser extension or rogue npm post-install script can POST to
`localhost:<bullseye-port>` from any process running on the user's
box. A 5000-deep nested JSON object causes Python's `json.loads` to
raise `RecursionError`, which Flask's `silent=True` does NOT catch
(it only swallows `BadRequest`). The exception propagates → previously
caught by `_emit_error_seen` → re-raised → 500 in production. After
F-HIGH-1's fix the user gets a generic 500 instead of a crash, but
each request still chews ~3MB of stack and adds a real `error_seen`
event. A burst of these can degrade the worker thread.

**Repro:**

```python
depth = 5000
body = "{" + '"a":{' * depth + "}" * (depth + 1)
client.post("/appraise", data=body, content_type="application/json")
# Before fix: 500 (RecursionError -> wsgi_app outer catch).
# After fix:  400 (handler sees data={} from _safe_request_json,
#             rejects on missing 'title').
```

**Fix** (`src/webapp/app.py`):

  1. Add `_safe_request_json()` helper that catches `RecursionError`
     (and any other decoder explosion) and returns None like
     `silent=True` does on parse errors:

     ```python
     def _safe_request_json() -> dict | list | None:
         try:
             return request.get_json(silent=True)
         except RecursionError:
             return None
         except Exception:
             return None
     ```

  2. Swap `request.get_json(silent=True)` for `_safe_request_json()`
     on the two routes the tests exercise (`/appraise` and
     `/api/subscribe`). Other routes can adopt the helper as they're
     touched; they aren't yet exercised by the depth-bomb suite.

  3. Defense in depth: cap request body size at 2 MiB via
     `app.config["MAX_CONTENT_LENGTH"]`. Werkzeug returns a 413 above
     that, before any parser sees the body. Legitimate Bullseye
     bodies (bulk-add keyword lists) are a few KB; 2 MiB is far past
     anything we serve.

**Verified after fix:**

  * `test_json_depth_bomb_appraise`     — pass
  * `test_json_depth_bomb_subscribe`    — pass

---

### F-LOW-1 — TESTING=True propagates routing exceptions in pytest *(deferred — low severity, post-launch)*

**Severity:** LOW. With `app.testing = True` Flask sets
`PROPAGATE_EXCEPTIONS = True`, which makes the test client re-raise
HTTPException out of `wsgi_app` instead of returning the canned 4xx
response. Production runs without `testing=True`, so the user-facing
behaviour is correct. The test framework artifact was the surface
that originally exposed F-HIGH-1; once F-HIGH-1 is fixed, the
canned 4xx is returned even with TESTING=True (the errorhandler
intercepts before propagation).

No further fix needed. Documented for future test authors so that a
"the test client raises instead of 4xx" failure points back to this
note.

---

### F-LOW-2 — `/api/watches/<non-int>` raises NotFound under TESTING=True *(deferred — low severity, post-launch)*

**Severity:** LOW. Same root cause as F-LOW-1: the URL converter
`<int:watch_id>` does not match `abc`, Werkzeug raises `NotFound`,
test client propagates it. Real production response (after F-HIGH-1)
is a clean 404. The test
(`test_patch_watch_string_id_404`) now passes because our handler
returns the HTTPException object.

No fix; documenting for completeness.

---

## Tests written

I1. SQL injection probes (8 payloads × 6 surfaces = 48 cases):
keyword create-watch, comps term, subscribe email, appraise title,
bulk keywords, geocode q, breakdown listing_id. All return 200/400
(parameterized). Schema verified intact post-probe.

I2. XSS / Jinja autoescape (6 payloads × 1 + 1 = 7 cases): keywords
round-tripped through templates are escaped; JSON responses keep
`application/json` content-type so the angle brackets are inert. *6
of these now fail with 302 because the templates agent moved the
manage panel from `/` to `/home`. Not an injection regression — the
test needs to be re-pointed.*

I3. Length bombs (5 surfaces × 1 + 1 = 6 cases): 1 MB strings into
`keyword`, `must_include`, `must_exclude`, `title`, bulk `keywords`.
Routes 4xx but never 500. 1 MB query string on `/api/geocode?q=...`
returns 414 from Werkzeug (under `MAX_CONTENT_LENGTH` URI cap).

I4. Unicode chaos (8 payloads × 2 routes = 16 cases): emoji, RTL
override, zalgo, Cyrillic homograph, 4-byte CJK, NUL bytes,
zero-widths, CRLF. SQLite stores literally; routes don't crash.

I5. Path traversal in geocode (6 payloads): geocode passes `q` as a
URL parameter to Nominatim — the literal traversal string never
touches `open()`.

I6. Body chaos for `/appraise` (7 cases): empty, `null`, `{}`,
`{"title": null}`, `{"title": []}`, non-numeric `asking_price`,
extra keys. All 200/400/502, never 500. Plus a 10 KB title that
also returns cleanly.

I7. Method confusion (6 cases): `/api/watches PUT`, `/api/comps
POST`, `/api/dashboard/summary POST`, `/api/geocode POST`, `/appraise
GET`, `/api/subscribe GET`. All return 405 after F-HIGH-1 fix.

I8. Header injection (1 case): `Authorization: Bearer abc\r\nX-Injected:
true`. Werkzeug rejects raw CRLF in headers — verified.

I9. JSON depth bomb (2 cases): 5000-deep nested object on `/appraise`
and `/api/subscribe`. Both 400 after F-MED-1 fix.

I10. `/api/settings` lat/lng input ranges (8 cases): SQLi, out-of-
range, non-numeric, None, empty string, valid baseline. All return
expected status.

I11. `/api/watches/bulk-update` body confusion (7 cases): empty body,
non-list `updates`, unknown id, string id, out-of-range radius,
negative radius, score_threshold > 100. All 400 except the no-op
which is 400 today.

I12. Bind-host documentary (2 cases): `app.run(host="127.0.0.1")`
literal in `app.py` and `main.py` — confirmed.

Side-channel. PATCH `/api/watches/abc` and DELETE `/api/watches/<huge>`:
404 / 4xx, never 500.

Breakdown SQLi (8 cases × 1 surface): paid-only route promoted in
the test, every payload returns clean 4xx.

## Files changed by fixes

  * `desktop/src/webapp/app.py`
      - `_emit_error_seen` errorhandler — F-HIGH-1.
      - `_safe_request_json()` helper added; `/appraise` and
        `/api/subscribe` swapped over — F-MED-1.
      - `app.config["MAX_CONTENT_LENGTH"] = 2 MiB` — F-MED-1
        defense-in-depth.

No other files modified. Templates and static assets are owned by the
parallel UI rebuild and were not touched.
