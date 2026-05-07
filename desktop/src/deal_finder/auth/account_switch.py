"""Detect account-switch on sign-in and wipe local user-scoped data.

Why this module exists:

    The desktop SQLite schema is account-blind — `user_searches`,
    `listings`, `subscribers`, `user_settings`, and `scheduler_events`
    do not carry a `user_id` column. That was fine when the assumed
    deployment model was "one machine = one account", but real users
    have multiple accounts (work email + personal Gmail) and switch
    between them on the same machine. Without this guard, a user who
    signs out of account A and signs into account B sees account A's
    saved searches, scored listings, and notification prefs — a
    serious data-isolation bug.

    Hard-isolation fix: when a sign-in happens, decode the user_id
    from the new JWT, compare to the previously-stored value in
    `app_state['last_user_id']`, and if they differ, wipe every
    user-scoped table before the new account starts using the DB.

How to use:

    from deal_finder.auth import account_switch
    account_switch.handle_sign_in(new_jwt)

    Called from BOTH auth surfaces:
      - webapp/auth_routes.py    _persist_session()   (email auth)
      - deal_finder/auth/google_oauth.py              (OAuth callback)

    Called BEFORE token_store.save() so the wipe-decision is made
    against the user the new JWT belongs to, not the user we're
    replacing. Idempotent / fails-open: any error is logged and
    swallowed, so a wipe failure never blocks sign-in (worst case:
    stale data persists, which is the pre-fix bug — no regression).

What's wiped vs preserved:

    WIPED (per-user data):
      - user_searches      saved searches the user created
      - listings           scraped + appraised listings
      - scheduler_events   poll history
      - subscribers        notification prefs
      - user_settings      per-user prefs

    PRESERVED (cache + config, not user-scoped):
      - app_state          key-value (install_id, last_user_id)
      - city_geocache      geographic-name cache
      - comps              local comp cache
      - comps_local_cache  same
      - comps_meta         same
      - schema_version     migration tracking
"""
from __future__ import annotations

import base64
import json
import logging

logger = logging.getLogger(__name__)


_USER_SCOPED_TABLES = (
    "listings",
    "scheduler_events",
    "user_searches",
    "subscribers",
    "user_settings",
)


def _decode_jwt_sub(jwt: str | None) -> str | None:
    """Extract the `sub` claim (user_id) from a JWT WITHOUT verifying
    the signature — the caller has just gotten this token from
    Supabase Auth, so we already trust it. We're only reading the
    user_id so we can detect account switches.

    Returns None on any parse error — caller should treat as
    "couldn't decide, skip the wipe" (fail-open).
    """
    if not jwt:
        return None
    try:
        parts = jwt.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        # JWT base64 is URL-safe and unpadded.
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        sub = payload.get("sub")
        if isinstance(sub, str) and sub:
            return sub
    except Exception:  # noqa: BLE001
        pass
    return None


def handle_sign_in(jwt: str | None) -> None:
    """Compare the new JWT's user_id to the previously stored one in
    app_state. If they differ, wipe user-scoped tables and persist
    the new user_id.

    Always (re)records the current user_id — including on first
    sign-in, so future sign-ins can detect the switch.

    First-sign-in safety: when app_state['last_user_id'] is missing,
    we skip the wipe (no data to lose anyway, and we don't want to
    bulldoze a legitimate fresh-DB sign-in).

    Errors are logged and swallowed: a failed wipe must NOT block
    sign-in, since the worst case is the existing buggy behaviour
    (stale data) and the best case is an unrelated DB hiccup.
    """
    new_user_id = _decode_jwt_sub(jwt)
    if not new_user_id:
        return  # couldn't decode — fail open, no wipe

    try:
        # Lazy import — token_store/auth modules load early in the
        # app lifecycle, before the DB layer. Importing here avoids
        # a hard dependency at import time.
        from deal_finder.db.connection import get_conn
    except Exception as e:  # noqa: BLE001
        logger.exception("account_switch: db import failed: %s", e)
        return

    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT value FROM app_state WHERE key = 'last_user_id'"
            )
            row = cur.fetchone()
            previous = row[0] if row else None

            if previous and previous != new_user_id:
                logger.info(
                    "account switch detected (%s -> %s); wiping local user data",
                    previous[:8], new_user_id[:8],
                )
                with conn:
                    for table in _USER_SCOPED_TABLES:
                        try:
                            conn.execute(f"DELETE FROM {table}")
                        except Exception as e:  # noqa: BLE001
                            # Table may not exist on older schemas;
                            # log and continue rather than abort the
                            # whole switch.
                            logger.warning(
                                "account_switch: wipe of %s failed (continuing): %s",
                                table, e,
                            )

            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO app_state (key, value) "
                    "VALUES ('last_user_id', ?)",
                    (new_user_id,),
                )
    except Exception as e:  # noqa: BLE001
        logger.exception("account_switch: handle_sign_in failed: %s", e)
