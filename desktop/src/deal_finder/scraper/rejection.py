"""Rejection filter.

Three-stage gate run after price extraction, before deduplication and DB
write. Listings that match any stage get persisted with rejected=True
and a rejection_reason, but skip the eBay/LLM appraisal pipeline.

  Stage 1: regex patterns matched against title + " " + description (lowered)
  Stage 2: substring keywords matched against title only (lowered)
  Stage 3: COMPOUND price + OBO/negotiable rule. We deliberately keep
           generic "OBO" / "or best offer" / "negotiable" out of the
           regex blocklist because real listings with real prices use
           that language all the time — those are still valuable comps.
           BUT when the asking price is $0 or $1 (i.e. the seller
           refuses to anchor) AND the listing says OBO/negotiable, we
           have nothing to compare against, so we drop the listing.

Patterns and keywords live in plain-text config files:
  config/rejection_patterns.txt
  config/rejection_keywords.txt

Both support # comments and blank lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


# Resolve config dir relative to project root. Override with
# DEAL_FINDER_CONFIG_DIR env var if running outside the repo.
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


@dataclass(frozen=True)
class RejectionResult:
    """Outcome of running the rejection filter.

    rejected -- True if either stage matched
    reason   -- e.g. "pattern: \\bswap\\b" or "keyword: parts only"; None when
                rejected is False
    """
    rejected: bool
    reason: str | None


def _read_config_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


@lru_cache(maxsize=4)
def _load_patterns(config_dir: str) -> list[re.Pattern[str]]:
    cdir = Path(config_dir)
    raws = _read_config_lines(cdir / "rejection_patterns.txt")
    compiled: list[re.Pattern[str]] = []
    for raw in raws:
        try:
            compiled.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            # Skip bad regex lines silently — config errors shouldn't kill
            # the pipeline. (We log them in production via a logger; the
            # spike module stays log-free.)
            continue
    return compiled


@lru_cache(maxsize=4)
def _load_keywords(config_dir: str) -> tuple[str, ...]:
    cdir = Path(config_dir)
    raws = _read_config_lines(cdir / "rejection_keywords.txt")
    return tuple(k.lower() for k in raws)


# Stage 3 helpers — compound price + OBO/negotiable rejection.
# Only fires when ask_price is $0 or $1 AND the listing has OBO-ish
# language. Real-priced OBO listings are NOT rejected here.
_OBO_NEGOTIABLE_RE = re.compile(
    r"\b(obo|o\.b\.o\.|or\s+best\s+offer|"
    r"best\s+offer|negotiable|"
    r"price\s+is\s+(negotiable|flexible)|"
    r"open\s+to\s+offers?)\b",
    re.IGNORECASE,
)


def _is_anchor_price_missing(ask_price: float | int | None) -> bool:
    """True if the seller didn't provide a meaningful asking price.

    $0 and $1 are the two values Marketplace sellers use as
    placeholders to surface their listing under "Free" / "$1" filters.
    Anything >= $2 is treated as a real anchor for comp comparison
    (even if "OBO" is in the description — they at least gave us a
    starting point).
    """
    try:
        return ask_price is not None and float(ask_price) <= 1.0
    except (TypeError, ValueError):
        return False


def evaluate(
    title: str,
    description: str | None = None,
    *,
    ask_price: float | int | None = None,
    config_dir: Path | str | None = None,
) -> RejectionResult:
    """Run all stages. Returns RejectionResult.

    Args:
        title:       listing title
        description: optional listing body / description
        ask_price:   optional seller-stated asking price in CAD; used
                     by the Stage-3 compound rule to drop $0/$1 OBO
                     listings (no anchor → can't compare to comps).
        config_dir:  defaults to <repo>/config, but tests can override.
    """
    cdir = str(config_dir) if config_dir else str(_DEFAULT_CONFIG_DIR)

    title_l = (title or "").lower()
    combined_raw = (title or "") + " " + (description or "")
    combined_l = combined_raw.lower().strip()

    # Stage 1: regex patterns against title + description
    for pat in _load_patterns(cdir):
        if pat.search(combined_l):
            return RejectionResult(
                rejected=True, reason=f"pattern: {pat.pattern}"
            )

    # Stage 2: keyword substring against title only
    for kw in _load_keywords(cdir):
        if kw in title_l:
            return RejectionResult(
                rejected=True, reason=f"keyword: {kw}"
            )

    # Stage 3: compound price + OBO/negotiable rule. Only fires for
    # $0/$1 listings; we want to keep real-priced OBO listings since
    # those are valuable comp data.
    if _is_anchor_price_missing(ask_price) and _OBO_NEGOTIABLE_RE.search(
        combined_raw,
    ):
        return RejectionResult(
            rejected=True,
            reason=f"price_no_anchor: ask=${ask_price}, OBO/negotiable",
        )

    return RejectionResult(rejected=False, reason=None)


def reset_cache() -> None:
    """Clear the LRU caches. Useful for tests that swap config dirs."""
    _load_patterns.cache_clear()
    _load_keywords.cache_clear()
