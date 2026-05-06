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

import atexit
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

    # Match the in-app warm off-white so the brief blank flash before
    # Flask responds doesn't show a stark white panel inside the title
    # bar. Windows still draws its own dark/light title bar (we can't
    # control that from pywebview without a Win32 hack), but we kill
    # the inner pure-white flash. Color is --bg from shell.css.
    window = webview.create_window(
        "Bullseye",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=820,
        min_size=(960, 640),
        background_color="#fafaf7",
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

    # Windows: force a light-mode title bar that matches the warm
    # off-white app background. By default Windows draws the title bar
    # in the system theme — dark Windows users see a black bar over our
    # off-white app, which looks broken.
    #
    # Two-pronged approach:
    #   1. DWMWA_USE_IMMERSIVE_DARK_MODE = 0  →  light caption + buttons
    #      (Win10 1809+; Win11 honors this fully)
    #   2. DWMWA_CAPTION_COLOR = 0x00f7faff   →  Win11 only, paints the
    #      bar to match --bg (#fafaf7). Color is BGR (0x00BBGGRR).
    #
    # Both calls fail-soft: any error here just leaves the user with the
    # system default. We retry over a few seconds because pywebview's
    # `events.shown` sometimes fires before the HWND is fully realized
    # (especially on Win11 with WebView2 cold-start).
    def _apply_light_titlebar():
        """Find the Bullseye window by title and force its title bar
        to light mode + white caption color via DWM API.

        Why FindWindow instead of pywebview attributes: pywebview's
        `native_window` / `_native_window` properties on Windows
        return a winforms wrapper object whose pointer-equivalent
        isn't a raw HWND we can pass to ctypes — DwmSetWindowAttribute
        gets a bogus handle and silently fails. FindWindow with the
        exact window title returns the real HWND.

        Title is 'Bullseye' (matches webview.create_window's first
        arg). If two windows ever exist with the same title we'd
        grab whichever Windows finds first; for v1 we only have one.
        """
        import sys as _sys
        if _sys.platform != "win32":
            return
        import ctypes
        import time as _time

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_CAPTION_COLOR = 35  # Win11 only
        DWMWA_TEXT_COLOR = 36     # Win11 only — caption foreground

        # COLORREF is 0x00BBGGRR (low byte = R). Match the app's
        # SIDEBAR background --bg-sunk #f3efe7 — the warmer off-white
        # the sidebar (top-left of every page) actually uses. Matching
        # to --bg #fafaf7 looked seamy because the sidebar tone runs
        # right up to the title bar; matching to the sidebar makes
        # the warm beige run unbroken from the title bar through the
        # logo strip and down. The right-edge mismatch with the main
        # content (#fafaf7) is barely perceptible since both are warm
        # off-whites and the right edge has cards layered on top.
        bg_colorref = 0x00E7EFF3
        # Caption text: SAME as bg → "Bullseye" caption text becomes
        # invisible. We have a sidebar-header logo a few px below;
        # showing the same word twice looked redundant. The taskbar
        # tooltip + alt-tab still read "Bullseye" from the window
        # title (which we keep set), this just kills the text in
        # the title bar itself.
        text_colorref = 0x00E7EFF3

        FindWindowW = ctypes.windll.user32.FindWindowW
        FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        FindWindowW.restype = ctypes.c_void_p
        DwmSet = ctypes.windll.dwmapi.DwmSetWindowAttribute
        DwmSet.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        DwmSet.restype = ctypes.c_int32

        # Poll for up to 8s for the window to appear with the right title.
        # Pywebview's WebView2 cold-start can take 1-3s on first launch.
        deadline = _time.monotonic() + 8.0
        attempts = 0
        while _time.monotonic() < deadline:
            attempts += 1
            hwnd = FindWindowW(None, "Bullseye")
            if hwnd:
                try:
                    light = ctypes.c_int(0)  # 0 = light mode
                    DwmSet(
                        hwnd,
                        DWMWA_USE_IMMERSIVE_DARK_MODE,
                        ctypes.byref(light),
                        ctypes.sizeof(light),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("DWMWA_USE_IMMERSIVE_DARK_MODE failed: %s", e)
                try:
                    caption = ctypes.c_uint32(bg_colorref)
                    DwmSet(
                        hwnd,
                        DWMWA_CAPTION_COLOR,
                        ctypes.byref(caption),
                        ctypes.sizeof(caption),
                    )
                except Exception as e:  # noqa: BLE001
                    # Win10 doesn't support CAPTION_COLOR; the dark-mode
                    # flag alone still gives white bar with black text.
                    logger.debug("DWMWA_CAPTION_COLOR failed: %s", e)
                try:
                    text = ctypes.c_uint32(text_colorref)
                    DwmSet(
                        hwnd,
                        DWMWA_TEXT_COLOR,
                        ctypes.byref(text),
                        ctypes.sizeof(text),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("DWMWA_TEXT_COLOR failed: %s", e)

                # Kill the small Bullseye icon in the title bar
                # (the duplicate of the sidebar logo immediately
                # below it). Setting WM_SETICON to NULL doesn't
                # suffice — Win11 paints a default placeholder glyph
                # in the slot when no icon is present (small green-
                # square thing the user spotted). The fix is to set
                # an explicit FULLY TRANSPARENT 16x16 icon, so the
                # slot is "filled" but renders as zero pixels.
                #
                # CreateIcon takes an AND mask + XOR mask. For a
                # transparent icon: AND=all 1s (every pixel is
                # transparent), XOR=all 0s (irrelevant when AND
                # makes the pixel transparent anyway). 16x16
                # monochrome = 16 rows × 2 bytes/row = 32 bytes per
                # mask.
                #
                # We deliberately DO NOT touch ICON_BIG / GCLP_HICON
                # — those drive the taskbar + alt-tab icon, which
                # the user does want to see.
                try:
                    WM_SETICON = 0x0080
                    ICON_SMALL = 0
                    ICON_SMALL2 = 2
                    GCLP_HICONSM = -34

                    CreateIcon = ctypes.windll.user32.CreateIcon
                    CreateIcon.argtypes = [
                        ctypes.c_void_p,        # hInstance
                        ctypes.c_int,           # width
                        ctypes.c_int,           # height
                        ctypes.c_ubyte,         # planes
                        ctypes.c_ubyte,         # bits per pixel
                        ctypes.c_char_p,        # AND mask
                        ctypes.c_char_p,        # XOR mask
                    ]
                    CreateIcon.restype = ctypes.c_void_p
                    and_bits = b"\xff" * 32   # all transparent
                    xor_bits = b"\x00" * 32   # all black (masked out)
                    blank_hicon = CreateIcon(
                        None, 16, 16, 1, 1, and_bits, xor_bits,
                    )

                    SendMessageW = ctypes.windll.user32.SendMessageW
                    SendMessageW.argtypes = [
                        ctypes.c_void_p, ctypes.c_uint32,
                        ctypes.c_void_p, ctypes.c_void_p,
                    ]
                    SendMessageW.restype = ctypes.c_void_p
                    SendMessageW(hwnd, WM_SETICON, ICON_SMALL, blank_hicon)
                    SendMessageW(hwnd, WM_SETICON, ICON_SMALL2, blank_hicon)

                    # Override the WNDCLASS small icon too, so any
                    # window message that re-queries the class icon
                    # (Win11 likes to do this on focus change) gets
                    # the blank one back, not the original.
                    SetClassLongPtrW = ctypes.windll.user32.SetClassLongPtrW
                    SetClassLongPtrW.argtypes = [
                        ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p,
                    ]
                    SetClassLongPtrW.restype = ctypes.c_void_p
                    SetClassLongPtrW(hwnd, GCLP_HICONSM, blank_hicon)

                    # Force a non-client area redraw so the change
                    # takes effect immediately instead of only on
                    # the next focus change.
                    SetWindowPos = ctypes.windll.user32.SetWindowPos
                    SWP_NOMOVE = 0x0002
                    SWP_NOSIZE = 0x0001
                    SWP_NOZORDER = 0x0004
                    SWP_FRAMECHANGED = 0x0020
                    SetWindowPos(
                        hwnd, None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                        | SWP_FRAMECHANGED,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("title-bar icon clear failed: %s", e)

                logger.info(
                    "light titlebar applied to hwnd 0x%x (after %d attempts)",
                    hwnd, attempts,
                )
                return
            _time.sleep(0.15)

        logger.warning(
            "light titlebar: FindWindow('Bullseye') returned 0 within "
            "8s — title bar may stay dark",
        )

    # Run in a daemon thread so we don't block webview.start(). The
    # thread polls until the window appears, applies the DWM tweaks,
    # and exits. Also still hook events.shown as a faster path for
    # backends that expose a real HWND there.
    window.events.shown += _apply_light_titlebar
    Thread(
        target=_apply_light_titlebar, name="titlebar", daemon=True,
    ).start()

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


def _wire_telemetry() -> None:
    """Emit app_open, register an atexit flush, and install an excepthook
    that emits error_seen for every uncaught exception.

    Telemetry is fire-and-forget: every call here is wrapped to never
    raise. A crashing telemetry layer must not crash the app.
    """
    try:
        from deal_finder.cloud import telemetry
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry import failed; skipping wiring: %s", e)
        return

    try:
        telemetry.emit("app_open")
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry app_open emit failed: %s", e)

    # On normal exit (tray Quit -> sys.exit) atexit fires before the
    # interpreter tears down. Emit app_close then drain the buffer
    # with a short timeout so we don't block exit on a slow network.
    def _on_exit():
        try:
            telemetry.emit("app_close")
            telemetry.shutdown(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_on_exit)

    # Sentry-equivalent "error_seen" path. Chain to the existing hook
    # (Sentry will have set one if SENTRY_DSN was present).
    prev_hook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        try:
            telemetry.emit("error_seen", {
                "error_type": getattr(exc_type, "__name__", str(exc_type)),
                "where": "excepthook",
            })
        except Exception:  # noqa: BLE001
            pass
        try:
            prev_hook(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = _excepthook


def main() -> None:
    """Boot the app. See module docstring for sequence."""
    # Two log handlers: stderr (visible if launched from a console) and
    # a rotating file at %APPDATA%/Bullseye/bullseye.log so the user can
    # share the file when something breaks in the no-console PyInstaller
    # bundle. The file is the only diagnostic surface in production —
    # without it, OAuth or scheduler failures are completely opaque.
    log_handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        from deal_finder.auth.token_store import _user_data_dir
        log_path = _user_data_dir() / "bullseye.log"
        from logging.handlers import RotatingFileHandler
        log_handlers.append(
            RotatingFileHandler(
                str(log_path), maxBytes=1_000_000, backupCount=2,
                encoding="utf-8",
            )
        )
    except Exception:  # noqa: BLE001 — log file is non-critical
        pass

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        handlers=log_handlers,
        force=True,  # override any prior basicConfig
    )

    # 1. Sentry first — so failures in the rest of boot get reported.
    _init_sentry()

    # 2. Migrate before any other module touches the DB.
    _run_migrations()

    # 3. Auth check (non-blocking — missing tokens just send the user to /login).
    _check_auth()

    # 3a. Telemetry: emit app_open + register shutdown hooks. Must come
    # AFTER migrations (the install_id row lives in app_state) but
    # BEFORE Flask/scheduler so we capture early-boot crashes too.
    _wire_telemetry()

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
