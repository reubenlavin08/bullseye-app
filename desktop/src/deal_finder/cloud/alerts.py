"""Email-alert sender via cloud Edge Function `/alerts-send`.

Free-tier users get one daily digest at 8am local time.
Paid-tier users get instant emails with a 60s batch hold.

The cloud function decides which path to take based on the user's
license. The desktop app just hands over the matches and the
`type='digest'|'instant'` hint.
"""
from __future__ import annotations

import logging
from typing import Any

from .client import client, CloudError, CloudUnavailable, Unauthorized

logger = logging.getLogger(__name__)


def send_digest(matches: list[dict]) -> dict:
    """Send a daily-digest email. Returns the cloud response dict
    `{sent: bool, queued: bool, count: int}`.

    On cloud failure, logs and returns a sentinel `{sent: False,
    queued: False, count: 0, error: ...}` so the orchestrator can
    decide whether to mark listings notified or leave them for retry.
    Never raises — email is best-effort.
    """
    return _post("digest", matches)


def send_instant(matches: list[dict]) -> dict:
    """Send an instant email with one or more matches. Paid-tier path.
    The 60s batching hold lives in `alerts/digest.py`, not here.

    Returns the cloud response or a sentinel error dict (see
    `send_digest` for the shape). Never raises.
    """
    return _post("instant", matches)


def _post(kind: str, matches: list[dict]) -> dict[str, Any]:
    if not matches:
        return {"sent": False, "queued": False, "count": 0}
    try:
        return client.post("alerts-send", {"type": kind, "matches": matches})
    except Unauthorized as e:
        logger.warning("alerts-send unauthorized; user must re-login: %s", e)
        return {
            "sent": False, "queued": False, "count": 0,
            "error": "unauthorized",
        }
    except CloudUnavailable as e:
        logger.warning("alerts-send cloud unavailable: %s", e)
        return {
            "sent": False, "queued": False, "count": 0,
            "error": "unavailable",
        }
    except CloudError as e:
        # 4xx — most commonly 503 "email service not configured" before
        # RESEND_API_KEY is provisioned, or a 403 "instant requires
        # paid" if the desktop tier check raced the cloud's view.
        logger.warning("alerts-send rejected: %s", e)
        return {
            "sent": False, "queued": False, "count": 0,
            "error": str(e),
        }
