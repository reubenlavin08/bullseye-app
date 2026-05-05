"""Adversarial license/tier-enforcement probes.

These tests intentionally try to bypass the cap, the kill switch, the
paid-only API gates, and the trial expiry. Each named L<n> follows the
attack list in `LICENSE-FINDINGS.md`.

Mocks `license_manager` and `token_store` like `test_webapp.py` does;
nothing here hits the real cloud (with one exception that's documented
in the per-test comment). SQLite is a tmp file via the shared fixture
pattern.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# --- Fixtures (mirrors test_webapp.py) ------------------------------------

@pytest.fixture
def bullseye_db(tmp_path, monkeypatch):
    db_path = tmp_path / "adv_bullseye.db"
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


def _set_license(monkeypatch, *, tier="free", watches_limit=3,
                 paid=False, kill_switched=False):
    from webapp import app as webapp_app
    lm = webapp_app.license_manager
    monkeypatch.setattr(lm, "is_paid", lambda: paid)
    monkeypatch.setattr(lm, "tier", lambda: tier)
    monkeypatch.setattr(lm, "watches_limit", lambda: watches_limit)
    monkeypatch.setattr(lm, "is_kill_switched", lambda: kill_switched)
    monkeypatch.setattr(lm, "get", lambda **kw: {"tier": tier})


def _seed_active(n: int, prefix: str = "kw"):
    from deal_finder.db.connection import get_connection
    conn = get_connection()
    with conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO user_searches (keyword, latitude, longitude, "
                "radius_km, active) VALUES (?, 49.28, -123.12, 40, 1)",
                (f"{prefix}_{i}",),
            )


# ---------------------------------------------------------------------------
# L1 — Race on watches_limit (TOCTOU between SELECT and INSERT)
# ---------------------------------------------------------------------------

def test_L1_race_watches_limit_concurrent_post(
    logged_in, monkeypatch, bullseye_db,
):
    """Free user with 2 active watches (cap=3). Two concurrent POSTs
    /api/watches both pass the SELECT-then-INSERT gate. The vulnerable
    pattern would let both rows write, putting actives = 4 (over cap).

    Uses per-thread test clients to avoid sharing a Flask request
    context across threads (which Flask's testing harness disallows).
    """
    _set_license(monkeypatch, paid=False, watches_limit=3)
    _seed_active(2, prefix="seed")

    from webapp import app as webapp_app
    webapp_app.app.config["TESTING"] = True

    barrier = threading.Barrier(2)
    results: list[int] = []
    lock = threading.Lock()

    def hammer(idx: int):
        with webapp_app.app.test_client() as c:
            barrier.wait()
            resp = c.post(
                "/api/watches",
                json={"keyword": f"race_{idx}", "lat": 49.28,
                      "lng": -123.12, "radius_km": 40},
            )
            with lock:
                results.append(resp.status_code)

    t1 = threading.Thread(target=hammer, args=(1,))
    t2 = threading.Thread(target=hammer, args=(2,))
    t1.start(); t2.start(); t1.join(); t2.join()

    # Final actives must not exceed the cap of 3.
    from deal_finder.db.connection import get_connection
    conn = get_connection()
    actives = conn.execute(
        "SELECT COUNT(*) FROM user_searches WHERE active = 1",
    ).fetchone()[0]
    assert actives <= 3, (
        f"RACE BREACH: {actives} active watches with cap=3. "
        f"HTTP results: {results}"
    )


# ---------------------------------------------------------------------------
# L2 — Bulk endpoint cap: 0 watches, 10 keywords, free tier (cap=3)
# ---------------------------------------------------------------------------

def test_L2_bulk_truncates_at_cap(client, logged_in, monkeypatch, bullseye_db):
    _set_license(monkeypatch, paid=False, watches_limit=3)

    keywords = ",".join(f"bulk_{i}" for i in range(10))
    resp = client.post(
        "/api/searches/bulk",
        json={"keywords": keywords, "lat": 49.28, "lng": -123.12,
              "radius_km": 40},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["created"]) == 3, (
        f"BREACH: bulk created {len(body['created'])} (expected 3)"
    )
    assert body["truncated_at_limit"] is True
    assert body["limit"] == 3

    from deal_finder.db.connection import get_connection
    conn = get_connection()
    actives = conn.execute(
        "SELECT COUNT(*) FROM user_searches WHERE active = 1",
    ).fetchone()[0]
    assert actives == 3


# ---------------------------------------------------------------------------
# L3 — Pause-create-unpause cycle
# ---------------------------------------------------------------------------

def test_L3_pause_create_unpause_cap_check(
    client, logged_in, monkeypatch, bullseye_db,
):
    """Cap=3. Pause one (2 active), create a new one (3 active, 1 paused),
    then unpause the paused one via PATCH. The PATCH-active=true endpoint
    must re-check the cap or the user lands at 4 active.
    """
    _set_license(monkeypatch, paid=False, watches_limit=3)
    _seed_active(3, prefix="L3")

    from deal_finder.db.connection import get_connection
    conn = get_connection()
    paused_id = conn.execute(
        "SELECT id FROM user_searches WHERE keyword = 'L3_0'",
    ).fetchone()[0]

    # 1. Pause one.
    r = client.patch(f"/api/watches/{paused_id}", json={"active": False})
    assert r.status_code == 200

    # 2. Create a new active one (now 3 active, 1 paused).
    r = client.post(
        "/api/watches",
        json={"keyword": "fresh", "lat": 49.28, "lng": -123.12, "radius_km": 40},
    )
    assert r.status_code == 200, r.get_json()

    # 3. Unpause the paused one. This is the moment of truth.
    r = client.patch(f"/api/watches/{paused_id}", json={"active": True})

    # After fix: 403 because we're already at cap. Before fix: 200.
    actives = conn.execute(
        "SELECT COUNT(*) FROM user_searches WHERE active = 1",
    ).fetchone()[0]
    assert actives <= 3, (
        f"BREACH: pause-create-unpause yields {actives} active watches. "
        f"PATCH unpause returned {r.status_code}: {r.get_json()}"
    )
    assert r.status_code == 403
    assert r.get_json()["error"] == "watches_limit_reached"


# ---------------------------------------------------------------------------
# L4 — Soft-delete reactivation
# ---------------------------------------------------------------------------

def test_L4_delete_is_hard_no_reactivate_path(
    client, logged_in, monkeypatch, bullseye_db,
):
    """DELETE /api/watches/<id> is a hard delete (RETURNING keyword
    confirms the row is gone). No `deleted_at` column to revive.
    Documented in findings: not soft-delete, can't be reactivated.
    """
    _set_license(monkeypatch, paid=False, watches_limit=3)
    _seed_active(1, prefix="del")

    from deal_finder.db.connection import get_connection
    conn = get_connection()
    wid = conn.execute(
        "SELECT id FROM user_searches WHERE keyword = 'del_0'",
    ).fetchone()[0]

    r = client.delete(f"/api/watches/{wid}")
    assert r.status_code == 200

    row = conn.execute(
        "SELECT id FROM user_searches WHERE id = ?", (wid,),
    ).fetchone()
    assert row is None, "row should be gone — DELETE is hard, not soft"

    r = client.patch(f"/api/watches/{wid}", json={"active": True})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# L5 — Kill switch sticky-on-offline
# ---------------------------------------------------------------------------

def test_L5_kill_switch_sticky_when_cloud_unavailable(tmp_path):
    """Once a kill-switch is observed, a transient cloud outage MUST NOT
    revive the deprecated client. Failing open here would let users keep
    using a build the team is trying to retire.
    """
    from deal_finder.cloud.client import CloudUnavailable
    from deal_finder.db import connection as conn_mod
    from deal_finder.db import migrate
    from deal_finder.license import manager as mgr

    db_path = tmp_path / "L5.db"
    conn_mod.reset_for_tests()
    conn_mod.set_db_path(str(db_path))
    migrate.run_migrations()
    try:
        lm = mgr.LicenseManager()

        # 1. Cloud says we're too old.
        kill_resp = {
            "tier": "free",
            "watches_limit": 3,
            "poll_interval_min": 30,
            "expires_at": None,
            "trial_ends_at": None,
            "cancel_at_period_end": False,
            "min_supported_version": "99.0.0",
        }
        with patch.object(mgr.client, "post", return_value=kill_resp):
            assert lm.is_kill_switched() is True

        # 2. Cloud goes down. Cached value still says kill-switched.
        with patch.object(mgr.client, "post",
                          side_effect=CloudUnavailable("offline")):
            assert lm.is_kill_switched() is True, (
                "BREACH: kill-switch dropped on CloudUnavailable. "
                "User can keep running a deprecated build offline."
            )

        # 3. Force a refresh while cloud is still down — fallback should
        #    still hold the kill-switch True (in-memory cache wins, then
        #    SQLite mirror).
        with patch.object(mgr.client, "post",
                          side_effect=CloudUnavailable("offline")):
            lm.get(force_refresh=True)
            assert lm.is_kill_switched() is True
    finally:
        conn_mod.close_thread_connection()
        conn_mod.set_db_path(None)


# ---------------------------------------------------------------------------
# L6 — Trial re-redemption via re-signup (policy doc only)
# ---------------------------------------------------------------------------

def test_L6_trial_re_signup_policy_documented():
    """Reads the cloud /license source to confirm trial is keyed off the
    `licenses` row, which is created by an on-signup trigger. If a user
    deletes their account and re-signs-up with the same email, Supabase
    creates a new auth.users row → new licenses row → fresh trial.

    This is by design (the cloud has no anti-reuse table keyed by email).
    Documented as a policy gap in LICENSE-FINDINGS.md, severity LOW.
    """
    src = Path(__file__).resolve().parents[3] / (
        "cloud/supabase/functions/license/index.ts"
    )
    txt = src.read_text(encoding="utf-8")
    # No anti-replay/email-history check exists today.
    assert "trial_redemption_history" not in txt
    assert "previous_trial" not in txt
    # `licenses` is keyed by user_id (a fresh auth row gives a fresh id).
    assert ".eq(\"user_id\", user.id)" in txt


# ---------------------------------------------------------------------------
# L7 — Concurrent license fetches
# ---------------------------------------------------------------------------

def test_L7_concurrent_license_fetches_consistent(tmp_path):
    """Two threads call get() with empty cache. The lock means at most
    one cloud call should be in flight; both threads must get a coherent
    dict and SQLite must not see a double write that breaks JSON.
    """
    from deal_finder.db import connection as conn_mod
    from deal_finder.db import migrate
    from deal_finder.license import manager as mgr

    db_path = tmp_path / "L7.db"
    conn_mod.reset_for_tests()
    conn_mod.set_db_path(str(db_path))
    migrate.run_migrations()
    try:
        lm = mgr.LicenseManager()

        gate = threading.Event()
        call_count = {"n": 0}

        def slow_post(*a, **kw):
            call_count["n"] += 1
            gate.wait(timeout=2.0)
            return {
                "tier": "paid",
                "watches_limit": None,
                "poll_interval_min": 5,
                "expires_at": None,
                "trial_ends_at": None,
                "cancel_at_period_end": False,
                "min_supported_version": "0.1.0",
            }

        results: list[dict] = []
        errors: list[BaseException] = []

        def worker():
            try:
                results.append(lm.get())
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        with patch.object(mgr.client, "post", side_effect=slow_post):
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start(); t2.start()
            # Let both threads pile up on the lock, then release.
            gate.set()
            t1.join(); t2.join()

        assert errors == []
        assert len(results) == 2
        assert results[0] == results[1]
        # The lock serializes; the second caller sees the cache.
        assert call_count["n"] == 1, (
            f"BREACH: {call_count['n']} cloud calls (expected 1) — lock "
            "isn't holding through the cloud round-trip."
        )

        # SQLite mirror is parseable.
        from deal_finder.db.connection import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = 'cached_license_v1'",
        ).fetchone()
        assert row is not None
        import json as _json
        loaded = _json.loads(row["value"])
        assert loaded["tier"] == "paid"
    finally:
        conn_mod.close_thread_connection()
        conn_mod.set_db_path(None)


# ---------------------------------------------------------------------------
# L8 — Garbage min_version: must fail open (not kill-switch)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_v", ["not.a.version", "", None, "abc.def.ghi",
                                    "1.x.0", "v0.1.0", "  "])
def test_L8_garbage_min_version_fails_open(tmp_path, bad_v):
    from deal_finder.db import connection as conn_mod
    from deal_finder.db import migrate
    from deal_finder.license import manager as mgr

    db_path = tmp_path / f"L8_{abs(hash(repr(bad_v)))}.db"
    conn_mod.reset_for_tests()
    conn_mod.set_db_path(str(db_path))
    migrate.run_migrations()
    try:
        lm = mgr.LicenseManager()
        resp = {
            "tier": "free",
            "watches_limit": 3,
            "poll_interval_min": 30,
            "expires_at": None,
            "trial_ends_at": None,
            "cancel_at_period_end": False,
            "min_supported_version": bad_v,
        }
        with patch.object(mgr.client, "post", return_value=resp):
            lm.get()
            assert lm.is_kill_switched() is False, (
                f"BREACH: garbage min_version {bad_v!r} wedged the user. "
                "Must fail open."
            )
    finally:
        conn_mod.close_thread_connection()
        conn_mod.set_db_path(None)


# ---------------------------------------------------------------------------
# L9 — Free user paid endpoints must 403
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/dashboard/breakdown/some-listing-id",
    "/api/dashboard/per-watch",
    "/api/dashboard/score-histogram",
])
def test_L9_paid_endpoints_403_for_free(
    client, logged_in, monkeypatch, path,
):
    _set_license(monkeypatch, paid=False)
    resp = client.get(path)
    assert resp.status_code == 403, (
        f"BREACH: free tier reached {path} (status={resp.status_code})"
    )
    body = resp.get_json()
    assert body["error"] == "upgrade_required"


def test_L9_trial_user_passes_paid_gate(client, logged_in, monkeypatch):
    """Trial counts as paid for gate purposes (is_paid() returns True
    for tier in {paid, trial})."""
    _set_license(monkeypatch, paid=True, tier="trial")
    resp = client.get("/api/dashboard/score-histogram")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# L10 — Direct cloud /license — covered by code trace (no real signup).
# ---------------------------------------------------------------------------

def test_L10_cloud_license_no_path_to_paid_without_stripe():
    """Static-trace probe: read cloud/supabase/functions/license/index.ts
    and confirm no codepath sets tier='paid' as a side effect of /license.
    The only writes are: (a) self-heal insert with tier='free', and
    (b) downgrade to 'free' on expiry. Stripe paths live elsewhere
    (stripe_webhook function), confirmed by absence of any 'paid' string
    INSERT/UPDATE inside this function.

    Cap on cloud calls means we don't sign up a real account here; the
    code trace + the unit-test on /license response shape is sufficient.
    """
    src = Path(__file__).resolve().parents[3] / (
        "cloud/supabase/functions/license/index.ts"
    )
    txt = src.read_text(encoding="utf-8")
    # The only INSERT seeds tier='free'.
    assert 'tier: "free"' in txt
    # The only UPDATE downgrades to 'free' (never up to 'paid').
    assert '.update({ tier: "free"' in txt
    assert '.update({ tier: "paid"' not in txt
    assert '.insert({ user_id: user.id, tier: "paid"' not in txt
    # FREE_LIMITS / PAID_LIMITS are constants — paid_limits is only
    # selected when license.tier is already paid/trial in the DB row.
    assert "FREE_LIMITS = { watches_limit: 3, poll_interval_min: 30 }" in txt
    assert "PAID_LIMITS = { watches_limit: null, poll_interval_min: 5 }" in txt


# ---------------------------------------------------------------------------
# L11 — Trial expiry auto-downgrade (code trace + behavioral assertion)
# ---------------------------------------------------------------------------

def test_L11_trial_expiry_downgrades_in_license_function():
    """The /license endpoint downgrades expired trials before returning.
    Verified by source inspection (we can't easily run a Deno function
    in pytest without spinning the local supabase stack)."""
    src = Path(__file__).resolve().parents[3] / (
        "cloud/supabase/functions/license/index.ts"
    )
    txt = src.read_text(encoding="utf-8")
    # The downgrade gate looks at trial_ends_at < now and updates tier=free.
    assert 'license.tier === "trial"' in txt
    assert "license.trial_ends_at" in txt
    assert 'new Date(license.trial_ends_at) < now' in txt
    assert 'needsDowngrade = true' in txt
    assert '.update({ tier: "free"' in txt
    # Also: same protection for paid subs that lapsed.
    assert 'license.tier === "paid"' in txt
    assert 'new Date(license.current_period_end) < now' in txt


def test_L11_desktop_trial_days_remaining_zero_at_expiry(tmp_path):
    """Desktop-side check: trial_days_remaining returns 0 (not negative)
    when trial_ends_at is in the past — the rare race window between
    cloud expiry and the next /license refresh."""
    from deal_finder.db import connection as conn_mod
    from deal_finder.db import migrate
    from deal_finder.license import manager as mgr

    db_path = tmp_path / "L11.db"
    conn_mod.reset_for_tests()
    conn_mod.set_db_path(str(db_path))
    migrate.run_migrations()
    try:
        lm = mgr.LicenseManager()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = {
            "tier": "trial",
            "watches_limit": None,
            "poll_interval_min": 5,
            "expires_at": None,
            "trial_ends_at": past,
            "cancel_at_period_end": False,
            "min_supported_version": "0.1.0",
        }
        with patch.object(mgr.client, "post", return_value=resp):
            assert lm.trial_days_remaining() == 0
    finally:
        conn_mod.close_thread_connection()
        conn_mod.set_db_path(None)


# ---------------------------------------------------------------------------
# L12 — Bulk-add corner cases
# ---------------------------------------------------------------------------

def test_L12a_bulk_zero_watches_ten_keywords(
    client, logged_in, monkeypatch, bullseye_db,
):
    _set_license(monkeypatch, paid=False, watches_limit=3)
    keywords = ",".join(f"x_{i}" for i in range(10))
    r = client.post("/api/searches/bulk", json={
        "keywords": keywords, "lat": 49.28, "lng": -123.12, "radius_km": 40,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["created"]) == 3
    assert body["truncated_at_limit"] is True


def test_L12b_bulk_only_dup_does_not_truncate(
    client, logged_in, monkeypatch, bullseye_db,
):
    _set_license(monkeypatch, paid=False, watches_limit=3)
    # Pre-seed at exact same lat/lng/radius so dedupe match catches it.
    from deal_finder.db.connection import get_connection
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO user_searches (keyword, latitude, longitude, "
            "radius_km, active) VALUES ('dup_kw', 49.28, -123.12, 40, 0)",
        )

    r = client.post("/api/searches/bulk", json={
        "keywords": "dup_kw", "lat": 49.28, "lng": -123.12, "radius_km": 40,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["created"]) == 0
    assert len(body["duplicate"]) == 1
    assert body["truncated_at_limit"] is False


def test_L12c_bulk_at_cap_truncates_immediately(
    client, logged_in, monkeypatch, bullseye_db,
):
    _set_license(monkeypatch, paid=False, watches_limit=3)
    _seed_active(3, prefix="full")

    r = client.post("/api/searches/bulk", json={
        "keywords": "newone", "lat": 49.28, "lng": -123.12, "radius_km": 40,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["created"]) == 0
    assert body["truncated_at_limit"] is True
    assert body["limit"] == 3
