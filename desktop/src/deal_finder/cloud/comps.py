"""Comp data fetcher — replaces direct eBay calls.

Cloud function `/comps` does the actual eBay API call (with our shared
dev key) and the parts/accessory exclusion. Result is cached server-
side by normalized search term across ALL users — that shared cache
is the actual cost moat at scale.

Local fallback: if the cloud is offline, return whatever's in the
local SQLite comp cache (24h TTL there as a safety net).
"""
from __future__ import annotations


def get_comps(search_term: str, *, region: str = "EBAY-ENCA") -> dict:
    """Fetch comp stats for `search_term`. Returns the same shape as
    the existing CompStats dataclass: median, mean, min, max, p10/q1/q3/p90,
    sample_size, source, search_term.

    Raises nothing — falls back to local cache or returns an empty
    CompStats on total failure.
    """
    # TODO: client.post('comps', {'search_term': ..., 'region': ...})
    # TODO: on CloudUnavailable, check local SQLite cache
    raise NotImplementedError
