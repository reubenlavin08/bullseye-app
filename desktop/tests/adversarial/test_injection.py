"""Adversarial input handling tests for the local Flask webapp.

Goal: try to BREAK the input surface — SQL injection, XSS, length
bombs, unicode chaos, path traversal, body confusion, method confusion,
header injection, JSON depth bombs, range checks. Every probe should
either be parameterized away (no SQL execution leak), rejected with a
4xx, or handled gracefully — NEVER 500, NEVER reflected, NEVER leaked.

Run:
    pytest desktop/tests/adversarial/test_injection.py -q

These tests share the `client` + `logged_in` + `bullseye_db` fixtures
from `tests/test_webapp.py` via the same conftest. We re-import them
here as fixture functions so pytest discovers them in this module
without depending on test_webapp's collection order.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


# ---------------------------------------------------------------------------
# Fixture re-exports — same shape as desktop/tests/test_webapp.py
# ---------------------------------------------------------------------------

@pytest.fixture
def bullseye_db(tmp_path, monkeypatch):
    db_path = tmp_path / "bullseye_test.db"
    monkeypatch.setenv("BULLSEYE_DB_PATH", str(db_path))
    from deal_finder.db import connection as conn_mod
    from deal_finder.db import migrate
    conn_mod.reset_for_tests()
    conn_mod.set_db_path(str(db_path))
    migrate.run_migrations()
    yield db_path
    conn_mod.reset_for_tests()
    conn_mod.set_db_path(None)


@pytest.fixture
def client(bullseye_db):
    from webapp import app as webapp_app
    webapp_app.app.config["TESTING"] = True
    with webapp_app.app.test_client() as c:
        yield c


@pytest.fixture
def logged_in(monkeypatch):
    from deal_finder.auth import token_store
    from webapp import app as webapp_app
    from webapp import auth_routes as ar
    monkeypatch.setattr(token_store, "is_logged_in", lambda: True)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: True)
    monkeypatch.setattr(ar.token_store, "is_logged_in", lambda: True)


@pytest.fixture(autouse=True)
def _free_tier(monkeypatch):
    """Default to a logged-in-able free tier so the kill switch + paid
    gates don't dominate. Tests that need different shapes override."""
    from webapp import app as webapp_app
    lm = webapp_app.license_manager
    monkeypatch.setattr(lm, "is_paid", lambda: False)
    monkeypatch.setattr(lm, "tier", lambda: "free")
    monkeypatch.setattr(lm, "watches_limit", lambda: 100)  # don't trip cap
    monkeypatch.setattr(lm, "is_kill_switched", lambda: False)
    monkeypatch.setattr(lm, "get", lambda **kw: {"tier": "free"})


# ---------------------------------------------------------------------------
# Payload catalogues
# ---------------------------------------------------------------------------

SQLI_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE listings;--",
    "' UNION SELECT * FROM auth.users--",
    "\x00anything",                # raw NULL byte
    "%00",                          # URL-encoded NULL literal (passed as text)
    "1' OR 1=1 --",
    "admin'--",
    "\\'; SELECT * FROM sqlite_master--",
]

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "\"><script>alert(1)</script>",
    "<svg/onload=alert(1)>",
    "'\"--></style></script><script>alert(1)</script>",
]

UNICODE_CHAOS = [
    "Hello 🎉🚀💥",                            # emoji
    "abc‮dcba",                            # RTL override
    "Z̴̢͚̱͙͕͙͔͙̈́̾͌̎́̕͝͠͠a̷̢̛̩͕̾̑l̷̥̟̊̑g̷͔͝͝o̴͙̮͊͝",  # zalgo
    "аррӏе",                                    # Cyrillic homograph 'apple'
    "𠜎 𠜱 𠝹",                                  # 4-byte UTF-8 (CJK Ext B)
    "embedded\x00null\x00bytes",
    "​‌‍ invisible",             # zero-width chars
    "test\r\nX-Injected: true",                 # CRLF
]

PATH_TRAVERSAL = [
    "../../etc/passwd",
    "..\\..\\..\\windows\\system32\\drivers\\etc\\hosts",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "....//....//etc/passwd",
    "/etc/passwd",
    "file:///etc/passwd",
]


# ---------------------------------------------------------------------------
# I1 — SQL injection probes into search/email/keyword surfaces
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sqli_in_keyword_create_watch(client, logged_in, payload):
    """POST /api/watches with a SQLi-looking keyword. Parameterized
    inserts must store it literally and 200 — never 500, never break
    the schema."""
    resp = client.post(
        "/api/watches",
        json={"keyword": payload, "lat": 49.28, "lng": -123.12, "radius_km": 40},
    )
    assert resp.status_code != 500, f"500 on payload {payload!r}"
    # Either 200 (stored literally) or a 400 if the input layer rejects it.
    assert resp.status_code in (200, 400), resp.status_code

    # Side effect verification: schema still intact — listings table exists.
    from deal_finder.db.connection import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        ).fetchall()
        assert len(rows) == 1, "listings table missing — DROP succeeded!"


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sqli_in_comps_term(client, logged_in, payload):
    """GET /api/comps?term=<payload>. The query is parameterized; no
    matching rows is fine. The 500-budget guard is the assertion."""
    resp = client.get(f"/api/comps?term={payload}")
    assert resp.status_code != 500, f"500 on /api/comps term={payload!r}"
    assert resp.status_code in (200, 400)


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sqli_in_subscribe_email(client, logged_in, payload):
    """POST /api/subscribe with SQLi in email field."""
    # Insert a real watch to subscribe against.
    from deal_finder.db.connection import get_conn
    with get_conn() as conn:
        with conn:
            conn.execute(
                "INSERT INTO user_searches (keyword, latitude, longitude, "
                "radius_km, active) VALUES ('seed', 49.28, -123.12, 40, 1)"
            )
            sid = conn.execute(
                "SELECT id FROM user_searches WHERE keyword='seed'"
            ).fetchone()[0]

    resp = client.post(
        "/api/subscribe",
        json={"email": payload, "search_id": sid},
    )
    assert resp.status_code != 500
    # Most payloads will be rejected (no @) → 400; valid-looking ones
    # may insert literally → 200.
    assert resp.status_code in (200, 400)


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sqli_in_appraise_title(client, logged_in, monkeypatch, payload):
    """POST /appraise with SQLi in title — get_comps is mocked so we
    only test the route's input handling."""
    from webapp import app as webapp_app
    monkeypatch.setattr(
        webapp_app, "get_comps",
        lambda *a, **kw: {"sample_size": 0, "median": 0, "raw_comps": []},
    )
    resp = client.post(
        "/appraise",
        json={"title": payload, "asking_price": 100},
    )
    assert resp.status_code != 500
    assert resp.status_code in (200, 400, 502)


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sqli_in_searches_bulk_keywords(client, logged_in, payload):
    resp = client.post(
        "/api/searches/bulk",
        json={"keywords": payload},
    )
    assert resp.status_code != 500
    assert resp.status_code in (200, 400)


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sqli_in_geocode_q(client, logged_in, monkeypatch, payload):
    """GET /api/geocode?q=<payload>. Nominatim is mocked so we don't
    burn its rate budget."""
    import requests as real_requests

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return []

    def fake_get(*a, **kw):
        return FakeResp()

    from webapp import app as webapp_app
    monkeypatch.setattr(real_requests, "get", fake_get)

    resp = client.get(f"/api/geocode?q={payload}")
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# I2 — XSS / Jinja autoescape sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_xss_keyword_persists_escaped_in_dashboard(client, logged_in, payload):
    """Insert a watch whose keyword is an XSS payload, then verify the
    HTML pages don't reflect raw < or >."""
    resp = client.post(
        "/api/watches",
        json={"keyword": payload, "lat": 49.28, "lng": -123.12, "radius_km": 40},
    )
    assert resp.status_code in (200, 400)

    # Pull the home shell. `/` redirects through to `/home` after the
    # Wispr-style restructure, so follow redirects here. The home tab
    # renders watches client-side (textContent only), so the keyword
    # never appears in the initial HTML — but we still assert no raw
    # <script> tag, in case future template changes start interpolating.
    page = client.get("/", follow_redirects=True)
    assert page.status_code == 200
    body = page.data.decode("utf-8", errors="replace")
    # The literal "<script>" string must not appear unescaped.
    assert "<script>alert(1)</script>" not in body
    # Either the payload is escaped or the template doesn't include
    # the dynamic string at all — both are fine.


def test_xss_comps_term_in_json_response(client, logged_in):
    """JSON responses must not interpret HTML. The body comes back as
    application/json so this is mostly about content-type discipline."""
    resp = client.get("/api/comps?term=<script>alert(1)</script>")
    assert resp.content_type.startswith("application/json")
    # Even if the term is echoed back, JSON encoding handles the angle
    # brackets safely (browsers won't execute JSON).
    body = resp.get_json()
    if body and "term" in body:
        # Encoded form is fine; raw HTML in a JSON string is also fine
        # because content-type isn't text/html.
        assert isinstance(body["term"], str)


# ---------------------------------------------------------------------------
# I3 — Length bombs
# ---------------------------------------------------------------------------

ONE_MB = "A" * (1024 * 1024)


@pytest.mark.parametrize("field,endpoint,payload_key", [
    ("keyword", "/api/watches", "keyword"),
    ("must_include", "/api/watches/bulk-update", "must_include"),
    ("must_exclude", "/api/watches/bulk-update", "must_exclude"),
    ("title", "/appraise", "title"),
    ("keywords", "/api/searches/bulk", "keywords"),
])
def test_length_bomb_does_not_500(client, logged_in, monkeypatch,
                                  field, endpoint, payload_key):
    from webapp import app as webapp_app
    monkeypatch.setattr(
        webapp_app, "get_comps",
        lambda *a, **kw: {"sample_size": 0, "median": 0, "raw_comps": []},
    )
    body = {payload_key: ONE_MB}
    if endpoint == "/api/watches":
        body.update({"lat": 49.28, "lng": -123.12, "radius_km": 40})
    if endpoint == "/appraise":
        body["asking_price"] = 100
    resp = client.post(endpoint, json=body)
    assert resp.status_code != 500, (
        f"{endpoint} 500'd on 1MB {field}"
    )
    # Anything in 2xx/4xx is acceptable; we just don't want a crash.
    assert 200 <= resp.status_code < 500 or resp.status_code in (502, 503)


def test_length_bomb_in_q_param(client, logged_in, monkeypatch):
    """Geocode q gets URL-encoded into a query string. 1MB query
    strings can blow past server limits — should fail gracefully."""
    import requests as real_requests

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return []

    monkeypatch.setattr(real_requests, "get", lambda *a, **kw: FakeResp())

    resp = client.get(f"/api/geocode?q={'A' * 100_000}")
    # Werkzeug enforces a default URI cap (~64KB); we expect 414 or 200.
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# I4 — Unicode chaos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", UNICODE_CHAOS)
def test_unicode_chaos_in_keyword(client, logged_in, payload):
    resp = client.post(
        "/api/watches",
        json={"keyword": payload, "lat": 49.28, "lng": -123.12, "radius_km": 40},
    )
    assert resp.status_code != 500


@pytest.mark.parametrize("payload", UNICODE_CHAOS)
def test_unicode_chaos_in_appraise(client, logged_in, monkeypatch, payload):
    from webapp import app as webapp_app
    monkeypatch.setattr(
        webapp_app, "get_comps",
        lambda *a, **kw: {"sample_size": 0, "median": 0, "raw_comps": []},
    )
    resp = client.post(
        "/appraise",
        json={"title": payload, "asking_price": 100},
    )
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# I5 — Path traversal in geocode q
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", PATH_TRAVERSAL)
def test_path_traversal_in_geocode(client, logged_in, monkeypatch, payload):
    """Nominatim treats traversal payloads as literal search strings.
    No file-read should ever happen; we mock the network call so this
    is purely a sanity check that the route doesn't open() the input."""
    import requests as real_requests

    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return []

    def fake_get(url, **kw):
        captured["url"] = url
        captured["params"] = kw.get("params")
        return FakeResp()

    monkeypatch.setattr(real_requests, "get", fake_get)

    resp = client.get(f"/api/geocode?q={payload}")
    assert resp.status_code != 500
    # If the call was made, q was passed as a URL parameter (literal).
    if captured:
        assert captured["params"]["q"] == payload, (
            "geocode rewrote q — possible traversal vector"
        )


# ---------------------------------------------------------------------------
# I6 — /appraise (the local proxy for /comps) body chaos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "",
    "null",
    "{}",
    '{"title": null}',
    '{"title": []}',
    '{"title":"x", "asking_price": "not-a-number"}',
    # Extra keys are ignored — must not break.
    '{"title":"x","asking_price":100,"user_id":"<other>","tier":"paid"}',
])
def test_appraise_body_chaos(client, logged_in, monkeypatch, body):
    from webapp import app as webapp_app
    monkeypatch.setattr(
        webapp_app, "get_comps",
        lambda *a, **kw: {"sample_size": 0, "median": 0, "raw_comps": []},
    )
    resp = client.post("/appraise", data=body, content_type="application/json")
    assert resp.status_code != 500, f"500 on appraise body {body!r}"
    assert resp.status_code in (200, 400, 502)


def test_appraise_huge_title(client, logged_in, monkeypatch):
    from webapp import app as webapp_app
    monkeypatch.setattr(
        webapp_app, "get_comps",
        lambda *a, **kw: {"sample_size": 0, "median": 0, "raw_comps": []},
    )
    resp = client.post(
        "/appraise",
        json={"title": "x" * 10_000, "asking_price": 100},
    )
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# I7 — Method confusion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,method,expected_405", [
    ("/api/watches", "PUT", True),
    ("/api/comps", "POST", True),
    ("/api/dashboard/summary", "POST", True),
    ("/api/geocode", "POST", True),
    ("/appraise", "GET", True),
    ("/api/subscribe", "GET", True),
])
def test_method_confusion(client, logged_in, path, method, expected_405):
    resp = client.open(path, method=method)
    if expected_405:
        assert resp.status_code in (405, 404), (
            f"{method} {path} returned {resp.status_code}, expected 405/404"
        )


# ---------------------------------------------------------------------------
# I8 — Header injection (CRLF in Authorization)
# ---------------------------------------------------------------------------

def test_header_injection_authorization(client, logged_in):
    """Werkzeug's test client sanitizes raw \\r\\n in headers. We assert
    the request fails or no injected header is reflected."""
    try:
        resp = client.get(
            "/api/dashboard/summary",
            headers={"Authorization": "Bearer abc\r\nX-Injected: true"},
        )
    except ValueError:
        # werkzeug rejects raw CRLF in headers — perfect.
        return
    # If it didn't raise, X-Injected must not appear in response.
    assert "X-Injected" not in dict(resp.headers)


# ---------------------------------------------------------------------------
# I9 — JSON depth bomb
# ---------------------------------------------------------------------------

def test_json_depth_bomb_appraise(client, logged_in):
    """Construct a 10000-deep nested JSON object. Flask's get_json
    silent=True returns None on parse failure; the route should 400,
    not 500."""
    # Build deep nesting as text so we don't hit Python's recursion
    # limit constructing it dict-by-dict.
    depth = 5000
    body = "{" + '"a":{' * depth + "}" * (depth + 1)
    resp = client.post(
        "/appraise",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code != 500


def test_json_depth_bomb_subscribe(client, logged_in):
    depth = 5000
    body = "{" + '"a":{' * depth + "}" * (depth + 1)
    resp = client.post(
        "/api/subscribe",
        data=body,
        content_type="application/json",
    )
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# I10 — /api/settings input ranges
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lat,lng,expected", [
    ("1; DROP TABLE user_settings--", -123, 400),
    (999_999, 0, 400),                 # out of range
    (-999_999, 0, 400),
    (0, 999_999, 400),
    ("not a number", 0, 400),
    (None, None, 400),
    ("", "", 400),
    (45, 45, 200),                      # valid baseline
])
def test_settings_lat_lng_validation(client, logged_in, lat, lng, expected):
    resp = client.post(
        "/api/settings",
        json={"home_latitude": lat, "home_longitude": lng},
    )
    assert resp.status_code == expected, (
        f"lat={lat!r} lng={lng!r} -> {resp.status_code}"
    )


def test_settings_email_field_huge(client, logged_in):
    """home_label is a free-text label — accept anything reasonable.
    A 1KB string should NOT trip a 500."""
    resp = client.post(
        "/api/settings",
        json={
            "home_label": "X" * 1024,
            "home_latitude": 49.28,
            "home_longitude": -123.12,
        },
    )
    assert resp.status_code in (200, 400)


# ---------------------------------------------------------------------------
# I11 — bulk-update body confusion
# ---------------------------------------------------------------------------

def test_bulk_update_empty_body(client, logged_in):
    """{} should be a noop -> 400 'no valid fields to update' (current
    behavior). We accept any non-500."""
    resp = client.post("/api/watches/bulk-update", json={})
    assert resp.status_code != 500
    assert resp.status_code in (200, 400)


def test_bulk_update_updates_not_a_list(client, logged_in):
    """Spec accepts {active, radius_km, ...} as fields, not 'updates'.
    Sending {'updates': '...'} should still 4xx (no recognized fields)."""
    resp = client.post("/api/watches/bulk-update", json={"updates": "not a list"})
    assert resp.status_code != 500
    assert resp.status_code in (200, 400)


def test_bulk_update_unknown_id_does_not_500(client, logged_in):
    """The endpoint operates field-wise across all watches; unknown ids
    aren't its model. But we send a structured payload to confirm no
    crash on weird shapes."""
    resp = client.post(
        "/api/watches/bulk-update",
        json={"updates": [{"id": 99999, "active": True}]},
    )
    assert resp.status_code != 500


def test_bulk_update_string_id(client, logged_in):
    resp = client.post(
        "/api/watches/bulk-update",
        json={"updates": [{"id": "abc"}]},
    )
    assert resp.status_code != 500


def test_bulk_update_radius_out_of_range(client, logged_in):
    """radius_km must be 1-500."""
    resp = client.post("/api/watches/bulk-update", json={"radius_km": 99999})
    assert resp.status_code == 400


def test_bulk_update_radius_negative(client, logged_in):
    resp = client.post("/api/watches/bulk-update", json={"radius_km": -5})
    assert resp.status_code == 400


def test_bulk_update_score_threshold_out_of_range(client, logged_in):
    resp = client.post(
        "/api/watches/bulk-update",
        json={"score_threshold": 200},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# I12 — CSRF / bind documentary check
# ---------------------------------------------------------------------------

def test_flask_binds_localhost_only():
    """Ensure run() pins host to 127.0.0.1, never 0.0.0.0. This is
    documentary — we read the source and assert the literal."""
    src = (
        Path(__file__).resolve().parents[2] / "src" / "webapp" / "app.py"
    ).read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in src
    assert 'host="0.0.0.0"' not in src
    assert "host='0.0.0.0'" not in src


def test_main_py_binds_localhost():
    src = (
        Path(__file__).resolve().parents[2] / "src" / "main.py"
    ).read_text(encoding="utf-8")
    assert '"127.0.0.1"' in src or "'127.0.0.1'" in src
    assert "0.0.0.0" not in src


# ---------------------------------------------------------------------------
# Side-channel: PATCH /api/watches/<int> id type confusion
# ---------------------------------------------------------------------------

def test_patch_watch_string_id_404(client, logged_in):
    """The url converter is <int:watch_id>; a non-int path matches no
    route → 404, never 500."""
    resp = client.patch("/api/watches/abc", json={"active": True})
    assert resp.status_code in (404, 405)


def test_delete_watch_huge_id(client, logged_in):
    resp = client.delete(f"/api/watches/{2**63 - 1}")
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# /api/dashboard/breakdown/<id> — listing_id is a free string
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_breakdown_listing_id_sqli(client, logged_in, monkeypatch, payload):
    """breakdown is paid-only; promote then probe."""
    from webapp import app as webapp_app
    monkeypatch.setattr(webapp_app.license_manager, "is_paid", lambda: True)
    # url-encode-safe — the route accepts any string, parameterized
    # against listings.id (TEXT). 404 expected; 500 forbidden.
    safe = payload.replace("/", "%2F")
    resp = client.get(f"/api/dashboard/breakdown/{safe}")
    assert resp.status_code != 500
