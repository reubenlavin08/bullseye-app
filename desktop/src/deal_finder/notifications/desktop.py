"""Native desktop notifications via `plyer`.

Both free and paid tiers get desktop toasts (it's the cheap, always-on
delivery mechanism). The DIFFERENCE between tiers is email behavior:
free gets a daily 8am digest, paid gets instant emails with batching.

Clicking a toast should open the listing URL. plyer's basic API
doesn't support click handlers; we may need to drop down to
`win10toast-click` on Windows for that, but only if user complaints
warrant it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# How long the toast stays on screen, in seconds. plyer's Windows
# backend silently ignores values above ~10 (Windows action-center
# default), so we keep it conservative.
_DEFAULT_TIMEOUT_S = 10
_APP_NAME = "Bullseye"


def fire(*, title: str, summary: str, score: int, listing_url: str) -> None:
    """Fire one toast. Non-blocking, fire-and-forget.

    Plyer's `notification.notify` returns immediately on every supported
    platform — the OS schedules the toast and we move on. The `score`
    and `listing_url` arguments are kept in the signature even though
    plyer's plain-text API can't render a clickable link or a numeric
    badge: callers (digest worker, instant alerts) build their toast
    payloads with all four fields and we only embed what plyer can show.

    If plyer's backend is broken on the host platform (very common on
    Windows when the WinRT fallback can't be loaded, or on stripped-down
    Linux desktops without notify-osd / dbus), we swallow the error and
    log a warning. A missing toast must never crash the scheduler.
    """
    # plyer is imported lazily so test code can monkey-patch it without
    # the real package being installed, and so import-time failures on
    # weird Linux desktops don't take down the whole app.
    try:
        from plyer import notification  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001 — plyer can fail at import time
        logger.warning("plyer import failed; toast dropped: %s", e)
        return

    # Bullseye toasts always include the score in the body so the user
    # can decide at a glance whether to click through. The listing URL
    # is appended on its own line — plyer can't make it clickable, but
    # it lets the user copy/paste from the action-center history.
    body = f"Score {score}\n{summary}\n{listing_url}"

    try:
        notification.notify(
            title=title,
            message=body,
            app_name=_APP_NAME,
            timeout=_DEFAULT_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001 — backend can raise NotImplementedError, OSError, ...
        logger.warning("plyer notify failed; toast dropped: %s", e)
