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
    badge: callers build their toast payloads with all four fields and
    we only embed what plyer can show.

    If plyer's backend is broken on the host platform (Windows WinRT
    fallback, stripped-down Linux desktops without notify-osd / dbus),
    we swallow the error and log a warning. A missing toast must never
    crash the scheduler.

    Toast layout (refined 2026-05-07 after user feedback that the body
    looked cluttered):
      Title:  "Bullseye · score 89"
      Body:   "DJI Mini 3 Pro + Accessories
               Save $146 vs eBay comps"
    The score lives in the title only (was previously duplicated in
    body). The raw URL was dropped from the body — plyer can't make it
    clickable, and it made the toast read like a spam notification. The
    Activity feed in the app is the canonical "click to open" surface.
    """
    # plyer is imported lazily so test code can monkey-patch it without
    # the real package being installed, and so import-time failures on
    # weird Linux desktops don't take down the whole app.
    try:
        from plyer import notification  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001 — plyer can fail at import time
        logger.warning("plyer import failed; toast dropped: %s", e)
        return

    # Body is just the listing summary. The summary string (built by
    # the caller in scheduler/jobs.py) already includes "— save $X" when
    # we have a fair_value to compare against. So a typical toast body
    # reads cleanly as a single line: "DJI Mini 3 Pro — save $146".
    body = (summary or "").strip() or "(untitled listing)"

    try:
        notification.notify(
            title=title,
            message=body,
            app_name=_APP_NAME,
            timeout=_DEFAULT_TIMEOUT_S,
            # Pointing at the bundled .ico gives the toast a real brand
            # icon in the Action Center instead of the generic Python
            # placeholder. We probe a couple of candidate locations
            # because the file's location varies between dev runs and
            # PyInstaller-frozen runs.
            app_icon=_resolve_app_icon(),
        )
    except Exception as e:  # noqa: BLE001 — backend can raise NotImplementedError, OSError, ...
        logger.warning("plyer notify failed; toast dropped: %s", e)


_resolved_icon_path: str | None = None
_resolved_icon_checked: bool = False


def _resolve_app_icon() -> str | None:
    """Return the absolute path to the bundled .ico for use as toast
    icon, or None if it can't be found. Result is cached so we only
    walk the filesystem once per process.

    Search order:
      1. PyInstaller frozen bundle: sys._MEIPASS/<assets>/icon.ico
      2. Dev mode: desktop/assets/icon.ico relative to the source tree
      3. Anywhere on PATH (give up gracefully)
    """
    global _resolved_icon_path, _resolved_icon_checked
    if _resolved_icon_checked:
        return _resolved_icon_path
    _resolved_icon_checked = True

    import os
    import sys
    from pathlib import Path

    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "icon.ico")
        candidates.append(Path(meipass) / "icon.ico")
    here = Path(__file__).resolve()
    for parents in range(4, 8):
        try:
            candidates.append(here.parents[parents] / "assets" / "icon.ico")
        except IndexError:
            break

    for c in candidates:
        try:
            if c.exists() and c.is_file():
                _resolved_icon_path = str(c)
                return _resolved_icon_path
        except OSError:
            continue
    return None
