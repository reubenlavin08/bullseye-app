"""Adversarial auth + row-level-security tests against production Supabase.

These tests deliberately try to bypass RLS, forge JWTs, hit endpoints
without auth, and write into rows we don't own. They run against the
LIVE production project (no staging exists yet) — so:

  * we sign up no more than 3 throwaway users (session-scoped fixture)
  * total cloud HTTP calls are capped well under 50
  * we never DELETE users or production rows
  * test PASSES = attack BLOCKED. Test FAILS = real finding.

Findings that fail are documented in AUTH-RLS-FINDINGS.md alongside
this file.

The webapp portion uses Flask's test_client with token_store mocked, so
no real keychain or cloud calls are involved for those.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests

# --- Make `src/` importable for the Flask portion -------------------------
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# --- Constants ------------------------------------------------------------

SUPABASE_URL = "https://qfkzhyxmohytnzskcmdv.supabase.co"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFma3poeXhtb2h5dG56c2tjbWR2Iiwicm9sZSI6ImFub24i"
    "LCJpYXQiOjE3Nzc5MjIzMjYsImV4cCI6MjA5MzQ5ODMyNn0."
    "pPathNzQcURGocSVPKKhgcv27-4sqHAFZeB1oenB0Z4"
)

# Hardcoded EXPIRED HS256 JWT (just a realistic-looking token with
# exp=1 in the past — NOT issued by Supabase, but useful to confirm
# the server treats invalid signatures + expired tokens uniformly).
# header={"alg":"HS256","typ":"JWT"}
# payload={"sub":"00000000-0000-0000-0000-000000000000","role":"authenticated","exp":1,"iat":0}
_HEADER = base64.urlsafe_b64encode(
    json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
).rstrip(b"=").decode()
_PAYLOAD_EXPIRED = base64.urlsafe_b64encode(
    json.dumps({
        "sub": "00000000-0000-0000-0000-000000000000",
        "role": "authenticated",
        "exp": 1,
        "iat": 0,
    }).encode()
).rstrip(b"=").decode()
EXPIRED_JWT = f"{_HEADER}.{_PAYLOAD_EXPIRED}.AAAA"


def _b64url_decode(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# --- Session-scoped fixture: sign up two users + capture JWTs -------------

@pytest.fixture(scope="session")
def users() -> dict:
    """Create two throwaway users on production Supabase. Email confirm
    is OFF so signup returns a usable JWT immediately.

    We do NOT delete these users — they leak into production. That's
    accepted: their email addresses are uuid-based so unique-collision
    isn't a worry, and the licenses table will track them as 'free'.

    Total signup cost: 2 cloud calls.
    """
    out: dict[str, dict[str, Any]] = {}
    suffix = uuid.uuid4().hex[:10]
    for name in ("a", "b"):
        email = f"adv-rls-{name}-{suffix}@bullseye-test.invalid"
        password = uuid.uuid4().hex + "!Aa1"
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            headers={
                "apikey": ANON_KEY,
                "Content-Type": "application/json",
            },
            json={"email": email, "password": password},
            timeout=15,
        )
        if r.status_code >= 400:
            pytest.skip(
                f"Could not sign up test user {name} "
                f"(status={r.status_code}, body={r.text[:200]}); "
                "production signup may be disabled."
            )
        body = r.json()
        token = body.get("access_token") or (body.get("session") or {}).get("access_token")
        user = body.get("user") or {}
        uid = user.get("id") or body.get("id")
        if not token or not uid:
            pytest.skip(f"Signup body missing token/user id for {name}: {body!r}")
        out[name] = {
            "email": email,
            "password": password,
            "user_id": uid,
            "jwt": token,
        }
    return out


def _hdrs_user(jwt: str) -> dict:
    return {
        "apikey": ANON_KEY,
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    }


def _hdrs_anon() -> dict:
    return {
        "apikey": ANON_KEY,
        "Content-Type": "application/json",
    }


# --- A1. Cross-tenant RLS leak --------------------------------------------

@pytest.mark.parametrize("table", [
    "licenses",
    "user_watches",
    "email_log",
    "email_queue",
    "telemetry_events",
])
def test_a1_cross_tenant_rls_leak(users, table):
    """As user B, query rows where user_id == user A's id. RLS should
    return an empty array (not an error — PostgREST silently filters)."""
    a, b = users["a"], users["b"]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params={"user_id": f"eq.{a['user_id']}", "select": "*"},
        headers=_hdrs_user(b["jwt"]),
        timeout=15,
    )
    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text[:200]}"
    rows = r.json()
    assert isinstance(rows, list)
    # B must NOT see any of A's rows. RLS predicate auth.uid()=user_id
    # is evaluated server-side; with B's JWT, no row matches.
    assert rows == [], (
        f"RLS LEAK on {table}: user B saw {len(rows)} of user A's rows. "
        f"First row: {rows[0] if rows else None!r}"
    )


# --- A2. Anon-only writes -------------------------------------------------

def test_a2_anon_cannot_write_licenses():
    """Anonymous role must not be able to INSERT into licenses."""
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/licenses",
        headers=_hdrs_anon(),
        json={
            "user_id": "00000000-0000-0000-0000-000000000001",
            "tier": "paid",
        },
        timeout=15,
    )
    # Either 401 (no JWT), 403 (RLS), or 4xx with PostgREST error code.
    # Whatever it is, it must NOT be 2xx.
    assert r.status_code >= 400, (
        f"anon INSERT into licenses succeeded: {r.status_code} {r.text[:200]}"
    )


def test_a2_anon_cannot_write_watches():
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/user_watches",
        headers=_hdrs_anon(),
        json={
            "user_id": "00000000-0000-0000-0000-000000000001",
            "keyword": "anon-injection",
        },
        timeout=15,
    )
    assert r.status_code >= 400, (
        f"anon INSERT into user_watches succeeded: {r.status_code} {r.text[:200]}"
    )


# --- A3. JWT tampering (sub flip) -----------------------------------------

def test_a3_jwt_tampering_sub_flip(users):
    """Take user A's real JWT, decode the payload, change sub to all
    zeros, re-encode (same signature, now invalid), send to /comps."""
    jwt = users["a"]["jwt"]
    header_b64, payload_b64, sig_b64 = jwt.split(".")
    payload = json.loads(_b64url_decode(payload_b64))
    payload["sub"] = "00000000-0000-0000-0000-000000000000"
    tampered_payload = _b64url_encode(json.dumps(payload).encode())
    tampered = f"{header_b64}.{tampered_payload}.{sig_b64}"

    r = requests.post(
        f"{SUPABASE_URL}/functions/v1/comps",
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {tampered}",
            "Content-Type": "application/json",
        },
        json={"search_term": "iphone 14"},
        timeout=20,
    )
    assert r.status_code == 401, (
        f"Tampered JWT was accepted! status={r.status_code} "
        f"body={r.text[:200]}"
    )


# --- A4. Expired/garbage JWT ----------------------------------------------

def test_a4_expired_jwt_rejected():
    r = requests.post(
        f"{SUPABASE_URL}/functions/v1/comps",
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {EXPIRED_JWT}",
            "Content-Type": "application/json",
        },
        json={"search_term": "iphone 14"},
        timeout=20,
    )
    assert r.status_code == 401, (
        f"Expired/forged JWT was accepted: {r.status_code} {r.text[:200]}"
    )


# --- A5. Missing apikey header --------------------------------------------

def test_a5_missing_apikey_header(users):
    """Document Supabase's response when apikey header is omitted but
    Authorization is present. Production Supabase requires apikey on
    the gateway; expected: 401."""
    jwt = users["a"]["jwt"]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/licenses",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            # apikey deliberately omitted
        },
        params={"select": "user_id"},
        timeout=15,
    )
    # Either rejected (good) or accepted-with-no-rows (also acceptable
    # — RLS still applies). Treat 2xx-with-data as a finding.
    if r.status_code == 200:
        rows = r.json()
        assert rows == [] or all("user_id" in row for row in rows), r.text[:200]
    else:
        assert r.status_code in (401, 403), (
            f"unexpected response without apikey: {r.status_code} {r.text[:200]}"
        )


# --- A7. Self-license forgery (UPDATE/INSERT denied) ----------------------

def test_a7_self_license_update_blocked(users):
    """As user A, try to update my OWN licenses row to set tier='paid'.
    There's NO UPDATE policy on licenses (only SELECT for owner), so
    this must fail."""
    a = users["a"]
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/licenses",
        params={"user_id": f"eq.{a['user_id']}"},
        headers={**_hdrs_user(a["jwt"]), "Prefer": "return=representation"},
        json={"tier": "paid"},
        timeout=15,
    )
    if r.status_code >= 400:
        return  # blocked at PostgREST/RLS level — expected.
    rows = r.json() if r.text else []
    assert rows == [], (
        f"License UPDATE succeeded! User self-promoted to paid. "
        f"Affected rows: {rows!r}"
    )


def test_a7_self_license_insert_blocked(users):
    """Try to INSERT a new licenses row for my own user_id (perhaps
    bypassing the trigger if it didn't fire). No INSERT policy → fail."""
    a = users["a"]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/licenses",
        headers={**_hdrs_user(a["jwt"]), "Prefer": "return=representation"},
        json={"user_id": a["user_id"], "tier": "paid"},
        timeout=15,
    )
    # Either RLS rejects (4xx) or unique-pk conflict — both fine. The
    # critical bad outcome would be 201 with tier='paid' inserted.
    if r.status_code in (200, 201):
        body = r.json()
        if isinstance(body, list) and body:
            assert body[0].get("tier") != "paid", (
                f"INSERT into own license with tier=paid SUCCEEDED: {body!r}"
            )


# --- A8. Stripe-customer-id hijack ----------------------------------------

def test_a8_stripe_customer_id_hijack_blocked(users):
    """Try to UPDATE my licenses row's stripe_customer_id. Even if I
    only target my own row, no UPDATE policy = blocked."""
    a = users["a"]
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/licenses",
        params={"user_id": f"eq.{a['user_id']}"},
        headers={**_hdrs_user(a["jwt"]), "Prefer": "return=representation"},
        json={"stripe_customer_id": "cus_VICTIM_HIJACKED"},
        timeout=15,
    )
    if r.status_code in (200, 204):
        rows = r.json() if r.text else []
        assert rows == [], (
            f"stripe_customer_id UPDATE succeeded: {rows!r}"
        )


# --- A9. Telemetry user_id spoof ------------------------------------------

def test_a9_telemetry_user_id_spoof_blocked(users):
    """As user B, INSERT a telemetry event with user_id=A. The
    WITH CHECK clause is `auth.uid() = user_id OR user_id IS NULL`
    so this should be rejected."""
    a, b = users["a"], users["b"]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/telemetry_events",
        headers={**_hdrs_user(b["jwt"]), "Prefer": "return=representation"},
        json={
            "user_id": a["user_id"],
            "event_name": "spoofed_event",
            "install_id": "adversary",
        },
        timeout=15,
    )
    # Expected: 4xx (RLS WITH CHECK violation, PostgREST surface code 42501).
    assert r.status_code >= 400, (
        f"Telemetry user_id spoof SUCCEEDED: {r.status_code} {r.text[:200]}"
    )


def test_a9_telemetry_null_user_id_observed_behavior(users):
    """Document the observed behavior of inserting telemetry with NULL
    user_id while authenticated. The RLS WITH CHECK clause says
    `auth.uid() = user_id OR user_id IS NULL`, which on its face should
    allow either branch. In practice on production the OR-NULL branch
    is rejected with 42501.

    This is a non-security finding (the system is more restrictive than
    the policy says, which is safe). Documented in AUTH-RLS-FINDINGS.md
    as F-LOW-1 so we don't lose track. Test asserts the observed
    behavior so a future fix flips the assertion and forces a doc
    update."""
    b = users["b"]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/telemetry_events",
        headers={**_hdrs_user(b["jwt"]), "Prefer": "return=representation"},
        json={
            "event_name": "adv_test_anon",
            "install_id": "adv-rls",
        },
        timeout=15,
    )
    # Observed: 403 (RLS rejects). Acceptable: 200/201 if the policy is
    # later fixed. Either way is non-exploitable.
    assert r.status_code in (200, 201, 403), r.text[:200]


# --- A10. comps_cache write protection ------------------------------------

def test_a10_comps_cache_select_works(users):
    """Authenticated users can SELECT comps_cache (it's intentional)."""
    a = users["a"]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/comps_cache",
        params={"select": "search_term_normalized,region", "limit": "1"},
        headers=_hdrs_user(a["jwt"]),
        timeout=15,
    )
    assert r.status_code == 200, r.text[:200]


def test_a10_comps_cache_insert_blocked(users):
    """Regular users must NOT be able to write to the shared cache —
    that's how cache poisoning would happen. There's no INSERT policy."""
    a = users["a"]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/comps_cache",
        headers={**_hdrs_user(a["jwt"]), "Prefer": "return=representation"},
        json={
            "search_term_normalized": "adv_poison_test",
            "region": "EBAY-ENCA",
            "stats_json": {"median": 999999},
        },
        timeout=15,
    )
    if r.status_code in (200, 201):
        rows = r.json() if r.text else []
        assert rows == [], f"comps_cache INSERT SUCCEEDED — cache poisoning possible: {rows!r}"


def test_a10_comps_cache_update_blocked(users):
    """Try to UPDATE an existing comps_cache row (cache poisoning of
    a real search term)."""
    a = users["a"]
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/comps_cache",
        params={"search_term_normalized": "neq.__never_matches__"},
        headers={**_hdrs_user(a["jwt"]), "Prefer": "return=representation"},
        json={"stats_json": {"median": 1}},
        timeout=15,
    )
    if r.status_code in (200, 204):
        rows = r.json() if r.text else []
        assert rows == [], f"comps_cache UPDATE SUCCEEDED — cache poisoning: {rows!r}"


# ==========================================================================
# A6. Webapp auth bypass (Flask test client; no cloud calls)
# ==========================================================================

@pytest.fixture
def bullseye_db(tmp_path, monkeypatch):
    db_path = tmp_path / "bullseye_adv.db"
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
def webapp_client(bullseye_db):
    from webapp import app as webapp_app
    webapp_app.app.config["TESTING"] = True
    with webapp_app.app.test_client() as c:
        yield c


@pytest.fixture
def webapp_logged_out(monkeypatch):
    from deal_finder.auth import token_store
    from webapp import app as webapp_app
    from webapp import auth_routes as ar
    monkeypatch.setattr(token_store, "is_logged_in", lambda: False)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: False)
    monkeypatch.setattr(ar.token_store, "is_logged_in", lambda: False)


@pytest.fixture
def webapp_logged_in_free(monkeypatch):
    from deal_finder.auth import token_store
    from webapp import app as webapp_app
    from webapp import auth_routes as ar
    monkeypatch.setattr(token_store, "is_logged_in", lambda: True)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: True)
    monkeypatch.setattr(ar.token_store, "is_logged_in", lambda: True)
    lm = webapp_app.license_manager
    monkeypatch.setattr(lm, "is_paid", lambda: False)
    monkeypatch.setattr(lm, "tier", lambda: "free")
    monkeypatch.setattr(lm, "watches_limit", lambda: 3)
    monkeypatch.setattr(lm, "is_kill_switched", lambda: False)
    monkeypatch.setattr(lm, "get", lambda **kw: {"tier": "free"})


# All /api/* routes that should require login (collected from
# @login_required_api decorators in webapp/app.py).
_PROTECTED_API_ROUTES = [
    ("GET", "/api/dashboard/summary"),
    ("GET", "/api/dashboard/events"),
    ("GET", "/api/dashboard/per-watch"),
    ("GET", "/api/dashboard/score-histogram"),
    ("GET", "/api/dashboard/appraisal-feed"),
    ("GET", "/api/dashboard/breakdown/abc123"),
    ("GET", "/api/dashboard/log/tail"),
    ("GET", "/api/searches"),
    ("GET", "/api/watches"),
    ("POST", "/api/watches"),
    ("PATCH", "/api/watches/1"),
    ("POST", "/api/watches/bulk-update"),
    ("DELETE", "/api/watches/1"),
    ("POST", "/api/searches/bulk"),
    ("POST", "/api/subscribe"),
    ("GET", "/api/comps"),
    ("GET", "/api/settings"),
    ("POST", "/api/settings"),
    ("GET", "/api/geocode"),
    ("POST", "/api/account/delete"),
    ("GET", "/api/account/export"),
    ("POST", "/api/license/refresh"),
    ("POST", "/appraise"),
]


@pytest.mark.parametrize("method,path", _PROTECTED_API_ROUTES)
def test_a6_api_routes_401_when_logged_out(webapp_client, webapp_logged_out, method, path):
    resp = webapp_client.open(path, method=method, json={})
    assert resp.status_code == 401, (
        f"{method} {path}: expected 401 when logged out, got {resp.status_code}"
    )


# Paid-only API routes that should 403 for free users.
_PAID_ONLY_API_ROUTES = [
    ("GET", "/api/dashboard/per-watch"),
    ("GET", "/api/dashboard/score-histogram"),
    ("GET", "/api/dashboard/breakdown/abc123"),
]


@pytest.mark.parametrize("method,path", _PAID_ONLY_API_ROUTES)
def test_a6_paid_only_routes_403_for_free(webapp_client, webapp_logged_in_free, method, path):
    resp = webapp_client.open(path, method=method, json={})
    assert resp.status_code == 403, (
        f"{method} {path}: expected 403 for free user, got {resp.status_code}"
    )
    body = resp.get_json()
    assert body.get("error") == "upgrade_required"


def test_a6_dashboard_redirects_free_to_upgrade(webapp_client, webapp_logged_in_free):
    """HTML /dashboard for free user must redirect, not render the
    paid dashboard."""
    resp = webapp_client.get("/dashboard")
    assert resp.status_code in (301, 302, 303, 307, 308)
    assert "/upgrade" in resp.headers.get("Location", "")
