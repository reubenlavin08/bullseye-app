"""Streak + Pro-day banking client.

Talks to the `/streak` and `/license` Edge Functions to:
    - tick the user's daily-active streak (called once per app open),
    - surface the current streak + banked Pro days for the dashboard,
    - redeem 7 banked Pro days for a 7-day Pro trial.

All calls are best-effort and degrade to None on failure — the
desktop app remains fully functional without streaks (the user just
doesn't see the gamification surfaces).

Failure modes:
    - Unauthorized          user not logged in / token expired
    - CloudUnavailable      offline, DNS, 5xx
Both are caught and converted to None. The caller (dashboard JS via
the webview bridge) treats None as "streaks unavailable" and hides
the relevant UI.

Why no caching here:
    Unlike /license which the LicenseManager caches in SQLite for
    offline tier resolution, /streak is purely additive UX. If we're
    offline, hiding the streak widget is fine — the cloud will catch
    up the next time the user is online. Avoiding a local cache also
    keeps the freeze logic single-sourced (only the cloud knows for
    sure whether this month's freeze has been used).
"""
from __future__ import annotations

import logging
from typing import Optional

from .client import client, CloudError, CloudUnavailable, Unauthorized

logger = logging.getLogger(__name__)


def fetch_streak() -> Optional[dict]:
    """POST /streak — tick the daily-active streak and return state.

    Returns a dict with shape:
        {
            "current_streak": int,
            "longest_streak": int,
            "freeze_available": bool,
            "pro_days_banked": int,
            "milestones_earned_today": list[str],
        }
    or None on Unauthorized / CloudUnavailable / unexpected error.
    The dashboard JS treats None as "streaks unavailable" and hides
    the widget rather than showing stale data.
    """
    try:
        return client.post("streak", {})
    except Unauthorized:
        logger.info("streak fetch unauthorized; user must re-login")
        return None
    except CloudUnavailable as e:
        logger.warning("streak unavailable: %s", e)
        return None
    except CloudError as e:
        # 4xx — log but don't surface; gamification is non-essential.
        logger.warning("streak rejected: %s", e)
        return None


def redeem_pro_days() -> Optional[dict]:
    """POST /license {action: "redeem_pro_days"} — convert 7 banked
    Pro days into a 7-day Pro trial.

    Returns the updated license dict on success (same shape as
    /license read), or None on failure. The caller is expected to
    handle the None case by surfacing a generic "couldn't redeem"
    message — for richer error detail, catch the underlying client
    exceptions yourself instead of using this helper.

    The cloud enforces:
        - tier must be 'free' (paid/trial users get 403)
        - pro_days_banked must be >= 7 (otherwise 403)

    Both rejection cases are 4xx CloudErrors here. We swallow them
    deliberately — the UI affordance to redeem is only shown when
    the cloud already told us can_redeem_trial=true, so a 403 here
    means the cloud and the UI have drifted (expected briefly during
    e.g. a multi-tab session) and the right move is to refetch
    /license fresh.
    """
    try:
        return client.post("license", {"action": "redeem_pro_days"})
    except Unauthorized:
        logger.info("redeem unauthorized; user must re-login")
        return None
    except CloudUnavailable as e:
        logger.warning("redeem cloud unavailable: %s", e)
        return None
    except CloudError as e:
        logger.warning("redeem rejected: %s", e)
        return None
