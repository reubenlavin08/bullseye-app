"""Anonymous usage telemetry (opt-out, default on).

Events get batched in-memory and flushed every 60s or when the buffer
hits 50 events, whichever first. Never blocks the main thread; never
raises (a telemetry bug must not crash the user's session).

Schema (matches `cloud/.../migrations/005_telemetry.sql`):
    {
        install_id: UUID (generated on first run, stored in SQLite),
        event_name: 'app_open' | 'watch_created' | 'alert_fired' | ...,
        properties: arbitrary JSON,
        app_version: from __version__,
    }

Events the gamification layer (v1.1) will read:
    - app_open, app_close
    - watch_created, watch_deleted, watch_paused, watch_unpaused
    - alert_fired (with score)              [scheduler-side, wired step 7]
    - alert_email_sent                      [cloud-side, wired separately]
    - alert_clicked                         [breakdown endpoint hit]
    - listing_appraised (with deal_score)   [scheduler post-compute_score]
    - upgrade_clicked                       [/api/checkout/start]
    - trial_started, trial_converted,
      trial_expired_no_convert              [cloud-side via stripe-webhook]
    - error_seen (with error_type)          [excepthook + Flask handler]

Track ALL of these from v1.0 even though gamification is post-launch —
they accumulate so the v1.1 unlock system has historical data to read
on day one.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Tunables — module-level so tests can monkeypatch.
FLUSH_INTERVAL_S = 60
BUFFER_FLUSH_THRESHOLD = 50
MAX_BUFFER_SIZE = 500           # hard cap; drops oldest on overflow
MAX_PROPERTIES_BYTES = 4096     # mirrors cloud function cap
MAX_EVENTS_PER_REQUEST = 100    # mirrors cloud function cap

# In-memory state. Guarded by `_lock` for all reads and writes.
_lock = threading.Lock()
_buffer: list[dict[str, Any]] = []
_install_id_cache: str | None = None
_opt_out_cache: bool | None = None
_flusher_thread: threading.Thread | None = None
_flusher_stop = threading.Event()


# ---------------------------------------------------------------------------
# install_id + opt-out (persisted in app_state)
# ---------------------------------------------------------------------------

def _read_app_state(key: str) -> str | None:
    """Read one app_state row. Returns None on missing key OR any DB
    error — telemetry never raises out of the helper."""
    try:
        from ..db.connection import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        # sqlite3.Row supports both index and key access.
        try:
            return row["value"]
        except (IndexError, KeyError):
            return row[0]
    except Exception as e:  # noqa: BLE001 — telemetry never raises
        logger.debug("telemetry app_state read failed (%s): %s", key, e)
        return None


def _write_app_state(key: str, value: str) -> bool:
    """Upsert one app_state row. Returns False on any error."""
    try:
        from ..db.connection import get_connection
        conn = get_connection()
        with conn:
            conn.execute(
                """INSERT INTO app_state (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry app_state write failed (%s): %s", key, e)
        return False


def get_install_id() -> str:
    """Stable per-installation UUID. Generated on first run, persisted
    in SQLite. Anonymous (no auth user link).

    Cached in memory after the first call so we don't hit SQLite for
    every emit. The cache key is module-global so it survives across
    threads.
    """
    global _install_id_cache
    if _install_id_cache is not None:
        return _install_id_cache

    existing = _read_app_state("install_id")
    if existing:
        _install_id_cache = existing
        return _install_id_cache

    new_id = str(uuid.uuid4())
    if _write_app_state("install_id", new_id):
        _install_id_cache = new_id
    else:
        # DB write failed — return the freshly-minted id but DO NOT
        # cache it; we'll try again on next call.
        return new_id
    return _install_id_cache


def is_opted_out() -> bool:
    """Read telemetry_opt_out from app_state. Default: opt-in (False)."""
    global _opt_out_cache
    if _opt_out_cache is not None:
        return _opt_out_cache
    raw = _read_app_state("telemetry_opt_out")
    if raw is None:
        _opt_out_cache = False
        return False
    _opt_out_cache = str(raw).strip() in ("1", "true", "yes", "on")
    return _opt_out_cache


def reset_caches_for_tests() -> None:
    """Clear module-level caches. Pytest fixtures use this between
    cases — install_id and opt-out flag are sticky otherwise."""
    global _install_id_cache, _opt_out_cache
    with _lock:
        _install_id_cache = None
        _opt_out_cache = None
        _buffer.clear()


# ---------------------------------------------------------------------------
# Buffer + emit
# ---------------------------------------------------------------------------

def _truncate_properties(props: dict | None) -> dict | None:
    """Drop properties that would exceed MAX_PROPERTIES_BYTES once
    JSON-serialized. Better to send the event with empty props than
    have the cloud reject the whole batch."""
    if not props:
        return props or None
    try:
        encoded = json.dumps(props, default=str)
    except Exception:  # noqa: BLE001 — non-serializable user input
        return {"_telemetry_error": "non_serializable_properties"}
    if len(encoded.encode("utf-8")) <= MAX_PROPERTIES_BYTES:
        return props
    return {"_telemetry_error": "properties_too_large",
            "_size_bytes": len(encoded.encode("utf-8"))}


def emit(event_name: str, properties: dict | None = None) -> None:
    """Buffer one event. Non-blocking, never raises.

    Drops silently if the user has opted out. Drops oldest if the
    buffer is at MAX_BUFFER_SIZE — we'd rather lose an old event than
    block a user action or grow memory unboundedly while offline.

    Triggers a flush on a background thread when the buffer crosses
    BUFFER_FLUSH_THRESHOLD; the periodic flusher handles the steady
    state.
    """
    try:
        if not isinstance(event_name, str) or not event_name:
            return
        if is_opted_out():
            return

        # Lazy-resolve install_id; we want the very first emit to
        # generate it if needed.
        install_id = get_install_id()

        # Lazy-import __version__ to dodge a circular import during
        # package init.
        try:
            from .. import __version__
        except Exception:  # noqa: BLE001
            __version__ = "unknown"

        event = {
            "event_name": event_name,
            "properties": _truncate_properties(properties),
            "app_version": __version__,
            "install_id": install_id,
            "client_ts": time.time(),
        }

        should_flush = False
        with _lock:
            if len(_buffer) >= MAX_BUFFER_SIZE:
                # Drop oldest to make room. This is a "shouldn't happen"
                # path — only triggered by sustained offline + high
                # event rate.
                _buffer.pop(0)
            _buffer.append(event)
            if len(_buffer) >= BUFFER_FLUSH_THRESHOLD:
                should_flush = True

        # Make sure the periodic flusher is running. Cheap idempotent
        # check; the function itself is no-op if a thread already
        # exists.
        _ensure_flusher_started()

        if should_flush:
            # Fire-and-forget: flush on a one-shot worker thread so
            # the caller doesn't block on network. This is the
            # "buffer overflow" path; the periodic flusher handles
            # the calm path.
            t = threading.Thread(target=_flush_safely,
                                 name="telemetry-flush-burst",
                                 daemon=True)
            t.start()
    except Exception as e:  # noqa: BLE001 — emit MUST NOT raise
        logger.debug("telemetry emit dropped event %r: %s", event_name, e)


# ---------------------------------------------------------------------------
# Flush
# ---------------------------------------------------------------------------

def _flush_safely() -> None:
    """flush() with all exceptions swallowed. Used by background
    workers where a raise would otherwise kill the thread."""
    try:
        flush()
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry background flush failed: %s", e)


def flush() -> None:
    """Send the buffer to /telemetry and clear it. Called by
    background timer + on app shutdown.

    Sends in chunks of MAX_EVENTS_PER_REQUEST. On any error the
    failing chunk is DROPPED (not re-buffered). Telemetry is
    best-effort — we'd rather lose a batch than retry forever and
    drown the cloud function in duplicates after a long offline
    stretch.
    """
    with _lock:
        if not _buffer:
            return
        pending = list(_buffer)
        _buffer.clear()

    # Lazy-import the client to avoid circulars during package init.
    try:
        from . import client as client_mod
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry: cloud.client import failed: %s", e)
        return

    for i in range(0, len(pending), MAX_EVENTS_PER_REQUEST):
        chunk = pending[i:i + MAX_EVENTS_PER_REQUEST]
        try:
            client_mod.client.post("telemetry", {"events": chunk})
        except Exception as e:  # noqa: BLE001 — drop on any failure
            logger.debug(
                "telemetry: dropping %d events (cloud error: %s)",
                len(chunk), e,
            )


# ---------------------------------------------------------------------------
# Background flusher thread
# ---------------------------------------------------------------------------

def _flusher_loop() -> None:
    """Run forever (daemon). Wakes every FLUSH_INTERVAL_S to drain
    the buffer. Exits cleanly when _flusher_stop is set (used on
    shutdown so the join() in main.py doesn't hang the exit)."""
    while not _flusher_stop.is_set():
        # `wait` returns True if the event was set, False on timeout.
        if _flusher_stop.wait(FLUSH_INTERVAL_S):
            break
        _flush_safely()


def _ensure_flusher_started() -> None:
    """Start the periodic flusher thread on first emit. Idempotent —
    callable from any thread, only spawns one worker."""
    global _flusher_thread
    # Cheap fast-path without taking the lock.
    if _flusher_thread is not None and _flusher_thread.is_alive():
        return
    with _lock:
        if _flusher_thread is not None and _flusher_thread.is_alive():
            return
        _flusher_stop.clear()
        t = threading.Thread(
            target=_flusher_loop,
            name="telemetry-flusher",
            daemon=True,                # MUST be daemon so it never
        )                                # blocks process exit.
        t.start()
        _flusher_thread = t


def shutdown(timeout: float = 2.0) -> None:
    """Stop the flusher and drain the buffer. Called from main.py on
    tray-Quit so the final batch lands before the process exits.

    `timeout` caps how long we'll wait for the cloud post to return —
    a slow network must NOT delay shutdown more than this.
    """
    _flusher_stop.set()
    t = _flusher_thread
    if t is not None and t.is_alive():
        try:
            t.join(timeout=0.1)   # let the loop exit; don't block long
        except Exception:  # noqa: BLE001
            pass

    # Best-effort final drain. Run on a worker thread so we can cap
    # the wait at `timeout` even if the cloud post hangs.
    drainer = threading.Thread(target=_flush_safely,
                               name="telemetry-final-flush",
                               daemon=True)
    drainer.start()
    drainer.join(timeout=timeout)
