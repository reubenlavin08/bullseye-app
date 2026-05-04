"""Native desktop notifications via `plyer`.

Both free and paid tiers get desktop toasts (it's the cheap, always-on
delivery mechanism). The DIFFERENCE between tiers is email behavior:
free gets a daily 8am digest, paid gets instant emails with batching.

Clicking a toast should open the listing URL. plyer's basic API
doesn't support click handlers; we may need to drop down to
`win10toast-click` on Windows for that, but only if user complaints
warrant it.
"""
from __future__ import annotations


def fire(*, title: str, summary: str, score: int, listing_url: str) -> None:
    """Fire one toast. Non-blocking, fire-and-forget."""
    # TODO: plyer.notification.notify(title=..., message=..., timeout=10)
    raise NotImplementedError
