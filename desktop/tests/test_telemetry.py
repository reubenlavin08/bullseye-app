"""Tests for cloud.telemetry — the v1.1 retention foundation.

These cover the hot paths only:
    1. emit + flush roundtrip (mocked client.post)
    2. buffer overflow triggers a flush
    3. opt-out drops events silently
    4. install_id is stable across calls
    5. emit never raises (network errors, bad input, anything)
    6. background flusher thread is daemon (won't block exit)

Network is fully mocked via `patch.object(cloud.telemetry.client.client,
'post')`, so no real cloud calls escape the test process.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from deal_finder.cloud import telemetry
from deal_finder.cloud.client import CloudUnavailable
from deal_finder.db import connection, migrate


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Per-test SQLite DB at a fresh path with migrations applied.
    Also resets telemetry's module-level caches so install_id +
    opt-out don't leak between tests."""
    db_path = tmp_path / "telemetry_test.db"
    monkeypatch.setenv("BULLSEYE_DB_PATH", str(db_path))
    connection.reset_for_tests()
    connection.set_db_path(str(db_path))
    migrate.run_migrations()
    telemetry.reset_caches_for_tests()
    yield db_path
    telemetry.reset_caches_for_tests()
    connection.reset_for_tests()
    connection.set_db_path(None)


# --- 1. emit + flush roundtrip --------------------------------------------

def test_emit_then_flush_posts_to_cloud(fresh_db):
    """A single emit + flush should result in exactly one cloud call
    carrying the event in the {events: [...]} envelope."""
    with patch("deal_finder.cloud.client.client.post",
               return_value={"inserted": 1}) as mock_post:
        telemetry.emit("app_open", {"foo": "bar"})
        telemetry.flush()

    mock_post.assert_called_once()
    args, _ = mock_post.call_args
    assert args[0] == "telemetry"
    payload = args[1]
    assert "events" in payload
    assert len(payload["events"]) == 1
    ev = payload["events"][0]
    assert ev["event_name"] == "app_open"
    assert ev["properties"] == {"foo": "bar"}
    assert ev["install_id"]            # non-empty UUID
    assert "app_version" in ev


# --- 2. buffer overflow triggers flush ------------------------------------

def test_buffer_overflow_triggers_flush(fresh_db, monkeypatch):
    """When the buffer crosses BUFFER_FLUSH_THRESHOLD, emit must spawn
    a background flush. We assert the cloud post is called by the
    burst flusher thread within a small wait window."""
    # Lower threshold so we don't have to enqueue 50 events.
    monkeypatch.setattr(telemetry, "BUFFER_FLUSH_THRESHOLD", 3)

    flushed = threading.Event()

    def _fake_post(endpoint, payload):
        flushed.set()
        return {"inserted": len(payload.get("events", []))}

    with patch("deal_finder.cloud.client.client.post",
               side_effect=_fake_post):
        for i in range(3):
            telemetry.emit(f"e{i}")
        # The burst flusher runs on its own thread; give it a moment.
        assert flushed.wait(timeout=2.0), \
            "buffer-overflow flush did not fire within 2s"


# --- 3. opt-out drops events ----------------------------------------------

def test_opt_out_drops_events(fresh_db):
    """When telemetry_opt_out is set in app_state, emit must be a
    no-op and flush must not call the cloud."""
    conn = connection.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO app_state (key, value)
               VALUES ('telemetry_opt_out', '1')
               ON CONFLICT(key) DO UPDATE SET value = '1'""",
        )
    telemetry.reset_caches_for_tests()

    with patch("deal_finder.cloud.client.client.post") as mock_post:
        telemetry.emit("app_open")
        telemetry.emit("watch_created", {"id": 1})
        telemetry.flush()

    mock_post.assert_not_called()


# --- 4. install_id is stable ----------------------------------------------

def test_install_id_is_stable_across_calls(fresh_db):
    """Repeated calls must return the SAME UUID, and it must persist
    across cache resets (i.e. live in app_state)."""
    a = telemetry.get_install_id()
    b = telemetry.get_install_id()
    assert a == b
    # New "process" simulation — clear caches but keep the DB. The
    # install_id row in app_state should still drive the result.
    telemetry.reset_caches_for_tests()
    c = telemetry.get_install_id()
    assert c == a


# --- 5. emit never raises -------------------------------------------------

def test_emit_swallows_all_errors(fresh_db):
    """emit() must NEVER raise — caller never has to wrap it."""
    # Bad event_name types: must not raise.
    telemetry.emit("")              # type: ignore[arg-type]
    telemetry.emit(None)            # type: ignore[arg-type]
    telemetry.emit(123)             # type: ignore[arg-type]

    # Non-serializable property — emit truncates it gracefully.
    telemetry.emit("x", {"obj": object()})

    # Cloud post raises — flush() must not propagate.
    with patch("deal_finder.cloud.client.client.post",
               side_effect=CloudUnavailable("offline")):
        telemetry.emit("alert_clicked")
        telemetry.flush()           # would raise if not handled


def test_emit_drops_oversized_properties(fresh_db):
    """A 4KB+ property blob must be replaced with a marker, not crash."""
    big = {"k": "x" * 5000}
    captured: list[dict] = []

    def _capture(endpoint, payload):
        captured.append(payload)
        return {"inserted": len(payload["events"])}

    with patch("deal_finder.cloud.client.client.post", side_effect=_capture):
        telemetry.emit("watch_created", big)
        telemetry.flush()

    assert captured, "expected one cloud call"
    ev = captured[0]["events"][0]
    # Either the marker is present or properties is the small marker dict.
    assert ev["properties"] != big
    assert isinstance(ev["properties"], dict)
    assert "_telemetry_error" in ev["properties"]


# --- 6. flusher thread is daemon ------------------------------------------

def test_background_flusher_is_daemon(fresh_db):
    """The periodic flusher MUST be a daemon thread; otherwise process
    exit blocks until the next tick."""
    with patch("deal_finder.cloud.client.client.post",
               return_value={"inserted": 0}):
        telemetry.emit("app_open")
        # The flusher is started lazily on first emit.
        time.sleep(0.05)
    t = telemetry._flusher_thread     # noqa: SLF001 — test-only access
    assert t is not None, "flusher thread should exist after emit"
    assert t.daemon is True, "flusher MUST be daemon (else exit hangs)"
