"""Alert delivery — desktop toasts + cloud-sent emails.

The personal tool sends emails directly via SMTP. The product sends
them via the cloud `/alerts-send` Edge Function (which uses Resend).
This module is the orchestration layer:

    - free tier: 1 daily digest at 8am local
    - paid tier: instant emails with 60s batching hold

Wiring:
    `digest.send_instant_for_pending()` — paid tick
    `digest.send_daily_digest()`        — free 8am-local job
    `cloud.alerts.send_digest/instant`  — actual transport

Desktop toasts fire on every match regardless of tier — that's the
free-and-immediate path. Email is the tier differentiator.
"""
from __future__ import annotations

from .digest import (
    DigestMatch,
    collect_pending_for_email,
    send_daily_digest,
    send_instant_for_pending,
)

__all__ = [
    "DigestMatch",
    "collect_pending_for_email",
    "send_daily_digest",
    "send_instant_for_pending",
]
