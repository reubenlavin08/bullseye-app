"""JWT + refresh-token storage in the OS keychain (with file fallback).

`keyring` abstracts:
    Windows  -> Credential Manager
    macOS    -> Keychain
    Linux    -> Secret Service / GNOME Keyring / KWallet

The keychain is preferred. But keyring's PyInstaller-frozen behaviour is
unreliable on some Windows setups (no backend detected, silent failures,
or Credential Manager rejects the write). When that happens, we fall
back to a JSON file at %APPDATA%/Bullseye/tokens.json (chmod-style
0o600 best-effort on POSIX).

Tokens-on-disk is not the security disaster it sounds like for a desktop
app: the user's local OS account is the trust boundary either way. If
someone has read access to your %APPDATA%, they can already keylog you
into Google. Storing tokens in a 0o600 file keeps them out of casual
grep scans of cloud-synced folders, which is the realistic threat.

API:
    save(jwt, refresh_token)         persist both
    load_jwt() -> str | None         current JWT or None
    load_refresh_token() -> str|None refresh token or None
    clear()                          wipe both (logout)
    is_logged_in() -> bool           convenience: any token present
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = "bullseye-app"
_USER_JWT = "jwt"
_USER_RT = "refresh_token"


def _get_keyring():
    """Lazy import so test code can monkey-patch `keyring` without
    needing it installed at module-import time."""
    import keyring
    return keyring


def _user_data_dir() -> Path:
    """%APPDATA%/Bullseye on Windows, ~/.config/bullseye elsewhere.

    Created with parents=True, exist_ok=True. Always returns even on
    permission errors — caller checks that file ops succeed."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
        d = Path(base) / "Bullseye"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Bullseye"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        d = Path(base) / "bullseye"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("could not create user data dir %s: %s", d, e)
    return d


def _token_file() -> Path:
    return _user_data_dir() / "tokens.json"


# --- File-based fallback persistence -----------------------------------

def _file_save(jwt: str, refresh_token: str) -> bool:
    """Write tokens to disk. Returns True on success."""
    p = _token_file()
    try:
        p.write_text(
            json.dumps({"jwt": jwt, "refresh_token": refresh_token}),
            encoding="utf-8",
        )
        # Best-effort restrict perms; ignored on Windows.
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return True
    except OSError as e:
        logger.warning("file token save failed at %s: %s", p, e)
        return False


def _file_load() -> dict | None:
    """Read tokens from disk. Returns dict with jwt/refresh_token or None."""
    p = _token_file()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("file token load failed at %s: %s", p, e)
        return None


def _file_clear() -> None:
    p = _token_file()
    try:
        if p.exists():
            p.unlink()
    except OSError as e:
        logger.warning("file token clear failed at %s: %s", p, e)


# --- Public API: save/load/clear ---------------------------------------

def save(jwt: str, refresh_token: str) -> None:
    """Persist tokens to keychain AND file. We attempt both because
    either can fail silently. is_logged_in() checks both sources, so
    if EITHER works, the user appears signed in.
    """
    if not jwt or not refresh_token:
        raise ValueError("save requires non-empty jwt + refresh_token")

    keychain_ok = False
    try:
        kr = _get_keyring()
        kr.set_password(SERVICE_NAME, _USER_JWT, jwt)
        kr.set_password(SERVICE_NAME, _USER_RT, refresh_token)
        keychain_ok = True
        logger.info("tokens saved to keychain")
    except Exception as e:  # noqa: BLE001 — keyring backend may not exist
        logger.warning("keychain save failed (will rely on file fallback): %s", e)

    file_ok = _file_save(jwt, refresh_token)
    if file_ok:
        logger.info("tokens saved to file at %s", _token_file())

    if not (keychain_ok or file_ok):
        # Both failed — escalate. Caller should surface this to the user
        # because nothing was persisted and re-login won't help.
        raise RuntimeError(
            "could not persist tokens (keychain + file both failed)"
        )


def load_jwt() -> str | None:
    """Return current JWT or None. Prefers keychain, falls back to file."""
    try:
        v = _get_keyring().get_password(SERVICE_NAME, _USER_JWT)
        if v:
            return v
    except Exception as e:  # noqa: BLE001
        logger.debug("keychain read failed (jwt): %s", e)
    f = _file_load()
    if f and f.get("jwt"):
        return f["jwt"]
    return None


def load_refresh_token() -> str | None:
    """Return refresh token or None. Prefers keychain, falls back to file."""
    try:
        v = _get_keyring().get_password(SERVICE_NAME, _USER_RT)
        if v:
            return v
    except Exception as e:  # noqa: BLE001
        logger.debug("keychain read failed (rt): %s", e)
    f = _file_load()
    if f and f.get("refresh_token"):
        return f["refresh_token"]
    return None


def clear() -> None:
    """Wipe tokens from BOTH keychain and file."""
    try:
        kr = _get_keyring()
        for username in (_USER_JWT, _USER_RT):
            try:
                kr.delete_password(SERVICE_NAME, username)
            except Exception:  # noqa: BLE001 — already absent is fine
                pass
    except Exception as e:  # noqa: BLE001
        logger.debug("keychain clear failed: %s", e)
    _file_clear()
    logger.info("tokens cleared from keychain + file")


def is_logged_in() -> bool:
    """True iff at least one token (jwt or refresh) is available
    via keychain OR file."""
    return bool(load_jwt() or load_refresh_token())


def load_email() -> str | None:
    """Decode the email claim out of the current JWT, no signature check."""
    jwt = load_jwt()
    if not jwt:
        return None
    try:
        import base64
        parts = jwt.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(raw.decode("utf-8", errors="replace"))
        email = claims.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
        return None
    except Exception as e:  # noqa: BLE001
        logger.debug("jwt email decode failed: %s", e)
        return None
