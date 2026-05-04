"""Tests for the desktop-side cloud.comps wrapper.

Mocks the HTTP client (`cloud.client.client.post`) so we can exercise:
    - Successful cloud fetch -> shape transform + local cache mirror
    - CloudUnavailable -> local fallback
    - Local cache hit / miss / TTL expiry
    - Empty stats fallback when nothing's available
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from deal_finder.cloud import comps as comps_mod
from deal_finder.cloud.client import CloudUnavailable, CloudError, Unauthorized
from deal_finder.db import connection, migrate


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    connection.set_db_path(str(db))
    connection.reset_for_tests()
    migrate.run_migrations()
    yield db
    connection.close_thread_connection()
    connection.set_db_path(None)


# ---------------------------------------------------------------- Cloud OK

def test_get_comps_success_returns_normalized_shape(tmp_db):
    cloud_response = {
        "stats": {
            "sample_size": 4, "median": 200, "mean": 210,
            "minimum": 149, "maximum": 290,
            "p10": 150, "q1": 175, "q3": 240, "p90": 280,
            "iqr": 65, "iqr_ratio": 0.32,
        },
        "raw_comps": [
            {"title": "iRobot Roomba i5", "price": 200, "currency": "USD",
             "listing_url": "https://ebay.com/itm/1", "location": "US"},
        ],
        "source": "fresh",
        "age_seconds": 0,
        "search_term_normalized": "i5 irobot roomba",
        "region": "EBAY-ENCA",
    }
    with patch.object(comps_mod.client, "post", return_value=cloud_response):
        out = comps_mod.get_comps("iRobot Roomba i5")

    # Shape matches what the appraisal formula expects
    assert out["sample_size"] == 4
    assert out["median"] == 200
    assert out["search_term"] == "iRobot Roomba i5"
    assert out["region"] == "EBAY-ENCA"
    assert out["source"] == "ebay_fresh"
    assert len(out["raw_comps"]) == 1


def test_get_comps_mirrors_to_local_cache(tmp_db):
    cloud_response = {
        "stats": {
            "sample_size": 3, "median": 100, "mean": 110,
            "minimum": 80, "maximum": 150,
            "p10": 85, "q1": 90, "q3": 130, "p90": 145,
            "iqr": 40, "iqr_ratio": 0.4,
        },
        "raw_comps": [],
        "source": "fresh",
    }
    with patch.object(comps_mod.client, "post", return_value=cloud_response):
        comps_mod.get_comps("arduino uno")

    # The row should now be in comps_local_cache
    conn = connection.get_connection()
    row = conn.execute(
        "SELECT * FROM comps_local_cache WHERE search_term = ?",
        ("arduino uno",),
    ).fetchone()
    assert row is not None
    stats = json.loads(row["stats_json"])
    assert stats["median"] == 100
    assert stats["sample_size"] == 3


# --------------------------------------------------------- Local fallback

def test_local_fallback_used_when_cloud_unavailable(tmp_db):
    # Pre-seed local cache as if a previous successful fetch happened
    conn = connection.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO comps_local_cache
                   (search_term, region, stats_json, raw_comps_json, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "raspberry pi 4", "EBAY-ENCA",
                json.dumps({"sample_size": 5, "median": 80, "mean": 82,
                            "minimum": 50, "maximum": 120,
                            "p10": 55, "q1": 65, "q3": 100, "p90": 115,
                            "iqr": 35, "iqr_ratio": 0.44}),
                json.dumps([]),
                time.time(),  # fresh
            ),
        )
    # Cloud raises CloudUnavailable
    with patch.object(comps_mod.client, "post",
                      side_effect=CloudUnavailable("network down")):
        out = comps_mod.get_comps("raspberry pi 4")

    assert out["sample_size"] == 5
    assert out["median"] == 80
    assert out["source"] == "cache_local"


def test_stale_local_cache_ignored(tmp_db):
    """A local cache row older than LOCAL_FALLBACK_TTL_S should NOT be
    used. Better to admit we have no data than serve 2-week-old comps."""
    conn = connection.get_connection()
    stale_ts = time.time() - (comps_mod.LOCAL_FALLBACK_TTL_S + 60)
    with conn:
        conn.execute(
            """INSERT INTO comps_local_cache
                   (search_term, region, stats_json, raw_comps_json, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("stale-term", "EBAY-ENCA",
             json.dumps({"sample_size": 99, "median": 999, "mean": 0,
                         "minimum": 0, "maximum": 0,
                         "p10": 0, "q1": 0, "q3": 0, "p90": 0,
                         "iqr": 0, "iqr_ratio": 0}),
             "[]", stale_ts),
        )
    with patch.object(comps_mod.client, "post",
                      side_effect=CloudUnavailable("offline")):
        out = comps_mod.get_comps("stale-term")
    assert out["sample_size"] == 0  # empty fallback, NOT the stale row
    assert out["source"] == "empty"


def test_no_local_cache_no_cloud_returns_empty(tmp_db):
    with patch.object(comps_mod.client, "post",
                      side_effect=CloudUnavailable("offline")):
        out = comps_mod.get_comps("never-searched-before")
    assert out["sample_size"] == 0
    assert out["source"] == "empty"


# ----------------------------------------------------------- Auth + errors

def test_unauthorized_falls_back_to_local(tmp_db):
    # Pre-seed local
    conn = connection.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO comps_local_cache
                   (search_term, region, stats_json, raw_comps_json, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("logged-out", "EBAY-ENCA",
             json.dumps({"sample_size": 2, "median": 50, "mean": 50,
                         "minimum": 50, "maximum": 50,
                         "p10": 50, "q1": 50, "q3": 50, "p90": 50,
                         "iqr": 0, "iqr_ratio": 0}),
             "[]", time.time()),
        )
    with patch.object(comps_mod.client, "post",
                      side_effect=Unauthorized("token gone")):
        out = comps_mod.get_comps("logged-out")
    assert out["sample_size"] == 2  # served from local cache
    assert out["source"] == "cache_local"


def test_cloud_validation_error_returns_empty(tmp_db):
    """A 4xx from the cloud (e.g. malformed search_term) should give us
    an empty result, NOT fall back to a possibly-related local row."""
    with patch.object(comps_mod.client, "post",
                      side_effect=CloudError("comps 400: search_term required")):
        out = comps_mod.get_comps("anything")
    assert out["sample_size"] == 0
    assert out["source"] == "empty"


def test_blank_search_term_returns_empty(tmp_db):
    out = comps_mod.get_comps("")
    assert out["sample_size"] == 0
    assert out["source"] == "empty"
    out = comps_mod.get_comps("   ")
    assert out["sample_size"] == 0


# -------------------------------------------------------- Scheduler xfail flip

def test_scheduler_xfail_marker_should_now_flip():
    """Sanity: the scheduler test_cloud_comps_stub_raises_until_step_3
    is marked xfail strict. Now that step 3 is in, that test should
    START FAILING — which means pytest.mark.xfail's strict=True turns
    a passing-xfail into a real failure. This test reminds us to flip
    the marker.

    For now we just import the module and check the function is
    NO LONGER raising NotImplementedError, which is the indirect
    confirmation that the xfail flip is now correct.
    """
    # Mock the cloud client to prevent a real network call
    with patch.object(comps_mod.client, "post",
                      return_value={"stats": {"sample_size": 0, "median": 0,
                                              "mean": 0, "minimum": 0, "maximum": 0,
                                              "p10": 0, "q1": 0, "q3": 0, "p90": 0,
                                              "iqr": 0, "iqr_ratio": 0},
                                    "raw_comps": [], "source": "fresh"}):
        # Should NOT raise NotImplementedError anymore
        result = comps_mod.get_comps("any-term")
        assert isinstance(result, dict)
        assert "sample_size" in result
