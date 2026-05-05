"""Digest orchestrator — collects pending matches from SQLite and
hands them to the cloud `/alerts-send` Edge Function.

Tier behavior (decided server-side, but we send a hint):
    free  -> POST type='digest', once a day at 8am local
    paid  -> POST type='instant', with a 60s batching hold to
             collapse arrival bursts into one email

This module is the SQLite-side port of the personal tool's
`deal_finder/src/deal_finder/alerts/digest.py::collect_pending_for_email`,
with three changes from the original:

    1. Connection is sqlite3 (not psycopg2). `?` placeholders, INTEGER
       0/1 booleans, ISO-8601 strings for timestamps.
    2. The deliverable side calls `cloud.alerts.send_digest/instant`
       instead of SMTP.
    3. Single-user model — there is one local user, so we don't iterate
       subscribers. We collect across every active user_searches row.

We still apply the watch's price + radius bounds as a final gate so old
listings ingested before a tightening don't sneak through. Suppressed
listings get marked notified with an annotation so they're never
reconsidered.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..cloud import alerts as cloud_alerts
from ..db.connection import get_conn
from ..db.geo import geocode_city, haversine_km

logger = logging.getLogger(__name__)


# How long to hold pending matches before sending, to give multiple
# above-threshold listings a chance to batch into one email. Set to
# DIGEST_BATCH_HOLD_S=0 to disable batching for tests / debug.
DIGEST_BATCH_HOLD_S = int(os.environ.get("DIGEST_BATCH_HOLD_S", "60"))


@dataclass
class DigestMatch:
    listing_id: str
    title: str
    asking_price: float | None
    fair_value: float | None
    deal_score: int
    confidence_label: str | None
    confidence_pm: int | None
    listing_url: str
    photo_url: str | None
    seller_location: str | None
    listed_at: str | None    # ISO-8601 (or None)
    keyword: str

    def to_payload(self) -> dict[str, Any]:
        """Cloud-bound JSON shape — matches DigestMatch in resend.ts."""
        return asdict(self)


# --- Internal helpers ------------------------------------------------------

def _passes_watch_bounds(
    *,
    price: float | None,
    seller_location: str | None,
    home_lat: float | None,
    home_lng: float | None,
    radius_km: int | None,
    price_min: int | None,
    price_max: int | None,
    detail_lat: float | None = None,
    detail_lng: float | None = None,
) -> tuple[bool, str | None]:
    """Reapply the watch's price + radius bounds. Returns (kept, reason)."""
    if price is not None:
        if price_min is not None and price < price_min:
            return False, f"price ${price:.0f} < min ${price_min}"
        if price_max is not None and price > price_max:
            return False, f"price ${price:.0f} > max ${price_max}"
    if not radius_km or home_lat is None or home_lng is None:
        return True, None
    if detail_lat is not None and detail_lng is not None:
        dist = haversine_km(home_lat, home_lng, detail_lat, detail_lng)
        if dist > float(radius_km):
            return False, f"{dist:.0f} km > {radius_km} km radius (precise)"
        return True, None
    if not seller_location:
        return True, None
    coords = geocode_city(seller_location)
    if coords is None:
        return True, None  # fail open on geocode error
    dist = haversine_km(home_lat, home_lng, coords[0], coords[1])
    if dist > float(radius_km):
        return False, f"{dist:.0f} km > {radius_km} km radius"
    return True, None


def _suppress_listings(conn, listing_ids: list[str], reason: str) -> None:
    """Mark listings notified=1 with an annotation, never re-considered."""
    if not listing_ids:
        return
    placeholders = ",".join("?" * len(listing_ids))
    annot = f" [suppressed: {reason}]"
    with conn:
        conn.execute(
            f"""UPDATE listings SET
                  notified = 1,
                  notified_at = CURRENT_TIMESTAMP,
                  appraisal_note = COALESCE(appraisal_note, '') || ?
                WHERE id IN ({placeholders})""",
            (annot, *listing_ids),
        )


def _mark_notified(conn, listing_ids: list[str]) -> None:
    if not listing_ids:
        return
    placeholders = ",".join("?" * len(listing_ids))
    with conn:
        conn.execute(
            f"""UPDATE listings SET
                  notified = 1,
                  notified_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})""",
            tuple(listing_ids),
        )


def _appraised_at_dt(s: str | None) -> datetime | None:
    """SQLite stores TEXT timestamps. CURRENT_TIMESTAMP writes
    'YYYY-MM-DD HH:MM:SS' (UTC, no tz suffix); ISO writes from Python
    code may include 'T' and a tz. Parse both."""
    if not s:
        return None
    try:
        # Try ISO-8601 first (Python-side writes).
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --- Main collector --------------------------------------------------------

def collect_pending_for_email(
    *,
    score_threshold: int = 70,
    apply_batch_hold: bool = True,
) -> list[DigestMatch]:
    """All not-yet-notified, score-passing listings across every active
    watch. Applies bounds recheck + the optional batch hold.

    Returns an EMPTY list if the batch hold is active (i.e. the oldest
    eligible match is younger than DIGEST_BATCH_HOLD_S). Caller should
    treat that as "wait until next tick" — does NOT consume the matches.

    Set apply_batch_hold=False for the daily-digest job (we always want
    to send at 8am no matter how recent the matches are).
    """
    matches: list[DigestMatch] = []
    suppress: dict[str, list[str]] = {}
    appraised_ats: list[datetime] = []

    with get_conn() as conn:
        cur = conn.execute(
            """SELECT l.id, l.title, l.price, l.fair_value,
                      l.deal_score, l.appraisal_breakdown,
                      l.listing_url, l.photo_url, l.seller_location,
                      l.listed_at,
                      us.keyword,
                      us.latitude, us.longitude, us.radius_km,
                      us.price_min, us.price_max,
                      l.appraised_at,
                      l.detail_latitude, l.detail_longitude
               FROM listings l
               JOIN user_searches us ON us.id = l.search_id
               WHERE us.active = 1
                 AND l.appraised = 1
                 AND l.rejected = 0
                 AND l.notified = 0
                 AND l.deal_score IS NOT NULL
                 AND l.deal_score >= ?
               ORDER BY l.deal_score DESC, l.scraped_at DESC""",
            (score_threshold,),
        )
        rows = cur.fetchall()

        # Pull the (single-user) home coords once for the bounds gate.
        home = conn.execute(
            "SELECT home_latitude, home_longitude FROM user_settings "
            "WHERE user_id = 1"
        ).fetchone()
        home_lat = home["home_latitude"] if home else None
        home_lng = home["home_longitude"] if home else None

        for r in rows:
            # appraisal_breakdown is JSON TEXT in SQLite.
            bd: dict = {}
            if r["appraisal_breakdown"]:
                import json
                try:
                    bd = json.loads(r["appraisal_breakdown"])
                except (json.JSONDecodeError, TypeError):
                    bd = {}

            price = float(r["price"]) if r["price"] is not None else None
            d_lat = (
                float(r["detail_latitude"])
                if r["detail_latitude"] is not None else None
            )
            d_lng = (
                float(r["detail_longitude"])
                if r["detail_longitude"] is not None else None
            )

            ok, reason = _passes_watch_bounds(
                price=price,
                seller_location=r["seller_location"],
                home_lat=home_lat,
                home_lng=home_lng,
                radius_km=r["radius_km"],
                price_min=r["price_min"],
                price_max=r["price_max"],
                detail_lat=d_lat,
                detail_lng=d_lng,
            )
            if not ok:
                suppress.setdefault(reason or "out of bounds", []).append(r["id"])
                continue

            matches.append(DigestMatch(
                listing_id=r["id"],
                title=r["title"] or "",
                asking_price=price,
                fair_value=(
                    float(r["fair_value"])
                    if r["fair_value"] is not None else None
                ),
                deal_score=int(r["deal_score"]),
                confidence_label=bd.get("confidence_label"),
                confidence_pm=bd.get("confidence_pm"),
                listing_url=r["listing_url"] or "",
                photo_url=r["photo_url"],
                seller_location=r["seller_location"],
                listed_at=r["listed_at"],
                keyword=r["keyword"],
            ))

            ad = _appraised_at_dt(r["appraised_at"])
            if ad is not None:
                appraised_ats.append(ad)

        for reason, ids in suppress.items():
            _suppress_listings(conn, ids, reason)
            logger.info(
                "suppressed %d out-of-bounds listing(s): %s",
                len(ids), reason,
            )

    # Batching hold: if the OLDEST eligible match is younger than
    # DIGEST_BATCH_HOLD_S, hold this tick.
    if (apply_batch_hold and matches and DIGEST_BATCH_HOLD_S > 0
            and appraised_ats):
        oldest = min(appraised_ats)
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - oldest).total_seconds()
        if age_s < DIGEST_BATCH_HOLD_S:
            logger.debug(
                "batch hold: %d match(es), oldest %ds < %ds — waiting",
                len(matches), int(age_s), DIGEST_BATCH_HOLD_S,
            )
            return []

    return matches


# --- Send paths ------------------------------------------------------------

def send_instant_for_pending() -> dict[str, Any]:
    """Paid-tier path: collect pending high-score listings, apply 60s
    batching hold, post to /alerts-send as type='instant'.

    On a successful cloud send, marks the included listings notified.
    On hold (matches exist but too young), returns
    `{sent: False, queued: False, count: 0, held: True}` and leaves
    them alone for the next tick.
    """
    matches = collect_pending_for_email(apply_batch_hold=True)
    if not matches:
        return {"sent": False, "queued": False, "count": 0, "held": True}
    payload = [m.to_payload() for m in matches]
    resp = cloud_alerts.send_instant(payload)
    if resp.get("sent") or resp.get("queued"):
        with get_conn() as conn:
            _mark_notified(conn, [m.listing_id for m in matches])
    return resp


def send_daily_digest() -> dict[str, Any]:
    """Free-tier path: collect today's pending matches and post as
    type='digest'. Skips the 60s batch hold (we run this once a day,
    we want everything in one email).

    On cloud success (sent or queued), marks every match notified so
    they don't appear in the next digest.
    """
    matches = collect_pending_for_email(apply_batch_hold=False)
    if not matches:
        return {"sent": False, "queued": False, "count": 0}
    payload = [m.to_payload() for m in matches]
    resp = cloud_alerts.send_digest(payload)
    if resp.get("sent") or resp.get("queued"):
        with get_conn() as conn:
            _mark_notified(conn, [m.listing_id for m in matches])
    return resp
