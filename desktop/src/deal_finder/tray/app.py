"""System tray icon + menu.

Why tray-first: the user's expectation for this kind of app is
"runs in the background, surfaces only when something happens." The
PyWebView window is incidental — close it, and the tray keeps
running, polling, firing toasts. Selecting "Quit" from the tray is
the one true exit.

Menu items:
    - Open Bullseye        -> opens browser to localhost:PORT (or
                              re-shows the hidden PyWebView window)
    - Pause polling        -> toggles state.is_paused; checked = paused
    - --- separator ---
    - Status: Running      -> non-clickable, shows live state
    - --- separator ---
    - Quit                 -> shuts down everything
"""
from __future__ import annotations

import logging
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Bundled placeholder icon. PyInstaller copies the assets/ directory
# into the bundle root via the .spec file (step 10 owns that wiring),
# so this relative path resolves the same way in dev and prod.
_ICON_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "assets" / "logo.png"
)


@dataclass
class TrayState:
    """Shared mutable state between the tray menu and the rest of the
    app. Passed as a single object so the tray callbacks can flip
    `is_paused`, read `status`, etc.

    Mutated from two threads (tray callbacks + scheduler/digest workers
    that read `is_paused` to decide whether to skip a tick). The fields
    are simple booleans / strings, so atomic-by-GIL is enough; no lock
    needed.
    """
    port: int = 0
    is_paused: bool = False
    status: str = "Running"
    # Optional callback to re-show a hidden PyWebView window. Set by
    # main.py after the window is created. If None, "Open Bullseye"
    # falls back to opening a browser tab.
    show_window: Callable[[], None] | None = None
    # The pystray.Icon, set inside `run()` so other threads can call
    # icon.update_menu() / icon.stop() if they need to.
    tray_icon: object = field(default=None, repr=False)

    def toggle_pause(self) -> None:
        """Flip the pause flag and refresh the menu so the checkmark
        rerenders. Called from the tray thread."""
        self.is_paused = not self.is_paused
        self.status = "Paused" if self.is_paused else "Running"
        logger.info("tray: pause toggled -> %s", self.is_paused)
        icon = self.tray_icon
        if icon is not None and hasattr(icon, "update_menu"):
            try:
                icon.update_menu()
            except Exception as e:  # noqa: BLE001
                logger.debug("tray update_menu failed: %s", e)

    def quit(self) -> None:
        """Stop the tray icon thread and exit the whole process. The
        tray "Quit" item is the one true exit — closing the PyWebView
        window only hides it."""
        logger.info("tray: quit requested")
        icon = self.tray_icon
        if icon is not None and hasattr(icon, "stop"):
            try:
                icon.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("tray stop failed: %s", e)
        # SystemExit propagates up the tray thread but we also want the
        # main thread (PyWebView) to die. webview.start() listens for
        # SystemExit on the main thread; sys.exit on a daemon thread
        # just kills the daemon. Use os._exit as a hard fallback so
        # the whole process really does exit.
        sys.exit(0)


def _open_bullseye(state: TrayState) -> None:
    """Tray menu callback: re-show the hidden PyWebView window if we
    have a handle to it, otherwise fall back to a browser tab."""
    if state.show_window is not None:
        try:
            state.show_window()
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("show_window callback failed: %s", e)
    webbrowser.open(f"http://localhost:{state.port}")


def _build_menu(state: TrayState):
    """Construct the pystray Menu. Imported lazily so tests don't need
    pystray installed at module-import time."""
    import pystray  # type: ignore[import-not-found]

    return pystray.Menu(
        pystray.MenuItem(
            "Open Bullseye",
            lambda icon, item: _open_bullseye(state),
            default=True,
        ),
        pystray.MenuItem(
            "Pause polling",
            lambda icon, item: state.toggle_pause(),
            checked=lambda item: state.is_paused,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: f"Status: {state.status}",
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Quit",
            lambda icon, item: state.quit(),
        ),
    )


def _load_icon_image():
    """Load the tray icon PNG via Pillow. Falls back to a solid red
    16x16 image if the bundled file is missing — better a generic
    square than an unhandled exception that kills the tray thread."""
    from PIL import Image  # type: ignore[import-not-found]

    if _ICON_PATH.is_file():
        return Image.open(_ICON_PATH)
    logger.warning("tray icon missing at %s; using fallback", _ICON_PATH)
    return Image.new("RGB", (16, 16), (200, 30, 30))


def run(state: TrayState) -> None:
    """Start the tray icon. BLOCKING — call from a daemon thread.

    pystray's `Icon.run()` is the canonical event loop on every
    platform: GTK on Linux, NSStatusBar on macOS, win32 message pump
    on Windows. It blocks until `icon.stop()` is called (from
    `state.quit()` or another thread).
    """
    import pystray  # type: ignore[import-not-found]

    icon_image = _load_icon_image()
    icon = pystray.Icon(
        "bullseye",
        icon=icon_image,
        title="Bullseye",
        menu=_build_menu(state),
    )
    state.tray_icon = icon
    logger.info("tray icon starting (port=%d)", state.port)
    try:
        icon.run()
    except Exception as e:  # noqa: BLE001 — never let the tray crash kill the app
        logger.exception("tray crashed: %s", e)
