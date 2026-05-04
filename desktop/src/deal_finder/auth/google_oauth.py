"""Google OAuth flow via Supabase Auth.

Opens a browser to Supabase's OAuth endpoint, listens on a local HTTP
callback for the redirect, and parses tokens from the URL fragment via
a tiny in-page JS shim (Supabase puts tokens in `#fragment`, not
`?query`, so the server-side handler needs JS help to read them).

Flow:
    1. Spin up an HTTP server on localhost:CALLBACK_PORT
    2. Open browser to https://<project>.supabase.co/auth/v1/authorize
       ?provider=google&redirect_to=http://localhost:CALLBACK_PORT/auth/callback
    3. User signs in, Google redirects back through Supabase, Supabase
       redirects to our localhost callback with #access_token=...
    4. Our /auth/callback returns HTML that reads `window.location.hash`,
       POSTs tokens to /tokens, then displays "Login complete"
    5. /tokens stores tokens in keyring + signals the main thread

The CALLBACK_PORT is fixed (53682) because it's pre-configured in
Supabase's allowed redirect URIs. Future: dynamic port + register at
Supabase via API on first run.
"""
from __future__ import annotations

CALLBACK_PORT = 53682
LOGIN_TIMEOUT_S = 300  # 5 minutes for the user to complete the dance


def run_login_flow() -> dict:
    """Block until login is complete. Returns {access_token, refresh_token}.

    Raises TimeoutError if the user doesn't complete within
    LOGIN_TIMEOUT_S. Side effect: tokens get written to keyring via
    `token_store.save`.
    """
    # TODO: implement HTTP callback server + browser launch + token capture
    raise NotImplementedError
