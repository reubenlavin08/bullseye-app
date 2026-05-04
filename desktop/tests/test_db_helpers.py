"""Integration smoke tests for the ported SQLite db helpers.

Exercises the actual SQL we hand-translated from psycopg2 against a
real on-disk SQLite. Catches things a static `python -c "import"`
sanity check can't: bad column names, wrong placeholder count,
JSON-encoding mismatches, ON CONFLICT typos, etc.

Run from the desktop/ directory:
    pytest tests/test_db_helpers.py -v
"""
from __future__ import annotations

import json

import pytest

from deal_finder.db import connection, migrate
from deal_finder.db.events import record_event
from deal_finder.db.geo import haversine_km
from deal_finder.db.listings import (
    existing_ids,
    filter_new_ids,
    upsert_processed,
    update_appraisal,
    update_comps_resolution,
    mark_notified,
)
from deal_finder.scraper.pipeline import ProcessedListing


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


def _fake_pl(*, listing_id: str = "fb-1", price: float = 250.0,
             rejected: bool = False) -> ProcessedListing:
    """Build a minimal ProcessedListing the upsert helper accepts."""
    return ProcessedListing(
        id=listing_id,
        title="Arduino Uno R3",
        photo_url="https://example.com/p.jpg",
        listing_url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        is_pending=False,
        previous_price=None,
        seller_location="Vancouver, BC",
        price_formatted="CA$250",
        description="brand new in box, mint condition",
        seller_name="seller",
        seller_type="user",
        listed_at_unix=None,
        detail_source="pdp",
        category_id=None,
        detail_errors=[],
        detail_latitude=49.28,
        detail_longitude=-123.12,
        raw_price=price,
        resolved_price=price,
        price_extracted_from_description=False,
        rejected=rejected,
        rejection_reason="bad listing" if rejected else None,
    )


# -- events.record_event ---------------------------------------------------

def test_record_event_writes_row(tmp_db):
    record_event("poll", search_id=42, duration_ms=850, raw_count=5,
                 new_count=2, appraised_count=1)
    conn = connection.get_connection()
    rows = conn.execute(
        "SELECT event_type, detail FROM scheduler_events"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "poll"
    detail = json.loads(rows[0]["detail"])
    assert detail["search_id"] == 42
    assert detail["duration_ms"] == 850
    assert detail["raw_count"] == 5


def test_record_event_with_no_detail_writes_null(tmp_db):
    record_event("scheduler_boot")
    conn = connection.get_connection()
    row = conn.execute(
        "SELECT detail FROM scheduler_events"
    ).fetchone()
    assert row["detail"] is None


def test_record_event_swallows_failures(tmp_db, caplog):
    """Even if the table doesn't exist, record_event must not raise."""
    conn = connection.get_connection()
    with conn:
        conn.execute("DROP TABLE scheduler_events")
    # Must not raise:
    record_event("anything", foo="bar")


# -- listings.upsert_processed --------------------------------------------

def test_upsert_processed_inserts_then_updates(tmp_db):
    pl = _fake_pl(listing_id="fb-1", price=250.0)
    conn = connection.get_connection()
    with conn:
        first = upsert_processed(conn, pl, search_id=None)
    assert first is True

    row = conn.execute(
        "SELECT id, title, price, rejected FROM listings WHERE id = 'fb-1'"
    ).fetchone()
    assert row["title"] == "Arduino Uno R3"
    assert row["price"] == 250.0
    assert row["rejected"] == 0

    # Second upsert — same id, different price
    pl2 = _fake_pl(listing_id="fb-1", price=199.0)
    with conn:
        second = upsert_processed(conn, pl2, search_id=None)
    assert second is False  # update path

    row = conn.execute(
        "SELECT price FROM listings WHERE id = 'fb-1'"
    ).fetchone()
    assert row["price"] == 199.0


def test_existing_ids_handles_empty_input(tmp_db):
    conn = connection.get_connection()
    assert existing_ids(conn, []) == set()
    assert existing_ids(conn, [None, ""]) == set()


def test_existing_ids_returns_seen_only(tmp_db):
    conn = connection.get_connection()
    with conn:
        upsert_processed(conn, _fake_pl(listing_id="fb-a"), search_id=None)
        upsert_processed(conn, _fake_pl(listing_id="fb-b"), search_id=None)
    seen = existing_ids(conn, ["fb-a", "fb-b", "fb-c"])
    assert seen == {"fb-a", "fb-b"}


def test_filter_new_ids_round_trip(tmp_db):
    conn = connection.get_connection()
    with conn:
        upsert_processed(conn, _fake_pl(listing_id="fb-x"), search_id=None)
    new = filter_new_ids(["fb-x", "fb-y", "fb-z"])
    assert set(new) == {"fb-y", "fb-z"}


# -- listings.update_appraisal --------------------------------------------

def test_update_appraisal_persists_score_and_breakdown(tmp_db):
    conn = connection.get_connection()
    with conn:
        upsert_processed(conn, _fake_pl(), search_id=None)
        update_appraisal(
            conn, "fb-1",
            deal_score=82,
            fair_value=180.0,
            appraisal_note="[high ±5]",
            appraisal_model="formula-only",
            breakdown={"deal_score": 82, "ratio": 0.74},
        )
    row = conn.execute(
        """SELECT deal_score, fair_value, appraisal_note,
                  appraisal_breakdown, appraised, appraised_at
           FROM listings WHERE id = 'fb-1'"""
    ).fetchone()
    assert row["deal_score"] == 82
    assert row["fair_value"] == 180.0
    assert row["appraised"] == 1
    assert row["appraised_at"] is not None
    assert row["appraisal_note"] == "[high ±5]"
    breakdown = json.loads(row["appraisal_breakdown"])
    assert breakdown["deal_score"] == 82


# -- listings.update_comps_resolution -------------------------------------

def test_update_comps_resolution(tmp_db):
    conn = connection.get_connection()
    with conn:
        upsert_processed(conn, _fake_pl(), search_id=None)
        update_comps_resolution(
            conn, "fb-1",
            search_term="arduino uno r3",
            source="ebay",
            median=210.0, mean=215.0, minimum=180.0, maximum=290.0,
            sample_size=8,
        )
    row = conn.execute(
        """SELECT comp_search_term, comp_source, comp_median, comp_sample_size
           FROM listings WHERE id = 'fb-1'"""
    ).fetchone()
    assert row["comp_search_term"] == "arduino uno r3"
    assert row["comp_source"] == "ebay"
    assert row["comp_median"] == 210.0
    assert row["comp_sample_size"] == 8


# -- listings.mark_notified -----------------------------------------------

def test_mark_notified_flips_flag(tmp_db):
    conn = connection.get_connection()
    with conn:
        upsert_processed(conn, _fake_pl(), search_id=None)
        mark_notified(conn, "fb-1")
    row = conn.execute(
        "SELECT notified, notified_at FROM listings WHERE id = 'fb-1'"
    ).fetchone()
    assert row["notified"] == 1
    assert row["notified_at"] is not None


# -- geo.haversine_km ------------------------------------------------------

def test_haversine_km_basic():
    # Vancouver -> Burnaby city centroid is ~12 km
    d = haversine_km(49.2827, -123.1207, 49.2488, -122.9805)
    assert 9 < d < 15


def test_haversine_km_zero_distance():
    assert haversine_km(49.0, -123.0, 49.0, -123.0) == pytest.approx(0.0, abs=1e-6)
