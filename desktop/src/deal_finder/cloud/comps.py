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


# Country (parsed from the Nominatim-formatted home_label) → eBay
# marketplace ID. The mapping is what matches FB Marketplace's local
# currency: FB returns USD listings to a US user, so we want eBay-US
# (also USD); FB returns CAD to a Canadian user, so eBay-ENCA (CAD).
# Mixed-currency comp/listing comparison is the bug this fixes — a
# $200 USD listing scored against CAD comps would look ~30% cheaper
# than it actually is.
_COUNTRY_TO_REGION = {
    "united states": "EBAY-US",
    "usa": "EBAY-US",
    "canada": "EBAY-ENCA",
    "united kingdom": "EBAY-GB",
    "uk": "EBAY-GB",
    "australia": "EBAY-AU",
    "germany": "EBAY-DE",
    "france": "EBAY-FR",
    "italy": "EBAY-IT",
    "spain": "EBAY-ES",
}


def resolve_ebay_region() -> str:
    """Derive the eBay marketplace region from the user's saved home
    location so comp prices come back in the same currency as the FB
    listings we're scoring.

    The Settings UI stores a Nominatim-formatted label whose last
    comma-separated token is the country name. We match that against
    `_COUNTRY_TO_REGION` and fall back to EBAY-ENCA when the user
    hasn't set a location (preserves legacy behavior for installs that
    pre-date the location-required gate).
    """
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT home_label FROM user_settings WHERE user_id = 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 — never crash scoring on a settings read
        return "EBAY-ENCA"
    label = row["home_label"] if row else None
    if not label:
        return "EBAY-ENCA"
    tokens = [t.strip().lower() for t in str(label).split(",") if t.strip()]
    if not tokens:
        return "EBAY-ENCA"
    return _COUNTRY_TO_REGION.get(tokens[-1], "EBAY-ENCA")


def get_comps(
    search_term: str,
    *,
    region: str | None = None,
    force_refresh: bool = False,
    category_hint: str | None = None,
    coarse_low: float | None = None,
    coarse_high: float | None = None,
) -> dict[str, Any]:
    """Fetch comp stats for `search_term`. Never raises — returns an
    empty CompStats on total failure so the appraisal pipeline can
    proceed and mark the listing unscoreable.

    Optional cloud-side guards (Option 1+4 from the architecture
    review on accessory contamination):
      - category_hint  →  cloud maps to eBay categoryId so accessories
                          and parts physically cannot appear in the
                          result set
      - coarse_low/high →  cloud applies MinPrice/MaxPrice as a 0.20x..5x
                          band around the LLM's expected range to drop
                          cheap-accessory and absurdly-priced outliers

    Both are optional. Backward compatible — when neither is passed the
    /comps function behaves like the pre-014 version.

    Side effect: on a successful cloud fetch, mirrors the result into
    the local SQLite cache for offline fallback.
    """
    if region is None:
        region = resolve_ebay_region()
    if not search_term or not search_term.strip():
        return _empty_stats(search_term, region)

    # 1. Try the cloud
    payload: dict[str, Any] = {
        "search_term": search_term,
        "region": region,
        "force_refresh": force_refresh,
    }
    if category_hint:
        payload["category_hint"] = category_hint
    if coarse_low is not None:
        payload["coarse_low"] = coarse_low
    if coarse_high is not None:
        payload["coarse_high"] = coarse_high
    try:
        resp = client.post("comps", payload)
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
    # Forward the cloud's filter-trace fields so the desktop's debug
    # panel can show which categoryId actually fired and which price
    # band was applied. Optional — pre-014 cloud builds don't return
    # them and `dict.get` returns None gracefully.
    if "category_hint" in resp:
        out["category_hint"] = resp["category_hint"]
    if "category_id" in resp:
        out["category_id"] = resp["category_id"]
    if "price_band" in resp:
        out["price_band"] = resp["price_band"]
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
