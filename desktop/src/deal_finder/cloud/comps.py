"""Comp data fetcher — replaces direct eBay calls.

Cloud function `/comps` does the actual eBay API call (with our shared
dev key) and the parts/accessory exclusion. Result is cached server-
side by normalized search term across ALL users — that shared cache is
the actual cost moat at scale.

Local fallback chain:
    1. Cloud /comps (cache hit or fresh fetch)
    2. Local SQLite comps cache (24h TTL — last-known-good fallback)
    3. Empty CompStats (caller must mark listing unscoreable)

We return a dict shaped to match the desktop scoring formula's
expectations — the same fields `appraisal/formula.py::compute_score`
reads from CompStats:
    {
        sample_size, median, mean, minimum, maximum,
        p10, q1, q3, p90, iqr, iqr_ratio,
        source: 'ebay'|'cache_local',
        search_term, region,
        raw_comps: [{title, price, currency, listing_url, location}]
    }
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..db.connection import get_connection
from .client import client, CloudError, CloudUnavailable, Unauthorized

logger = logging.getLogger(__name__)

# Local SQLite-side TTL for the last-known-good fallback. Longer than
# the cloud's 12h cache because we use it ONLY when the cloud is down.
LOCAL_FALLBACK_TTL_S = 24 * 60 * 60


def get_comps(
    search_term: str,
    *,
    region: str = "EBAY-ENCA",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch comp stats for `search_term`. Never raises — returns an
    empty CompStats on total failure so the appraisal pipeline can
    proceed and mark the listing unscoreable.

    Side effect: on a successful cloud fetch, mirrors the result into
    the local SQLite cache for offline fallback.
    """
    if not search_term or not search_term.strip():
        return _empty_stats(search_term, region)

    # 1. Try the cloud
    try:
        resp = client.post(
            "comps",
            {
                "search_term": search_term,
                "region": region,
                "force_refresh": force_refresh,
            },
        )
    except Unauthorized:
        logger.warning("comps fetch unauthorized; user must re-login")
        return _local_fallback(search_term, region)
    except CloudUnavailable as e:
        logger.warning("comps cloud unavailable, falling back: %s", e)
        return _local_fallback(search_term, region)
    except CloudError as e:
        logger.warning("comps cloud error: %s", e)
        return _empty_stats(search_term, region)

    stats = resp.get("stats") or {}
    raw_comps = resp.get("raw_comps") or []
    cache_source = resp.get("source", "fresh")
    out = _build_result(
        stats=stats,
        raw_comps=raw_comps,
        search_term=search_term,
        region=region,
        source=f"ebay_{cache_source}",
    )
    # Mirror to local cache for offline fallback
    _save_to_local(out)
    return out


# --- Local fallback -----------------------------------------------------

def _local_fallback(search_term: str, region: str) -> dict[str, Any]:
    """Return cached result from SQLite if it exists and is fresh."""
    conn = get_connection()
    row = conn.execute(
        """SELECT stats_json, raw_comps_json, fetched_at
           FROM comps_local_cache
           WHERE search_term = ? AND region = ?""",
        (search_term, region),
    ).fetchone()
    if not row:
        return _empty_stats(search_term, region)
    try:
        fetched_at = float(row["fetched_at"])
    except (TypeError, ValueError):
        return _empty_stats(search_term, region)
    age_s = time.time() - fetched_at
    if age_s > LOCAL_FALLBACK_TTL_S:
        return _empty_stats(search_term, region)
    try:
        stats = json.loads(row["stats_json"])
        raw_comps = json.loads(row["raw_comps_json"] or "[]")
    except json.JSONDecodeError:
        return _empty_stats(search_term, region)
    return _build_result(
        stats=stats,
        raw_comps=raw_comps,
        search_term=search_term,
        region=region,
        source="cache_local",
    )


def _save_to_local(result: dict[str, Any]) -> None:
    """Mirror a cloud response into the local SQLite cache. Idempotent
    upsert keyed on (search_term, region)."""
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """INSERT INTO comps_local_cache
                       (search_term, region, stats_json, raw_comps_json, fetched_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(search_term, region) DO UPDATE SET
                       stats_json     = excluded.stats_json,
                       raw_comps_json = excluded.raw_comps_json,
                       fetched_at     = excluded.fetched_at""",
                (
                    result["search_term"],
                    result["region"],
                    json.dumps({k: v for k, v in result.items() if k not in
                                ("raw_comps", "search_term", "region", "source")}),
                    json.dumps(result.get("raw_comps") or []),
                    time.time(),
                ),
            )
    except Exception:  # noqa: BLE001 — fallback cache must never crash
        logger.exception("failed to save comps to local cache")


# --- Shape helpers ------------------------------------------------------

def _empty_stats(search_term: str, region: str) -> dict[str, Any]:
    return _build_result(
        stats={
            "sample_size": 0, "median": 0, "mean": 0,
            "minimum": 0, "maximum": 0,
            "p10": 0, "q1": 0, "q3": 0, "p90": 0,
            "iqr": 0, "iqr_ratio": 0,
        },
        raw_comps=[],
        search_term=search_term,
        region=region,
        source="empty",
    )


def _build_result(
    *,
    stats: dict,
    raw_comps: list,
    search_term: str,
    region: str,
    source: str,
) -> dict[str, Any]:
    return {
        **stats,
        "search_term": search_term,
        "region": region,
        "source": source,
        "raw_comps": raw_comps,
    }
