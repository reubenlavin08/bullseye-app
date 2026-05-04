"""Authenticated HTTP client for Supabase Edge Functions.

Every cloud module (`cloud.comps`, `cloud.alerts`, etc.) goes through
this. Handles:
    - Bearer-token injection from keyring on every request
    - 401 -> refresh JWT -> retry once
    - Timeouts -> raise CloudUnavailable so callers can fall back gracefully
    - apikey header (Supabase requires both `Authorization` and `apikey`)
"""
from __future__ import annotations


# These get baked into the PyInstaller bundle. Anon key is safe to ship.
SUPABASE_URL = ""           # filled in via env or build-time replacement
SUPABASE_ANON_KEY = ""      # ditto
DEFAULT_TIMEOUT_S = 10


class CloudUnavailable(Exception):
    """Raised when the cloud is unreachable (timeout, DNS, 5xx). Callers
    should fall back to local cache/state."""


class Unauthorized(Exception):
    """Raised when both the JWT and refresh token are rejected. Caller
    should clear tokens and prompt re-login."""


class CloudClient:
    """Thin wrapper around `requests`. One instance per process is fine."""

    def post(self, endpoint: str, data: dict, *, retry: bool = True) -> dict:
        """POST to /functions/v1/<endpoint>. Returns parsed JSON.

        Raises CloudUnavailable on transport errors, Unauthorized on
        401 after refresh attempt.
        """
        raise NotImplementedError

    def _refresh_jwt(self) -> None:
        """Use the stored refresh token to mint a new JWT. Updates
        keyring on success. Raises Unauthorized if refresh itself fails."""
        raise NotImplementedError


# Module-level singleton — import this from other cloud modules.
client = CloudClient()
