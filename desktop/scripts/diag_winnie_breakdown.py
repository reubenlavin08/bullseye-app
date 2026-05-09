"""Inspect the score breakdown for the user's reported "Lego Winnie
the Pooh" listing to see why HETEROGENEOUS_COMPS_SCORE_CAP didn't fire."""
import json
import os
import sqlite3

db = os.path.expanduser("~/.bullseye/bullseye.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

# Find the Winnie the Pooh listing
rows = conn.execute(
    """SELECT id, title, deal_score, fair_value, price,
              comp_search_term, comp_source, comp_sample_size, comp_median,
              appraisal_breakdown
       FROM listings
       WHERE LOWER(title) LIKE '%winnie%' OR LOWER(comp_search_term) LIKE '%winnie%'
       ORDER BY scraped_at DESC LIMIT 5"""
).fetchall()

if not rows:
    print("no Winnie the Pooh listings found in local DB")
else:
    for r in rows:
        print(f"id={r['id']}  score={r['deal_score']}  ask={r['price']}  fair={r['fair_value']}")
        print(f"  title: {r['title']!r}")
        print(f"  comp_search_term: {r['comp_search_term']!r}")
        print(f"  comp_sample_size: {r['comp_sample_size']}  comp_median: {r['comp_median']}")
        bd = json.loads(r["appraisal_breakdown"] or "{}")
        # Show the iqr / cap-related fields
        keys_of_interest = [
            "iqr", "iqr_ratio", "iqr_to_median_ratio",
            "trimmed_median", "median", "p25", "p75", "q1", "q3",
            "min", "minimum", "max", "maximum",
            "data_quality_poor", "cap_reason", "honesty_cap_reason",
            "raw_score", "raw_score_pre_condition", "score",
            "percentile_rank", "confidence_label", "confidence_pm",
        ]
        for k in keys_of_interest:
            if k in bd:
                print(f"  bd.{k}: {bd[k]}")
        # Also check the comp section if nested
        if "comp" in bd and isinstance(bd["comp"], dict):
            print("  bd.comp:")
            for k, v in bd["comp"].items():
                print(f"    {k}: {v}")
        print()
