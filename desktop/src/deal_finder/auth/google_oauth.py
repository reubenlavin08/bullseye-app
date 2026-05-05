"""Google OAuth via Supabase Auth.

Why we host a local HTTP server: Supabase returns OAuth tokens in the
URL fragment (#access_token=...) NOT the query string, so a server
side handler can't read them. We serve a tiny HTML page that JS-reads
the fragment and POSTs it back to a /tokens endpoint on the same
local server.

Flow:
    1. Spin up an HTTP server on localhost:CALLBACK_PORT (53682)
    2. Open the user's browser to
         {SUPABASE_URL}/auth/v1/authorize?provider=google
            &redirect_to=http://localhost:53682/auth/callback
    3. User signs into Google, Google bounces through Supabase, Supabase
       302s back to our localhost callback with #access_token=&refresh_token=
    4. /auth/callback returns an HTML+JS page that:
         a. parses window.location.hash
         b. POSTs the tokens to /tokens
         c. shows "You can close this tab"
    5. /tokens stores them in keyring + signals the main thread
    6. run_login_flow returns the tokens

Why CALLBACK_PORT is fixed at 53682:
    Supabase's redirect-URL allowlist is configured per-project in
    the dashboard. The desktop app can't add entries dynamically.
    A fixed port means one-time setup; a dynamic port would require
    Supabase Management API calls on every launch.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import threading
import time
import urllib.parse
import webbrowser

from . import token_store

logger = logging.getLogger(__name__)

CALLBACK_PORT = 53682
LOGIN_TIMEOUT_S = 300       # 5 min for user to complete the OAuth dance


def _supabase_url() -> str:
    # Late import; keeps the auth module light.
    from ..cloud.client import _supabase_url as get_url
    return get_url()


# --- Local callback server -------------------------------------------------

_CALLBACK_HTML = b"""<!DOCTYPE html>
<html><head><title>Bullseye sign-in</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
max-width:480px;margin:80px auto;padding:24px;color:#1a1614;text-align:center}
h1{font-family:Georgia,serif;font-weight:500;letter-spacing:-0.02em}
.muted{color:#6b5d52;font-size:14px}</style></head>
<body>
<h1 id="msg">Signing you in...</h1>
<p class="muted" id="sub">This tab will close itself.</p>
<script>
(async function() {
  const params = new URLSearchParams(window.location.hash.substring(1));
  const access_token = params.get('access_token');
  const refresh_token = params.get('refresh_token');
  const error = params.get('error') || params.get('error_description');
  if (error) {
    document.getElementById('msg').innerText = 'Sign-in failed';
    document.getElementById('sub').innerText = decodeURIComponent(error);
    return;
  }
  if (!access_token || !refresh_token) {
    document.getElementById('msg').innerText = 'No tokens received';
    document.getElementById('sub').innerText = 'Try again from the app.';
    return;
  }
  try {
    await fetch('/tokens', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({access_token, refresh_token})});
    document.getElementById('msg').innerText = 'Signed in.';
    document.getElementById('sub').innerText = 'You can close this tab.';
    setTimeout(() => window.close(), 800);
  } catch (e) {
    document.getElementById('msg').innerText = 'Could not deliver tokens';
    document.getElementById('sub').innerText = String(e);
  }
})();
</script></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    """Single-request handler. Sets a class-level `tokens` attribute
    once /tokens is hit; the main thread polls for that signal."""

    tokens: dict | None = None
    error: str | None = None

    def log_message(self, *args, **kwargs):  # noqa: D401 — silence access log
        return

    def do_GET(self):  # noqa: N802 — http.server name
        # Browser landed here after Supabase OAuth completed. Serve
        # the HTML+JS shim that pulls tokens out of the URL fragment.
        path = urllib.parse.urlparse(self.path).path
        if path == "/auth/callback":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_CALLBACK_HTML)))
            self.end_headers()
            self.wfile.write(_CALLBACK_HTML)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # noqa: N802
        # JS shim from the callback page POSTs tokens here. We do NOT
        # set _Handler.error on malformed/missing-token POSTs because
        # the OAuth callback runs in the user's browser — a buggy
        # request could come in (browser extensions, network retries)
        # while the real OAuth flow is still pending. Treat bad POSTs
        # as transient: respond 400 and keep waiting for a good one.
        # _Handler.error is reserved for OAuth-side errors (signaled
        # by error= query param on the callback URL — handled in JS).
        path = urllib.parse.urlparse(self.path).path
        if path != "/tokens":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_response(400)
            self.end_headers()
            return
        access = body.get("access_token")
        refresh = body.get("refresh_token")
        if not access or not refresh:
            self.send_response(400)
            self.end_headers()
            return
        _Handler.tokens = {"access_token": access, "refresh_token": refresh}
        self.send_response(204)
        self.end_headers()


# --- Public API ------------------------------------------------------------

def run_login_flow(*, open_browser: bool = True) -> dict:
    """Block until login completes. Returns {access_token, refresh_token}.

    Side effect: tokens get persisted to the OS keychain via
    token_store.save() before this returns.

    Raises:
        TimeoutError    if the user doesn't complete in LOGIN_TIMEOUT_S
        RuntimeError    if Supabase returned an error
        OSError         if the callback port is already in use
    """
    # Reset class-level state so a second call works after a first.
    _Handler.tokens = None
    _Handler.error = None

    # Allow_reuse_address keeps re-launches snappy on Windows where
    # the OS sometimes holds the port for a few seconds after close.
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("localhost", CALLBACK_PORT), _Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        params = urllib.parse.urlencode({
            "provider": "google",
            "redirect_to": f"http://localhost:{CALLBACK_PORT}/auth/callback",
        })
        auth_url = f"{_supabase_url()}/auth/v1/authorize?{params}"
        logger.info("opening browser for Google OAuth")
        if open_browser:
            webbrowser.open(auth_url)

        # Poll for either tokens or an error. Sleep in 200ms ticks so
        # the user isn't waiting up to a full second after they finish.
        deadline = time.monotonic() + LOGIN_TIMEOUT_S
        while time.monotonic() < deadline:
            if _Handler.tokens is not None:
                tokens = _Handler.tokens
                token_store.save(tokens["access_token"], tokens["refresh_token"])
                return tokens
            if _Handler.error is not None:
                raise RuntimeError(f"OAuth error: {_Handler.error}")
            time.sleep(0.2)
        raise TimeoutError(
            f"Login did not complete within {LOGIN_TIMEOUT_S}s"
        )
    finally:
        server.shutdown()
        server.server_close()


def auth_url() -> str:
    """Return the URL the user should be sent to. Useful when the webapp
    wants to render its own 'Sign in with Google' button instead of
    auto-opening a browser."""
    params = urllib.parse.urlencode({
        "provider": "google",
        "redirect_to": f"http://localhost:{CALLBACK_PORT}/auth/callback",
    })
    return f"{_supabase_url()}/auth/v1/authorize?{params}"
