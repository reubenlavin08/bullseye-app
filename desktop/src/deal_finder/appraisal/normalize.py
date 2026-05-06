"""LLM title normalization client.

Thin wrapper over the cloud `/appraise-normalize` edge function.
Two callers:

  1. /appraise (click-to-appraise on the user's open Marketplace card):
     called with one item, returns the normalized result.
  2. Watch poller (background, every 5-30 min): called with a batch
     of survivors after the local rejection.evaluate() filter has
     dropped obvious junk. Batch size is bounded to MAX_BATCH_SIZE
     (50, mirrored on the cloud side).

Both paths share the same edge function / cache. Two listings with
different raw titles but same canonical_kind hit the same comp set,
which is the entire point of this module.

Robust to cloud failures: if the cloud is down or returns a non-2xx,
we return a "fallback" result with empty canonical_kind and
worth_deep=True. The caller's existing path (use raw title for comp
lookup) still works — we degrade gracefully rather than fail the
whole appraise. The UI will show a "couldn't normalize" footnote.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


# Mirror the cloud-side cap. Anything above this gets sliced into
# multiple round trips by `normalize_batch`.
MAX_BATCH_SIZE = 50


@dataclass(frozen=True)
class NormalizedListing:
    """One result from the LLM normalize call.

    canonical_kind:
        Cleaned product identifier (e.g. "iPhone 12 64GB"). Empty
        string when the LLM couldn't determine one (services,
        empty body, off-topic). Caller should fall back to raw
        title for comp lookup in that case.
    coarse_low / coarse_high:
        Rough expected used-price range, CAD. Used by the watcher
        to gate expensive comp lookups (e.g. skip if asking_price
        is way above coarse_high — the LLM thinks it's overpriced).
    confidence:
        'low' | 'medium' | 'high'. Plumbed into the score
        confidence_pm so low-confidence normalizations widen the
        score band.
    worth_deep:
        False = junk listing (WTB, services, vague). Caller should
        show "Couldn't appraise — low-quality data" and skip
        comp lookup.
    red_flags:
        List of short tokens (broken_screen, icloud_locked, etc.).
        UI surfaces these as small chips on the card.
    """
    listing_url: str
    canonical_kind: str
    coarse_low: int
    coarse_high: int
    confidence: str
    worth_deep: bool
    red_flags: list[str]
    reasoning: str | None
    cache_hit: bool

    @property
    def is_fallback(self) -> bool:
        """True when the cloud failed and we returned a no-op record."""
        return (
            not self.canonical_kind
            and self.confidence == "low"
            and not self.cache_hit
        )


def _empty_fallback(listing_url: str) -> NormalizedListing:
    """No-op record returned when the cloud is unavailable."""
    return NormalizedListing(
        listing_url=listing_url,
        canonical_kind="",
        coarse_low=0,
        coarse_high=0,
        confidence="low",
        worth_deep=True,  # trust the existing pipeline rather than dropping
        red_flags=[],
        reasoning=None,
        cache_hit=False,
    )


def normalize_one(
    *,
    listing_url: str,
    title: str,
    body: str | None = None,
    ask_price: float | int | None = None,
) -> NormalizedListing:
    """One-shot normalize. Wraps normalize_batch for click paths."""
    out = normalize_batch([{
        "listing_url": listing_url,
        "title": title,
        "body": body or "",
        "ask_price": ask_price,
    }])
    return out[0] if out else _empty_fallback(listing_url)


def normalize_batch(items: Iterable[dict]) -> list[NormalizedListing]:
    """Batch normalize. Items: [{listing_url, title, body, ask_price}].

    Returns one NormalizedListing per input, in the same order. On
    cloud failure each entry becomes an empty_fallback so the caller
    can still proceed (with raw-title comp lookup for that entry).
    """
    items_list = list(items)
    if not items_list:
        return []

    # Slice into chunks <= MAX_BATCH_SIZE so very large watch polls
    # don't blow past the edge function's batch cap.
    out: list[NormalizedListing] = []
    for i in range(0, len(items_list), MAX_BATCH_SIZE):
        chunk = items_list[i:i + MAX_BATCH_SIZE]
        out.extend(_normalize_chunk(chunk))
    return out


def _normalize_chunk(chunk: list[dict]) -> list[NormalizedListing]:
    # Imported lazily so this module is safe to import at boot
    # (cloud client may not be initialized yet during migrations).
    from deal_finder.cloud.client import (
        client as cloud_client, CloudError, CloudUnavailable,
    )

    payload = {
        "items": [{
            "listing_url": str(it.get("listing_url") or ""),
            "title": str(it.get("title") or ""),
            "body": str(it.get("body") or "")[:1000],
            "ask_price": (
                float(it["ask_price"]) if it.get("ask_price") is not None
                else None
            ),
        } for it in chunk if it.get("listing_url") and it.get("title")],
    }
    if not payload["items"]:
        return [_empty_fallback(str(it.get("listing_url") or ""))
                for it in chunk]

    try:
        resp = cloud_client.post("appraise-normalize", payload)
    except CloudUnavailable as e:
        logger.warning("normalize: cloud unavailable: %s", e)
        return [_empty_fallback(it["listing_url"]) for it in payload["items"]]
    except CloudError as e:
        logger.warning("normalize: cloud error: %s", e)
        return [_empty_fallback(it["listing_url"]) for it in payload["items"]]
    except Exception as e:  # noqa: BLE001
        logger.warning("normalize: unexpected error: %s", e)
        return [_empty_fallback(it["listing_url"]) for it in payload["items"]]

    results = (resp or {}).get("results") or []
    by_url = {r.get("listing_url"): r for r in results if r.get("listing_url")}

    out: list[NormalizedListing] = []
    for it in payload["items"]:
        r = by_url.get(it["listing_url"])
        if not r:
            out.append(_empty_fallback(it["listing_url"]))
            continue
        out.append(NormalizedListing(
            listing_url=it["listing_url"],
            canonical_kind=str(r.get("canonical_kind") or ""),
            coarse_low=int(r.get("coarse_low") or 0),
            coarse_high=int(r.get("coarse_high") or 0),
            confidence=(
                r.get("confidence") if r.get("confidence")
                in ("low", "medium", "high") else "low"
            ),
            worth_deep=bool(r.get("worth_deep")),
            red_flags=list(r.get("red_flags") or []),
            reasoning=r.get("reasoning"),
            cache_hit=bool(r.get("cache_hit")),
        ))
    return out
