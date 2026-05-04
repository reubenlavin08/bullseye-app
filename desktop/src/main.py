"""Bullseye desktop entry point.

Orchestrates the full process tree at startup. The order matters:
Sentry must init before anything else can throw; DB migrations must
finish before the scheduler reads from it; auth must complete (or the
user must be on the login screen) before the cloud client makes any
calls; Flask must be up before PyWebView opens its window pointed at
localhost.

Boot sequence:
    1. Sentry.init  -> crash reports start flowing
    2. DB migrate   -> SQLite schema up to date
    3. Auth check   -> JWT in keyring, or run OAuth flow
    4. Find free port + start Flask in a daemon thread
    5. Start scheduler in a daemon thread (only polls if user is logged in)
    6. Start digest worker in a daemon thread
    7. Build State object for tray menu
    8. Start tray icon in a daemon thread
    9. Open PyWebView window (BLOCKS — webview.start runs the GUI loop)

Closing the window hides it instead of quitting; the tray keeps running.
Selecting "Quit" from the tray is the only way to fully exit.
"""
from __future__ import annotations

# TODO: real imports
# import sys, socket
# from threading import Thread
# import sentry_sdk, webview
# from deal_finder.db import migrate
# from deal_finder.auth import token_store, google_oauth
# from deal_finder.scheduler import main as scheduler_main
# from deal_finder.alerts import digest
# from deal_finder.tray import app as tray_app
# from webapp import app as flask_app


def find_free_port() -> int:
    """Bind to port 0 and let the OS pick a free port."""
    raise NotImplementedError


def main() -> None:
    """Boot the app. See module docstring for sequence."""
    # 1. Sentry
    # sentry_sdk.init(dsn=..., release=...)

    # 2. DB migrate
    # migrate.run_migrations()

    # 3. Auth
    # if not token_store.load_jwt():
    #     google_oauth.run_login_flow()

    # 4. Flask thread
    # port = find_free_port()
    # Thread(target=lambda: flask_app.run(port=port), daemon=True).start()

    # 5. Scheduler thread
    # Thread(target=scheduler_main.run, daemon=True).start()

    # 6. Digest worker thread
    # Thread(target=digest.run_loop, daemon=True).start()

    # 7. State for tray
    # state = State(port=port)

    # 8. Tray thread
    # Thread(target=lambda: tray_app.run(state), daemon=True).start()

    # 9. PyWebView (blocks)
    # window = webview.create_window("Bullseye", f"http://localhost:{port}")
    # window.events.closing += lambda: window.hide() or False
    # webview.start()

    raise NotImplementedError("Phase 2 — wire all modules above.")


if __name__ == "__main__":
    main()
