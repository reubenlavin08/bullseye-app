"""JWT + refresh-token storage in the OS keychain via `keyring`.

On Windows: Credential Manager.
On macOS:   Keychain.
On Linux:   Secret Service / GNOME Keyring / KWallet.

`keyring` abstracts these so this module doesn't care which OS we're
on. We never write tokens to disk.
"""
from __future__ import annotations

SERVICE_NAME = "bullseye-app"


def save(jwt: str, refresh_token: str) -> None:
    """Persist both tokens. Overwrites previous values."""
    raise NotImplementedError


def load_jwt() -> str | None:
    """Return the current JWT, or None if not logged in."""
    raise NotImplementedError


def load_refresh_token() -> str | None:
    """Return the refresh token used to mint new JWTs when the current
    one expires. Returns None if not logged in."""
    raise NotImplementedError


def clear() -> None:
    """Wipe both tokens. Called on explicit logout and account delete."""
    raise NotImplementedError
