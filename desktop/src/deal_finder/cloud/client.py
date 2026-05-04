"""Authenticated HTTP client for Supabase Edge Functions.

Every cloud module (`cloud.comps`, `cloud.alerts`, etc.) goes through
this. Handles:
    - Bearer-token injection from keyring on every request
    - 401 -> refresh JWT via stored refresh_token -> retry once
    - apikey header (Supabase requires both Authorization + apikey)
    - Timeouts -> raise CloudUnavailable so callers can fall back
    - Idempotent across threads (one shared session)

Constants are baked in at the top so the PyInstaller bundle works
without any env config. Override via env for sandbox testing:
    BULLSEYE_SUPABASE_URL
    BULLSEYE_SUPABASE_ANON_KEY
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


# These get baked into the PyInstaller bundle. Anon key is safe to ship —
# it's a JWT scoped to the 'anon' role, gated by Postgres row-level
# security policies. Even if extracted from the binary, it grants only
# what an unauthenticated user can already do. The service role key (which
# bypasses RLS) lives in Supabase function secrets, never here.
#
# Override via BULLSEYE_SUPABASE_URL / BULLSEYE_SUPABASE_ANON_KEY env vars
# during local dev so the same binary can target a sandbox project.
SUPABASE_URL = "https://qfkzhyxmohytnzskcmdv.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFma3poeXhtb2h5dG56c2tjbWR2Iiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3Nzc5MjIzMjYsImV4cCI6MjA5MzQ5ODMyNn0."
    "pPathNzQcURGocSVPKKhgcv27-4sqHAFZeB1oenB0Z4"
)
DEFAULT_TIMEOUT_S = 10


def _supabase_url() -> str:
    return os.environ.get("BULLSEYE_SUPABASE_URL") or SUPABASE_URL


def _supabase_anon_key() -> str:
    return os.environ.get("BULLSEYE_SUPABASE_ANON_KEY") or SUPABASE_ANON_KEY


class CloudUnavailable(Exception):
    """Raised when the cloud is unreachable (timeout, DNS, 5xx). Callers
    should fall back to local cache/state."""


class Unauthorized(Exception):
    """Raised when both the JWT and refresh token are rejected. Caller
    should clear tokens and prompt re-login."""


class CloudError(Exception):
    """Raised for non-retryable 4xx errors (validation, missing param).
    Carries the parsed error message from the function."""


class CloudClient:
    """Thin wrapper around `requests`. One module-level instance is fine —
    requests.Session is thread-safe for concurrent .post calls."""

    def __init__(self, *, timeout_s: int = DEFAULT_TIMEOUT_S):
        self._timeout = timeout_s
        self._session = requests.Session()

    # --- Headers --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        # Lazy import: avoids circular dependency during scaffolding
        from ..auth import token_store
        from .. import __version__

        try:
            jwt = token_store.load_jwt()
        except NotImplementedError:
            jwt = None  # tests / pre-step-4 mode

        h = {
            "apikey": _supabase_anon_key(),
            "Content-Type": "application/json",
            "x-app-version": __version__,
        }
        if jwt:
            h["Authorization"] = f"Bearer {jwt}"
        else:
            # Anon-only requests still need an Authorization header to
            # satisfy Supabase's gateway — using the anon key as a
            # bearer is the convention.
            h["Authorization"] = f"Bearer {_supabase_anon_key()}"
        return h

    # --- JWT refresh ----------------------------------------------------

    def _refresh_jwt(self) -> bool:
        """Use the stored refresh token to mint a new JWT. Updates
        keyring on success. Returns False if there's no refresh token
        or the refresh itself fails (caller should treat as logged out).
        """
        from ..auth import token_store

        try:
            rt = token_store.load_refresh_token()
        except NotImplementedError:
            return False
        if not rt:
            return False

        try:
            resp = self._session.post(
                f"{_supabase_url()}/auth/v1/token?grant_type=refresh_token",
                headers={
                    "apikey": _supabase_anon_key(),
                    "Content-Type": "application/json",
                },
                data=json.dumps({"refresh_token": rt}),
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            logger.warning("jwt refresh network error: %s", e)
            return False
        if resp.status_code != 200:
            logger.info("jwt refresh rejected (%d)", resp.status_code)
            return False
        body = resp.json()
        access = body.get("access_token")
        new_rt = body.get("refresh_token")
        if not access or not new_rt:
            return False
        try:
            token_store.save(access, new_rt)
        except NotImplementedError:
            return False
        return True

    # --- Core post ------------------------------------------------------

    def post(self, endpoint: str, data: dict | None = None, *, retry: bool = True) -> dict:
        """POST to /functions/v1/<endpoint>. Returns parsed JSON.

        Errors:
            CloudUnavailable    transport failure (timeout, DNS, 5xx)
            Unauthorized        JWT + refresh both failed
            CloudError          4xx with a parsed error message
        """
        url = f"{_supabase_url()}/functions/v1/{endpoint}"
        payload = json.dumps(data or {})
        try:
            resp = self._session.post(
                url, headers=self._headers(), data=payload, timeout=self._timeout,
            )
        except requests.Timeout as e:
            raise CloudUnavailable(f"timeout calling {endpoint}: {e}") from e
        except requests.ConnectionError as e:
            raise CloudUnavailable(f"connection error calling {endpoint}: {e}") from e
        except requests.RequestException as e:
            raise CloudUnavailable(f"request failed: {e}") from e

        if resp.status_code == 401 and retry:
            if self._refresh_jwt():
                return self.post(endpoint, data, retry=False)
            raise Unauthorized("token refresh failed; user must re-login")

        if resp.status_code >= 500:
            raise CloudUnavailable(
                f"{endpoint} returned {resp.status_code}: {resp.text[:200]}"
            )

        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", resp.text[:200])
            except ValueError:
                err = resp.text[:200]
            raise CloudError(f"{endpoint} {resp.status_code}: {err}")

        try:
            return resp.json()
        except ValueError as e:
            raise CloudUnavailable(f"non-JSON response from {endpoint}: {e}") from e


# Module-level singleton — import this from other cloud modules.
client = CloudClient()
