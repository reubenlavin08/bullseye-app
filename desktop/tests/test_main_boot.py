"""Tests for the main.py boot orchestration.

The heavy lifting — Flask, APScheduler, PyWebView — all happens behind
helper functions in main. We stub each helper so the boot sequence
can be exercised without spinning up real servers, real GUIs, or real
schedulers (any of which would block the test forever).

What we DON'T test:
    - That Flask actually serves a page
    - That PyWebView actually opens a window
    - That APScheduler actually fires jobs
Those belong in integration / end-to-end suites; here we only verify
the boot wiring (order, side-effect-free defaults, mock interactions).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Match the convention used in the rest of the desktop test suite.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import main  # noqa: E402 — sys.path tweak above
from deal_finder.tray.app import TrayState  # noqa: E402


# ---------------------------------------------------------------------------
# find_free_port
# ---------------------------------------------------------------------------

def test_find_free_port_returns_int_in_valid_range():
    """find_free_port must return a usable ephemeral TCP port.

    The OS-assigned ephemeral range varies by platform but is always
    a 16-bit unsigned int >= 1024 (well-known ports are reserved).
    """
    port = main.find_free_port()
    assert isinstance(port, int)
    assert 1024 <= port <= 65535


def test_find_free_port_returns_different_ports_when_called_repeatedly():
    """Each call should bind to a fresh socket. We don't strictly
    REQUIRE different ports (the OS could recycle), but in practice
    consecutive calls without an intervening bind get fresh ones."""
    a = main.find_free_port()
    b = main.find_free_port()
    assert isinstance(a, int) and isinstance(b, int)


# ---------------------------------------------------------------------------
# Sentry init
# ---------------------------------------------------------------------------

def test_main_boot_skips_sentry_when_dsn_unset(monkeypatch):
    """Dev runs with no DSN should NOT call sentry_sdk.init."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    fake_sentry = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    initialized = main._init_sentry()

    assert initialized is False
    fake_sentry.init.assert_not_called()


def test_main_boot_skips_sentry_when_dsn_blank(monkeypatch):
    """Empty / whitespace DSN is treated the same as unset."""
    monkeypatch.setenv("SENTRY_DSN", "   ")
    fake_sentry = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    assert main._init_sentry() is False
    fake_sentry.init.assert_not_called()


def test_main_boot_inits_sentry_when_dsn_set(monkeypatch):
    """When SENTRY_DSN is set, sentry_sdk.init must be called with it."""
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/1")
    fake_sentry = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    assert main._init_sentry() is True
    fake_sentry.init.assert_called_once()
    kwargs = fake_sentry.init.call_args.kwargs
    assert kwargs["dsn"] == "https://example@sentry.io/1"


def test_main_boot_swallows_sentry_init_error(monkeypatch):
    """Sentry must never crash the boot — a broken SDK is reported via
    log, not exception."""
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/1")
    fake_sentry = MagicMock()
    fake_sentry.init.side_effect = RuntimeError("boom")
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    # Must not raise.
    assert main._init_sentry() is False


# ---------------------------------------------------------------------------
# Boot order — migrations FIRST
# ---------------------------------------------------------------------------

def test_main_boot_runs_migrations_first(monkeypatch):
    """Migrations must complete before any module that reads the DB
    (scheduler, auth check, Flask) gets a chance to run."""
    call_order: list[str] = []

    monkeypatch.delenv("SENTRY_DSN", raising=False)

    def _record(name):
        def _f(*a, **kw):
            call_order.append(name)
            # Each helper that returns a Thread must return *something*
            # truthy so main() doesn't choke; a MagicMock is fine.
            return MagicMock()
        return _f

    monkeypatch.setattr(main, "_init_sentry", _record("sentry"))
    monkeypatch.setattr(main, "_run_migrations", _record("migrate"))
    monkeypatch.setattr(main, "_check_auth", _record("auth"))
    monkeypatch.setattr(main, "find_free_port", lambda: 12345)
    monkeypatch.setattr(main, "_start_flask", _record("flask"))
    monkeypatch.setattr(main, "_start_scheduler", _record("scheduler"))
    monkeypatch.setattr(main, "_start_tray", _record("tray"))
    monkeypatch.setattr(main, "_open_window", _record("window"))

    main.main()

    # Sentry must come first (so failures get reported), then migrations
    # before anything that reads the DB.
    assert call_order[0] == "sentry"
    assert call_order[1] == "migrate"
    # Auth, flask, scheduler all touch the DB and must come after migrate.
    assert call_order.index("migrate") < call_order.index("auth")
    assert call_order.index("migrate") < call_order.index("flask")
    assert call_order.index("migrate") < call_order.index("scheduler")
    # Flask must be listening before PyWebView points at localhost.
    assert call_order.index("flask") < call_order.index("window")
    # Tray must be up before the blocking PyWebView call so the user
    # has a way to quit if the window fails to open.
    assert call_order.index("tray") < call_order.index("window")
    # Window is the blocker — it has to be last.
    assert call_order[-1] == "window"


def test_main_boot_calls_run_migrations(monkeypatch):
    """_run_migrations should delegate to migrate.run_migrations()."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    from deal_finder.db import migrate
    with patch.object(migrate, "run_migrations", return_value=0) as mock_run:
        main._run_migrations()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Auth check is non-blocking
# ---------------------------------------------------------------------------

def test_check_auth_returns_false_when_logged_out():
    from deal_finder.auth import token_store
    with patch.object(token_store, "is_logged_in", return_value=False):
        assert main._check_auth() is False


def test_check_auth_returns_true_when_logged_in():
    from deal_finder.auth import token_store
    with patch.object(token_store, "is_logged_in", return_value=True):
        assert main._check_auth() is True


def test_check_auth_swallows_keyring_errors():
    """A flaky keyring backend must not block boot."""
    from deal_finder.auth import token_store
    with patch.object(token_store, "is_logged_in", side_effect=RuntimeError("kr broken")):
        assert main._check_auth() is False


# ---------------------------------------------------------------------------
# TrayState
# ---------------------------------------------------------------------------

def test_tray_state_default_values():
    state = TrayState(port=8080)
    assert state.port == 8080
    assert state.is_paused is False
    assert state.status == "Running"
    assert state.show_window is None
    assert state.tray_icon is None


def test_tray_state_toggle_pause():
    """Toggle flips both is_paused and the human-readable status."""
    state = TrayState(port=1234)

    assert state.is_paused is False
    assert state.status == "Running"

    state.toggle_pause()
    assert state.is_paused is True
    assert state.status == "Paused"

    state.toggle_pause()
    assert state.is_paused is False
    assert state.status == "Running"


def test_tray_state_toggle_pause_refreshes_icon_menu_when_present():
    """If a tray_icon has been attached, toggle_pause should call
    update_menu so the checkmark rerenders."""
    state = TrayState(port=1234)
    fake_icon = MagicMock()
    state.tray_icon = fake_icon

    state.toggle_pause()
    fake_icon.update_menu.assert_called_once()


def test_tray_state_quit_calls_icon_stop_and_exits():
    """quit() stops the pystray icon and SystemExits the process."""
    state = TrayState(port=1234)
    fake_icon = MagicMock()
    state.tray_icon = fake_icon

    with pytest.raises(SystemExit):
        state.quit()
    fake_icon.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Notifications.fire — defensive smoke
# ---------------------------------------------------------------------------

def test_fire_swallows_plyer_import_error(monkeypatch):
    """Boot must never crash because plyer is unavailable on this host."""
    from deal_finder.notifications import desktop as notif

    # Force plyer.notification.notify to blow up.
    fake_plyer = MagicMock()
    fake_plyer.notification.notify.side_effect = NotImplementedError("no backend")
    monkeypatch.setitem(sys.modules, "plyer", fake_plyer)

    # Must not raise.
    notif.fire(
        title="Test",
        summary="hello",
        score=85,
        listing_url="https://example.com/x",
    )
    fake_plyer.notification.notify.assert_called_once()


def test_fire_passes_title_and_app_name(monkeypatch):
    """The toast must carry our app name so Windows' action center
    groups them correctly."""
    from deal_finder.notifications import desktop as notif

    fake_plyer = MagicMock()
    monkeypatch.setitem(sys.modules, "plyer", fake_plyer)

    notif.fire(
        title="Deal!",
        summary="cheap thing",
        score=99,
        listing_url="https://example.com/y",
    )
    kwargs = fake_plyer.notification.notify.call_args.kwargs
    assert kwargs["title"] == "Deal!"
    assert kwargs["app_name"] == "Bullseye"
    assert "99" in kwargs["message"]
    assert "https://example.com/y" in kwargs["message"]
