"""LicenseManager — tier checks the rest of the app uses.

Behavior:
    - First call: hits cloud /license, caches for 1 hour, mirrors to
      SQLite app_state so we have an offline fallback.
    - Subsequent calls within the hour: return cached.
    - On CloudUnavailable: return last known cached value from SQLite.
      If we've never been online, return the default-free fallback.

Trade-off: 1h cache means a sophisticated user could hold paid
features for up to 1h after their subscription ends or they cancel.
For $9.99/mo this is fine. To tighten, force a refresh on app boot
and on any cloud-call 401.

Also enforces the kill-switch: if `min_supported_version` from the
cloud is greater than our `__version__`, every method short-circuits
to "free / not allowed" and `is_kill_switched()` returns True. The
scheduler reads that flag to refuse to poll, and the UI shows a
"please update" hard-stop banner.

Thread-safe: the in-memory cache is guarded by a lock so concurrent
readers (scheduler thread + Flask request thread + tray thread) all
agree on the latest value.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import __version__
from ..cloud.client import CloudUnavailable, Unauthorized, client
from ..db.connection import get_connection

logger = logging.getLogger(__name__)


# How long to trust a fetched license before re-checking the cloud.
CACHE_TTL = timedelta(hours=1)

# Conservative defaults used when we've never been online + have no
# cached value. Free-tier limits.
_DEFAULT_FALLBACK = {
    "tier": "free",
    "watches_limit": 3,
    "poll_interval_min": 30,
    "expires_at": None,
    "trial_ends_at": None,
    "cancel_at_period_end": False,
    "min_supported_version": "0.0.0",  # never kill-switch on a stub
}

_CACHE_KEY = "cached_license_v1"


@dataclass
class _CacheEntry:
    data: dict
    fetched_at: datetime


class LicenseManager:
    def __init__(self) -> None:
        self._cached: _CacheEntry | None = None
        self._lock = threading.RLock()

    # --- Core fetch -----------------------------------------------------

    def get(self, *, force_refresh: bool = False) -> dict:
        """Return current LicenseInfo. Hits cloud if cache is stale.
        Falls back to SQLite-mirrored last-known-good on CloudUnavailable.
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            if (not force_refresh
                    and self._cached is not None
                    and now - self._cached.fetched_at < CACHE_TTL):
                return self._cached.data

            try:
                resp = client.post("license", {})
            except Unauthorized:
                logger.info("license fetch unauthorized; user must re-login")
                return self._fallback_or_default()
            except CloudUnavailable as e:
                logger.warning("license cloud unavailable: %s", e)
                return self._fallback_or_default()
            except Exception as e:  # noqa: BLE001
                logger.warning("license fetch unexpected error: %s", e)
                return self._fallback_or_default()

            self._cached = _CacheEntry(data=resp, fetched_at=now)
            self._save_to_sqlite(resp)
            return resp

    def _fallback_or_default(self) -> dict:
        """In-memory cache (if any) > SQLite cache > hardcoded default."""
        if self._cached is not None:
            return self._cached.data
        loaded = self._load_from_sqlite()
        if loaded is not None:
            self._cached = _CacheEntry(
                data=loaded, fetched_at=datetime.now(timezone.utc),
            )
            return loaded
        return dict(_DEFAULT_FALLBACK)

    # --- Cheap accessors for callers throughout the app ----------------

    def watches_limit(self) -> int | None:
        """Max watches the current tier allows. None = unlimited (paid)."""
        if self.is_kill_switched():
            return 0
        v = self.get().get("watches_limit")
        return int(v) if v is not None else None

    def poll_interval_min(self) -> int:
        """Minimum minutes between polls per watch (clamps user setting)."""
        if self.is_kill_switched():
            return 60 * 24  # effectively pauses polling
        return int(self.get().get("poll_interval_min") or 30)

    def is_paid(self) -> bool:
        """True for tier='paid' or active 'trial'."""
        if self.is_kill_switched():
            return False
        return self.get().get("tier") in ("paid", "trial")

    def tier(self) -> str:
        """One of 'free', 'paid', 'trial'."""
        return self.get().get("tier") or "free"

    def is_kill_switched(self) -> bool:
        """True iff the cloud says our version is too old.

        Kill switch comparison uses semver-ish 'MAJOR.MINOR.PATCH'.
        Returns False on any parse/network error so a transient cloud
        outage doesn't lock everyone out.
        """
        try:
            data = self._cached.data if self._cached else self.get()
        except Exception:  # noqa: BLE001
            return False
        try:
            min_v = data.get("min_supported_version") or "0.0.0"
            return _semver_less(__version__, min_v)
        except Exception:  # noqa: BLE001 — never let this raise
            return False

    def trial_days_remaining(self) -> int | None:
        """Days until trial ends. None if not on trial. 0 if expired
        but not yet downgraded (rare race window)."""
        data = self.get()
        if data.get("tier") != "trial":
            return None
        ends = data.get("trial_ends_at")
        if not ends:
            return None
        try:
            ends_dt = datetime.fromisoformat(ends.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        delta = ends_dt - datetime.now(timezone.utc)
        return max(0, delta.days)

    def invalidate(self) -> None:
        """Force the next get() to hit the cloud. Called by the
        webapp's /api/license/refresh, post-Stripe checkout, on logout."""
        with self._lock:
            self._cached = None

    # --- SQLite persistence (last-known-good fallback) -----------------

    def _save_to_sqlite(self, data: dict) -> None:
        try:
            conn = get_connection()
            with conn:
                conn.execute(
                    """INSERT INTO app_state (key, value, updated_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET
                           value = excluded.value,
                           updated_at = excluded.updated_at""",
                    (_CACHE_KEY, json.dumps(data)),
                )
        except Exception:  # noqa: BLE001 — fallback-cache write must never crash
            logger.exception("failed to mirror license to SQLite")

    def _load_from_sqlite(self) -> dict | None:
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (_CACHE_KEY,),
            ).fetchone()
        except Exception:  # noqa: BLE001
            return None
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return None


# --- Helpers --------------------------------------------------------------

def _semver_less(a: str, b: str) -> bool:
    """Return True iff version `a` is strictly less than `b`.
    Strict parser: every present component must be a non-negative
    integer. Missing trailing components default to 0. ANY unparseable
    component anywhere → return False (fail open: never kill-switch
    on garbage input — including partial garbage like '1.x.0' which
    must NOT be silently coerced to (1,0,0))."""
    def parse(v: str) -> tuple[int, int, int] | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        parts = s.split(".")[:3]
        out: list[int] = []
        for p in parts:
            p = p.strip()
            if not p or not p.isdigit():
                return None
            out.append(int(p))
        while len(out) < 3:
            out.append(0)
        return (out[0], out[1], out[2])

    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        return False
    try:
        return pa < pb
    except Exception:  # noqa: BLE001
        return False


# Module-level singleton. Import `license_manager` from anywhere.
license_manager = LicenseManager()
