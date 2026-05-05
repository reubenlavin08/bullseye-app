"""JWT + refresh-token storage in the OS keychain.

`keyring` abstracts:
    Windows  -> Credential Manager
    macOS    -> Keychain
    Linux    -> Secret Service / GNOME Keyring / KWallet

We never write tokens to disk. The JWT is short-lived (~1 hour);
the refresh token mints new JWTs.

API:
    save(jwt, refresh_token)         persist both
    load_jwt() -> str | None         current JWT or None
    load_refresh_token() -> str|None refresh token or None
    clear()                          wipe both (logout)
    is_logged_in() -> bool           convenience: any token present
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SERVICE_NAME = "bullseye-app"
_USER_JWT = "jwt"
_USER_RT = "refresh_token"


def _get_keyring():
    """Lazy import so test code can monkey-patch `keyring` without
    needing it installed at module-import time. Production code
    always has it (it's in requirements.txt)."""
    import keyring
    return keyring


def save(jwt: str, refresh_token: str) -> None:
    """Persist both tokens. Overwrites prior values."""
    if not jwt or not refresh_token:
        raise ValueError("save requires non-empty jwt + refresh_token")
    kr = _get_keyring()
    kr.set_password(SERVICE_NAME, _USER_JWT, jwt)
    kr.set_password(SERVICE_NAME, _USER_RT, refresh_token)
    logger.debug("tokens saved to keychain")


def load_jwt() -> str | None:
    """Return current JWT or None if not logged in."""
    try:
        return _get_keyring().get_password(SERVICE_NAME, _USER_JWT)
    except Exception as e:  # noqa: BLE001 — keyring backend can fail
        logger.warning("keyring read failed: %s", e)
        return None


def load_refresh_token() -> str | None:
    """Return refresh token used to mint new JWTs."""
    try:
        return _get_keyring().get_password(SERVICE_NAME, _USER_RT)
    except Exception as e:  # noqa: BLE001
        logger.warning("keyring read failed: %s", e)
        return None


def clear() -> None:
    """Wipe both tokens. Called on logout, account-delete, or
    when a refresh permanently fails."""
    kr = _get_keyring()
    for username in (_USER_JWT, _USER_RT):
        try:
            kr.delete_password(SERVICE_NAME, username)
        except Exception:  # noqa: BLE001 — already absent is fine
            pass
    logger.info("tokens cleared from keychain")


def is_logged_in() -> bool:
    """Cheap check used by the webapp to gate UI without a network call.
    True iff at least one token is in the keychain."""
    return bool(load_jwt() or load_refresh_token())
