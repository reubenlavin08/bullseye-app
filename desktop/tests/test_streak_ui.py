"""Tests for the retention-v1.1 streak proxy endpoints.

Covers:
    - GET  /api/streak shape passthrough from cloud.streak.fetch_streak
    - GET  /api/streak returns 503 when the cloud helper returns None
    - GET  /api/streak returns 401 when not logged in
    - POST /api/streak/redeem invalidates the license cache on success
    - POST /api/streak/redeem returns 503 on cloud failure

The cloud-side `deal_finder.cloud.streak` module is owned by the
streak-backend agent and may or may not exist when this test runs. We
inject a stub into `sys.modules` so the lazy import inside the route
resolves to our mock regardless of whether the real module landed
yet. Using sys.modules instead of patch.dict gives us full control
over fetch_streak / redeem_pro_days without needing to know the
internal contract.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# --- Fixtures -------------------------------------------------------------

@pytest.fixture
def bullseye_db(tmp_path, monkeypatch):
    """Fresh SQLite DB pointed at a temp file, schema applied."""
    db_path = tmp_path / "bullseye_streak_test.db"
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
    monkeypatch.setattr(token_store, "is_logged_in", lambda: True)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: True)


@pytest.fixture
def logged_out(monkeypatch):
    from deal_finder.auth import token_store
    from webapp import app as webapp_app
    monkeypatch.setattr(token_store, "is_logged_in", lambda: False)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: False)


def _set_license(monkeypatch, *, paid=False, kill_switched=False):
    from webapp import app as webapp_app
    lm = webapp_app.license_manager
    monkeypatch.setattr(lm, "is_paid", lambda: paid)
    monkeypatch.setattr(lm, "tier", lambda: "paid" if paid else "free")
    monkeypatch.setattr(lm, "watches_limit", lambda: None if paid else 3)
    monkeypatch.setattr(lm, "is_kill_switched", lambda: kill_switched)
    monkeypatch.setattr(lm, "get", lambda **kw: {"tier": "paid" if paid else "free"})


@pytest.fixture
def fake_streak_module(monkeypatch):
    """Inject a stub `deal_finder.cloud.streak` so the lazy imports
    inside /api/streak and /api/streak/redeem resolve to our mocks.

    Yields the module object so each test can set
    .fetch_streak / .redeem_pro_days to whatever it needs.
    """
    mod = types.ModuleType("deal_finder.cloud.streak")
    mod.fetch_streak = MagicMock(return_value=None)
    mod.redeem_pro_days = MagicMock(return_value=None)
    monkeypatch.setitem(sys.modules, "deal_finder.cloud.streak", mod)
    yield mod


# --- /api/streak ----------------------------------------------------------

def test_api_streak_login_required(client, logged_out, monkeypatch):
    """No token → 401, no cloud round-trip."""
    _set_license(monkeypatch, paid=False)
    resp = client.get("/api/streak")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["error"] == "login_required"


def test_api_streak_returns_cloud_payload(
    client, logged_in, monkeypatch, fake_streak_module,
):
    """Happy path: pass through whatever cloud.streak.fetch_streak
    returned, wrapped with ok=True so the JS knows to render it."""
    _set_license(monkeypatch, paid=False)
    fake_streak_module.fetch_streak.return_value = {
        "current_streak_days": 7,
        "pro_days_banked": 3,
        "pro_days_total_earned": 3,
        "next_reward_in_days": 23,
        "just_banked_today": True,
        "last_milestone": "7_day",
    }

    resp = client.get("/api/streak")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["current_streak_days"] == 7
    assert body["pro_days_banked"] == 3
    assert body["next_reward_in_days"] == 23
    assert body["just_banked_today"] is True
    fake_streak_module.fetch_streak.assert_called_once_with()


def test_api_streak_503_when_cloud_unavailable(
    client, logged_in, monkeypatch, fake_streak_module,
):
    """fetch_streak returning None → 503 with streak_unavailable so
    the dashboard JS hides the card without breaking other widgets."""
    _set_license(monkeypatch, paid=False)
    fake_streak_module.fetch_streak.return_value = None

    resp = client.get("/api/streak")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["ok"] is False
    assert body["streak_unavailable"] is True


# --- /api/streak/redeem ---------------------------------------------------

def test_api_streak_redeem_login_required(client, logged_out, monkeypatch):
    _set_license(monkeypatch, paid=False)
    resp = client.post("/api/streak/redeem")
    assert resp.status_code == 401


def test_api_streak_redeem_invalidates_license_on_success(
    client, logged_in, monkeypatch, fake_streak_module,
):
    """The redeem flow has to bust the license cache so the next
    /api/dashboard call sees tier='trial' rather than the stale
    'free'. Without this the user redeems, sees no UI change, and
    files a support ticket."""
    _set_license(monkeypatch, paid=False)

    # Spy on license_manager.invalidate so we can assert it was called
    # exactly once after a successful redeem.
    from webapp import app as webapp_app
    invalidate_calls = []
    monkeypatch.setattr(
        webapp_app.license_manager,
        "invalidate",
        lambda: invalidate_calls.append(True),
    )

    fake_streak_module.redeem_pro_days.return_value = {
        "redeemed_days": 7,
        "trial_ends_at": "2026-05-11T00:00:00Z",
    }

    resp = client.post("/api/streak/redeem")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["redeemed_days"] == 7
    assert body["trial_ends_at"] == "2026-05-11T00:00:00Z"
    assert len(invalidate_calls) == 1, (
        "license_manager.invalidate() must be called exactly once on "
        "successful redeem so the dashboard repaint sees the new tier"
    )


def test_api_streak_redeem_503_on_cloud_failure_does_not_invalidate(
    client, logged_in, monkeypatch, fake_streak_module,
):
    """If the cloud redeem fails (None return), we must NOT invalidate
    the cache — that would force an unnecessary refetch for no benefit
    and could mask a transient cloud issue as a license-cache problem."""
    _set_license(monkeypatch, paid=False)

    from webapp import app as webapp_app
    invalidate_calls = []
    monkeypatch.setattr(
        webapp_app.license_manager,
        "invalidate",
        lambda: invalidate_calls.append(True),
    )

    fake_streak_module.redeem_pro_days.return_value = None

    resp = client.post("/api/streak/redeem")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"] == "redeem_failed"
    assert invalidate_calls == [], (
        "invalidate() should NOT fire when redeem failed"
    )
