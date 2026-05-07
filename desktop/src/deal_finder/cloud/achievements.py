"""Action-based achievement helper — calls the cloud /award-action
endpoint when a relevant local event happens (deal scored 80+,
lifetime savings hit a threshold, first watch created, etc.).

All calls are best-effort: cloud unavailability or auth failure is
silently swallowed (logged at warning level). Achievements are a UX
flourish, not a critical-path feature, and a failed grant just means
the user might re-trigger it later — the unique constraint on
user_unlocks makes the call idempotent server-side, so retries are
safe.

Usage from anywhere in the desktop app:

    from deal_finder.cloud import achievements
    achievements.try_award("first_deal_80")
    achievements.try_award("savings_500")

The function is non-blocking by design — it spawns a daemon thread
so the caller's hot path (e.g. the appraisal pipeline) isn't slowed
by a cloud round-trip.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .client import client, CloudError, CloudUnavailable, Unauthorized

logger = logging.getLogger(__name__)


# Allowlist mirrored from cloud/_shared/achievements.ts CLIENT_GRANTABLE.
# Keeping a local copy avoids a round-trip for "is this id valid?" but
# the cloud endpoint is the authority — anything missed here just gets
# rejected at the cloud with a clearer error message.
_CLIENT_GRANTABLE = frozenset({
    "first_deal_80",
    "five_deals_80",
    "twenty_five_deals_80",
    "savings_100",
    "savings_500",
    "savings_1000",
    "savings_5000",
    "first_email_click",
    "first_watch_created",
})


def try_award(action_id: str) -> None:
    """Fire-and-forget award request. Spawns a daemon thread so the
    caller's hot path is never blocked.

    Idempotent at the cloud — repeated calls with the same action_id
    after the first successful grant return `awarded: false` and a
    `pro_days: 0` credit. Safe to call from poll loops, appraisal
    callbacks, or anywhere a relevant event is detected.
    """
    if action_id not in _CLIENT_GRANTABLE:
        logger.warning("try_award: unknown action_id %r — ignoring", action_id)
        return

    def _runner() -> None:
        try:
            resp = client.post("award-action", {"action_id": action_id})
            if resp.get("awarded"):
                days = resp.get("pro_days") or 0
                banked = resp.get("pro_days_banked") or 0
                logger.info(
                    "achievement awarded: %s (+%d Pro days, %d banked total)",
                    action_id, days, banked,
                )
        except Unauthorized:
            # Logged out / token expired — silent.
            logger.debug("award-action unauthorized; skipping %s", action_id)
        except CloudUnavailable as e:
            logger.warning("award-action cloud unavailable: %s", e)
        except CloudError as e:
            # 4xx from cloud (bad action_id, etc.). Log + drop.
            logger.warning("award-action rejected %s: %s", action_id, e)
        except Exception as e:  # noqa: BLE001
            logger.exception("award-action unexpected: %s", e)

    threading.Thread(
        target=_runner, name=f"award-action:{action_id}", daemon=True,
    ).start()


def fetch_gallery() -> Optional[dict]:
    """GET-or-POST /achievements — returns the master gallery joined
    with the user's unlock state. Used by the desktop achievement-
    gallery modal.

    Returns the raw cloud response dict on success, None on any error
    (caller should treat None as "couldn't load — show a message").
    """
    try:
        return client.post("achievements", {})
    except Unauthorized:
        logger.info("achievements fetch unauthorized")
        return None
    except CloudUnavailable as e:
        logger.warning("achievements unavailable: %s", e)
        return None
    except CloudError as e:
        logger.warning("achievements rejected: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        logger.exception("achievements unexpected: %s", e)
        return None
