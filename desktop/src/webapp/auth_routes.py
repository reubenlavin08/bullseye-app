"""Auth routes for the local Flask UI: /login, /logout, /api/auth/start.

The actual OAuth dance happens via `deal_finder.auth.google_oauth`,
which spawns its own callback server on localhost:53682. That's a
separate listener from this Flask app's random port (the OAuth
server has to be on a stable port that Supabase's redirect-URL
allowlist knows about).
"""
from __future__ import annotations

from threading import Thread

from flask import Blueprint, jsonify, redirect, render_template, request

from deal_finder.auth import google_oauth, token_store

bp = Blueprint("auth", __name__)


@bp.route("/login")
def login_page():
    """Render the sign-in page."""
    if token_store.is_logged_in():
        # Already signed in — bounce to the app proper.
        return redirect("/")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    """Clear keyring tokens and redirect to /login."""
    token_store.clear()
    return redirect("/login")


@bp.route("/api/auth/start", methods=["POST"])
def auth_start():
    """Begin the Google OAuth flow.

    Returns the URL the browser should navigate to. We also kick off
    a background thread that runs the local callback server + waits
    for tokens, so by the time the user finishes signing in, we're
    ready to receive the redirect.

    The thread is fire-and-forget — we don't block the HTTP response
    on it, since the user is about to leave this page anyway. Token
    capture happens in the OAuth callback server's class-level state.
    """
    # Defensive: if a previous flow is still running, the second call
    # will collide on the port. We try to be graceful by ignoring
    # OSError; the new tokens will still arrive at the existing server.
    def _runner():
        try:
            google_oauth.run_login_flow(open_browser=False)
        except OSError:
            pass  # port already bound by an in-progress flow
        except Exception:  # noqa: BLE001
            # Swallow — this is fire-and-forget; failures surface via
            # the user re-checking their login state on the next page.
            pass

    Thread(target=_runner, daemon=True).start()
    return jsonify({"url": google_oauth.auth_url()})


@bp.route("/api/auth/status")
def auth_status():
    """Cheap polling endpoint: tells the login.html whether the user
    has finished signing in. Login page polls this every 500ms after
    starting the flow so it can auto-redirect once tokens land."""
    return jsonify({"logged_in": token_store.is_logged_in()})
