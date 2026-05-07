"""Auth routes for the local Flask UI.

Two sign-in paths supported:

1. **Google OAuth** — browser-based dance. `google_oauth.run_login_flow`
   spawns a local callback server on port 53682 (must be in Supabase's
   redirect allowlist). Tokens flow back via URL navigation.

2. **Email + password** — direct call to Supabase Auth API endpoints
   `/auth/v1/signup` and `/auth/v1/token?grant_type=password`. Tokens
   come back in the JSON response and we persist them via token_store.
   No browser bounce.

Both paths terminate in `token_store.save(jwt, refresh_token)`, so
downstream code (`/api/auth/status`, license_manager, cloud client)
doesn't care which path was used.
"""
from __future__ import annotations

import json
import logging
from threading import Thread
from typing import Any

import requests
from flask import Blueprint, jsonify, redirect, render_template, request

from deal_finder.auth import google_oauth, token_store
from deal_finder.cloud.client import _supabase_url, _supabase_anon_key

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)


@bp.route("/login")
def login_page():
    """Legacy alias — kept so old links + bookmarks don't 404. The
    new sign-in surface lives at /auth (centered card, sidebar-less,
    visually cohesive with the rest of the desktop shell)."""
    if token_store.is_logged_in():
        return redirect("/home")
    return redirect("/auth")


@bp.route("/logout", methods=["POST"])
def logout():
    """Clear keyring tokens + cached license, redirect to /auth.

    De-escalated 2026-05-07 from a previous version that ALSO wiped
    user_searches/listings/etc. on every logout. That was too
    aggressive — a user who logged out and signed back in as the SAME
    user would lose all their scored listings and saved searches,
    making Recent Finds look broken to anyone who ever touched the
    Sign-out button.

    Account-switch isolation now lives entirely at sign-in time, in
    deal_finder/auth/account_switch.handle_sign_in() — that compares
    the new JWT.sub to app_state['last_user_id'] and wipes only when
    they actually differ. Same-user logout/login round-trips preserve
    data; cross-user switches still wipe.

    License cache is still invalidated so the next sign-in's tier is
    read fresh and doesn't briefly show the prior account's tier.
    """
    try:
        from deal_finder.license.manager import license_manager
        license_manager.invalidate()
    except Exception as e:  # noqa: BLE001
        logger.debug("logout: license invalidate failed (non-fatal): %s", e)
    token_store.clear()
    return redirect("/auth")


@bp.route("/api/auth/start", methods=["POST"])
def auth_start():
    """Begin the Google OAuth flow."""
    def _runner():
        try:
            google_oauth.run_login_flow(open_browser=False)
        except OSError:
            pass
        except Exception:  # noqa: BLE001
            pass

    Thread(target=_runner, daemon=True).start()
    return jsonify({"url": google_oauth.auth_url()})


@bp.route("/api/auth/status")
def auth_status():
    """Cheap polling endpoint: tells the auth page whether the user
    has finished signing in."""
    return jsonify({"logged_in": token_store.is_logged_in()})


# ---------------------------------------------------------------------------
# Email + password — direct Supabase Auth API calls. We persist tokens
# locally (keychain + file fallback) so the rest of the app sees the
# user as authenticated immediately, no extra round-trip.
# ---------------------------------------------------------------------------

def _supabase_auth_call(path: str, body: dict[str, Any]) -> tuple[int, dict]:
    """POST to Supabase Auth's /auth/v1/<path>. Returns (status, json)."""
    url = f"{_supabase_url()}/auth/v1/{path}"
    try:
        resp = requests.post(
            url,
            headers={
                "apikey": _supabase_anon_key(),
                "Content-Type": "application/json",
            },
            data=json.dumps(body),
            timeout=15,
        )
    except requests.RequestException as e:
        logger.warning("supabase auth call failed: %s", e)
        return 503, {"error": "cloud_unavailable", "message": str(e)}
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"error": "non_json_response", "message": resp.text[:200]}


def _persist_session(payload: dict) -> bool:
    """Pull access_token + refresh_token out of a Supabase auth
    response and save them via token_store. Returns True on success.

    Also detects account switches (different user_id from the previous
    sign-in on this machine) and wipes local user-scoped tables before
    persisting, so the new account starts with a clean local DB. See
    deal_finder.auth.account_switch for details.
    """
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not access or not refresh:
        return False
    try:
        # Account-switch detection BEFORE token persist so the wipe-
        # decision is made against the user the new JWT belongs to.
        from deal_finder.auth import account_switch
        account_switch.handle_sign_in(access)
        token_store.save(access, refresh)
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("token_store.save failed: %s", e)
        return False


def _validate_credentials(data: dict) -> tuple[str | None, str | None, str | None]:
    """Pull + validate email/password from a request body. Returns
    (email, password, error_message). On error, the first two are None
    and error_message describes the problem."""
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or "@" not in email or len(email) > 320:
        return None, None, "valid email required"
    if not password or len(password) < 8:
        return None, None, "password must be at least 8 characters"
    if len(password) > 128:
        return None, None, "password too long"
    return email, password, None


@bp.route("/api/auth/email/signup", methods=["POST"])
def email_signup():
    """Create a Supabase user with email + password. If Supabase Auth
    is configured to require email confirmation, the response will NOT
    include a session — we surface that to the UI as a clear "check
    your email" state. Otherwise we persist tokens and return ok."""
    data = request.get_json(silent=True) or {}
    email, password, err = _validate_credentials(data)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    status, body = _supabase_auth_call("signup", {
        "email": email,
        "password": password,
    })
    if status >= 400:
        # Smart-auth fallback: if Supabase says the user already
        # exists, attempt sign-in with the same credentials instead of
        # throwing a confusing "user already registered" error. This
        # is the Wispr-style "one-button auth" pattern — typing your
        # existing email + password into "Create account" just signs
        # you in. (User feedback 2026-05-07: 'accounts that have
        # already been created before should just go sign in instead
        # of creating the account again'.)
        raw = (
            body.get("error_description")
            or body.get("msg")
            or body.get("error")
            or ""
        ).lower()
        already_exists = (
            "already registered" in raw
            or "already exists" in raw
            or "duplicate" in raw
            or "email_exists" in raw
            or body.get("error_code") == "user_already_exists"
        )
        if already_exists:
            si_status, si_body = _supabase_auth_call(
                "token?grant_type=password",
                {"email": email, "password": password},
            )
            if si_status < 400 and _persist_session(si_body):
                # Existing account, correct password → just signed in.
                return jsonify({
                    "ok": True,
                    "needs_confirmation": False,
                    "signed_in_existing": True,
                })
            # Existing account, wrong password → don't reveal that the
            # account exists (account-enumeration concern). Generic
            # error matching the sign-in error path.
            return jsonify({
                "ok": False,
                "error": "Email or password is incorrect.",
            }), 400

        return jsonify({
            "ok": False,
            "error": body.get("msg") or body.get("error") or body.get("error_description") or f"signup failed ({status})",
        }), 400

    # Supabase signup response shape varies based on email-confirmation
    # setting. With confirmation OFF: the response is a session object
    # with access_token + refresh_token at top level. With confirmation
    # ON: the response is a user object only — no session.
    if _persist_session(body):
        return jsonify({"ok": True, "needs_confirmation": False})

    # No session means email confirmation is required. Tell the UI
    # so it can show a "check your email" state instead of trying to
    # redirect to /home (which would bounce back to /auth).
    return jsonify({
        "ok": True,
        "needs_confirmation": True,
        "message": "Check your email to confirm your account.",
    })


@bp.route("/api/auth/email/resend", methods=["POST"])
def email_resend():
    """Re-send a Supabase confirmation email for a previously-signed-up
    address that hasn't been confirmed yet.

    Why this exists: Resend deliverability is inconsistent on first
    sends to certain inboxes — Outlook/Hotmail in particular silently
    quarantine mail from new senders without DMARC reputation, and
    some users never see the first confirmation email. Hitting Supabase's
    /auth/v1/resend endpoint generates a fresh token and triggers a
    new send.

    Supabase rate-limits resend at 60s per email. If the user spam-
    clicks, we get a 429 back which we surface as a friendly
    "wait a moment" message.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "valid email required"}), 400

    status, body = _supabase_auth_call("resend", {
        "type": "signup",
        "email": email,
    })
    if status == 429 or (status >= 400 and "rate" in str(
            body.get("error_description") or body.get("msg") or ""
        ).lower()):
        return jsonify({
            "ok": False,
            "error": "rate_limited",
            "message": "Please wait a minute before requesting another email.",
        }), 429
    if status >= 400:
        # Generic error — don't leak whether the email exists.
        return jsonify({
            "ok": False,
            "error": "Could not resend the email. Try again in a minute.",
        }), 400
    return jsonify({"ok": True})


@bp.route("/api/auth/email/signin", methods=["POST"])
def email_signin():
    """Sign in with email + password. Calls Supabase's password grant.
    On success, persist tokens and return ok."""
    data = request.get_json(silent=True) or {}
    email, password, err = _validate_credentials(data)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    status, body = _supabase_auth_call("token?grant_type=password", {
        "email": email,
        "password": password,
    })
    if status >= 400:
        # GENERIC error mapping — do NOT pass Supabase's verbatim
        # `error_description` through. Historically Supabase has leaked
        # email-existence ("User already registered") which enables
        # account enumeration. Map every credential-failure to the same
        # generic string. (Audit finding 2026-05-06.)
        raw = (
            body.get("error_description")
            or body.get("msg")
            or body.get("error")
            or ""
        ).lower()
        if "rate" in raw or "too many" in raw:
            msg = "Too many attempts. Try again in a minute."
        elif "confirm" in raw or "verified" in raw:
            msg = "Please confirm your email before signing in."
        else:
            # Bad password, no user, malformed request, server error —
            # all collapse to the same generic message.
            msg = "Email or password is incorrect."
        return jsonify({"ok": False, "error": msg}), 400

    if not _persist_session(body):
        return jsonify({
            "ok": False,
            "error": "auth response missing tokens",
        }), 502
    return jsonify({"ok": True})
