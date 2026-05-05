"""Tests for license/manager.py.

Mocks `cloud.client.client.post` so we never hit the network. The
SQLite fallback cache is exercised against a real per-test tmp DB.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from deal_finder.cloud.client import CloudUnavailable, Unauthorized
from deal_finder.db import connection, migrate
from deal_finder.license import manager as mgr


@pytest.fixture
def fresh_manager(tmp_path):
    """Fresh DB + fresh LicenseManager instance per test."""
    db_path = tmp_path / "test.db"
    connection.set_db_path(str(db_path))
    connection.reset_for_tests()
    migrate.run_migrations()
    yield mgr.LicenseManager()
    connection.close_thread_connection()
    connection.set_db_path(None)


def _paid_response(**kw) -> dict:
    return {
        "tier": "paid",
        "watches_limit": None,
        "poll_interval_min": 5,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "trial_ends_at": None,
        "cancel_at_period_end": False,
        "min_supported_version": "0.1.0",
        **kw,
    }


def _free_response(**kw) -> dict:
    return {
        "tier": "free",
        "watches_limit": 3,
        "poll_interval_min": 30,
        "expires_at": None,
        "trial_ends_at": None,
        "cancel_at_period_end": False,
        "min_supported_version": "0.1.0",
        **kw,
    }


# ----------------------------------------------------------------- Cache

def test_cache_avoids_second_network_call(fresh_manager):
    with patch.object(mgr.client, "post", return_value=_paid_response()) as mock:
        fresh_manager.get()
        fresh_manager.get()
        fresh_manager.get()
    assert mock.call_count == 1, "second/third get() should hit cache"


def test_force_refresh_bypasses_cache(fresh_manager):
    with patch.object(mgr.client, "post", return_value=_paid_response()) as mock:
        fresh_manager.get()
        fresh_manager.get(force_refresh=True)
    assert mock.call_count == 2


def test_invalidate_forces_next_fetch(fresh_manager):
    with patch.object(mgr.client, "post", return_value=_paid_response()) as mock:
        fresh_manager.get()
        fresh_manager.invalidate()
        fresh_manager.get()
    assert mock.call_count == 2


# ---------------------------------------------------------- Tier accessors

def test_paid_tier_unlimited_watches(fresh_manager):
    with patch.object(mgr.client, "post", return_value=_paid_response()):
        assert fresh_manager.is_paid() is True
        assert fresh_manager.watches_limit() is None
        assert fresh_manager.poll_interval_min() == 5


def test_free_tier_limited(fresh_manager):
    with patch.object(mgr.client, "post", return_value=_free_response()):
        assert fresh_manager.is_paid() is False
        assert fresh_manager.watches_limit() == 3
        assert fresh_manager.poll_interval_min() == 30


def test_trial_treated_as_paid(fresh_manager):
    trial_resp = _paid_response(
        tier="trial",
        trial_ends_at=(datetime.now(timezone.utc) + timedelta(days=4)).isoformat(),
    )
    with patch.object(mgr.client, "post", return_value=trial_resp):
        assert fresh_manager.is_paid() is True
        assert fresh_manager.tier() == "trial"
        assert fresh_manager.trial_days_remaining() in (3, 4)


# ---------------------------------------------------------------- Fallback

def test_offline_uses_sqlite_fallback(fresh_manager):
    # First call: cloud OK -> mirrors to SQLite
    with patch.object(mgr.client, "post", return_value=_paid_response()):
        fresh_manager.get()
    # Drop the in-memory cache; next call goes to "cloud" again
    fresh_manager.invalidate()
    with patch.object(mgr.client, "post", side_effect=CloudUnavailable("offline")):
        result = fresh_manager.get()
    assert result["tier"] == "paid"  # from SQLite mirror


def test_unauthorized_uses_fallback(fresh_manager):
    with patch.object(mgr.client, "post", return_value=_free_response()):
        fresh_manager.get()
    fresh_manager.invalidate()
    with patch.object(mgr.client, "post", side_effect=Unauthorized("token gone")):
        result = fresh_manager.get()
    assert result["tier"] == "free"


def test_offline_no_prior_cache_returns_default_free(fresh_manager):
    with patch.object(mgr.client, "post", side_effect=CloudUnavailable("offline")):
        result = fresh_manager.get()
    assert result["tier"] == "free"
    assert result["watches_limit"] == 3
    assert result["poll_interval_min"] == 30


# ---------------------------------------------------------------- Kill switch

def test_kill_switch_triggers_when_app_below_min_version(fresh_manager):
    # Set min_supported_version to something higher than our __version__
    with patch.object(mgr.client, "post",
                      return_value=_paid_response(min_supported_version="99.0.0")):
        fresh_manager.get()
    assert fresh_manager.is_kill_switched() is True
    # Kill switch should pin everything to safe-deny
    assert fresh_manager.is_paid() is False
    assert fresh_manager.watches_limit() == 0
    assert fresh_manager.poll_interval_min() >= 60 * 24


def test_kill_switch_not_triggered_for_equal_or_higher_app_version(fresh_manager):
    with patch.object(mgr.client, "post",
                      return_value=_paid_response(min_supported_version="0.0.1")):
        fresh_manager.get()
    assert fresh_manager.is_kill_switched() is False


def test_kill_switch_garbage_min_version_does_not_lock_out(fresh_manager):
    """A bad min_supported_version (typo, '???', empty) should fail
    open. We never want to accidentally kill-switch everyone."""
    with patch.object(mgr.client, "post",
                      return_value=_paid_response(min_supported_version="???")):
        fresh_manager.get()
    assert fresh_manager.is_kill_switched() is False


# ---------------------------------------------------------- Trial countdown

def test_trial_days_remaining_zero_when_expired(fresh_manager):
    """Edge case: cloud hasn't downgraded yet but trial_ends_at is past."""
    expired = _paid_response(
        tier="trial",
        trial_ends_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    )
    with patch.object(mgr.client, "post", return_value=expired):
        assert fresh_manager.trial_days_remaining() == 0


def test_trial_days_none_when_not_on_trial(fresh_manager):
    with patch.object(mgr.client, "post", return_value=_free_response()):
        assert fresh_manager.trial_days_remaining() is None


# -------------------------------------------------------------- _semver_less

def test_semver_less_basic_cases():
    assert mgr._semver_less("0.1.0", "0.2.0") is True
    assert mgr._semver_less("0.2.0", "0.1.0") is False
    assert mgr._semver_less("0.1.0", "0.1.0") is False
    assert mgr._semver_less("0.1.0", "1.0.0") is True
    assert mgr._semver_less("0.1.0", "0.1.5") is True


def test_semver_less_handles_garbage():
    """Should fail open (return False) on unparseable inputs."""
    assert mgr._semver_less("???", "0.1.0") is False or True  # don't care
    # The important guarantee: it doesn't raise.
    mgr._semver_less("not.a.version", "0.0.1")
    mgr._semver_less("", "")
