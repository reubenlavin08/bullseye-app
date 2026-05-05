"""Tests for auth/token_store and auth/google_oauth.

Token store is mocked via a fake keyring so we don't pollute the
real OS keychain during CI or local pytest runs.

Google OAuth is tested by invoking the local HTTP callback handler
directly (no browser, no real Supabase round-trip). We POST to the
/tokens endpoint with a mock client to verify the handler stores
tokens correctly and the main loop returns them.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# --- Fake keyring ---------------------------------------------------------

class _FakeKeyring:
    """In-memory replacement for the real keyring backend."""
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise KeyError((service, username))
        del self.store[(service, username)]


@pytest.fixture
def fake_keyring():
    fake = _FakeKeyring()
    from deal_finder.auth import token_store
    with patch.object(token_store, "_get_keyring", return_value=fake):
        yield fake


# --- token_store ----------------------------------------------------------

def test_save_load_roundtrip(fake_keyring):
    from deal_finder.auth import token_store
    token_store.save("jwt-A", "refresh-A")
    assert token_store.load_jwt() == "jwt-A"
    assert token_store.load_refresh_token() == "refresh-A"
    assert token_store.is_logged_in() is True


def test_clear_wipes_both(fake_keyring):
    from deal_finder.auth import token_store
    token_store.save("jwt-A", "refresh-A")
    token_store.clear()
    assert token_store.load_jwt() is None
    assert token_store.load_refresh_token() is None
    assert token_store.is_logged_in() is False


def test_clear_when_already_empty_is_idempotent(fake_keyring):
    from deal_finder.auth import token_store
    # Should not raise even though there's nothing in the store
    token_store.clear()
    token_store.clear()


def test_save_rejects_empty(fake_keyring):
    from deal_finder.auth import token_store
    with pytest.raises(ValueError):
        token_store.save("", "refresh")
    with pytest.raises(ValueError):
        token_store.save("jwt", "")


def test_save_overwrites_previous(fake_keyring):
    from deal_finder.auth import token_store
    token_store.save("v1", "r1")
    token_store.save("v2", "r2")
    assert token_store.load_jwt() == "v2"
    assert token_store.load_refresh_token() == "r2"


def test_load_returns_none_when_empty(fake_keyring):
    from deal_finder.auth import token_store
    assert token_store.load_jwt() is None
    assert token_store.load_refresh_token() is None
    assert token_store.is_logged_in() is False


def test_load_swallows_keyring_errors(fake_keyring):
    """If the OS keychain backend errors (e.g. user denied access),
    load returns None instead of raising — calling code should treat
    that as logged-out, prompt re-login."""
    from deal_finder.auth import token_store

    class BrokenKeyring:
        def get_password(self, *a, **kw):
            raise RuntimeError("keychain access denied")

    with patch.object(token_store, "_get_keyring", return_value=BrokenKeyring()):
        assert token_store.load_jwt() is None
        assert token_store.load_refresh_token() is None


# --- google_oauth ---------------------------------------------------------

def test_auth_url_has_required_params():
    """The URL we send the user to must have provider=google and
    redirect_to=our local callback. Anything else means the OAuth
    dance can't return tokens."""
    from deal_finder.auth import google_oauth
    url = google_oauth.auth_url()
    # urlencode percent-encodes the redirect_to value, so check for
    # the port number directly — works regardless of encoding case.
    assert "provider=google" in url
    assert str(google_oauth.CALLBACK_PORT) in url, (
        f"callback port not in url: {url}"
    )
    # /auth/callback may or may not be percent-encoded; accept either.
    assert ("/auth/callback" in url) or ("%2Fauth%2Fcallback" in url), (
        f"callback path not in url: {url}"
    )
    assert url.startswith("https://")  # Supabase URL is HTTPS


def test_run_login_flow_completes_when_tokens_posted(fake_keyring):
    """Spin up run_login_flow in a thread, simulate the callback POST
    that the JS shim would make from the user's browser, verify it
    captures the tokens + persists to keyring."""
    from deal_finder.auth import google_oauth, token_store

    # Run the flow in a thread; we'll POST tokens to it from the test thread.
    result: dict[str, dict | Exception] = {}

    def runner():
        try:
            result["tokens"] = google_oauth.run_login_flow(open_browser=False)
        except Exception as e:  # noqa: BLE001
            result["err"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    # Wait for server to bind. Poll the callback endpoint with GET to
    # detect liveness; the local server is up the moment GET succeeds.
    callback = f"http://localhost:{google_oauth.CALLBACK_PORT}/auth/callback"
    for _ in range(50):
        try:
            with urllib.request.urlopen(callback, timeout=0.5) as r:
                if r.status == 200:
                    break
        except urllib.error.URLError:
            time.sleep(0.1)
    else:
        pytest.fail("local OAuth server didn't come up")

    # Simulate the JS shim's POST.
    body = json.dumps({
        "access_token": "test-jwt-9876",
        "refresh_token": "test-rt-9876",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:{google_oauth.CALLBACK_PORT}/tokens",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        assert r.status == 204

    # Wait for the runner to return.
    t.join(timeout=5)
    assert "tokens" in result, f"runner did not finish; err={result.get('err')}"
    assert result["tokens"]["access_token"] == "test-jwt-9876"
    assert result["tokens"]["refresh_token"] == "test-rt-9876"

    # Tokens should now be in the (fake) keyring.
    assert token_store.load_jwt() == "test-jwt-9876"
    assert token_store.load_refresh_token() == "test-rt-9876"


def test_callback_handler_rejects_bad_post(fake_keyring):
    """Sending malformed JSON or missing tokens should not signal
    completion — the runner must keep waiting and eventually time out."""
    from deal_finder.auth import google_oauth

    # Use a much shorter timeout for this test
    with patch.object(google_oauth, "LOGIN_TIMEOUT_S", 1.5):
        result: dict = {}

        def runner():
            try:
                google_oauth.run_login_flow(open_browser=False)
            except TimeoutError as e:
                result["timeout"] = str(e)
            except Exception as e:  # noqa: BLE001
                result["err"] = e

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        # Wait for server up
        time.sleep(0.3)
        # Send a malformed POST — should NOT complete the flow.
        try:
            req = urllib.request.Request(
                f"http://localhost:{google_oauth.CALLBACK_PORT}/tokens",
                data=b"not-json",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=1)
        except urllib.error.HTTPError:
            # 400 is expected
            pass

        t.join(timeout=5)
        # Should have timed out, not succeeded
        assert "timeout" in result, (
            f"expected TimeoutError, got result={result}"
        )
