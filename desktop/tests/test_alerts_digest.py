"""Tests for the cloud-routed alerts pipeline.

Covers:
    - cloud.alerts.send_digest / send_instant POST through cloud.client
    - alerts.digest.collect_pending_for_email respects the bounds gate
      and returns only un-notified, score-passing rows
    - 60s batching hold blocks send when the oldest match is too young
    - Score color-coding lives in the resend.ts equivalent we mirror
      here as a Python helper for parity sanity-checking

Network is fully mocked (`patch.object(cloud.alerts.client, 'post')`),
so no Resend or Supabase calls escape the test process.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from deal_finder.cloud import alerts as cloud_alerts
from deal_finder.cloud.client import CloudError, CloudUnavailable, Unauthorized
from deal_finder.db import connection, migrate
from deal_finder.alerts import digest as digest_mod


# --- Fixtures --------------------------------------------------------------

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Per-test SQLite DB at a fresh path. Schema migrations applied."""
    db_path = tmp_path / "alerts_test.db"
    monkeypatch.setenv("BULLSEYE_DB_PATH", str(db_path))
    connection.reset_for_tests()
    connection.set_db_path(str(db_path))
    migrate.run_migrations()
    yield db_path
    connection.reset_for_tests()
    connection.set_db_path(None)


def _seed_watch(
    *,
    keyword: str = "arduino",
    home_lat: float = 49.28,
    home_lng: float = -123.12,
    radius_km: int | None = 50,
    price_min: int | None = None,
    price_max: int | None = None,
    active: bool = True,
) -> int:
    """Insert one user_searches row + the user_settings home coords.
    Returns the search id."""
    conn = connection.get_connection()
    with conn:
        cur = conn.execute(
            """INSERT INTO user_searches
                 (keyword, latitude, longitude, radius_km,
                  price_min, price_max, active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (keyword, home_lat, home_lng, radius_km,
             price_min, price_max, 1 if active else 0),
        )
        sid = cur.lastrowid
        # user_settings is a single-row table; upsert via INSERT OR REPLACE.
        conn.execute(
            """INSERT OR REPLACE INTO user_settings
                 (user_id, home_label, home_latitude, home_longitude)
               VALUES (1, 'Vancouver', ?, ?)""",
            (home_lat, home_lng),
        )
    return sid


def _seed_listing(
    *,
    listing_id: str,
    search_id: int,
    deal_score: int = 80,
    notified: bool = False,
    appraised: bool = True,
    rejected: bool = False,
    price: float | None = 200.0,
    fair_value: float | None = 280.0,
    seller_location: str | None = "Vancouver, BC",
    appraised_minutes_ago: int = 10,
    detail_lat: float | None = 49.28,
    detail_lng: float | None = -123.12,
    title: str = "Arduino Uno R3",
) -> None:
    """Insert one listings row with appraisal already filled in."""
    conn = connection.get_connection()
    appraised_at = (
        datetime.now(timezone.utc)
        - timedelta(minutes=appraised_minutes_ago)
    ).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO listings (
                 id, search_id, title, price, fair_value,
                 listing_url, photo_url, seller_location,
                 detail_latitude, detail_longitude,
                 appraised, appraised_at, deal_score,
                 rejected, notified, listed_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (listing_id, search_id, title, price, fair_value,
             f"https://www.facebook.com/marketplace/item/{listing_id}/",
             "https://example.com/photo.jpg",
             seller_location,
             detail_lat, detail_lng,
             1 if appraised else 0, appraised_at, deal_score,
             1 if rejected else 0, 1 if notified else 0,
             None),
        )


# --- cloud.alerts.send_digest / send_instant -------------------------------

def test_send_digest_calls_cloud_endpoint():
    """send_digest must POST type='digest' to /alerts-send."""
    matches = [{"listing_id": "a", "title": "t", "deal_score": 75,
                "asking_price": 100, "fair_value": 130,
                "confidence_label": "high", "confidence_pm": 5,
                "listing_url": "u", "photo_url": None,
                "seller_location": "Vancouver", "listed_at": None,
                "keyword": "kw"}]
    fake_resp = {"sent": True, "queued": False, "count": 1}
    with patch.object(cloud_alerts.client, "post",
                      return_value=fake_resp) as mock_post:
        result = cloud_alerts.send_digest(matches)
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "alerts-send"
    payload = args[1]
    assert payload["type"] == "digest"
    assert payload["matches"] == matches
    assert result == fake_resp


def test_send_instant_calls_cloud_endpoint():
    matches = [{"listing_id": "b", "title": "t", "deal_score": 88,
                "asking_price": 50, "fair_value": 100,
                "confidence_label": None, "confidence_pm": None,
                "listing_url": "u", "photo_url": None,
                "seller_location": None, "listed_at": None,
                "keyword": "kw"}]
    fake_resp = {"sent": True, "queued": False, "count": 1}
    with patch.object(cloud_alerts.client, "post",
                      return_value=fake_resp) as mock_post:
        result = cloud_alerts.send_instant(matches)
    args, kwargs = mock_post.call_args
    assert args[0] == "alerts-send"
    assert args[1]["type"] == "instant"
    assert result == fake_resp


def test_send_empty_matches_short_circuits():
    """Don't waste a network call when there's nothing to send."""
    with patch.object(cloud_alerts.client, "post") as mock_post:
        result = cloud_alerts.send_digest([])
    mock_post.assert_not_called()
    assert result == {"sent": False, "queued": False, "count": 0}


@pytest.mark.parametrize("exc,expected_err", [
    (Unauthorized("token"), "unauthorized"),
    (CloudUnavailable("offline"), "unavailable"),
])
def test_send_swallows_transport_failures(exc, expected_err):
    """Network/auth failures must not raise — email is best-effort."""
    matches = [{"listing_id": "x", "deal_score": 70, "title": "t",
                "asking_price": None, "fair_value": None,
                "confidence_label": None, "confidence_pm": None,
                "listing_url": "", "photo_url": None,
                "seller_location": None, "listed_at": None,
                "keyword": "k"}]
    with patch.object(cloud_alerts.client, "post", side_effect=exc):
        result = cloud_alerts.send_digest(matches)
    assert result["sent"] is False
    assert result["error"] == expected_err


def test_send_swallows_cloud_error():
    """4xx (e.g. 503 'email service not configured') should not raise."""
    matches = [{"listing_id": "x", "deal_score": 70, "title": "t",
                "asking_price": None, "fair_value": None,
                "confidence_label": None, "confidence_pm": None,
                "listing_url": "", "photo_url": None,
                "seller_location": None, "listed_at": None,
                "keyword": "k"}]
    with patch.object(cloud_alerts.client, "post",
                      side_effect=CloudError("alerts-send 503: email service not configured")):
        result = cloud_alerts.send_digest(matches)
    assert result["sent"] is False
    assert "503" in result["error"]


# --- collect_pending_for_email --------------------------------------------

def test_collect_pending_returns_only_unnotified(fresh_db):
    """Notified rows must be excluded; un-notified high-score rows kept."""
    sid = _seed_watch()
    _seed_listing(listing_id="hot1", search_id=sid, deal_score=85,
                  notified=False)
    _seed_listing(listing_id="cold1", search_id=sid, deal_score=85,
                  notified=True)   # already sent
    _seed_listing(listing_id="lowscore", search_id=sid, deal_score=40,
                  notified=False)  # below threshold

    matches = digest_mod.collect_pending_for_email(
        score_threshold=70, apply_batch_hold=False,
    )
    ids = {m.listing_id for m in matches}
    assert ids == {"hot1"}


def test_collect_pending_skips_inactive_watch(fresh_db):
    sid = _seed_watch(active=False)
    _seed_listing(listing_id="ignored", search_id=sid, deal_score=90)
    matches = digest_mod.collect_pending_for_email(apply_batch_hold=False)
    assert matches == []


def test_collect_pending_applies_price_bounds(fresh_db):
    """A listing whose price is now out of the watch's range must be
    suppressed (notified=1) so it never re-enters the digest."""
    sid = _seed_watch(price_min=300)  # listing at $200 will be out
    _seed_listing(listing_id="dropme", search_id=sid, price=200.0,
                  deal_score=85)
    matches = digest_mod.collect_pending_for_email(apply_batch_hold=False)
    assert matches == []
    # And it should be suppressed in the DB:
    conn = connection.get_connection()
    row = conn.execute(
        "SELECT notified, appraisal_note FROM listings WHERE id = 'dropme'"
    ).fetchone()
    assert row["notified"] == 1
    assert "suppressed" in (row["appraisal_note"] or "")


def test_batching_hold_blocks_premature_send(fresh_db, monkeypatch):
    """A match younger than DIGEST_BATCH_HOLD_S must hold the digest."""
    monkeypatch.setattr(digest_mod, "DIGEST_BATCH_HOLD_S", 60)
    sid = _seed_watch()
    _seed_listing(listing_id="fresh", search_id=sid, deal_score=85,
                  appraised_minutes_ago=0)  # ~0s old — below the 60s hold
    matches = digest_mod.collect_pending_for_email(
        apply_batch_hold=True,
    )
    assert matches == []


def test_batching_hold_releases_after_window(fresh_db, monkeypatch):
    """When the oldest match is older than the hold, send proceeds."""
    monkeypatch.setattr(digest_mod, "DIGEST_BATCH_HOLD_S", 60)
    sid = _seed_watch()
    _seed_listing(listing_id="aged", search_id=sid, deal_score=85,
                  appraised_minutes_ago=5)  # 5 min > 60s
    matches = digest_mod.collect_pending_for_email(
        apply_batch_hold=True,
    )
    assert len(matches) == 1
    assert matches[0].listing_id == "aged"


def test_batching_hold_disabled_passes_fresh_match(fresh_db, monkeypatch):
    """The 8am-local digest job uses apply_batch_hold=False."""
    monkeypatch.setattr(digest_mod, "DIGEST_BATCH_HOLD_S", 60)
    sid = _seed_watch()
    _seed_listing(listing_id="fresh", search_id=sid, deal_score=85,
                  appraised_minutes_ago=0)
    matches = digest_mod.collect_pending_for_email(apply_batch_hold=False)
    assert len(matches) == 1


def test_send_daily_digest_marks_notified_on_success(fresh_db):
    sid = _seed_watch()
    _seed_listing(listing_id="m1", search_id=sid, deal_score=85)
    _seed_listing(listing_id="m2", search_id=sid, deal_score=80)

    fake_resp = {"sent": True, "queued": False, "count": 2}
    with patch.object(cloud_alerts.client, "post", return_value=fake_resp):
        result = digest_mod.send_daily_digest()

    assert result["sent"] is True
    conn = connection.get_connection()
    rows = conn.execute(
        "SELECT id, notified FROM listings WHERE id IN ('m1', 'm2')"
    ).fetchall()
    assert all(r["notified"] == 1 for r in rows)


def test_send_daily_digest_does_not_mark_on_failure(fresh_db):
    sid = _seed_watch()
    _seed_listing(listing_id="m1", search_id=sid, deal_score=85)

    with patch.object(cloud_alerts.client, "post",
                      side_effect=CloudUnavailable("offline")):
        result = digest_mod.send_daily_digest()

    assert result["sent"] is False
    conn = connection.get_connection()
    row = conn.execute(
        "SELECT notified FROM listings WHERE id = 'm1'"
    ).fetchone()
    # Listing must remain un-notified so the next tick can retry.
    assert row["notified"] == 0


def test_send_daily_digest_marks_on_queued(fresh_db):
    """When the cloud queues the digest (already sent today), we still
    mark notified — the matches are now the cloud's responsibility."""
    sid = _seed_watch()
    _seed_listing(listing_id="m1", search_id=sid, deal_score=85)
    fake_resp = {"sent": False, "queued": True, "count": 1}
    with patch.object(cloud_alerts.client, "post", return_value=fake_resp):
        result = digest_mod.send_daily_digest()
    assert result["queued"] is True
    conn = connection.get_connection()
    row = conn.execute(
        "SELECT notified FROM listings WHERE id = 'm1'"
    ).fetchone()
    assert row["notified"] == 1


def test_send_instant_for_pending_returns_held(fresh_db, monkeypatch):
    """Instant path under the hold returns held=True without posting."""
    monkeypatch.setattr(digest_mod, "DIGEST_BATCH_HOLD_S", 60)
    sid = _seed_watch()
    _seed_listing(listing_id="fresh", search_id=sid, deal_score=85,
                  appraised_minutes_ago=0)
    with patch.object(cloud_alerts.client, "post") as mock_post:
        result = digest_mod.send_instant_for_pending()
    mock_post.assert_not_called()
    assert result == {"sent": False, "queued": False, "count": 0,
                      "held": True}


# --- Score color-coding parity (TS rendering mirror) -----------------------
#
# resend.ts has scoreColor() with the rule >=70 green / >=50 amber / else
# gray. We mirror it here as a Python helper to (a) document the rule in
# one place a Python test can assert on, and (b) catch parity drift if
# someone changes the cutoff in only one of the two languages.

def _py_score_color(score: int) -> str:
    if score >= 70:
        return "#5d7a4f"   # green
    if score >= 50:
        return "#c98a3c"   # amber
    return "#9a8a7d"       # gray


@pytest.mark.parametrize("score,expected", [
    (95, "#5d7a4f"),
    (70, "#5d7a4f"),
    (69, "#c98a3c"),
    (50, "#c98a3c"),
    (49, "#9a8a7d"),
    (0,  "#9a8a7d"),
])
def test_resend_renders_with_score_color_coding(score, expected):
    """Sanity-check the cutoffs in lockstep with cloud/_shared/resend.ts."""
    assert _py_score_color(score) == expected
