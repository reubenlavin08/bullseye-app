"""Manual diagnostic for the /api/comps endpoint.

Simulates what the new endpoint logic does (read from
comps_local_cache, unpack raw_comps_json) against the live
SQLite DB at ~/.bullseye/bullseye.db. Prints whether comps
would be returned for the most recently appraised listing's
search term.

Run with the venv's python:
    python desktop/scripts/diag_api_comps.py
"""
import json
import os
import sqlite3
import statistics
import time
from datetime import datetime, timezone


def main() -> None:
    db = os.path.expanduser("~/.bullseye/bullseye.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    listing = conn.execute(
        """SELECT id, title, comp_search_term, comp_source FROM listings
           WHERE comp_search_term IS NOT NULL AND deal_score IS NOT NULL
           ORDER BY scraped_at DESC LIMIT 1"""
    ).fetchone()
    if not listing:
        print("no scored listings to test against")
        return
    print(f"Testing against listing: id={listing['id']} title={listing['title'][:40]!r}")
    print(f"  comp_search_term={listing['comp_search_term']!r}")
    print()

    term = listing["comp_search_term"]
    region = "EBAY-ENCA"
    ttl_seconds = 12 * 3600

    row = conn.execute(
        """SELECT raw_comps_json, fetched_at
           FROM comps_local_cache
           WHERE search_term = ? AND region = ?""",
        (term, region),
    ).fetchone()

    if not row:
        print("RESULT: empty (no comps_local_cache row for term+region)")
        return

    fetched_at = float(row["fetched_at"])
    age = time.time() - fetched_at
    print(f"  cache row found, age={age:.0f}s, TTL={ttl_seconds}s")
    if age > ttl_seconds:
        print("RESULT: empty (TTL expired)")
        return

    raw = json.loads(row["raw_comps_json"] or "[]")
    rows = []
    fetched_iso = datetime.fromtimestamp(fetched_at, tz=timezone.utc).isoformat()
    for item in raw:
        price = item.get("price")
        rows.append({
            "price": float(price) if price is not None else None,
            "title": item.get("title"),
            "listing_url": item.get("listing_url"),
            "location": item.get("location"),
            "fetched_at": fetched_iso,
        })
    rows.sort(key=lambda r: (r["price"] is None, r["price"] or 0))
    prices = [r["price"] for r in rows if r["price"] is not None]
    if not prices:
        print(f"RESULT: rows={len(rows)} but no prices")
        return

    print("RESULT: success")
    print(f"  sample_size: {len(prices)}")
    print(f"  median: {statistics.median(prices):.2f}")
    print(f"  min:    {min(prices):.2f}")
    print(f"  max:    {max(prices):.2f}")
    print(f"  rows[0]:  title={rows[0]['title'][:50]!r} price={rows[0]['price']}")
    print(f"  rows[-1]: title={rows[-1]['title'][:50]!r} price={rows[-1]['price']}")


if __name__ == "__main__":
    main()
