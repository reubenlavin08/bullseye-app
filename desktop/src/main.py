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


def _resolve_icon_path() -> Path | None:
    """Locate logo.ico relative to the running process.

    PyInstaller bundle: the spec includes assets/ via `datas`, which
    PyInstaller unpacks to `sys._MEIPASS/assets/`. Dev run: assets/
    sits two parents above this file (desktop/assets/).

    Returns None if the file doesn't exist on either path — caller
    should treat that as "no override; fall back to default behavior".
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "logo.ico")
    candidates.append(
        Path(__file__).resolve().parent.parent / "assets" / "logo.ico"
    )
    for p in candidates:
        if p.exists():
            return p
    return None


def _set_app_user_model_id() -> None:
    """Tell Windows our process is its own app, with its own taskbar
    identity, NOT just another instance of `python.exe` or whatever
    `sys.executable` resolves to.

    Without this, Win11's taskbar groups our running window under the
    icon associated with the executable's path in IconCache.db. That
    cache is keyed by path + LastWriteTime; if the path is reused
    (which it always is when the user reinstalls Bullseye over the
    previous install), Windows keeps showing the OLD icon regardless
    of what's now embedded in the .exe.

    SetCurrentProcessExplicitAppUserModelID gives us our own AUMID
    namespace. Win11 then resolves the taskbar icon via the form's
    Icon property (which pywebview sets from `_state['icon']`),
    bypassing the stale IconCache.db entry entirely.

    AUMID format: "CompanyName.ProductName.SubProduct.Version" —
    Microsoft's recommended convention. Must be set BEFORE the first
    window of the process is created.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "app.getbullseye.desktop"
        )
        logger.info("AUMID set: app.getbullseye.desktop")
    except Exception as e:  # noqa: BLE001
        logger.debug("AUMID set failed (non-fatal): %s", e)


def _invalidate_shell_icon_cache() -> None:
    """Tell Windows the file association for our .exe changed so the
    shell drops any cached icon for it. Cheap and safe — does not
    delete IconCache.db, just nudges the shell to re-query.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None,
        )
        logger.debug("shell icon cache invalidated")
    except Exception as e:  # noqa: BLE001
        logger.debug("SHChangeNotify failed (non-fatal): %s", e)


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

                # FIRST: set the BIG icon (taskbar + alt-tab) to our
                # bundled logo.ico. Pywebview's WinForms backend
                # creates the window with .NET's default form Icon,
                # which overrides whatever we baked into the .exe
                # resource — the running window's taskbar entry
                # would otherwise show .NET's placeholder glyph
                # regardless of what's in logo.ico.
                #
                # Strategy: LoadImageW from the bundled logo.ico path,
                # then WM_SETICON ICON_BIG. We pass LR_LOADFROMFILE +
                # LR_DEFAULTSIZE so Windows picks the largest size
                # available in the .ico (32x32 or 48x48 typically for
                # Win11 taskbar at 100% scale; 64x64 at high DPI).
                try:
                    import sys as _sys2
                    from pathlib import Path as _P
                    if hasattr(_sys2, "_MEIPASS"):
                        # PyInstaller bundle — assets unpack to _MEI*/assets/
                        ico_path = _P(_sys2._MEIPASS) / "assets" / "logo.ico"
                    else:
                        # Dev run — relative to this file
                        ico_path = (_P(__file__).resolve().parent.parent
                                    / "assets" / "logo.ico")

                    if ico_path.exists():
                        WM_SETICON = 0x0080
                        ICON_BIG = 1
                        IMAGE_ICON = 1
                        LR_LOADFROMFILE = 0x00000010
                        LR_DEFAULTSIZE = 0x00000040
                        LR_SHARED = 0x00008000

                        LoadImageW = ctypes.windll.user32.LoadImageW
                        LoadImageW.argtypes = [
                            ctypes.c_void_p, ctypes.c_wchar_p,
                            ctypes.c_uint32, ctypes.c_int, ctypes.c_int,
                            ctypes.c_uint32,
                        ]
                        LoadImageW.restype = ctypes.c_void_p
                        # Load big variant for taskbar (request 32x32;
                        # Windows will pick the closest available size
                        # in the multi-size .ico).
                        big_hicon = LoadImageW(
                            None, str(ico_path), IMAGE_ICON,
                            32, 32,
                            LR_LOADFROMFILE | LR_SHARED,
                        )
                        if big_hicon:
                            SendMessageW2 = ctypes.windll.user32.SendMessageW
                            SendMessageW2.argtypes = [
                                ctypes.c_void_p, ctypes.c_uint32,
                                ctypes.c_void_p, ctypes.c_void_p,
                            ]
                            SendMessageW2.restype = ctypes.c_void_p
                            SendMessageW2(hwnd, WM_SETICON, ICON_BIG, big_hicon)

                            # Also override the WNDCLASS big icon so
                            # any later focus-change re-query gets
                            # our logo, not .NET's default.
                            GCLP_HICON = -14
                            SetClassLongPtrW2 = ctypes.windll.user32.SetClassLongPtrW
                            SetClassLongPtrW2.argtypes = [
                                ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p,
                            ]
                            SetClassLongPtrW2.restype = ctypes.c_void_p
                            SetClassLongPtrW2(hwnd, GCLP_HICON, big_hicon)
                            logger.info(
                                "taskbar icon set from %s", ico_path,
                            )
                        else:
                            logger.warning(
                                "LoadImageW returned NULL for %s", ico_path,
                            )
                    else:
                        logger.warning(
                            "logo.ico not found at %s — taskbar icon will "
                            "use the .NET WinForms default", ico_path,
                        )
                except Exception as e:  # noqa: BLE001
                    logger.debug("taskbar icon set failed: %s", e)

                # NEXT: kill the small Bullseye icon in the TITLE BAR
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
                # ICON_BIG / GCLP_HICON were ALREADY set above to our
                # logo.ico — that's the taskbar + alt-tab icon the
                # user wants to see. Here we only override the SMALL
                # variants (title bar) with a transparent 16x16.
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

    # Wire the tray "Open Bullseye" action AND the second-launch IPC
    # signal to re-show the window when one exists, instead of opening
    # a fresh browser tab. Goal: when the user has minimized to tray
    # and then double-clicks the desktop/start-menu icon (which spawns
    # a duplicate Bullseye that dies on the single-instance lock), the
    # ALREADY-RUNNING window pops to the front.
    #
    # window.show() alone un-hides but doesn't foreground when the
    # request arrives from another thread (Win32 focus-stealing
    # protection). The HWND_TOPMOST → HWND_NOTOPMOST flip below
    # bypasses that — it raises the z-order without touching focus.
    def _show_window():
        try:
            window.show()
        except Exception as e:  # noqa: BLE001
            logger.debug("window.show failed: %s", e)
        try:
            window.restore()  # un-minimize if minimized
        except Exception as e:  # noqa: BLE001
            logger.debug("window.restore failed: %s", e)
        if sys.platform == "win32":
            try:
                import ctypes
                FindWindowW = ctypes.windll.user32.FindWindowW
                FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
                FindWindowW.restype = ctypes.c_void_p
                hwnd = FindWindowW(None, "Bullseye")
                if hwnd:
                    HWND_TOPMOST = -1
                    HWND_NOTOPMOST = -2
                    SWP_NOMOVE = 0x0002
                    SWP_NOSIZE = 0x0001
                    SWP_SHOWWINDOW = 0x0040
                    SetWindowPos = ctypes.windll.user32.SetWindowPos
                    SetWindowPos.argtypes = [
                        ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        ctypes.c_uint,
                    ]
                    SetWindowPos.restype = ctypes.c_int
                    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                    # Topmost briefly to force z-order, then drop the flag
                    # so the window doesn't stay always-on-top.
                    SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST),
                                 0, 0, 0, 0, flags)
                    SetWindowPos(hwnd, ctypes.c_void_p(HWND_NOTOPMOST),
                                 0, 0, 0, 0, flags)
            except Exception as e:  # noqa: BLE001
                logger.debug("Win32 foreground hop failed: %s", e)

    state.show_window = _show_window

    # Resolve and pass the icon to pywebview's start(). On Windows the
    # WinForms backend reads `_state['icon']` (despite a misleading
    # docstring claiming GTK/QT-only) and assigns it to Form.Icon —
    # which is the canonical property Win11 uses for the running-window
    # taskbar entry. This is the right level of intervention; my prior
    # WM_SETICON code was a workaround for a non-bug.
    icon_path = _resolve_icon_path()
    if icon_path:
        logger.info("PyWebView icon: %s", icon_path)
    else:
        logger.warning(
            "logo.ico not found — taskbar icon will fall back to "
            "the executable's resource icon (and through Windows "
            "icon cache, possibly to a stale entry)"
        )

    logger.info("opening PyWebView window at http://127.0.0.1:%d", port)
    if icon_path:
        webview.start(icon=str(icon_path))
    else:
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


_SINGLE_INSTANCE_PORT = 47823
_SHOW_WINDOW_MSG = b"SHOW\n"


def _acquire_single_instance_lock():
    """Bind a fixed local port as a single-instance flag. Returns the
    bound socket on success; returns None if another instance already
    owns the port (i.e. Bullseye is already running).

    The bound socket also doubles as an IPC channel — see
    `_serve_show_requests` below. When a second launch attempt fails
    to bind here, it connects to this port and sends `SHOW\n` so the
    first instance can foreground its (often hidden-to-tray) window.
    Without that, double-clicking the app icon while Bullseye is
    already running silently does nothing — the user thinks the app
    is broken.

    Why a TCP port and not a named mutex:
        Cross-platform-friendly (works the same on macOS / Linux), no
        ctypes, no Win32 imports, no permission issues. Port 47823 is
        in the IANA dynamic range and unlikely to collide. If the port
        IS already in use by something unrelated, we degrade to "treat
        as duplicate and exit" — slightly annoying but not destructive.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
        return None


def _signal_existing_instance_to_show() -> bool:
    """Connect to the running Bullseye and ask it to foreground its
    window. Returns True iff the message was delivered. Caller should
    exit the duplicate process either way — we already lost the
    single-instance race.
    """
    try:
        with socket.create_connection(
            ("127.0.0.1", _SINGLE_INSTANCE_PORT), timeout=2.0,
        ) as s:
            s.sendall(_SHOW_WINDOW_MSG)
        return True
    except OSError:
        return False


def _serve_show_requests(server_sock, show_callback) -> None:
    """Loop accepting connections on the single-instance port. Each
    connection should send `SHOW\n`; we call `show_callback()` to
    foreground the existing window. Runs forever in a daemon thread.

    Built deliberately tiny — no auth, no protocol versioning. The
    socket only listens on 127.0.0.1, so only processes on the same
    machine can connect, and the only side effect is showing our own
    window. If the message doesn't match exactly, we ignore it.
    """
    server_sock.settimeout(None)
    while True:
        try:
            conn, _addr = server_sock.accept()
        except OSError:
            return  # socket closed during shutdown
        try:
            conn.settimeout(2.0)
            data = conn.recv(64)
            if data and data.strip() == _SHOW_WINDOW_MSG.strip():
                try:
                    show_callback()
                except Exception as e:  # noqa: BLE001
                    logger.debug("show_callback raised: %s", e)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    """Boot the app. See module docstring for sequence."""
    # 0. Single-instance guard. Exit immediately if another Bullseye is
    #    already running so the bullseye:// protocol activation from the
    #    Stripe success page doesn't spawn duplicate processes.
    _instance_lock = _acquire_single_instance_lock()
    if _instance_lock is None:
        # Another instance owns the port. Tell it to bring its window
        # to the foreground (handles the "I closed the X, now clicking
        # the icon does nothing" UX trap) then exit. We never spawn a
        # duplicate Bullseye process — duplicate Flask, duplicate
        # scheduler, SQLite contention is way worse than a no-op.
        signaled = _signal_existing_instance_to_show()
        try:
            from pathlib import Path as _P
            log_path = _P(os.environ.get("APPDATA", ".")) / "Bullseye" / "bullseye.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                from datetime import datetime as _dt
                fh.write(
                    f"{_dt.utcnow().isoformat()}Z INFO single-instance: "
                    f"another Bullseye is already running, "
                    f"signaled-show={signaled} (argv={sys.argv[1:]})\n"
                )
        except Exception:  # noqa: BLE001
            pass
        return
    # Hold the socket on the module-level so it doesn't get GC'd until
    # the process exits. atexit will close it implicitly when Python
    # shuts down (no explicit cleanup needed).
    globals()["_INSTANCE_LOCK_SOCKET"] = _instance_lock

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

    # 0. Windows-only: set our AppUserModelID and nudge the shell to
    #    invalidate any cached icon for our exe path. Both must happen
    #    BEFORE any window is created — pywebview's first window
    #    creation reads the AUMID; later changes are ignored by Win11.
    _set_app_user_model_id()
    _invalidate_shell_icon_cache()

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

    # 8a. Start the IPC listener that handles SHOW requests from
    # second-launch attempts. The callback reads state.show_window
    # late so we don't care that _open_window hasn't wired it yet —
    # by the time a SHOW request arrives, the user has already had
    # the window open at least once and show_window is live.
    def _on_show_request():
        cb = state.show_window
        if cb is not None:
            cb()
    Thread(
        target=_serve_show_requests,
        args=(_instance_lock, _on_show_request),
        name="ipc-show-listener",
        daemon=True,
    ).start()

    # 9. PyWebView (blocks the main thread until all windows close).
    _open_window(port, state)


if __name__ == "__main__":
    main()
