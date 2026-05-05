"""Bullseye desktop entry point.

Orchestrates the full process tree at startup. The order matters:
Sentry must init before anything else can throw; DB migrations must
finish before the scheduler reads from it; auth must complete (or the
user must be on the login screen) before the cloud client makes any
calls; Flask must be up before PyWebView opens its window pointed at
localhost.

Boot sequence:
    1. Sentry.init  -> crash reports start flowing (skipped if no DSN)
    2. DB migrate   -> SQLite schema up to date
    3. Auth check   -> non-blocking; webapp /login handles the OAuth flow
    4. Find free port + start Flask in a daemon thread
    5. Start scheduler in a daemon thread (only polls if user is logged in)
    6. (skipped — digest worker rewrites in step 7 against cloud.alerts)
    7. Build TrayState
    8. Start tray icon in a daemon thread
    9. Open PyWebView window (BLOCKS — webview.start runs the GUI loop)

Closing the window hides it instead of quitting; the tray keeps running.
Selecting "Quit" from the tray is the only way to fully exit.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
from pathlib import Path
from threading import Thread

logger = logging.getLogger(__name__)

# Make `deal_finder.*` and `webapp.*` importable when running this file
# directly (`python desktop/src/main.py`) or from a PyInstaller bundle.
# When installed as a package the imports work without this; the
# duplicate insert is a no-op in that case.
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def find_free_port() -> int:
    """Bind to port 0 and let the OS pick a free port.

    Closes the temporary socket before returning, so there's a brief
    race window where another process could grab the port before Flask
    binds. On a desktop app with one user that's fine — the alternative
    (carrying the bound socket into Flask) requires monkey-patching
    werkzeug, which isn't worth it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _init_sentry() -> bool:
    """Init sentry_sdk if SENTRY_DSN is set. Returns True iff init ran.

    Dev runs typically have no DSN — we silently skip rather than
    nagging the developer with init warnings on every launch. The DSN
    gets baked into the env at PyInstaller-build time (see step 10).
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("SENTRY_DSN unset; skipping Sentry init")
        return False
    try:
        import sentry_sdk  # type: ignore[import-not-found]
        sentry_sdk.init(
            dsn=dsn,
            release=os.environ.get("BULLSEYE_RELEASE"),
            traces_sample_rate=0.0,  # crashes only — don't pay for perf APM
        )
        logger.info("Sentry initialized")
        return True
    except Exception as e:  # noqa: BLE001 — never let crash reporting crash us
        logger.warning("Sentry init failed: %s", e)
        return False


def _run_migrations() -> None:
    """Apply any pending SQLite migrations. Must run BEFORE any other
    module touches the DB."""
    from deal_finder.db import migrate
    n = migrate.run_migrations()
    if n:
        logger.info("applied %d migration(s)", n)


def _check_auth() -> bool:
    """Return whether the user has tokens in the keychain.

    Non-blocking: a missing token is fine. The webapp redirects to
    /login when needed, and the OAuth callback runs from there. We
    log the state so a missing-tokens issue is easy to diagnose from
    the scheduler log.
    """
    from deal_finder.auth import token_store
    try:
        logged_in = bool(token_store.is_logged_in())
    except Exception as e:  # noqa: BLE001
        logger.warning("auth check failed (treating as logged out): %s", e)
        return False
    logger.info("auth: logged_in=%s", logged_in)
    return logged_in


def _start_flask(port: int) -> Thread:
    """Start Flask on 127.0.0.1:port in a daemon thread."""
    from webapp.app import app as flask_app

    def _run():
        try:
            flask_app.run(
                host="127.0.0.1",
                port=port,
                use_reloader=False,
                debug=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Flask crashed: %s", e)

    t = Thread(target=_run, name="flask", daemon=True)
    t.start()
    logger.info("Flask thread started on 127.0.0.1:%d", port)
    return t


def _start_scheduler() -> Thread:
    """Start the APScheduler in a daemon thread. The scheduler module
    exposes `run_forever()` which blocks on `BlockingScheduler.start`."""
    from deal_finder.scheduler import main as scheduler_main

    def _run():
        try:
            scheduler_main.run_forever()
        except Exception as e:  # noqa: BLE001
            logger.exception("scheduler crashed: %s", e)

    t = Thread(target=_run, name="scheduler", daemon=True)
    t.start()
    logger.info("scheduler thread started")
    return t


def _start_tray(state) -> Thread:
    """Start the pystray icon in a daemon thread."""
    from deal_finder.tray import app as tray_app

    def _run():
        try:
            tray_app.run(state)
        except Exception as e:  # noqa: BLE001
            logger.exception("tray crashed: %s", e)

    t = Thread(target=_run, name="tray", daemon=True)
    t.start()
    logger.info("tray thread started")
    return t


def _open_window(port: int, state) -> None:
    """Create the PyWebView window pointed at the local Flask server.
    BLOCKS until `webview.start()` returns (i.e. all windows closed).

    Closing the window HIDES it instead of quitting — the tray "Quit"
    item is the only real exit. We hook the `closing` event and return
    False from the handler to cancel the close, then call `hide()`.
    """
    import webview  # type: ignore[import-not-found]

    window = webview.create_window(
        "Bullseye",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=820,
        min_size=(960, 640),
    )

    def _on_closing():
        # Returning False cancels the OS-level window close; we then
        # hide the window so the tray can re-show it later. Any error
        # here lets the close proceed (we'd rather lose the window than
        # leave the user with a frozen UI).
        try:
            window.hide()
        except Exception as e:  # noqa: BLE001
            logger.debug("window.hide failed: %s", e)
            return True
        return False

    window.events.closing += _on_closing

    # Wire the tray "Open Bullseye" action so it re-shows the window
    # when one exists, instead of opening a fresh browser tab.
    def _show_window():
        try:
            window.show()
        except Exception as e:  # noqa: BLE001
            logger.debug("window.show failed: %s", e)

    state.show_window = _show_window

    logger.info("opening PyWebView window at http://127.0.0.1:%d", port)
    webview.start()


def main() -> None:
    """Boot the app. See module docstring for sequence."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    # 1. Sentry first — so failures in the rest of boot get reported.
    _init_sentry()

    # 2. Migrate before any other module touches the DB.
    _run_migrations()

    # 3. Auth check (non-blocking — missing tokens just send the user to /login).
    _check_auth()

    # 4. Free port + Flask thread.
    port = find_free_port()
    _start_flask(port)

    # 5. Scheduler thread.
    _start_scheduler()

    # 6. Digest worker — TODO(step 7): replace with cloud-side instant alerts.
    #    The scheduler currently registers a stub `send_digest_emails` job
    #    that no-ops; once the cloud alerts module lands, swap in a thread
    #    that subscribes to push notifications and calls
    #    `deal_finder.notifications.desktop.fire(...)` for each.

    # 7. Tray state — single object shared across threads.
    from deal_finder.tray.app import TrayState
    state = TrayState(port=port, is_paused=False, status="Running")

    # 8. Tray thread.
    _start_tray(state)

    # 9. PyWebView (blocks the main thread until all windows close).
    _open_window(port, state)


if __name__ == "__main__":
    main()
