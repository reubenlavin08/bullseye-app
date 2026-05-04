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


class TrayState:
    """Shared mutable state between the tray menu and the rest of the
    app. Passed as a single object so the tray callbacks can flip
    `is_paused`, read `status`, etc."""
    port: int
    is_paused: bool = False
    status: str = "Running"
    tray_icon = None  # pystray.Icon, set by run()

    def toggle_pause(self) -> None:
        raise NotImplementedError

    def quit(self) -> None:
        raise NotImplementedError


def run(state: TrayState) -> None:
    """Start the tray icon. BLOCKING — call from a daemon thread."""
    # TODO: pystray.Icon('Bullseye', icon_image, 'Bullseye', menu).run()
    raise NotImplementedError
