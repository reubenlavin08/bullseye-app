"""Scheduler package — APScheduler-driven polling loop.

PHASE 2 PORT: copy from `../../../../../deal_finder/src/deal_finder/scheduler/`:
    - main.py          (APScheduler bootstrap, job registration)
    - jobs.py          (poll_search, poll_batch, _process_new_listing)
    - coordinator.py   (rate-gate, slow-start, half-open breaker)

Changes needed during port:
    - psycopg2 -> sqlite3 in any direct DB calls
    - get_comps() now calls cloud/comps.py (no local eBay client)
    - alerts.send_pending_digests() now calls cloud/alerts.py
    - poll interval is clamped by license_manager.poll_interval_min()
    - Refuses to run when license_manager.is_kill_switched() is True
    - Refuses to run when state.is_paused (tray pause toggle)
"""
