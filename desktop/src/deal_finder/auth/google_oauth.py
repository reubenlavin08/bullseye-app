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

# Both pages share the same off-white shell, Logo B, and Georgia
# headline so the OAuth round-trip feels like part of the app rather
# than a generic browser interlude. Tab dwells ~2s on success so the
# user actually sees the "Signed in" confirmation before the tab
# closes; previously it flashed and disappeared in <500ms.
_CALLBACK_HTML = b"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Bullseye - Signing in</title>
<style>
  html,body{margin:0;padding:0;background:#fafaf7;color:#1a1614;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#fff;border:1px solid #ebe2d4;border-radius:10px;
    padding:36px 40px;max-width:420px;width:calc(100% - 32px);
    box-shadow:0 1px 2px rgba(26,22,20,.04),0 4px 16px rgba(26,22,20,.06);
    text-align:center}
  .mark{width:44px;height:44px;margin:0 auto 14px;color:#1a1614}
  .kicker{font-size:11px;color:#6b5d52;text-transform:uppercase;
    letter-spacing:.10em;font-weight:600;margin-bottom:8px}
  h1{font-family:Georgia,serif;font-weight:400;letter-spacing:-0.02em;
    font-size:24px;margin:0 0 6px;color:#1a1614}
  p{color:#6b5d52;font-size:13px;margin:0;line-height:1.55}
  .spinner{display:inline-block;width:14px;height:14px;border:2px solid #ebe2d4;
    border-top-color:#c2410c;border-radius:50%;
    animation:spin 700ms linear infinite;vertical-align:-3px;margin-right:8px}
  @keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
  <div class="card">
    <svg class="mark" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx="14" cy="18" r="13" stroke="currentColor" stroke-width="1.25"/>
      <circle cx="14" cy="18" r="8"  stroke="currentColor" stroke-width="1.25"/>
      <circle cx="14" cy="18" r="3.5" fill="#c0202a"/>
      <line x1="22" y1="10" x2="14" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M22 10 L26 6 M22 10 L26 10 M22 10 L22 6" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/>
    </svg>
    <div class="kicker">Bullseye</div>
    <h1 id="msg"><span class="spinner"></span>Signing you in</h1>
    <p id="sub">This will only take a moment.</p>
  </div>
<script>
(function() {
  // Tokens come back from Supabase in the URL fragment (after #).
  // We CANNOT use fetch() to POST them - pywebview's WebView2 on
  // Windows is unreliable with localhost POST + JSON content-type
  // (silent "TypeError: Failed to fetch"). Pure GET works everywhere.
  const params = new URLSearchParams(window.location.hash.substring(1));
  const access_token = params.get('access_token');
  const refresh_token = params.get('refresh_token');
  const error = params.get('error') || params.get('error_description');
  if (error) {
    document.getElementById('msg').innerHTML = 'Sign-in failed';
    document.getElementById('sub').innerText = decodeURIComponent(error);
    return;
  }
  if (!access_token || !refresh_token) {
    document.getElementById('msg').innerHTML = 'No tokens received';
    document.getElementById('sub').innerText = 'Try again from the app.';
    return;
  }
  window.location.href = '/tokens?access_token='
    + encodeURIComponent(access_token)
    + '&refresh_token=' + encodeURIComponent(refresh_token);
})();
</script></body></html>"""


_SUCCESS_HTML = b"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Bullseye - Signed in</title>
<style>
  html,body{margin:0;padding:0;background:#fafaf7;color:#1a1614;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#fff;border:1px solid #ebe2d4;border-radius:10px;
    padding:36px 40px;max-width:420px;width:calc(100% - 32px);
    box-shadow:0 1px 2px rgba(26,22,20,.04),0 4px 16px rgba(26,22,20,.06);
    text-align:center}
  .check{width:44px;height:44px;margin:0 auto 14px;border-radius:50%;
    background:#e8eee2;display:flex;align-items:center;justify-content:center}
  .check svg{color:#5d7a4f}
  .kicker{font-size:11px;color:#6b5d52;text-transform:uppercase;
    letter-spacing:.10em;font-weight:600;margin-bottom:8px}
  h1{font-family:Georgia,serif;font-weight:400;letter-spacing:-0.02em;
    font-size:24px;margin:0 0 6px;color:#1a1614}
  p{color:#6b5d52;font-size:13px;margin:0;line-height:1.55}
</style></head>
<body>
  <div class="card">
    <div class="check" aria-hidden="true">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
    </div>
    <div class="kicker">Bullseye</div>
    <h1>Signed in</h1>
    <p>Returning you to the app. You can close this tab.</p>
  </div>
<script>setTimeout(function(){ window.close(); }, 1800);</script>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    """Single-request handler. Sets a class-level `tokens` attribute
    once /tokens is hit; the main thread polls for that signal."""

    tokens: dict | None = None
    error: str | None = None

    def log_message(self, *args, **kwargs):  # noqa: D401 — silence access log
        return

    def do_GET(self):  # noqa: N802 — http.server name
        # Two GET endpoints:
        #   /auth/callback  — serves the JS shim that converts URL
        #                     fragment to a query-string redirect.
        #   /tokens         — receives tokens via query params (the
        #                     redirect target). Saves them to the
        #                     class-level state and serves a success
        #                     page that auto-closes the tab.
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/auth/callback":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_CALLBACK_HTML)))
            self.end_headers()
            self.wfile.write(_CALLBACK_HTML)
            return

        if path == "/tokens":
            logger.info("OAuth: GET /tokens received")
            params = urllib.parse.parse_qs(parsed.query)
            access = params.get("access_token", [None])[0]
            refresh = params.get("refresh_token", [None])[0]
            if not access or not refresh:
                logger.warning(
                    "OAuth: /tokens missing fields. has_access=%s has_refresh=%s",
                    bool(access), bool(refresh),
                )
                err = b"<h1>Missing tokens</h1><p>Try again from the app.</p>"
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            logger.info(
                "OAuth: tokens received len(jwt)=%d len(rt)=%d",
                len(access), len(refresh),
            )
            _Handler.tokens = {"access_token": access, "refresh_token": refresh}
            # Save IMMEDIATELY here too, in addition to the run_login_flow
            # polling loop. If the polling thread already exited (race or
            # timeout), the GET handler still persists tokens directly.
            # token_store.save() writes to BOTH keychain AND file fallback,
            # so even if one path fails the other carries the auth state.
            try:
                token_store.save(access, refresh)
                logger.info("OAuth: token_store.save() ok from /tokens handler")
            except Exception as e:  # noqa: BLE001
                logger.exception("OAuth: token_store.save FAILED in /tokens: %s", e)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_SUCCESS_HTML)))
            self.end_headers()
            self.wfile.write(_SUCCESS_HTML)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):  # noqa: N802
        # Legacy: the previous design used POST + JSON. The new design
        # uses GET with query params (do_GET above) because pywebview's
        # WebView2 on Windows fails to deliver fetch() POSTs to localhost
        # reliably. We keep the POST handler around in case some flow
        # still tries it — same parse, same effect on _Handler.tokens.
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

    logger.info("OAuth: starting local callback server on port %d", CALLBACK_PORT)

    # Allow_reuse_address keeps re-launches snappy on Windows where
    # the OS sometimes holds the port for a few seconds after close.
    socketserver.TCPServer.allow_reuse_address = True
    try:
        server = socketserver.TCPServer(("localhost", CALLBACK_PORT), _Handler)
    except OSError as e:
        logger.error("OAuth: cannot bind port %d: %s", CALLBACK_PORT, e)
        raise
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    logger.info("OAuth: callback server bound and listening")

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
                logger.info("OAuth: polling thread caught tokens, saving")
                # Defensive — the GET /tokens handler now also saves, but
                # we save again here in case an earlier code path
                # (POST handler) set _Handler.tokens without saving.
                try:
                    token_store.save(tokens["access_token"], tokens["refresh_token"])
                except Exception as e:  # noqa: BLE001
                    logger.exception("OAuth: polling-thread save failed: %s", e)
                return tokens
            if _Handler.error is not None:
                raise RuntimeError(f"OAuth error: {_Handler.error}")
            time.sleep(0.2)
        logger.warning("OAuth: timed out after %ds without tokens", LOGIN_TIMEOUT_S)
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
