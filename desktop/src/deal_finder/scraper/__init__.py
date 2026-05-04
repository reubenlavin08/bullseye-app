"""Facebook Marketplace scraper.

PHASE 2 PORT: copy from `../../../../../deal_finder/src/deal_finder/scraper/`:
    - facebook.py         (search GraphQL + global rate-limit fence)
    - facebook_detail.py  (PDP + HTML fallback; precise coords extraction)
    - pipeline.py         (ProcessedListing dataclass + _combine)
    - price_extraction.py
    - rejection.py        (title/description regex filters)

This stays mostly identical between personal tool and product. The only
change is the rate-gate's `record_event` calls now write to the local
SQLite scheduler_events instead of Postgres.

NOT ported: `ebay.py` from the personal tool — comps come via cloud now.
"""
