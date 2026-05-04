"""Anonymous usage telemetry (opt-out, default on).

Events get batched in-memory and flushed every 60s or when the buffer
hits 50 events, whichever first. Never blocks the main thread.

Schema (matches `cloud/.../migrations/005_telemetry.sql`):
    {
        install_id: UUID (generated on first run, stored in SQLite),
        event_name: 'app_open' | 'watch_created' | 'alert_fired' | ...,
        properties: arbitrary JSON,
        app_version: from __version__,
    }

Events the gamification layer will need (v1.1):
    - app_open, app_close
    - watch_created, watch_deleted, watch_paused
    - alert_fired (with score)
    - email_sent, email_clicked
    - upgrade_clicked, trial_started, trial_converted, trial_expired
    - error_seen (with error type)

Track ALL of these from v1.0 even though gamification is post-launch —
they accumulate so the v1.1 unlock system has historical data to
read on day one.
"""
from __future__ import annotations


def emit(event_name: str, properties: dict | None = None) -> None:
    """Buffer one event. Non-blocking. Drops silently if buffer is full
    (we'd rather lose telemetry than block a user action)."""
    # TODO: append to thread-safe buffer; background flusher will send
    raise NotImplementedError


def flush() -> None:
    """Send the buffer to `/telemetry` and clear it. Called by
    background timer + on app shutdown."""
    # TODO: client.post('telemetry', {'events': [...]})
    raise NotImplementedError


def get_install_id() -> str:
    """Stable per-installation UUID. Generated on first run, persisted
    in SQLite. Anonymous (no auth user link)."""
    raise NotImplementedError
