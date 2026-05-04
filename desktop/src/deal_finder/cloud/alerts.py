"""Email-alert sender via cloud Edge Function `/alerts-send`.

Free-tier users get one daily digest at 8am local time.
Paid-tier users get instant emails with a 60s batch hold.

The cloud function decides which path to take based on the user's
license. The desktop app just hands over the matches and the
`type='digest'|'instant'` hint.
"""
from __future__ import annotations


def send_digest(matches: list[dict]) -> dict:
    """Send a daily-digest email. Returns the cloud response with
    {sent, queued, count}. Free-tier path."""
    # TODO: client.post('alerts-send', {'type': 'digest', 'matches': matches})
    raise NotImplementedError


def send_instant(matches: list[dict]) -> dict:
    """Send an instant email with one or more matches. Paid-tier path.
    The 60s batching hold lives in `alerts/digest.py`, not here."""
    # TODO: client.post('alerts-send', {'type': 'instant', 'matches': matches})
    raise NotImplementedError
