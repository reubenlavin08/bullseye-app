"""Flask app — the UI surface served at localhost:RANDOM_PORT.

PHASE 2 PORT: most of the existing routes from
`../../../../../deal_finder/webapp/app.py` carry over with these changes:

    - `psycopg2.connect` -> `db.connection.get_conn` (SQLite)
    - `get_comps()` -> `cloud.comps.get_comps()`
    - SMTP send paths -> `cloud.alerts.send_*()`
    - Add login/logout/upgrade routes (see auth_routes.py)
    - Apply license gates:
        - POST /api/watches: deny if count >= license_manager.watches_limit()
        - GET  /api/dashboard/breakdown/<id>: deny if not is_paid()
        - GET  /api/dashboard/per-watch: deny if not is_paid()

ROUTES (final list):
    /                           front page (manage watches + test appraiser)
    /dashboard                  live observability — paid only
    /upgrade                    pricing + Stripe Checkout button
    /login                      Google OAuth entry
    /logout                     clear keyring tokens
    /settings                   tier display, telemetry opt-out, account delete

    /api/watches                CRUD watches
    /api/searches/bulk          bulk add (existing)
    /api/dashboard/summary      live stats (free + paid)
    /api/dashboard/appraisal-feed
    /api/dashboard/breakdown/<id>     PAID
    /api/dashboard/per-watch          PAID
    /api/dashboard/score-histogram    PAID
    /api/comps                  read-only comp viewer
    /api/appraise               Test-Appraiser (free, public, the
                                acquisition surface)
    /api/account/delete         POST -> calls cloud /account-delete
    /api/account/export         GET  -> calls cloud /account-export
"""
from __future__ import annotations

# from flask import Flask
# app = Flask(__name__, static_folder='static', template_folder='templates')
# app.register_blueprint(auth_routes.bp)
# ... etc.


# Placeholder so `python -m webapp.app` doesn't crash with import errors
# during scaffolding-phase manual testing.
def run(*, port: int = 5000) -> None:
    raise NotImplementedError("Phase 2 — port routes from deal_finder/webapp/app.py.")
