"""LicenseManager — tier checks the rest of the app uses.

Behavior:
    - First call: hits cloud `/license`, caches for 1 hour, persists
      to SQLite (so we have a fallback when offline).
    - Subsequent calls within the hour: return cached.
    - On CloudUnavailable: return last known cached value from SQLite,
      or the default-free fallback if we've never been online.

Trade-off: 1h cache means a sophisticated user could keep paid
features for up to 1h after their subscription ends or they cancel.
For $9.99/mo this is a fine trade. If we want to tighten it, the
move is to refresh on every app boot and on cloud-call 401s.

Also enforces the kill-switch: if `min_supported_version` from the
cloud is greater than our `__version__`, every method short-circuits
to "not allowed" and the UI shows the hard-stop banner.
"""
from __future__ import annotations


class LicenseManager:
    def __init__(self) -> None:
        self._cached: dict | None = None
        self._cache_until = None  # datetime

    def get(self) -> dict:
        """Return current LicenseInfo. See cloud/license.py for shape."""
        # TODO: TTL check, cloud fetch, fallback to SQLite cache, fallback
        # to {'tier': 'free', 'watches_limit': 3, 'poll_interval_min': 30}
        raise NotImplementedError

    def watches_limit(self) -> int | None:
        """Max watches the current tier allows. None = unlimited."""
        raise NotImplementedError

    def poll_interval_min(self) -> int:
        """Minimum seconds between polls per watch (clamps user setting)."""
        raise NotImplementedError

    def is_paid(self) -> bool:
        """True for tier='paid' or active 'trial'."""
        raise NotImplementedError

    def is_kill_switched(self) -> bool:
        """True when min_supported_version > our __version__. UI shows
        a "please update" hard-stop and scheduler refuses to poll."""
        raise NotImplementedError

    def trial_days_remaining(self) -> int | None:
        """For tier='trial', days until trial_ends_at. Used by upgrade-
        prompt UI ("3 days left in your trial — upgrade now")."""
        raise NotImplementedError


# Module-level singleton
license_manager = LicenseManager()
