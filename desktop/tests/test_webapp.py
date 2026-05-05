"""Tests for the ported Flask webapp.

Focus areas (the rest of the route surface is exercised manually):
    - License gates: watches_limit on POST /api/watches; pro-only
      dashboards return 403 for free users; /dashboard redirects to
      /upgrade for free users.
    - Kill-switch: every route returns 503 when the cloud says we're
      too old.
    - Auth gates: HTML routes redirect to /login when no token; /api
      routes return 401.
    - The Test Appraiser endpoint requires login (it talks to cloud
      comps which needs a JWT).

We mock the singletons (`license_manager`, `token_store`) rather than
the cloud client itself — these tests don't care whether comps came
back, only whether the gate fired correctly.

Each test gets a fresh tmp SQLite DB via the `bullseye_db` fixture so
we can pre-populate watches without polluting other tests. The fixture
also runs migrations so the schema's there.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# --- Fixtures -------------------------------------------------------------

@pytest.fixture
def bullseye_db(tmp_path, monkeypatch):
    """Fresh SQLite DB pointed at a temp file, schema applied."""
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
    """Flask test client. Imports happen inside the fixture so the
    BULLSEYE_DB_PATH monkeypatch lands before any module captures it."""
    from webapp import app as webapp_app
    webapp_app.app.config["TESTING"] = True
    with webapp_app.app.test_client() as c:
        yield c


@pytest.fixture
def logged_in(monkeypatch):
    """Simulate a logged-in user. Patches token_store.is_logged_in →
    True everywhere callers consult it."""
    from deal_finder.auth import token_store
    from webapp import app as webapp_app
    monkeypatch.setattr(token_store, "is_logged_in", lambda: True)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: True)
    # auth_routes imports token_store at module scope; patch the bound
    # attribute too so /login redirects correctly when needed.
    from webapp import auth_routes as ar
    monkeypatch.setattr(ar.token_store, "is_logged_in", lambda: True)


@pytest.fixture
def logged_out(monkeypatch):
    from deal_finder.auth import token_store
    from webapp import app as webapp_app
    monkeypatch.setattr(token_store, "is_logged_in", lambda: False)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: False)


def _set_license(monkeypatch, *, tier="free", watches_limit=3,
                 paid=False, kill_switched=False):
    """Patch the license_manager singleton's public API for a test."""
    from webapp import app as webapp_app
    lm = webapp_app.license_manager
    monkeypatch.setattr(lm, "is_paid", lambda: paid)
    monkeypatch.setattr(lm, "tier", lambda: tier)
    monkeypatch.setattr(lm, "watches_limit", lambda: watches_limit)
    monkeypatch.setattr(lm, "is_kill_switched", lambda: kill_switched)
    monkeypatch.setattr(lm, "get", lambda **kw: {"tier": tier})


# --- Watches limit --------------------------------------------------------

def test_watches_limit_enforced(client, logged_in, monkeypatch, bullseye_db):
    """A free-tier user with 3 watches gets 403 when trying to add a 4th."""
    _set_license(monkeypatch, paid=False, watches_limit=3)

    # Pre-seed 3 active watches.
    from deal_finder.db.connection import get_connection
    conn = get_connection()
    with conn:
        for kw in ("aaa", "bbb", "ccc"):
            conn.execute(
                "INSERT INTO user_searches (keyword, latitude, longitude, "
                "radius_km, active) VALUES (?, 49.28, -123.12, 40, 1)",
                (kw,),
            )

    resp = client.post(
        "/api/watches",
        json={"keyword": "ddd", "lat": 49.28, "lng": -123.12, "radius_km": 40},
    )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["error"] == "watches_limit_reached"
    assert body["limit"] == 3


def test_watches_limit_allows_under_cap(client, logged_in, monkeypatch, bullseye_db):
    """Same user under the cap can add a watch."""
    _set_license(monkeypatch, paid=False, watches_limit=3)

    resp = client.post(
        "/api/watches",
        json={"keyword": "first", "lat": 49.28, "lng": -123.12, "radius_km": 40},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["keyword"] == "first"


def test_watches_limit_unlimited_for_paid(client, logged_in, monkeypatch, bullseye_db):
    """Paid users have watches_limit() == None; the gate skips."""
    _set_license(monkeypatch, paid=True, watches_limit=None)

    from deal_finder.db.connection import get_connection
    conn = get_connection()
    with conn:
        for kw in ("a", "b", "c", "d", "e"):
            conn.execute(
                "INSERT INTO user_searches (keyword, latitude, longitude, "
                "radius_km, active) VALUES (?, 49.28, -123.12, 40, 1)",
                (kw,),
            )

    resp = client.post(
        "/api/watches",
        json={"keyword": "sixth", "lat": 49.28, "lng": -123.12, "radius_km": 40},
    )
    assert resp.status_code == 200


# --- Kill switch ----------------------------------------------------------

def test_kill_switch_returns_503(client, logged_in, monkeypatch):
    """When the cloud says our version is too old, every route 503s."""
    _set_license(monkeypatch, kill_switched=True)

    # API path returns JSON 503
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["error"] == "kill_switched"
    assert "update" in body["message"].lower()

    # HTML path returns text 503
    resp = client.get("/")
    assert resp.status_code == 503
    assert b"update" in resp.data.lower()


def test_kill_switch_lets_logout_through(client, logged_in, monkeypatch):
    """The /logout route stays accessible so a stuck user can clear
    bad tokens even when kill-switched."""
    _set_license(monkeypatch, kill_switched=True)

    # token_store.clear() touches the OS keychain via `keyring`; in
    # tests that module isn't installed, so stub clear() too.
    from deal_finder.auth import token_store
    monkeypatch.setattr(token_store, "clear", lambda: None)

    # /logout should redirect (302), not 503
    resp = client.post("/logout")
    assert resp.status_code in (302, 303)


# --- /dashboard redirects free users -------------------------------------

def test_dashboard_redirects_free_user_to_upgrade(client, logged_in, monkeypatch):
    _set_license(monkeypatch, paid=False)
    resp = client.get("/dashboard")
    assert resp.status_code in (301, 302, 303, 307, 308)
    assert "/upgrade" in resp.headers.get("Location", "")


def test_dashboard_serves_paid_user(client, logged_in, monkeypatch):
    _set_license(monkeypatch, paid=True)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower()


# --- Pro-only API endpoints ----------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/dashboard/breakdown/abc123",
    "/api/dashboard/per-watch",
    "/api/dashboard/score-histogram",
])
def test_pro_only_endpoints_403_for_free(client, logged_in, monkeypatch, path):
    _set_license(monkeypatch, paid=False)
    resp = client.get(path)
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["error"] == "upgrade_required"


@pytest.mark.parametrize("path", [
    "/api/dashboard/per-watch",
    "/api/dashboard/score-histogram",
])
def test_pro_only_endpoints_200_for_paid(client, logged_in, monkeypatch, path):
    _set_license(monkeypatch, paid=True)
    resp = client.get(path)
    assert resp.status_code == 200


# --- /appraise requires login --------------------------------------------

def test_appraise_requires_login(client, logged_out, monkeypatch):
    _set_license(monkeypatch, paid=False)
    resp = client.post("/appraise", json={"title": "iphone 14", "asking_price": 500})
    assert resp.status_code == 401


def test_appraise_works_for_logged_in_user(client, logged_in, monkeypatch):
    """The cloud comps call is mocked; the test covers the route's
    plumbing only (auth, JSON shape, score math at low sample size)."""
    _set_license(monkeypatch, paid=False)
    fake_comp = {
        "search_term": "iphone 14",
        "region": "EBAY-ENCA",
        "source": "ebay_fresh",
        "sample_size": 0,
        "median": 0,
        "raw_comps": [],
    }
    from webapp import app as webapp_app
    monkeypatch.setattr(webapp_app, "get_comps", lambda *a, **kw: fake_comp)

    resp = client.post("/appraise", json={"title": "iphone 14", "asking_price": 500})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    # Empty sample → unscoreable, not a crash.
    assert body["unscoreable"] is True


# --- Login required for /api/watches -------------------------------------

def test_login_required_for_api_watches(client, logged_out, monkeypatch):
    _set_license(monkeypatch, paid=False)
    # GET
    assert client.get("/api/watches").status_code == 401
    # POST
    resp = client.post("/api/watches", json={"keyword": "test"})
    assert resp.status_code == 401


# --- HTML pages: / is public, /dashboard requires login -------------------

def test_index_public_for_logged_out(client, logged_out, monkeypatch):
    """The front page is the acquisition surface — must render even
    when the user isn't signed in. The template just shows different
    CTAs."""
    _set_license(monkeypatch, paid=False)
    resp = client.get("/")
    assert resp.status_code == 200


def test_dashboard_redirects_logged_out_to_login(client, logged_out, monkeypatch):
    _set_license(monkeypatch, paid=False)
    resp = client.get("/dashboard")
    assert resp.status_code in (301, 302, 303, 307, 308)
    assert "/login" in resp.headers.get("Location", "")


# --- /api/dashboard/summary works for free users -------------------------

def test_dashboard_summary_works_for_free(client, logged_in, monkeypatch):
    """The summary endpoint is the basic-overview tile — free tier
    sees it. Returns 200 even with an empty DB."""
    _set_license(monkeypatch, paid=False)
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "alive" in body
    assert "funnel_today" in body
    assert body["tier"] == "free"
    assert body["is_paid"] is False
