"""Comps data structures + stats computation (DB-free port).

This file is the bullseye-desktop port of the personal deal_finder's
`db/comps.py`. The personal version uses PostgreSQL (psycopg2) for
caching; bullseye desktop uses SQLite via a different path AND a
remote /comps cloud function as the persistent cache, so we do NOT
need the DB code paths here.

What we DO need:
  - CompObservation / CompStats dataclasses (used by scraper/ebay.py
    and appraisal/formula.py — both copied verbatim from personal).
  - _compute_stats(prices, ...) — pure-Python pipeline that takes a
    raw price list and returns a fully-populated CompStats with
    Tukey-trimmed median, IQR, percentiles, etc.
  - _maybe_split_bimodal — bimodal-cluster detection used inside
    _compute_stats.

What we DON'T provide (but used to):
  - insert_comps / fetch_stats — DB writes. Stubbed to raise so
    accidental imports surface loudly in dev. The bullseye `/appraise`
    endpoint pulls comps from the cloud /comps cache OR fetches
    fresh via scraper/ebay.py and pipes the prices straight into
    _compute_stats.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

logger = logging.getLogger(__name__)


# --- Public dataclasses ---------------------------------------------------

@dataclass
class CompObservation:
    """One observed price for a search term."""
    price: float
    title: str | None = None
    listing_url: str | None = None
    location: str | None = None


@dataclass
class CompStats:
    """Aggregated stats over a set of comps.

    Two flavors of central tendency are reported:
      * `median` / `mean`         -- across the full sample
      * `trimmed_median` / `trimmed_mean` -- after dropping Tukey-fence
        outliers (values outside Q1 - 1.5*IQR to Q3 + 1.5*IQR)

    The trimmed values are what the appraisal formula uses by default
    because a single $5000 scammer or $20 broken unit can otherwise
    yank the median in a small sample.
    """
    search_term: str
    source: str
    sample_size: int
    median: float | None = None
    mean: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    # Spread / quantiles
    stddev: float | None = None
    p10: float | None = None
    q1: float | None = None
    q3: float | None = None
    p90: float | None = None
    iqr: float | None = None
    # Outlier-trimmed central tendency
    trimmed_sample_size: int | None = None
    trimmed_median: float | None = None
    trimmed_mean: float | None = None
    outliers_dropped: int = 0
    # Embedding-based semantic filtering provenance (unused on bullseye)
    embedding_filter_applied: bool = False
    embedding_kept_count: int | None = None
    embedding_threshold: float | None = None
    # Provenance
    fetched_at: datetime | None = None
    fresh: bool = False  # True if within TTL (set by caller)


# --- Stub DB functions ----------------------------------------------------
# These exist so any leftover imports from the personal modules fail
# fast with a clear message. The bullseye appraise endpoint never calls
# them.

def insert_comps(*args, **kwargs):  # noqa: D401
    raise NotImplementedError(
        "insert_comps is not available on bullseye desktop — comps live "
        "in the cloud /comps cache. Compute stats via _compute_stats() "
        "directly from a price list."
    )


def fetch_stats(*args, **kwargs):  # noqa: D401
    raise NotImplementedError(
        "fetch_stats is not available on bullseye desktop — query the "
        "cloud /comps function instead."
    )


# --- Stats computation (DB-free, pure Python) -----------------------------

def compute_stats_from_prices(
    *,
    prices: Iterable[float],
    search_term: str = "",
    source: str = "",
    asking_price: float | None = None,
) -> CompStats:
    """Public entry point: take a raw price list and return CompStats.

    This is the path the bullseye `/appraise` endpoint uses. Internally
    it's just a thin wrapper around the (verbatim) personal _compute_stats
    pipeline so the appraisal formula gets the same data shape it always
    has.
    """
    stats, _trace = compute_stats_traced(
        prices=prices,
        search_term=search_term,
        source=source,
        asking_price=asking_price,
    )
    return stats


def compute_stats_traced(
    *,
    prices: Iterable[float],
    search_term: str = "",
    source: str = "",
    asking_price: float | None = None,
) -> tuple[CompStats, dict]:
    """Same pipeline as compute_stats_from_prices but ALSO returns a
    debug trace describing every transformation:

      trace = {
        "input_count": int,
        "sorted_prices": [float],
        "bimodal": {...},          # _maybe_split_bimodal info dict
        "cluster_prices": [float], # what survived the bimodal split
        "tukey": {                 # only present if cluster >= 4
            "q1": float, "q3": float, "iqr": float,
            "lo_fence": float, "hi_fence": float,
            "kept_prices": [float], "dropped_prices": [float],
        },
        "final": {
            "median": float, "trimmed_median": float,
            "outliers_dropped": int, "trimmed_n": int,
        },
      }

    The score-card "Debug" expander uses this to render every step the
    appraiser took. Free of LLM dependencies — purely deterministic
    pipeline trace.
    """
    cleaned = sorted(float(p) for p in prices if p is not None and p > 0)
    return _compute_stats_traced(
        prices=cleaned,
        search_term=search_term,
        source=source,
        fetched_at=None,
        asking_price=asking_price,
    )


def _compute_stats_traced(
    *,
    prices: list[float],
    search_term: str,
    source: str,
    fetched_at: datetime | None,
    asking_price: float | None = None,
) -> tuple[CompStats, dict]:
    """Same as _compute_stats but also returns the per-step trace dict.

    Kept side-by-side with _compute_stats so the existing function
    stays a verbatim copy of the personal pipeline (untouched on any
    code path that doesn't ask for debug data).
    """
    trace: dict = {
        "input_count": len(prices),
        "sorted_prices": list(prices),
        "bimodal": None,
        "cluster_prices": list(prices),
        "tukey": None,
        "final": {},
    }

    n_total = len(prices)
    if n_total == 0:
        empty = CompStats(
            search_term=search_term, source=source,
            sample_size=0, fetched_at=fetched_at, fresh=True,
        )
        return empty, trace
    sorted_p = sorted(prices)

    # Step 1: bimodal split
    cluster, split_info = _maybe_split_bimodal(sorted_p, asking_price)
    trace["bimodal"] = dict(split_info)
    trace["cluster_prices"] = list(cluster)
    n_cluster = len(cluster)

    # Step 2: stats over the active cluster (which may equal the full set)
    median = statistics.median(cluster)
    mean = statistics.fmean(cluster)
    stdev = statistics.pstdev(cluster) if n_cluster >= 2 else 0.0

    q1 = q3 = iqr = p10 = p90 = None
    trimmed_median = median
    trimmed_mean = mean
    trimmed_n = n_cluster
    outliers = split_info["dropped_other_cluster"]

    if n_cluster >= 4:
        try:
            qs = statistics.quantiles(cluster, n=4, method="exclusive")
            q1, _, q3 = qs[0], qs[1], qs[2]
        except statistics.StatisticsError:
            q1 = q3 = None

        if q1 is not None and q3 is not None:
            iqr = q3 - q1
            lo = q1 - 1.5 * iqr
            hi = q3 + 1.5 * iqr
            kept = [p for p in cluster if lo <= p <= hi]
            dropped = [p for p in cluster if p < lo or p > hi]
            tukey_outliers = n_cluster - len(kept)
            outliers += tukey_outliers
            trace["tukey"] = {
                "q1": q1, "q3": q3, "iqr": iqr,
                "lo_fence": lo, "hi_fence": hi,
                "kept_prices": kept,
                "dropped_prices": dropped,
            }
            if kept:
                trimmed_median = statistics.median(kept)
                trimmed_mean = statistics.fmean(kept)
                trimmed_n = len(kept)

    if n_cluster >= 10:
        try:
            deciles = statistics.quantiles(cluster, n=10, method="exclusive")
            p10 = deciles[0]
            p90 = deciles[8]
        except statistics.StatisticsError:
            p10 = p90 = None

    trace["final"] = {
        "median": median,
        "trimmed_median": trimmed_median,
        "outliers_dropped": outliers,
        "trimmed_n": trimmed_n,
    }

    stats = CompStats(
        search_term=search_term,
        source=source,
        sample_size=n_total,
        median=median,
        mean=mean,
        minimum=cluster[0],
        maximum=cluster[-1],
        stddev=stdev,
        p10=p10,
        q1=q1,
        q3=q3,
        p90=p90,
        iqr=iqr,
        trimmed_sample_size=trimmed_n,
        trimmed_median=trimmed_median,
        trimmed_mean=trimmed_mean,
        outliers_dropped=outliers,
        fetched_at=fetched_at,
        fresh=True,
    )
    return stats, trace


def _compute_stats(
    *,
    prices: list[float],
    search_term: str,
    source: str,
    fetched_at: datetime | None,
    asking_price: float | None = None,
) -> CompStats:
    """Build a fully-populated CompStats from a price list.

    Pipeline (verbatim from personal deal_finder):
      1. Bimodal cluster detection (if `asking_price` is given) — split
         the comp set on the largest log-gap when one cluster's median
         is more than 2x another's. Keep the cluster nearest the asking
         price.
      2. Tukey-fence outlier trim on the remaining prices for
         `trimmed_*` fields.
      3. Compute median, mean, IQR, percentiles, etc.

    The full pre-cluster `sample_size` is preserved so the user can
    still see "12 comps total, 4 in your price band". `trimmed_sample_size`
    reflects the cluster that was actually used to derive `trimmed_median`.
    """
    n_total = len(prices)
    if n_total == 0:
        return CompStats(
            search_term=search_term, source=source,
            sample_size=0, fetched_at=fetched_at, fresh=True,
        )
    sorted_p = sorted(prices)

    # Step 1: bimodal split
    cluster, split_info = _maybe_split_bimodal(sorted_p, asking_price)
    n_cluster = len(cluster)

    # Step 2: stats over the active cluster (which may equal the full set)
    median = statistics.median(cluster)
    mean = statistics.fmean(cluster)
    stdev = statistics.pstdev(cluster) if n_cluster >= 2 else 0.0

    q1 = q3 = iqr = p10 = p90 = None
    trimmed_median = median
    trimmed_mean = mean
    trimmed_n = n_cluster
    outliers = split_info["dropped_other_cluster"]

    if n_cluster >= 4:
        try:
            qs = statistics.quantiles(cluster, n=4, method="exclusive")
            q1, _, q3 = qs[0], qs[1], qs[2]
        except statistics.StatisticsError:
            q1 = q3 = None

        if q1 is not None and q3 is not None:
            iqr = q3 - q1
            lo = q1 - 1.5 * iqr
            hi = q3 + 1.5 * iqr
            kept = [p for p in cluster if lo <= p <= hi]
            tukey_outliers = n_cluster - len(kept)
            outliers += tukey_outliers
            if kept:
                trimmed_median = statistics.median(kept)
                trimmed_mean = statistics.fmean(kept)
                trimmed_n = len(kept)

    if n_cluster >= 10:
        try:
            deciles = statistics.quantiles(cluster, n=10, method="exclusive")
            p10 = deciles[0]
            p90 = deciles[8]
        except statistics.StatisticsError:
            p10 = p90 = None

    return CompStats(
        search_term=search_term,
        source=source,
        sample_size=n_total,
        median=median,
        mean=mean,
        minimum=cluster[0],
        maximum=cluster[-1],
        stddev=stdev,
        p10=p10,
        q1=q1,
        q3=q3,
        p90=p90,
        iqr=iqr,
        trimmed_sample_size=trimmed_n,
        trimmed_median=trimmed_median,
        trimmed_mean=trimmed_mean,
        outliers_dropped=outliers,
        fetched_at=fetched_at,
        fresh=True,
    )


# --- Bimodal split detection (verbatim from personal) ---------------------

def _maybe_split_bimodal(
    sorted_prices: list[float], asking_price: float | None,
) -> tuple[list[float], dict]:
    """Detect a bimodal price distribution and pick the cluster closest
    to `asking_price`.

    Heuristic: walk the sorted prices, find the largest *ratio* gap
    between consecutive entries (price[i+1] / price[i]). If that gap
    is large enough AND splits the data into two non-trivial groups
    (each with >=2 entries) AND the medians of the two groups differ
    by more than 2x, treat it as bimodal. Then return the cluster
    whose median is closest to the asking price.

    If no split is triggered (no asking_price, too few comps, gap too
    small, or one cluster too tiny), return the full sorted list.

    Tuned conservatively — false positives (over-splitting) are worse
    than false negatives (missing a real split). This is the path that
    saves Honda Civic appraisals: the cheap parts cluster ($5-$80) gets
    split off from the actual cars cluster ($2k-$10k), and we keep
    only the cluster whose median is closest to the asking price.
    """
    n = len(sorted_prices)
    info = {
        "split_triggered": False,
        "split_at": None,
        "left_size": 0,
        "right_size": 0,
        "left_median": None,
        "right_median": None,
        "chose": None,
        "dropped_other_cluster": 0,
    }
    if asking_price is None or n < 6:
        return sorted_prices, info

    # Find largest ratio gap. Skip prices <= 0 to avoid div-by-zero.
    best_gap_ratio = 1.0
    best_idx = -1
    for i in range(n - 1):
        if sorted_prices[i] <= 0:
            continue
        ratio = sorted_prices[i + 1] / sorted_prices[i]
        if ratio > best_gap_ratio:
            best_gap_ratio = ratio
            best_idx = i

    # Need both halves >= 3 to consider them meaningful clusters.
    if best_idx < 2 or (n - 1 - best_idx) < 3:
        return sorted_prices, info

    # Need at least 2.5x gap to call it bimodal.
    if best_gap_ratio < 2.5:
        return sorted_prices, info

    left = sorted_prices[: best_idx + 1]
    right = sorted_prices[best_idx + 1:]
    left_med = statistics.median(left)
    right_med = statistics.median(right)

    # Need the two medians to differ by >2x to confirm clusters are
    # genuinely different price tiers.
    if right_med < left_med * 2:
        return sorted_prices, info

    # Pick the cluster whose median is closer to asking (in log space).
    import math
    log_ask = math.log(max(asking_price, 1.0))
    log_left = math.log(max(left_med, 1.0))
    log_right = math.log(max(right_med, 1.0))
    chose_left = abs(log_ask - log_left) <= abs(log_ask - log_right)
    chosen = left if chose_left else right
    other_n = len(right) if chose_left else len(left)

    info.update({
        "split_triggered": True,
        "split_at": sorted_prices[best_idx],
        "left_size": len(left),
        "right_size": len(right),
        "left_median": left_med,
        "right_median": right_med,
        "chose": "left" if chose_left else "right",
        "dropped_other_cluster": other_n,
    })
    return chosen, info
