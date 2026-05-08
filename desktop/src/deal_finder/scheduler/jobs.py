"""Scheduler jobs.

Two jobs run on a timer:

  poll_search(search_id)
      Hits Marketplace for one saved search. For every listing ID we
      haven't seen before, runs the FULL pipeline inline (detail fetch
      + price extract + reject + persist + comp lookup + score). This
      is the "tight polling" pattern — new listings get scored within
      seconds of being detected, not minutes.

  drain_appraisal_safety_net()
      Stub for now — the worker module is deferred (Pro feature). Will
      pick up any listings that ended up persisted but unappraised once
      step 5 of the implementation plan lands.

Per-listing processing happens INSIDE poll_search rather than via a
separate worker queue. For a single-machine setup with one user, this
is simpler and faster than a producer/consumer split.

Translation notes (vs. personal tool):
  * Postgres -> SQLite: %s -> ?, NOW() -> CURRENT_TIMESTAMP,
    INTERVAL 'N seconds' -> datetime('now', '-N seconds'),
    LATERAL joins rewritten as correlated subqueries,
    EXTRACT(EPOCH FROM ...) computed in Python,
    boolean columns stored as INTEGER 0/1.
  * comps: ebay + marketplace direct fetches replaced with a single
    cloud.comps.get_comps() (cloud handles the eBay API call with
    our shared dev key + serves results from a shared cache).
  * License: every poll tick checks license_manager.is_kill_switched()
    and clamps the effective interval to poll_interval_min().
  * secondary_check / digest / worker imports stripped — those ship
    in later steps of the implementation plan.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from ..appraisal.condition_signals import extract_condition_signals
from ..appraisal.formula import compute_score
from ..appraisal.normalize import normalize_batch, NormalizedListing
from ..appraisal.normalizer import normalize_title
from ..cloud import telemetry as cloud_telemetry
from ..cloud.comps import get_comps
from ..db.connection import get_conn
from ..db.events import record_event
from ..db.geo import geocode_city, haversine_km
from ..db.listings import (
    existing_ids,
    update_appraisal,
    update_comps_resolution,
    upsert_processed,
)
from ..license.manager import license_manager
from ..scraper.facebook import (
    FacebookRateLimited,
    SearchListing,
    SearchParams,
    get_default_client as get_search_client,
)
from ..scraper.facebook_detail import (
    get_default_client as get_detail_client,
)
from ..scraper.pipeline import _combine
from ..scraper.price_extraction import resolve_price
from ..scraper.rejection import evaluate as evaluate_rejection

logger = logging.getLogger(__name__)


# --- Inlined helpers from the personal tool's appraisal/worker.py --------
#
# worker.py is deferred (it owns the safety-drain loop and the LLM
# fallback path). The two functions we need from it are simple enough
# to inline here so we can keep the scheduler running.

def _recover_price(raw_price: float, description: str) -> tuple[float, bool]:
    """Recover a real asking price from a placeholder + description.

    Tries regex first (fast, free). LLM fallback omitted in this port —
    that path lives in the deferred worker.py + normalizer.extract_price_llm.
    Returns (resolved_price, extracted_flag).
    """
    pr = resolve_price(raw_price, description)
    return pr.price, pr.extracted


@dataclass
class PollResult:
    """One scrape-poll outcome."""
    search_id: int
    keyword: str
    raw_count: int           # listings returned by FB
    new_count: int           # not yet in our DB
    appraised_count: int     # successfully scored
    rejected_count: int      # dropped by rejection filter
    elapsed_s: float


def _kill_switch_active() -> bool:
    """Wrapper around license_manager.is_kill_switched() that fails
    open when the manager isn't fully wired up yet (NotImplementedError).

    During step 2 the manager is a stub that raises; in steps 3-5 it'll
    return real values. Either way the scheduler shouldn't crash.
    """
    try:
        return bool(license_manager.is_kill_switched())
    except NotImplementedError:
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("license kill-switch check raised: %s", e)
        return False


def _license_min_poll_interval_s() -> int:  # noqa: F811 — see helper below
    """Per-watch minimum interval in seconds, license-clamped.

    Prefers the new poll_interval_s field (lets Pro express 30s
    cadence — impossible in the integer-minutes API). Falls back to
    poll_interval_min × 60 for older cloud responses or stub
    license managers.
    """
    try:
        secs = license_manager.poll_interval_s()
        if secs is not None:
            return int(secs)
    except (NotImplementedError, AttributeError):
        # Older license_manager build without the helper — fall through
        # to the minutes-based path.
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning("license poll_interval_s raised: %s", e)
    return _license_min_poll_interval_s_minutes_path()


def _license_min_poll_interval_s_minutes_path() -> int:
    """Return the licensed minimum poll interval in seconds.

    Falls back to 0 (no clamp) when the manager isn't fully wired up.
    Used by callers that need to enforce a per-tier rate floor.
    """
    try:
        return int(license_manager.poll_interval_min()) * 60
    except NotImplementedError:
        return 0
    except Exception as e:  # noqa: BLE001
        logger.warning("license poll_interval_min raised: %s", e)
        return 0


def poll_search(search_id: int) -> PollResult:
    """Run one scrape cycle for a saved search and process any new listings.

    Designed to be safe to call repeatedly; deduplication via the DB
    keeps work to listings we've never seen before. If the search row
    has been disabled or deleted between scheduler firings, this is a
    cheap no-op.
    """
    if _kill_switch_active():
        logger.info("poll_search %d: kill-switch active, skipping", search_id)
        record_event("kill_switch_skip", search_id=search_id)
        return PollResult(search_id, "", 0, 0, 0, 0, 0.0)

    t0 = time.perf_counter()
    search = _load_search(search_id)
    if search is None:
        logger.info("poll_search %d: search no longer active, skipping", search_id)
        return PollResult(search_id, "", 0, 0, 0, 0, 0.0)

    keyword = search["keyword"]

    # 1) Hit Marketplace
    page = get_search_client().search(SearchParams(
        keyword=keyword,
        lat=search["latitude"],
        lng=search["longitude"],
        radius_km=search["radius_km"],
        price_min=search["price_min"],
        price_max=search["price_max"],
    ))

    # 2) Filter out already-seen listing IDs (cheap PK lookup)
    raw_ids = [sl.id for sl in page.listings]
    if not raw_ids:
        elapsed_s = time.perf_counter() - t0
        record_event(
            "poll", search_id=search_id, duration_ms=int(elapsed_s * 1000),
            keyword=keyword, raw_count=0, new_count=0,
            appraised_count=0, rejected_count=0,
        )
        return PollResult(search_id, keyword, 0, 0, 0, 0, elapsed_s)

    with get_conn() as conn:
        seen = existing_ids(conn, raw_ids)

    new_listings = [sl for sl in page.listings if sl.id not in seen]

    # Keyword filter: must_include / must_exclude on listing title.
    # Run BEFORE distance filter so we don't waste geocode round-trips
    # on listings we'll drop anyway.
    keyword_dropped = 0
    must_inc = _parse_word_list(search.get("must_include"))
    must_exc = _parse_word_list(search.get("must_exclude"))
    if new_listings and (must_inc or must_exc):
        kept_kw: list[SearchListing] = []
        for sl in new_listings:
            ok, reason = _passes_keyword_filter(sl.title, must_inc, must_exc)
            if ok:
                kept_kw.append(sl)
            else:
                keyword_dropped += 1
                logger.debug("%s dropped (keyword): %s | %s",
                             sl.id, reason, sl.title[:50])
        new_listings = kept_kw

    # Distance filter — see personal tool for the full rationale.
    distance_dropped = 0
    if new_listings and search.get("radius_km"):
        kept: list[SearchListing] = []
        home_lat = float(search["latitude"])
        home_lng = float(search["longitude"])
        radius = float(search["radius_km"])
        soft_radius = radius
        for sl in new_listings:
            if not sl.seller_location:
                kept.append(sl)
                continue
            coords = geocode_city(sl.seller_location)
            if coords is None:
                kept.append(sl)
                continue
            dist = haversine_km(home_lat, home_lng, coords[0], coords[1])
            if dist <= soft_radius:
                kept.append(sl)
            else:
                distance_dropped += 1
                logger.debug(
                    "%s dropped: %.1f km > %.1f km (%s)",
                    sl.id, dist, soft_radius, sl.seller_location,
                )
        new_listings = kept

    if not new_listings:
        elapsed_s = time.perf_counter() - t0
        record_event(
            "poll", search_id=search_id, duration_ms=int(elapsed_s * 1000),
            keyword=keyword, raw_count=len(page.listings),
            new_count=0, appraised_count=0, rejected_count=0,
            distance_dropped=distance_dropped,
            keyword_dropped=keyword_dropped,
        )
        return PollResult(
            search_id, keyword, len(page.listings), 0, 0, 0, elapsed_s,
        )

    logger.info(
        "poll_search %d (%r): %d new of %d returned",
        search_id, keyword, len(new_listings), len(page.listings),
    )

    # 2.5) Cheap reject filter pass — runs against title + (search-page
    # description if any) BEFORE we hit the cloud LLM. Drops obvious
    # junk (trade/swap/services/rentals/$0-OBO) for free, so the LLM
    # only ever sees real-product listings. Survivors are batch-
    # normalized below.
    pre_reject = 0
    survivors: list[SearchListing] = []
    for sl in new_listings:
        rej = evaluate_rejection(
            sl.title or "",
            getattr(sl, "description", None) or getattr(sl, "snippet", None),
            ask_price=getattr(sl, "price_amount", None),
        )
        if rej.rejected:
            pre_reject += 1
            logger.debug("%s pre-rejected: %s", sl.id, rej.reason)
            continue
        survivors.append(sl)
    new_listings = survivors

    # 2.6) Batch LLM normalize — one HTTP call to /appraise-normalize
    # carrying every survivor at once. The cloud function returns
    # {canonical_kind, worth_deep, red_flags, ...} per listing.
    # Cache hits are free; cache misses cost ~$0.0002 each.
    norm_by_url: dict[str, NormalizedListing] = {}
    if new_listings:
        try:
            batch = normalize_batch([{
                "listing_url": getattr(sl, "listing_url", "") or sl.id,
                "title": sl.title or "",
                "body": getattr(sl, "description", None) or "",
                "ask_price": getattr(sl, "price_amount", None),
            } for sl in new_listings])
            for r in batch:
                norm_by_url[r.listing_url] = r
        except Exception as e:  # noqa: BLE001
            # Cloud unreachable / timeout / parse error — log and keep
            # going with raw titles. Graceful degradation: every part
            # of the pipeline downstream tolerates a missing
            # canonical_kind by falling back to normalize_title().
            logger.warning("batch normalize failed (continuing with raw titles): %s", e)

    # 3) For each surviving listing: full pipeline inline
    appraised = 0
    rejected = pre_reject
    pp_home_lat = (
        float(search["latitude"]) if search.get("latitude") is not None else None
    )
    pp_home_lng = (
        float(search["longitude"]) if search.get("longitude") is not None else None
    )
    pp_radius = (
        float(search["radius_km"]) if search.get("radius_km") is not None else None
    )
    for sl in new_listings:
        url = getattr(sl, "listing_url", "") or sl.id
        norm = norm_by_url.get(url)
        try:
            outcome = _process_new_listing(
                sl, search_id=search_id,
                home_lat=pp_home_lat, home_lng=pp_home_lng, radius_km=pp_radius,
                norm=norm,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("processing %s failed: %s", sl.id, e)
            record_event(
                "pipeline_error",
                search_id=search_id,
                listing_id=sl.id,
                error=str(e)[:300],
                error_type=type(e).__name__,
            )
            continue
        if outcome == "appraised":
            appraised += 1
        elif outcome == "rejected":
            rejected += 1

    elapsed_s = time.perf_counter() - t0
    record_event(
        "poll",
        search_id=search_id,
        duration_ms=int(elapsed_s * 1000),
        keyword=keyword,
        raw_count=len(page.listings),
        new_count=len(new_listings),
        appraised_count=appraised,
        rejected_count=rejected,
        distance_dropped=distance_dropped,
        keyword_dropped=keyword_dropped,
    )
    return PollResult(
        search_id, keyword, len(page.listings), len(new_listings),
        appraised, rejected, elapsed_s,
    )


def _load_search(search_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT keyword, latitude, longitude, radius_km,
                      price_min, price_max, must_include, must_exclude
               FROM user_searches
               WHERE id = ? AND active = 1""",
            (search_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "keyword": row["keyword"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "radius_km": row["radius_km"],
        "price_min": row["price_min"],
        "price_max": row["price_max"],
        "must_include": row["must_include"],
        "must_exclude": row["must_exclude"],
    }


def _parse_word_list(s: str | None) -> list[str]:
    """Comma-separated -> list of lowercased trimmed tokens. Empty list
    when input is None/empty/whitespace.

    NOTE: SQLite stores `must_include` / `must_exclude` as JSON arrays
    in our schema (vs. comma strings in the personal tool's PG schema).
    We accept either shape — JSON list or comma string — to keep the
    helper forgiving across migrations.
    """
    if not s:
        return []
    text = s.strip()
    if not text:
        return []
    if text.startswith("["):
        import json
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(w).strip().lower() for w in parsed if str(w).strip()]
    return [w.strip().lower() for w in text.split(",") if w.strip()]


def _passes_keyword_filter(
    title: str, must_include: list[str], must_exclude: list[str],
) -> tuple[bool, str | None]:
    """Returns (kept, reason). reason is set when kept=False."""
    title_l = (title or "").lower()
    if must_include:
        if not any(w in title_l for w in must_include):
            return False, f"missing required: {','.join(must_include)}"
    if must_exclude:
        for w in must_exclude:
            if w in title_l:
                return False, f"contains banned: {w}"
    return True, None


def _process_new_listing(
    sl: SearchListing,
    *,
    search_id: int,
    home_lat: float | None = None,
    home_lng: float | None = None,
    radius_km: float | None = None,
    norm: NormalizedListing | None = None,
) -> str:
    """Run the full per-listing pipeline. Returns one of:
      "rejected"  — listing was filtered out
      "appraised" — listing was scored and persisted
      "unscoreable" — persisted but no score (insufficient comps)
      "skipped" — pipeline bailed out for some other reason

    Secondary-LLM check is deferred (Pro feature, step 4 of the plan).
    Until then we score on the formula alone.
    """
    detail = get_detail_client().fetch(sl.id)
    pl = _combine(sl, detail)

    # Precise radius gate — uses FB's per-listing coords (fuzzed to
    # ~1 mile by FB but vastly more accurate than the city centroid we
    # had at search time).
    if (radius_km is not None and radius_km > 0
            and home_lat is not None and home_lng is not None
            and pl.detail_latitude is not None
            and pl.detail_longitude is not None):
        precise_dist = haversine_km(
            float(home_lat), float(home_lng),
            pl.detail_latitude, pl.detail_longitude,
        )
        if precise_dist > float(radius_km):
            pl.rejected = True
            pl.rejection_reason = (
                f"out of radius: {precise_dist:.1f} km > {float(radius_km):.0f} km"
                f" (FB precise coords)"
            )
            logger.info(
                "%s out-of-radius: %.1f km > %.0f km | %s",
                sl.id, precise_dist, radius_km, sl.title[:60],
            )

    # Persist regardless — even rejected rows go in the DB so we don't
    # re-fetch them next poll cycle.
    with get_conn() as conn:
        with conn:
            upsert_processed(conn, pl, search_id=search_id)

    if pl.rejected:
        logger.info(
            "%s rejected: %s | %s",
            sl.id, pl.rejection_reason, pl.title[:60],
        )
        return "rejected"

    description = pl.description or ""
    asking = pl.resolved_price
    raw_price = pl.raw_price
    price_extracted = pl.price_extracted_from_description

    # JIT recovery for placeholder prices (regex-only in this port —
    # LLM fallback lives in the deferred worker.py).
    if asking <= 1.0 and description:
        asking, price_extracted = _recover_price(raw_price, description)
        if asking != pl.resolved_price:
            with get_conn() as conn:
                with conn:
                    conn.execute(
                        """UPDATE listings SET price = ?,
                              price_extracted_from_description = ?
                           WHERE id = ?""",
                        (asking, int(bool(price_extracted)), sl.id),
                    )

    # If asking is still a placeholder ($0 or $1) after price recovery,
    # we can't score it.
    if asking <= 1.0:
        logger.info(
            "%s unscoreable: no recoverable asking price (placeholder $%s) | %s",
            sl.id, asking, pl.title[:60],
        )
        with get_conn() as conn:
            with conn:
                conn.execute(
                    """UPDATE listings SET
                          appraised = 1,
                          appraised_at = CURRENT_TIMESTAMP,
                          appraisal_note = '[unscoreable] no recoverable asking price'
                       WHERE id = ?""",
                    (sl.id,),
                )
        return "unscoreable"

    # If LLM normalize said this listing isn't worth scoring (WTB,
    # services, vague), short-circuit. We persist it as appraised
    # with a low_quality_data note so the user sees "couldn't
    # appraise" but doesn't get re-asked next poll.
    if norm is not None and not norm.worth_deep and not norm.is_fallback:
        with get_conn() as conn:
            with conn:
                conn.execute(
                    """UPDATE listings SET
                          appraised = 1,
                          appraised_at = CURRENT_TIMESTAMP,
                          appraisal_note = '[unscoreable] low-quality listing data'
                       WHERE id = ?""",
                    (sl.id,),
                )
        logger.info(
            "%s low-quality (LLM): %s | %s",
            sl.id, (norm.reasoning or "no canonical_kind"), pl.title[:60],
        )
        return "unscoreable"

    # Comps via the cloud client. Use the LLM-normalized canonical_kind
    # if we have one (fixes the iPhone-12 scoring bug — two listings
    # with different raw titles will both look up the same comp set).
    # Falls back to the legacy local normalize_title when normalize
    # was unavailable or didn't return anything useful.
    canonical = norm.canonical_kind if (norm and norm.canonical_kind) else ""
    search_term = canonical or normalize_title(pl.title) or pl.title
    comp_kwargs = {"search_term": search_term, "region": "EBAY-ENCA"}
    if norm and norm.canonical_kind:
        if norm.category_hint:
            comp_kwargs["category_hint"] = norm.category_hint
        if norm.coarse_low and norm.coarse_low > 0:
            comp_kwargs["coarse_low"] = norm.coarse_low
        if norm.coarse_high and norm.coarse_high > 0:
            comp_kwargs["coarse_high"] = norm.coarse_high
    try:
        comp = get_comps(**comp_kwargs)
    except NotImplementedError:
        logger.debug(
            "%s skipped scoring: cloud.comps.get_comps not yet implemented",
            sl.id,
        )
        return "skipped"
    except Exception as e:  # noqa: BLE001
        logger.warning("%s comp fetch failed: %s", sl.id, e)
        return "skipped"

    # `get_comps` returns a DICT (cloud-flattened payload).
    # `compute_score` expects a CompStats dataclass — accessing
    # `comp.trimmed_sample_size` on a dict raises AttributeError, which
    # was crashing every poll silently. Mirror the /appraise route:
    # extract raw prices from the dict and re-compute stats through
    # the personal pipeline (bimodal split + Tukey trim + percentiles).
    from ..db.comps import compute_stats_from_prices
    raw_prices = [
        float(c.get("price")) for c in (comp.get("raw_comps") or [])
        if c.get("price") is not None and float(c.get("price")) > 0
    ]
    stats = compute_stats_from_prices(
        prices=raw_prices,
        search_term=comp.get("search_term") or search_term,
        source=comp.get("source") or "ebay",
        asking_price=asking,
    )

    cond = extract_condition_signals(description)
    breakdown = compute_score(
        asking_price=asking,
        comp=stats,
        condition_adjustment=cond.score_adjustment,
        condition_flags=cond.flags_fired,
        condition_note=cond.note,
        category_id=pl.category_id,
    )

    # Plumb LLM normalize confidence into the score band: low =
    # widen ±12, medium = ±6, high = pass through. Caps deal_score
    # at (100 - confidence_pm) so we never claim more confidence
    # than the underlying normalization supports.
    if (not breakdown.unscoreable and norm is not None
            and norm.canonical_kind and not norm.is_fallback):
        widen = {"low": 12, "medium": 6, "high": 0}.get(norm.confidence, 0)
        if widen > 0:
            new_pm = min(100, breakdown.confidence_pm + widen)
            new_score = min(breakdown.deal_score, max(0, 100 - new_pm))
            breakdown.confidence_pm = new_pm
            breakdown.deal_score = new_score

    note = (
        f"[unscoreable] {breakdown.unscoreable_reason}"
        if breakdown.unscoreable
        else f"[{breakdown.confidence_label} ±{breakdown.confidence_pm}]"
    )

    # Persist comps + appraisal. `comp` is a DICT from get_comps()
    # (cloud-flattened payload); `stats` is the proper CompStats
    # built from raw prices via compute_stats_from_prices() above.
    # Use stats for stat fields; pull search_term/source from
    # whichever has them.
    with get_conn() as conn:
        with conn:
            update_comps_resolution(
                conn, sl.id,
                search_term=(stats.search_term
                             or comp.get("search_term") or search_term),
                source=stats.source or comp.get("source") or "ebay",
                median=stats.median, mean=stats.mean,
                minimum=stats.minimum, maximum=stats.maximum,
                sample_size=stats.sample_size,
            )
            update_appraisal(
                conn, sl.id,
                deal_score=breakdown.deal_score,
                fair_value=breakdown.fair_value,
                appraisal_note=note,
                appraisal_model="formula-only",
                breakdown=breakdown,
            )

    if breakdown.unscoreable:
        logger.info("%s unscoreable: %s | %s",
                    sl.id, breakdown.unscoreable_reason, pl.title[:60])
        cloud_telemetry.emit("listing_appraised", {
            "listing_id": sl.id,
            "deal_score": None,
            "unscoreable": True,
            "reason": breakdown.unscoreable_reason,
        })
        return "unscoreable"

    logger.info(
        "%s SCORED %d (conf %s±%d, n=%d) | %s",
        sl.id, breakdown.deal_score, breakdown.confidence_label,
        breakdown.confidence_pm, stats.sample_size, pl.title[:60],
    )
    cloud_telemetry.emit("listing_appraised", {
        "listing_id": sl.id,
        "deal_score": int(breakdown.deal_score),
        "confidence_label": breakdown.confidence_label,
        "sample_size": stats.sample_size,
    })

    # Action-based achievements — fired off in a daemon thread so the
    # appraisal hot path stays uncontended. All calls are idempotent
    # at the cloud, so they're safe to fire on every appraisal.
    try:
        score = int(breakdown.deal_score)
        if score >= 80:
            from deal_finder.cloud import achievements as _ach
            from deal_finder.db.connection import get_conn as _get_conn
            _ach.try_award("first_deal_80")
            # Count distinct 80+ scored listings to drive the
            # five_deals_80 / twenty_five_deals_80 thresholds. Cheap
            # SELECT COUNT(*) — one round-trip per 80+ scored listing
            # is fine. (Could be debounced if scoring volume grows.)
            try:
                with _get_conn() as _c:
                    _row = _c.execute(
                        "SELECT COUNT(*) FROM listings "
                        "WHERE appraised = 1 AND rejected = 0 "
                        "AND deal_score IS NOT NULL AND deal_score >= 80"
                    ).fetchone()
                    n80 = int(_row[0] if _row else 0)
                if n80 >= 5:
                    _ach.try_award("five_deals_80")
                if n80 >= 25:
                    _ach.try_award("twenty_five_deals_80")
            except Exception as e:  # noqa: BLE001
                logger.debug("80+ count for achievement check failed: %s", e)
    except Exception as e:  # noqa: BLE001
        logger.debug("achievement award skipped: %s", e)

    # Desktop toast notification — fires once per listing when the score
    # crosses ALERT_SCORE_THRESHOLD (default 70). Wired up 2026-05-07
    # after a user-reported "I never see notifications" bug — the toast
    # plumbing existed (notifications/desktop.py + plyer dep) but nothing
    # actually called it. The scheduler's _process_new_listing is the
    # natural fire site since it's where every newly-scored listing
    # passes through.
    #
    # Idempotency: we use the dedicated `toast_fired` column added in
    # migration 008. The old code used the `notified` column, which is
    # ALSO consumed by the email digest pipeline (alerts/digest.py
    # `collect_pending_for_email` filters notified=0). Sharing the
    # column meant the toast (which runs synchronously here) marked
    # notified=1 before the 15s digest job could pick the listing up,
    # and the user never got an email — exactly the "is the system
    # emailing me?" symptom user reported on 2026-05-07. Splitting the
    # columns lets both channels fire independently.
    #
    # plyer's backend can fail on weird Windows configs (WinRT load
    # issues, action-center disabled by group policy) — those failures
    # are swallowed at the desktop module level so the appraisal hot
    # path always continues, and we wrap the whole block again here so
    # a DB-write failure on the toast_fired flag also can't break
    # scoring.
    try:
        score_int = int(breakdown.deal_score)
        threshold = int(os.environ.get("ALERT_SCORE_THRESHOLD", "70"))
        if score_int >= threshold:
            with get_conn() as _c:
                _row = _c.execute(
                    "SELECT toast_fired FROM listings WHERE id = ?", (sl.id,)
                ).fetchone()
            already_fired = bool(_row[0]) if _row else False
            if not already_fired:
                from deal_finder.notifications import desktop as _toast
                # Marketplace listing IDs map 1:1 to a public PDP URL.
                # If pl somehow already has the URL, prefer it; otherwise
                # construct from the listing id.
                pdp = (
                    getattr(pl, "listing_url", None)
                    or f"https://www.facebook.com/marketplace/item/{sl.id}"
                )
                # Two-line body: title (clean) on line 1, savings on
                # line 2. Reads cleanly in the Action Center. Title gets
                # truncated at 80 chars so a long product title doesn't
                # crowd out the savings line.
                title_line = (pl.title or "(untitled listing)").strip()
                if len(title_line) > 80:
                    title_line = title_line[:77].rstrip() + "…"
                summary = title_line
                if breakdown.fair_value and asking:
                    saved = max(0, breakdown.fair_value - asking)
                    if saved >= 1:
                        summary = f"{title_line}\nSave ${saved:.0f} vs eBay comps"
                _toast.fire(
                    title=f"Bullseye · score {score_int}",
                    summary=summary,
                    score=score_int,
                    listing_url=pdp,
                )
                # Mark toast_fired BEFORE returning so a re-poll doesn't
                # re-fire even if the toast itself silently failed
                # (better to under-notify than to spam). Crucially we
                # do NOT touch `notified` — that's owned by the email
                # digest pipeline.
                with get_conn() as _c:
                    with _c:
                        _c.execute(
                            "UPDATE listings SET toast_fired = 1 WHERE id = ?",
                            (sl.id,),
                        )
                logger.info(
                    "%s toast fired (score=%d, threshold=%d)",
                    sl.id, score_int, threshold,
                )
    except Exception as e:  # noqa: BLE001
        logger.debug("desktop toast skipped: %s", e)

    return "appraised"


def drain_appraisal_safety_net() -> None:
    """Stub. The appraisal worker (with the LLM fallback + safety drain)
    is deferred until step 5 of the implementation plan. For now this
    is a no-op so scheduler/main.py can still register the job."""
    return None


def send_digest_emails() -> None:
    """Instant-alert sweep — paid/trial only.

    Wired up 2026-05-07 after a user-reported "is the system emailing
    me?" question revealed this had been a stub for months. The
    underlying pipeline (alerts/digest.py + cloud/alerts.py +
    /alerts-send Edge Function with Resend) was fully built; just
    nothing in the scheduler ever called it. Result: above-threshold
    listings hit the desktop toast, but no email ever went out.

    Only paid/trial users go through this path because the cloud
    function rejects type='instant' from free-tier users (free gets
    one digest/day via send_daily_summary_emails). We short-circuit
    here so we don't even bother making the round-trip when we know
    it'll 403.

    The 60s batch-hold lives inside send_instant_for_pending() (in
    alerts/digest.py) — if the oldest pending match is younger than
    DIGEST_BATCH_HOLD_S, this returns held=True and the matches stay
    in the queue until the next 15s tick. That collapses arrival
    bursts into one email instead of N rapid-fire ones.

    Never raises — every error returns the sentinel dict from
    cloud.alerts. The scheduler logs but never crashes on email
    failures (we don't want a Resend hiccup to take down the
    coordinator).
    """
    try:
        if not license_manager.is_paid():
            return  # free tier — daily digest is the email path
    except (NotImplementedError, AttributeError):
        return
    except Exception as e:  # noqa: BLE001
        logger.debug("license check for instant alerts failed: %s", e)
        return

    try:
        from deal_finder.alerts.digest import send_instant_for_pending
        resp = send_instant_for_pending()
    except Exception as e:  # noqa: BLE001
        logger.exception("send_instant_for_pending crashed: %s", e)
        return

    # Quiet on the no-op case (held / nothing to send) — it fires
    # every 15s and would flood the log otherwise. Loud on actual
    # send/queue/error so we can debug delivery problems.
    if resp.get("sent") or resp.get("queued"):
        logger.info("instant alert: %s", resp)
        record_event(
            "alert_instant_sent",
            count=resp.get("count", 0),
            queued=bool(resp.get("queued")),
        )
    elif resp.get("error"):
        logger.warning("instant alert error: %s", resp.get("error"))
        record_event("alert_instant_error", error=str(resp.get("error")))


# How often to check for the daily-digest send window. The job runs
# hourly (DAILY_SUMMARY_INTERVAL_S=3600), but we only POST to the
# cloud when the current UTC hour matches DIGEST_SEND_HOUR_UTC. The
# cloud has its own per-day idempotency guard, so an extra hourly
# trigger inside the same UTC day is a no-op on its end too.
#
# 8am UTC was picked to roughly match "morning" for North America
# (00:00–05:00 PT/MT/CT/ET) and is what the cloud uses as its
# tomorrow-default. Override via env var if a user reports their
# digest landing at 4am local.
DIGEST_SEND_HOUR_UTC = int(os.environ.get("DIGEST_SEND_HOUR_UTC", "8"))

# Module-level guard to prevent double-sending if the hourly job
# fires twice within the same UTC hour due to clock drift / scheduler
# coalescing. The cloud also enforces uq_one_digest_per_day, so this
# is belt-and-suspenders.
_last_daily_digest_date_utc: str | None = None


def send_daily_summary_emails() -> None:
    """Daily-digest sweep — free tier only, 8am UTC.

    Wired up 2026-05-07 alongside send_digest_emails (see that
    docstring for context on why both stubs sat empty for months).

    The job is registered at 1-hour cadence by scheduler/main.py.
    On every tick we check whether (a) the current UTC hour matches
    DIGEST_SEND_HOUR_UTC and (b) we haven't already sent today.
    Only then do we collect + POST. The cloud /alerts-send Edge
    Function ALSO enforces uq_one_digest_per_day idempotently, so
    accidental double-fires can't actually deliver two emails — but
    it's wasteful to round-trip when we know we don't need to.

    Paid/trial users get instant alerts via send_digest_emails and
    are skipped here.
    """
    global _last_daily_digest_date_utc

    try:
        if license_manager.is_paid():
            return  # paid tier uses instant alerts
    except (NotImplementedError, AttributeError):
        pass
    except Exception as e:  # noqa: BLE001
        logger.debug("license check for daily digest failed: %s", e)
        # Fall through — better to attempt the send than to silently
        # block free-tier users on a license-cache hiccup.

    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    today_utc = now_utc.date().isoformat()

    if now_utc.hour != DIGEST_SEND_HOUR_UTC:
        return  # outside send window; will retry next hour

    if _last_daily_digest_date_utc == today_utc:
        return  # already sent (or attempted) today

    try:
        from deal_finder.alerts.digest import send_daily_digest
        resp = send_daily_digest()
    except Exception as e:  # noqa: BLE001
        logger.exception("send_daily_digest crashed: %s", e)
        # Mark today done so we don't retry for the rest of the
        # send window — but DO NOT mark it on a transient cloud
        # error (handled inside cloud.alerts), so a retry can
        # succeed if the cloud comes back within the hour.
        return

    if resp.get("sent") or resp.get("queued"):
        logger.info("daily digest: %s", resp)
        record_event(
            "alert_daily_sent",
            count=resp.get("count", 0),
            queued=bool(resp.get("queued")),
            date_utc=today_utc,
        )
        _last_daily_digest_date_utc = today_utc
    elif resp.get("error"):
        logger.warning("daily digest error: %s", resp.get("error"))
        record_event(
            "alert_daily_error",
            error=str(resp.get("error")),
            date_utc=today_utc,
        )
        # Don't set _last_daily_digest_date_utc on error — let the
        # next hourly tick retry, the cloud will still dedupe.
    else:
        # Empty response: no matches. Mark done so we don't retry the
        # collect every hour (cheap but pointless).
        _last_daily_digest_date_utc = today_utc


def list_active_search_ids() -> list[int]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id FROM user_searches WHERE active = 1 ORDER BY id",
        )
        return [r[0] for r in cur.fetchall()]


def pick_next_watch_to_poll() -> int | None:
    """Choose the stalest active watch to poll next.

    Uses scheduler_events as the source of truth for "when did this
    watch last poll?" so we naturally round-robin across N watches at
    the rate gate's pace. Newly-added watches (no poll event yet)
    sort first.

    SQLite has no LATERAL joins; we rewrite the personal tool's
    LATERAL subquery as a correlated subquery in the SELECT list.
    The poll's search_id is folded into the JSON `detail` blob so the
    correlated subquery looks at json_extract(detail, '$.search_id').

    Returns None if there are no active watches.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT us.id
               FROM user_searches us
               LEFT JOIN (
                   SELECT json_extract(detail, '$.search_id') AS sid,
                          MAX(created_at) AS last_polled
                   FROM scheduler_events
                   WHERE event_type = 'poll'
                   GROUP BY json_extract(detail, '$.search_id')
               ) p ON p.sid = us.id
               WHERE us.active = 1
               ORDER BY (p.last_polled IS NULL) DESC, p.last_polled ASC, us.id ASC
               LIMIT 1""",
        ).fetchone()
    return row[0] if row else None


# Default batch size — see personal tool for the full rationale.
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "4")) if os.environ.get("BATCH_SIZE") else 4


def attribute_listing(
    listing_title: str, batch: list[dict],
) -> dict | None:
    """Pick the watch in `batch` whose keyword best matches the listing.

    Strategy: lowercased, watch keyword's words must all appear (as
    substrings) in the title. Among matches, the longest keyword (most
    specific) wins. Ties broken by leftmost match position.
    """
    title_l = (listing_title or "").lower()
    matches: list[tuple[int, int, dict]] = []
    for w in batch:
        kw = (w["keyword"] or "").lower().strip()
        if not kw:
            continue
        words = kw.split()
        if not all(word in title_l for word in words):
            continue
        first_pos = title_l.find(words[0])
        matches.append((len(kw), -first_pos, w))
    if not matches:
        return None
    matches.sort(key=lambda m: (m[0], m[1]), reverse=True)
    return matches[0][2]


# --- Slow-start ramp -----------------------------------------------------
SLOW_START_MODE = os.environ.get("SLOW_START_MODE", "1") == "1"
SLOW_START_INITIAL_S = int(os.environ.get("SLOW_START_INITIAL_S", "60"))
SLOW_START_FLOOR_S = int(os.environ.get("SLOW_START_FLOOR_S", "20"))
SLOW_START_HEALTHY_PERIOD_S = int(os.environ.get("SLOW_START_HEALTHY_PERIOD_S", "300"))
SLOW_START_STEP_S = int(os.environ.get("SLOW_START_STEP_S", "5"))

_slow_start_state = {
    "min_interval_s": SLOW_START_INITIAL_S,
    "last_poll_attempt_t": 0.0,
    "last_ramp_check_t": 0.0,
}


def _no_rate_limits_in_last_period(period_s: int) -> bool:
    try:
        with get_conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) FROM scheduler_events
                    WHERE event_type='fb_rate_limit'
                      AND created_at >= datetime('now', ?)""",
                (f"-{int(period_s)} seconds",),
            ).fetchone()
            return row[0] == 0
    except Exception:  # noqa: BLE001
        return True  # fail open


def _slow_start_should_skip() -> bool:
    """When SLOW_START_MODE is on, skip ticks that arrive sooner than
    the current effective minimum interval. The minimum is also clamped
    to the licensed floor (license_manager.poll_interval_min())."""
    if not SLOW_START_MODE:
        return False
    now = time.monotonic()
    state = _slow_start_state

    if state["last_ramp_check_t"] == 0.0:
        state["last_ramp_check_t"] = now
        state["last_poll_attempt_t"] = now
        return False

    # Clamp to licensed minimum (in seconds) so a free-tier user can't
    # outpace the floor by configuring a tighter SLOW_START_FLOOR_S.
    license_floor_s = _license_min_poll_interval_s()
    effective_min = max(state["min_interval_s"], license_floor_s)

    elapsed = now - state["last_poll_attempt_t"]
    if elapsed < effective_min:
        return True

    if now - state["last_ramp_check_t"] >= SLOW_START_HEALTHY_PERIOD_S:
        state["last_ramp_check_t"] = now
        if _no_rate_limits_in_last_period(SLOW_START_HEALTHY_PERIOD_S):
            old = state["min_interval_s"]
            new = max(SLOW_START_FLOOR_S, old - SLOW_START_STEP_S)
            if new != old:
                state["min_interval_s"] = new
                logger.info(
                    "slow-start ramp: %ds clean -> effective interval %ds -> %ds",
                    SLOW_START_HEALTHY_PERIOD_S, old, new,
                )
                record_event(
                    "slow_start_ramp",
                    direction="down", from_s=old, to_s=new,
                )
        else:
            old = state["min_interval_s"]
            state["min_interval_s"] = SLOW_START_INITIAL_S
            if state["min_interval_s"] != old:
                logger.warning(
                    "slow-start reset: rate-limit in window -> interval %ds -> %ds",
                    old, state["min_interval_s"],
                )
                record_event(
                    "slow_start_ramp",
                    direction="reset",
                    from_s=old, to_s=state["min_interval_s"],
                )

    state["last_poll_attempt_t"] = now
    return False


# --- Adaptive rate-limit backoff -----------------------------------------
RATE_LIMIT_BASE_COOLDOWN_S = int(os.environ.get("RATE_LIMIT_BASE_COOLDOWN_S", "60"))
RATE_LIMIT_MAX_COOLDOWN_S = int(os.environ.get("RATE_LIMIT_MAX_COOLDOWN_S", "600"))
RATE_LIMIT_WINDOW_S = int(os.environ.get("RATE_LIMIT_WINDOW_S", "1800"))  # 30 min

_last_logged_cooldown_until: float = 0.0
_last_heartbeat_t: float = 0.0
HEARTBEAT_INTERVAL_S = 60


def coordinator_tick() -> None:
    """Pick the stalest watch and run one FB search for it.

    Three-stage gate before we actually poll:
      1. Kill-switch: if license_manager says we're below min supported
         version, skip + record an event.
      2. Cooldown gate (_should_skip_tick_for_backoff)
      3. Slow-start gate (_slow_start_should_skip)
      4. Half-open probe gate (_circuit_breaker_should_skip)

    Emits a 'coordinator_tick' event every fire so the Stats tab
    Scheduler Health panel can show a heartbeat. Without this, the
    user has no way to tell if the scheduler thread is alive or has
    crashed silently — every gate fires and emits its own event, but
    a healthy idle tick (everything fine, just nothing to do) was
    invisible.
    """
    record_event("coordinator_tick", phase="entry")
    if _kill_switch_active():
        record_event("kill_switch_skip", source="coordinator_tick")
        return
    if _should_skip_tick_for_backoff():
        return
    if _slow_start_should_skip():
        return
    if _circuit_breaker_should_skip():
        return

    sid = pick_next_watch_to_poll()
    if sid is None:
        # All watches paused / DB empty / pick query returned nothing.
        # Emit so the Stats panel can distinguish "alive but idle"
        # from "scheduler dead".
        record_event("coordinator_idle", reason="no_watch_to_poll")
        return
    try:
        poll_search(sid)
    except FacebookRateLimited as e:
        logger.warning(
            "coordinator(%s): hard-stop tripped pre-flight (~%ds remaining)",
            sid, int(e.seconds_remaining),
        )
        record_event("rate_limit_hard_stop",
                     search_id=sid,
                     remaining_s=int(e.seconds_remaining))
    except Exception as e:  # noqa: BLE001
        logger.exception("coordinator_tick(%s) crashed: %s", sid, e)


def manual_poll_watch(sid: int) -> bool:
    """Run a single watch through the polling pipeline RIGHT NOW,
    bypassing the slow-start gate.

    Why this exists (and why it's not just coordinator_tick(sid)):
    coordinator_tick() runs through every defensive gate including
    slow-start, which enforces a 60s minimum spacing between polls
    after the first one. That makes sense for the autonomous
    scheduler — bursting N requests in N seconds on a fresh boot
    can trip Facebook's rate limiter immediately. But it's wrong
    for two surfaces:

      1. The "Start your searches now" button on Home, which calls
         coordinator_tick once per active watch with 8s spacing.
         Watches 2..N hit the 60s slow-start window and silently
         skip — only the first watch actually polls. Users see the
         button "succeed" but no new finds appear.

      2. The boot-time first-poll burst (run_initial_poll_burst
         below). Without this we'd wait minutes for the round-robin
         coordinator to cycle through every watch one by one at
         60s+ intervals, which feels broken on a fresh install.

    The kill-switch, exponential-cooldown, and half-open circuit-
    breaker gates ARE still applied, because those reflect real
    Facebook protection state (we've actually been rate-limited,
    or FB is currently blocking us). Skipping those would compound
    a real block. Slow-start is the only gate we relax — it's a
    "be cautious about bursts" heuristic, not a response to an
    observed problem.

    Returns True if the watch was actually polled, False if any
    real-protection gate blocked it.
    """
    if _kill_switch_active():
        record_event("kill_switch_skip", source="manual_poll_watch", search_id=sid)
        return False
    if _should_skip_tick_for_backoff():
        # Real FB cooldown — even a manual click should respect this.
        return False
    if _circuit_breaker_should_skip():
        # FB is currently 4xx-ing us; manual override would just
        # confirm to FB that we're still here. Wait for the probe.
        return False
    try:
        poll_search(sid)
        return True
    except FacebookRateLimited as e:
        logger.warning(
            "manual_poll_watch(%s): hard-stop tripped pre-flight (~%ds remaining)",
            sid, int(e.seconds_remaining),
        )
        record_event(
            "rate_limit_hard_stop",
            search_id=sid,
            remaining_s=int(e.seconds_remaining),
            source="manual_poll_watch",
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.exception("manual_poll_watch(%s) crashed: %s", sid, e)
        return False


def run_initial_poll_burst(spacing_s: float = 8.0) -> int:
    """Poll every active watch ONCE right now, with `spacing_s` seconds
    between watches.

    Called from the scheduler boot path so a freshly-launched app
    surfaces results within ~`spacing_s * N` seconds rather than
    waiting for the round-robin coordinator to cycle through every
    watch at slow-start cadence (which is 60s+ per watch on cold
    boot — would take ~5 minutes for 5 watches).

    Spacing matches the FB scraper's default search interval so we
    stay within the per-IP rate-gate envelope. The function blocks
    its calling thread for the duration; callers should wrap in a
    daemon thread so app startup isn't held back.

    Returns the number of watches polled (or attempted) — i.e. the
    count BEFORE per-watch gates like cooldown/circuit-breaker can
    veto, which is what the UI wants to display.
    """
    sids = list_active_search_ids()
    if not sids:
        logger.info("initial poll burst: no active watches; skipping")
        record_event("initial_poll_burst", n_watches=0, status="empty")
        return 0
    logger.info(
        "initial poll burst: polling %d active watch(es) with %.1fs spacing",
        len(sids), spacing_s,
    )
    record_event("initial_poll_burst", n_watches=len(sids), status="start")
    polled = 0
    for sid in sids:
        try:
            ok = manual_poll_watch(sid)
            if ok:
                polled += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("initial poll burst: watch %s crashed: %s", sid, e)
        time.sleep(spacing_s)
    record_event(
        "initial_poll_burst",
        n_watches=len(sids),
        n_polled=polled,
        status="done",
    )
    return polled


def _compute_cooldown_remaining_s() -> int:
    """How many seconds to wait before next FB request, based on
    recent rate-limit history.

    Returns 0 when we're clear to proceed.

    SQLite version: EXTRACT(EPOCH FROM ...) becomes Python-side delta
    on ISO-string timestamps. We parse `created_at` with
    datetime.fromisoformat then subtract.
    """
    from datetime import datetime, timezone
    try:
        with get_conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS n,
                          MAX(created_at) AS max_ts
                   FROM scheduler_events
                   WHERE event_type = 'fb_rate_limit'
                     AND created_at >= datetime('now', ?)""",
                (f"-{int(RATE_LIMIT_WINDOW_S)} seconds",),
            ).fetchone()
    except Exception:  # noqa: BLE001
        return 0  # fail open

    n = row["n"] if row else 0
    max_ts = row["max_ts"] if row else None
    if not n or not max_ts:
        return 0

    try:
        # SQLite's CURRENT_TIMESTAMP yields "YYYY-MM-DD HH:MM:SS" UTC.
        last_dt = datetime.fromisoformat(str(max_ts))
    except ValueError:
        return 0
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    secs_since_last = (datetime.now(timezone.utc) - last_dt).total_seconds()
    last_epoch = last_dt.timestamp()

    nominal = min(
        RATE_LIMIT_BASE_COOLDOWN_S * (2 ** min(int(n) - 1, 4)),
        RATE_LIMIT_MAX_COOLDOWN_S,
    )
    import random as _r
    jitter_seed = int(last_epoch * 1000) ^ int(n) << 8
    rng = _r.Random(jitter_seed)
    jittered = nominal * rng.uniform(0.85, 1.15)
    remaining = jittered - float(secs_since_last)
    return max(0, int(remaining))


def _circuit_breaker_should_skip() -> bool:
    """Half-open probe gate. See personal tool for the full rationale.

    SQLite version: EXTRACT(EPOCH FROM (NOW() - X)) -> Python delta on
    ISO timestamps. json_extract(detail, '$.result') replaces PG's
    `detail->>'result'` operator.
    """
    from datetime import datetime, timezone
    HALF_OPEN_LOOKBACK_S = 600
    PROBE_COOLDOWN_S = 90

    def _secs_since(ts_str: str | None) -> float | None:
        if not ts_str:
            return None
        try:
            dt = datetime.fromisoformat(str(ts_str))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()

    try:
        with get_conn() as conn:
            row = conn.execute(
                """SELECT MAX(created_at) AS max_ts
                   FROM scheduler_events
                   WHERE event_type = 'fb_rate_limit'
                     AND created_at >= datetime('now', ?)""",
                (f"-{int(HALF_OPEN_LOOKBACK_S)} seconds",),
            ).fetchone()
            secs_since_last_rl = _secs_since(row["max_ts"] if row else None)

            row = conn.execute(
                """SELECT created_at,
                          json_extract(detail, '$.result') AS result
                   FROM scheduler_events
                   WHERE event_type = 'fb_probe'
                   ORDER BY created_at DESC
                   LIMIT 1""",
            ).fetchone()
            if row:
                secs = _secs_since(row["created_at"])
                if secs is not None and secs <= PROBE_COOLDOWN_S:
                    secs_since_probe, last_probe_result = secs, row["result"]
                else:
                    secs_since_probe, last_probe_result = None, None
            else:
                secs_since_probe, last_probe_result = None, None
    except Exception as e:  # noqa: BLE001
        logger.warning("circuit breaker query failed (fail open): %s", e)
        return False

    if secs_since_last_rl is None:
        return False

    if secs_since_probe is not None:
        if last_probe_result == "ok":
            return False
        return True

    try:
        result = get_search_client().probe_marketplace_root()
    except Exception as e:  # noqa: BLE001
        logger.warning("probe raised (treating as 'down'): %s", e)
        result = "down"

    record_event("fb_probe", result=result)
    logger.info("fb-probe (half-open) -> %s", result)

    if result == "ok":
        return False
    if result == "blocked":
        record_event(
            "fb_rate_limit",
            code=1675004,
            message="probe-detected block (no quota burned)",
            severity="probe",
        )
        return True
    return True


def _should_skip_tick_for_backoff() -> bool:
    """Skip this tick if we're inside the rate-limit cooldown window."""
    global _last_logged_cooldown_until, _last_heartbeat_t
    remaining = _compute_cooldown_remaining_s()
    if remaining <= 0:
        return False

    now = time.time()
    cooldown_until = now + remaining

    if cooldown_until > _last_logged_cooldown_until + 5:
        logger.warning(
            "coordinator: rate-limit cooldown active — skipping ticks for ~%ds",
            remaining,
        )
        record_event(
            "rate_limit_backoff",
            cooldown_remaining_s=remaining,
        )
        _last_logged_cooldown_until = cooldown_until
        _last_heartbeat_t = now
    elif (now - _last_heartbeat_t) > HEARTBEAT_INTERVAL_S:
        record_event(
            "scheduler_heartbeat",
            state="cooldown",
            cooldown_remaining_s=remaining,
        )
        _last_heartbeat_t = now
        logger.debug(
            "coordinator: heartbeat (cooldown ~%ds remaining)", remaining,
        )
    else:
        logger.debug(
            "coordinator: still in cooldown (~%ds remaining)", remaining,
        )
    return True
