"""Listings table data-access layer.

Persists the pipeline's `ProcessedListing` rows into SQLite, supports
fast dedup checks ("have we seen this ID?"), and provides the queries
the appraisal worker + alert layer will use.

All functions take an explicit sqlite3 connection so callers can run
multiple ops in one transaction. Convenience wrappers that open their
own connection live at the bottom of the module.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from ..scraper.pipeline import ProcessedListing
from .connection import get_conn

logger = logging.getLogger(__name__)


# --- Existence checks -----------------------------------------------------

def existing_ids(conn, candidate_ids: Iterable[str]) -> set[str]:
    """Return the subset of `candidate_ids` already present in `listings`.

    Used by the pipeline before doing any expensive per-listing work
    (description fetch, comp lookup, LLM scoring). One query, indexed
    primary-key lookup, fast even at 100k+ rows.

    SQLite has no `= ANY(?)` for arrays; we expand to an IN clause with
    one placeholder per id. SQLite's parameter limit defaults to 999
    (raised to ~32k in modern builds) which is fine — we batch chunks
    if a caller ever throws more than 900 candidates at us.
    """
    ids = list({str(i) for i in candidate_ids if i})
    if not ids:
        return set()

    found: set[str] = set()
    CHUNK = 900
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"SELECT id FROM listings WHERE id IN ({placeholders})",
            chunk,
        )
        for row in cur.fetchall():
            found.add(row[0])
    return found


# --- Inserts / upserts ----------------------------------------------------

# Columns we write on insert. Anything appraisal/comp/notification-related
# is filled in later by other workers, so we let those default to NULL.
_INSERT_COLS = (
    "id", "search_id", "title", "price", "raw_price",
    "price_extracted_from_description", "previous_price", "is_pending",
    "photo_url", "seller_name", "seller_location", "seller_type",
    "description", "listing_url", "category_id",
    "listed_at", "scraped_at",
    "detail_source", "rejected", "rejection_reason",
    "detail_latitude", "detail_longitude",
)


def upsert_processed(
    conn,
    pl: ProcessedListing,
    *,
    search_id: int | None = None,
) -> bool:
    """Insert a ProcessedListing row, or update it if the ID already
    exists. Returns True if this was a new insert, False if it was an
    update (caller can use the bool to decide whether to enqueue
    appraisal).

    Uses `INSERT ... ON CONFLICT (id) DO UPDATE` so we never lose history
    on a rescrape.

    SQLite has no `xmax` system column, so we detect the insert-vs-update
    distinction with a SELECT-before-write — fast (PK lookup) and
    correct.
    """
    listed_at = (
        datetime.fromtimestamp(pl.listed_at_unix, tz=timezone.utc).isoformat()
        if pl.listed_at_unix else None
    )
    scraped_at = datetime.now(timezone.utc).isoformat()
    values = {
        "id": pl.id,
        "search_id": search_id,
        "title": pl.title,
        "price": pl.resolved_price,
        "raw_price": pl.raw_price,
        # SQLite has no BOOLEAN — stored as INTEGER 0/1.
        "price_extracted_from_description": int(bool(pl.price_extracted_from_description)),
        "previous_price": pl.previous_price,
        "is_pending": int(bool(pl.is_pending)),
        "photo_url": pl.photo_url,
        "seller_name": pl.seller_name,
        "seller_location": pl.seller_location,
        "seller_type": pl.seller_type,
        "description": pl.description,
        "listing_url": pl.listing_url,
        "category_id": getattr(pl, "category_id", None),
        "listed_at": listed_at,
        "scraped_at": scraped_at,
        "detail_source": pl.detail_source,
        "rejected": int(bool(pl.rejected)),
        "rejection_reason": pl.rejection_reason,
        "detail_latitude": pl.detail_latitude,
        "detail_longitude": pl.detail_longitude,
    }

    # Was-this-an-insert: PK existence check before the upsert.
    existed_row = conn.execute(
        "SELECT 1 FROM listings WHERE id = ?", (pl.id,),
    ).fetchone()
    was_insert = existed_row is None

    cols = ", ".join(_INSERT_COLS)
    placeholders = ", ".join(":" + c for c in _INSERT_COLS)
    # Only update fields that can change between scrapes. Don't touch
    # appraisal/comp/notification fields — those are owned by other
    # workers.
    update_cols = [
        c for c in _INSERT_COLS
        if c not in ("id", "search_id", "scraped_at")
    ]
    update_set = ", ".join(f"{c} = excluded.{c}" for c in update_cols)

    sql = (
        f"INSERT INTO listings ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {update_set}, "
        f"scraped_at = excluded.scraped_at"
    )

    conn.execute(sql, values)
    return was_insert


def upsert_many(
    conn,
    listings: Iterable[ProcessedListing],
    *,
    search_id: int | None = None,
) -> tuple[int, int]:
    """Upsert a batch. Returns (n_inserted, n_updated)."""
    inserted = 0
    updated = 0
    for pl in listings:
        if upsert_processed(conn, pl, search_id=search_id):
            inserted += 1
        else:
            updated += 1
    return inserted, updated


# --- Read queries (used by appraisal + alert workers) --------------------

def fetch_unappraised(conn, limit: int = 50) -> list:
    """Pull the next batch of listings the LLM should score.

    Excludes rejected rows (those skip appraisal entirely) and rows that
    have already been appraised. Oldest-first so backlog drains in order.
    Returns sqlite3.Row objects (dict-style access by column name).
    """
    cur = conn.execute(
        """SELECT * FROM listings
           WHERE appraised = 0 AND rejected = 0
           ORDER BY scraped_at ASC
           LIMIT ?""",
        (limit,),
    )
    return list(cur.fetchall())


def fetch_alertable(
    conn, *, score_threshold: int = 70, limit: int = 25,
) -> list:
    """High-score listings the alert layer hasn't notified on yet."""
    cur = conn.execute(
        """SELECT * FROM listings
           WHERE appraised = 1
             AND rejected = 0
             AND notified = 0
             AND deal_score >= ?
           ORDER BY deal_score DESC
           LIMIT ?""",
        (score_threshold, limit),
    )
    return list(cur.fetchall())


# --- Write-back from later phases ----------------------------------------

def update_appraisal(
    conn,
    listing_id: str,
    *,
    deal_score: int,
    fair_value: float | None,
    appraisal_note: str | None,
    appraisal_model: str | None,
    breakdown=None,
) -> None:
    """Persist an appraisal result. `breakdown` is an optional
    `appraisal.formula.ScoreBreakdown` whose dataclass fields go into
    the `appraisal_breakdown` TEXT column (JSON-encoded) for full
    reproducibility.

    Note: the SQLite migration's `listings` table doesn't currently have
    an `appraisal_model` column — we store it inside the appraisal_note
    or breakdown JSON when needed. Kept in the signature for source
    compatibility with the personal tool.
    """
    import json
    from dataclasses import asdict, is_dataclass

    breakdown_json = None
    if breakdown is not None:
        if is_dataclass(breakdown):
            breakdown_json = json.dumps(asdict(breakdown))
        elif isinstance(breakdown, dict):
            breakdown_json = json.dumps(breakdown)

    conn.execute(
        """UPDATE listings SET
              deal_score = ?,
              fair_value = ?,
              appraisal_note = ?,
              appraisal_breakdown = ?,
              appraised = 1,
              appraised_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (deal_score, fair_value, appraisal_note,
         breakdown_json, listing_id),
    )


def update_comps_resolution(
    conn,
    listing_id: str,
    *,
    search_term: str,
    source: str,
    median: float | None,
    mean: float | None,
    minimum: float | None,
    maximum: float | None,
    sample_size: int,
) -> None:
    """Store the comp-fetch outcome on the listing row.

    Note: the SQLite schema doesn't currently track comp_mean / comp_min /
    comp_max / comps_resolved_at separately (those columns aren't in
    migration 001). We persist what's available; mean/min/max are
    accepted in the signature for symmetry but only the columns the
    schema knows about get written.
    """
    conn.execute(
        """UPDATE listings SET
              comp_search_term = ?,
              comp_source = ?,
              comp_median = ?,
              comp_sample_size = ?
           WHERE id = ?""",
        (search_term, source, median, sample_size, listing_id),
    )


def mark_notified(conn, listing_id: str) -> None:
    conn.execute(
        """UPDATE listings SET
              notified = 1,
              notified_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (listing_id,),
    )


# --- Convenience (own-connection) wrappers --------------------------------

def filter_new_ids(candidate_ids: Iterable[str]) -> list[str]:
    """Return only IDs not yet in the listings table. Opens its own conn."""
    candidates = [str(i) for i in candidate_ids if i]
    if not candidates:
        return []
    with get_conn() as conn:
        seen = existing_ids(conn, candidates)
    return [i for i in candidates if i not in seen]
