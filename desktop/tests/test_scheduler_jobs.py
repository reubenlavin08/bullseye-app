"""Smoke tests for scheduler/jobs.py SQL translations.

The trickiest psycopg2 -> SQLite ports are in jobs.py:
  * pick_next_watch_to_poll        — LATERAL join rewritten as
                                     correlated subquery
  * _compute_cooldown_remaining_s  — EXTRACT(EPOCH FROM ...) computed
                                     in Python
  * _no_rate_limits_in_last_period — INTERVAL 'N seconds' rewritten
                                     as datetime('now', '-N seconds')

These tests exercise those queries against a real SQLite to catch
SQL-level regressions early.

Run with:  pytest tests/test_scheduler_jobs.py -v

Some tests are xfail-marked because they're blocked on the cloud
comps + license stubs (steps 3-5). Those will flip to passing once
those modules land.
"""
from __future__ import annotations

import pytest

from deal_finder.db import connection, migrate
from deal_finder.db.events import record_event
from deal_finder.scheduler import jobs


@pytest.fixture
def tmp_db(tmp_path):
    """Per-test fresh DB with the initial schema applied."""
    db_path = tmp_path / "test.db"
    connection.set_db_path(str(db_path))
    connection.reset_for_tests()
    migrate.run_migrations()
    yield db_path
    connection.close_thread_connection()
    connection.set_db_path(None)


def _add_watch(conn, *, search_id: int, keyword: str, active: int = 1) -> None:
    with conn:
        conn.execute(
            """INSERT INTO user_searches (id, keyword, latitude, longitude,
                                          radius_km, active)
               VALUES (?, ?, 49.28, -123.12, 25, ?)""",
            (search_id, keyword, active),
        )


# -- pick_next_watch_to_poll ----------------------------------------------

def test_pick_next_watch_returns_none_when_no_watches(tmp_db):
    assert jobs.pick_next_watch_to_poll() is None


def test_pick_next_watch_picks_only_active_watches(tmp_db):
    conn = connection.get_connection()
    _add_watch(conn, search_id=1, keyword="arduino", active=0)
    _add_watch(conn, search_id=2, keyword="raspberry pi", active=1)
    sid = jobs.pick_next_watch_to_poll()
    assert sid == 2


def test_pick_next_watch_prefers_never_polled(tmp_db):
    """Watches with no poll event in scheduler_events should sort first."""
    conn = connection.get_connection()
    _add_watch(conn, search_id=1, keyword="arduino")
    _add_watch(conn, search_id=2, keyword="raspberry pi")
    # Mark watch 1 as recently polled
    record_event("poll", search_id=1, raw_count=0)
    # Watch 2 has no poll event -> should be picked
    sid = jobs.pick_next_watch_to_poll()
    assert sid == 2


def test_pick_next_watch_picks_stalest_among_polled(tmp_db):
    """When all watches have poll events, the oldest one wins."""
    import time as _t
    conn = connection.get_connection()
    _add_watch(conn, search_id=1, keyword="arduino")
    _add_watch(conn, search_id=2, keyword="raspberry pi")
    record_event("poll", search_id=1, raw_count=0)
    _t.sleep(1.1)  # ensure a measurably-later created_at
    record_event("poll", search_id=2, raw_count=0)
    sid = jobs.pick_next_watch_to_poll()
    assert sid == 1  # stalest


# -- cooldown / rate-limit gates ------------------------------------------

def test_cooldown_zero_when_no_rate_limits(tmp_db):
    assert jobs._compute_cooldown_remaining_s() == 0


def test_cooldown_nonzero_after_rate_limit(tmp_db):
    record_event("fb_rate_limit", code=1675004, message="test")
    remaining = jobs._compute_cooldown_remaining_s()
    # Base cooldown is 60s; jitter 0.85x-1.15x -> at least ~50s left.
    assert remaining > 30


def test_no_rate_limits_in_last_period_true_when_clean(tmp_db):
    assert jobs._no_rate_limits_in_last_period(60) is True


def test_no_rate_limits_in_last_period_false_after_event(tmp_db):
    record_event("fb_rate_limit", code=1675004)
    assert jobs._no_rate_limits_in_last_period(60) is False


# -- list_active_search_ids -----------------------------------------------

def test_list_active_search_ids_filters_inactive(tmp_db):
    conn = connection.get_connection()
    _add_watch(conn, search_id=1, keyword="arduino", active=1)
    _add_watch(conn, search_id=2, keyword="raspberry pi", active=0)
    _add_watch(conn, search_id=3, keyword="esp32", active=1)
    ids = jobs.list_active_search_ids()
    assert ids == [1, 3]


# -- _parse_word_list -----------------------------------------------------

def test_parse_word_list_handles_comma_string():
    assert jobs._parse_word_list("Apple, Banana, cherry ") == [
        "apple", "banana", "cherry",
    ]


def test_parse_word_list_handles_json_array():
    assert jobs._parse_word_list('["foo", "Bar"]') == ["foo", "bar"]


def test_parse_word_list_handles_empty():
    assert jobs._parse_word_list(None) == []
    assert jobs._parse_word_list("") == []
    assert jobs._parse_word_list("   ") == []


# -- attribute_listing ----------------------------------------------------

def test_attribute_listing_picks_longest_match():
    batch = [
        {"id": 1, "keyword": "arduino"},
        {"id": 2, "keyword": "arduino uno"},
    ]
    w = jobs.attribute_listing("arduino uno r3 brand new", batch)
    assert w["id"] == 2  # longer keyword wins


def test_attribute_listing_returns_none_when_no_match():
    batch = [{"id": 1, "keyword": "arduino"}]
    assert jobs.attribute_listing("ESP32 dev board", batch) is None


# -- license-aware paths --------------------------------------------------
#
# Step 5 replaced the license stub with a real LicenseManager. Without
# a JWT in the keyring, it falls through to default-free behavior:
#   tier='free', poll_interval_min=30, watches_limit=3, kill_switch=False.
# The "fails open" guarantee remains: cloud unreachable / unauth never
# crashes the scheduler.

def test_kill_switch_inactive_when_no_user_logged_in():
    """No keyring tokens -> license_manager returns default-free
    (kill_switch=False, since the default min_supported_version is
    "0.0.0"). Scheduler must not refuse to poll for unauth'd state."""
    assert jobs._kill_switch_active() is False


def test_license_min_poll_interval_returns_default_free_when_unauth():
    """Default-free poll interval is 30 min => 1800 seconds.
    The scheduler clamps user-configured intervals against this."""
    assert jobs._license_min_poll_interval_s() == 30 * 60


# -- step 3 landed: cloud.comps is wired up ------------------------------
#
# Previous version of this test asserted cloud.comps.get_comps raised
# NotImplementedError under an xfail-strict marker. Step 3 implemented
# the real call; the marker would now fire as a real failure. Replaced
# with a positive smoke test. Detailed cloud.comps scenarios live in
# tests/test_cloud_comps.py.

def test_cloud_comps_returns_canonical_shape():
    """Smoke check: cloud.comps.get_comps is callable and returns a
    dict with the fields the appraisal formula reads."""
    from unittest.mock import patch
    from deal_finder.cloud import comps as comps_mod

    with patch.object(
        comps_mod.client,
        "post",
        return_value={
            "stats": {
                "sample_size": 1, "median": 50, "mean": 50,
                "minimum": 50, "maximum": 50,
                "p10": 50, "q1": 50, "q3": 50, "p90": 50,
                "iqr": 0, "iqr_ratio": 0,
            },
            "raw_comps": [],
            "source": "fresh",
        },
    ):
        result = comps_mod.get_comps("arduino uno", region="EBAY-ENCA")

    assert isinstance(result, dict)
    for key in ("sample_size", "median", "mean", "minimum", "maximum",
                "search_term", "region", "source", "raw_comps"):
        assert key in result, f"missing field: {key}"
