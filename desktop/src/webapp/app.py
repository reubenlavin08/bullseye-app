"""Flask app — the UI surface served at localhost:RANDOM_PORT.

Ported from the personal tool's `deal_finder/webapp/app.py`. Translation
notes:

    psycopg2 -> sqlite3 (via deal_finder.db.connection.get_conn).
        - %s placeholders -> ?, %(name)s -> :name
        - NOW() / CURRENT_TIMESTAMP, INTERVAL '24 hours' -> datetime('now', '-24 hours')
        - EXTRACT(EPOCH FROM ...) computed Python-side after fetch
        - JSONB cols are TEXT in SQLite; we json.loads/json.dumps inline
        - COUNT(*) FILTER (WHERE x) -> SUM(CASE WHEN x THEN 1 ELSE 0 END)

    Direct comp/scraper calls -> cloud client. The Test-Appraiser endpoint
    talks to `deal_finder.cloud.comps.get_comps` instead of hitting eBay
    directly. SMTP sends are stubbed with TODO comments — they're rewired
    in step 7.

License gates (see BULLSEYE.md):
    - POST /api/watches            403 once free user hits watches_limit
    - GET  /dashboard              redirect to /upgrade if not paid
    - GET  /api/dashboard/breakdown/<id>     403 if not paid
    - GET  /api/dashboard/per-watch          403 if not paid
    - GET  /api/dashboard/score-histogram    403 if not paid

Auth gates:
    - HTML pages that need login redirect to /login
    - /api/* that need login return 401
    - / and /login itself are public; / shows a "Sign in" pill instead
      of the manage panel when logged out

Kill switch:
    - if license_manager.is_kill_switched(): every route returns 503
      with a "please update Bullseye" body.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
)

from deal_finder.auth import token_store
from deal_finder.cloud.comps import get_comps
from deal_finder.db.connection import get_conn
from deal_finder.license.manager import license_manager

from . import auth_routes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App + blueprint setup
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent

app = Flask(
    __name__,
    static_folder=str(_HERE / "static"),
    template_folder=str(_HERE / "templates"),
)
app.register_blueprint(auth_routes.bp)


# Geographic anchor used when the user hasn't set a home location yet.
DEFAULT_LAT = 49.2827
DEFAULT_LNG = -123.1207
DEFAULT_RADIUS_KM = 40


# ---------------------------------------------------------------------------
# Decorators: kill-switch, login-required, paid-only
# ---------------------------------------------------------------------------

def _kill_switch_response():
    """Build the 503 'please update' payload. Caller decides JSON vs HTML."""
    return (
        "Please update Bullseye before it can run. Reinstall the latest "
        "version - your saved watches will carry over.",
        503,
    )


@app.before_request
def _kill_switch_gate():
    """If the cloud says our version is too old, every route stops."""
    # Static files + the auth surface should still work so the user has
    # a path back: they need to be able to log out (clear bad token) or
    # at least see the page.
    p = request.path or ""
    if p.startswith("/static/") or p in ("/logout", "/login"):
        return None
    if license_manager.is_kill_switched():
        if p.startswith("/api/"):
            return jsonify({
                "ok": False,
                "error": "kill_switched",
                "message": _kill_switch_response()[0],
            }), 503
        return _kill_switch_response()
    return None


def login_required(view):
    """HTML routes: redirect to /login when no token in keyring."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not token_store.is_logged_in():
            return redirect("/login")
        return view(*args, **kwargs)
    return wrapper


def login_required_api(view):
    """API routes: 401 JSON when no token in keyring."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not token_store.is_logged_in():
            return jsonify({"ok": False, "error": "login_required"}), 401
        return view(*args, **kwargs)
    return wrapper


def paid_only_api(view):
    """API routes that require a paid (or trial) tier."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not token_store.is_logged_in():
            return jsonify({"ok": False, "error": "login_required"}), 401
        if not license_manager.is_paid():
            return jsonify({
                "ok": False,
                "error": "upgrade_required",
                "message": "This feature is part of Bullseye Pro.",
            }), 403
        return view(*args, **kwargs)
    return wrapper


@app.after_request
def _no_cache_for_live_endpoints(resp):
    """Stop the browser caching live JSON. Static assets keep defaults."""
    if (request.path or "").startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# JSON helpers — SQLite stores JSON as TEXT, so wrap parse/dump
# ---------------------------------------------------------------------------

def _json_loads(raw):
    """Decode a JSON column that may be NULL, str (already parsed once),
    or already-decoded dict/list (depending on driver). Returns None on
    bad input rather than raising — callers don't want to 500 on a bad
    detail blob."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _iso(value):
    """Coerce a SQLite TEXT timestamp to ISO 8601, returning the string
    untouched if it already looks ISO-shaped. None passes through."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_ts(value) -> datetime | None:
    """Best-effort SQLite TEXT timestamp -> datetime. Used for math
    that the personal tool did inside SQL via EXTRACT(EPOCH FROM ...).

    SQLite's CURRENT_TIMESTAMP writes 'YYYY-MM-DD HH:MM:SS' (UTC, no
    timezone). datetime.fromisoformat handles that on 3.11+; we also
    accept ISO strings emitted by Python code paths.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    try:
        # Python 3.11+ tolerates the 'YYYY-MM-DD HH:MM:SS' shape.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _humanize_age(ts: datetime | str | None) -> str | None:
    """Turn a timestamp into 'Posted 3h ago' / 'Posted yesterday' /
    'Posted Apr 28'."""
    dt = _parse_ts(ts) if not isinstance(ts, datetime) else ts
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = _now_utc() - dt
    secs = delta.total_seconds()
    if secs < 60:
        return "Just posted"
    if secs < 3600:
        return f"Posted {int(secs // 60)}m ago"
    if secs < 86400:
        return f"Posted {int(secs // 3600)}h ago"
    if secs < 86400 * 2:
        return "Posted yesterday"
    if secs < 86400 * 7:
        return f"Posted {int(secs // 86400)}d ago"
    try:
        return f"Posted {dt.strftime('%b %-d')}"
    except ValueError:
        # Windows %-d is unsupported — fall back.
        return f"Posted {dt.strftime('%b %d')}"


def _default_threshold() -> int:
    """Threshold used for 'passed' coloring on the appraisal feed.

    Pulls from env (ALERT_SCORE_THRESHOLD) — same default that governs
    new subscribers. Per-subscriber thresholds still drive actual emails.
    """
    try:
        return int(os.environ.get("ALERT_SCORE_THRESHOLD", "70"))
    except (TypeError, ValueError):
        return 70


def _is_alive(last_event_ts) -> bool:
    """Heuristic: scheduler is 'alive' if it produced any event in the
    last 120s. Mirrors the personal tool's threshold."""
    dt = _parse_ts(last_event_ts)
    if dt is None:
        return False
    return (_now_utc() - dt).total_seconds() < 120


# ---------------------------------------------------------------------------
# / — front page (manage + test appraiser). Public, but the manage panel
#     is hidden when not logged in.
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Front page. Shows the marketing surface + (when logged in) the
    manage / bulk-add / settings panels."""
    return render_template(
        "index.html",
        defaults={
            "keyword": "",
            "lat": DEFAULT_LAT,
            "lng": DEFAULT_LNG,
            "radius_km": DEFAULT_RADIUS_KM,
            "price_min": "",
            "price_max": "",
        },
        results=None,
        meta=None,
        error=None,
        logged_in=token_store.is_logged_in(),
        is_paid=license_manager.is_paid(),
    )


# ---------------------------------------------------------------------------
# /upgrade — pricing page (Stripe Checkout button). Public so logged-out
#            users can see it, but the CTA only fires once logged in.
# ---------------------------------------------------------------------------

@app.route("/upgrade")
def upgrade_page():
    return render_template("upgrade.html")


# ---------------------------------------------------------------------------
# /dashboard — observability page. Pro-only. Free users land on /upgrade.
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard_page():
    if not license_manager.is_paid():
        return redirect("/upgrade")
    return render_template("dashboard.html")


# ---------------------------------------------------------------------------
# /api/dashboard/summary — basic status strip + pipeline funnel.
#                          FREE tier sees this too (the alive/funnel
#                          numbers are useful even on a free plan).
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/summary")
@login_required_api
def api_dashboard_summary():
    """Light-weight status payload for the dashboard's top tiles.

    SQLite translation notes:
      - NOW() -> CURRENT_TIMESTAMP
      - INTERVAL 'N seconds' -> datetime('now', '-N seconds')
      - COUNT(*) FILTER (WHERE x) -> SUM(CASE WHEN x THEN 1 ELSE 0 END)
      - JSONB detail columns are TEXT here; the few summary queries
        don't filter on detail values, so no Python-side parse is needed.
    """
    with get_conn() as conn:
        # Most recent heartbeat (any event-type the scheduler emits).
        last_event_row = conn.execute(
            """SELECT MAX(created_at) FROM scheduler_events
               WHERE event_type IN (
                   'poll','reload','safety_drain','scheduler_boot',
                   'rate_limit_backoff','fb_probe','scheduler_heartbeat',
                   'slow_start_ramp','fb_rate_limit'
               )"""
        ).fetchone()
        last_event = last_event_row[0] if last_event_row else None

        # Most recent boot (fallback: oldest event).
        last_boot_row = conn.execute(
            """SELECT MAX(created_at) FROM scheduler_events
               WHERE event_type = 'scheduler_boot'"""
        ).fetchone()
        last_boot = last_boot_row[0] if last_boot_row else None
        if last_boot is None:
            row = conn.execute(
                "SELECT MIN(created_at) FROM scheduler_events"
            ).fetchone()
            last_boot = row[0] if row else None
        # Use a sentinel earlier-than-any-row if still null, so the
        # "since boot" filters don't blow up.
        last_boot_for_filter = last_boot or "0001-01-01"

        rates_row = conn.execute(
            """SELECT
                   SUM(CASE WHEN event_type='poll'
                            AND created_at >= datetime('now','-1 hours')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='poll'
                            AND created_at >= ?
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='fb_rate_limit'
                            AND created_at >= datetime('now','-1 hours')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='fb_rate_limit'
                            AND created_at >= datetime('now','-24 hours')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='fb_rate_limit'
                            AND created_at >= ?
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='fb_rate_limit' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='email_sent'
                            AND created_at >= date('now')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='email_sent' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='email_failed'
                            AND created_at >= date('now')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='pipeline_error'
                            AND created_at >= datetime('now','-24 hours')
                       THEN 1 ELSE 0 END)
               FROM scheduler_events""",
            (last_boot_for_filter, last_boot_for_filter),
        ).fetchone()
        (polls_1h, polls_since_boot,
         rate_1h, rate_24h, rate_since_boot, rate_total,
         emails_today, emails_total,
         email_fail_today, errors_24h) = rates_row

        # Subscriber threshold for the funnel — min across active subs.
        sub_thresh_row = conn.execute(
            "SELECT MIN(score_threshold) FROM subscribers WHERE active = 1"
        ).fetchone()
        funnel_threshold = (
            int(sub_thresh_row[0])
            if sub_thresh_row and sub_thresh_row[0] is not None
            else _default_threshold()
        )

        funnel_row = conn.execute(
            """SELECT
                   SUM(CASE WHEN scraped_at >= date('now')
                              AND search_id IS NOT NULL
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN rejected = 1
                              AND scraped_at >= date('now')
                              AND search_id IS NOT NULL
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN appraised = 1
                              AND scraped_at >= date('now')
                              AND search_id IS NOT NULL
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN deal_score IS NOT NULL
                              AND deal_score >= ? AND rejected = 0
                              AND scraped_at >= date('now')
                              AND search_id IS NOT NULL
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN notified = 1
                              AND notified_at >= date('now')
                              AND search_id IS NOT NULL
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN deal_score IS NOT NULL
                              AND deal_score >= ? AND rejected = 0
                              AND notified = 0
                              AND search_id IS NOT NULL
                       THEN 1 ELSE 0 END)
               FROM listings""",
            (funnel_threshold, funnel_threshold),
        ).fetchone()
        scraped, rej, appr, over, notif, pending = funnel_row

        watches_row = conn.execute(
            "SELECT SUM(CASE WHEN active=1 THEN 1 ELSE 0 END), COUNT(*) "
            "FROM user_searches"
        ).fetchone()
        active_w, total_w = watches_row

        # External API counters. The Postgres version filtered minimax
        # detail->>'success' inside SUM; SQLite's json_extract works
        # here too but we keep it simple with a Python-side parse pass
        # in the per-watch endpoint, and just record a count for the
        # summary tile (the dashboard only displays calls_today, not
        # per-success).
        external_row = conn.execute(
            """SELECT
                   SUM(CASE WHEN event_type='minimax_call'
                            AND created_at >= date('now')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='minimax_call' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='secondary_check'
                            AND created_at >= date('now')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='ebay_api'
                            AND created_at >= date('now')
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type='ebay_api' THEN 1 ELSE 0 END)
               FROM scheduler_events"""
        ).fetchone()
        mm_today, mm_total, sc_today, ebay_today, ebay_total = external_row

    try:
        mm_budget = int(os.environ.get("MINIMAX_DAILY_BUDGET", "20"))
    except (TypeError, ValueError):
        mm_budget = 20

    poll_timer = _compute_poll_timer()

    return jsonify({
        "alive": _is_alive(last_event),
        "last_event_iso": _iso(last_event),
        "active_watches": int(active_w or 0),
        "total_watches": int(total_w or 0),
        "rates": {
            "polls_last_1h": int(polls_1h or 0),
            "polls_since_boot": int(polls_since_boot or 0),
            "rate_limits_last_1h": int(rate_1h or 0),
            "rate_limits_last_24h": int(rate_24h or 0),
            "rate_limits_since_boot": int(rate_since_boot or 0),
            "rate_limits_total": int(rate_total or 0),
            "emails_today": int(emails_today or 0),
            "emails_total": int(emails_total or 0),
            "email_failures_today": int(email_fail_today or 0),
            "pipeline_errors_24h": int(errors_24h or 0),
        },
        "scheduler_booted_at": _iso(last_boot),
        "poll_timer": poll_timer,
        "external_apis": {
            "minimax": {
                "calls_today": int(mm_today or 0),
                "calls_today_succeeded": int(mm_today or 0),  # see comment above
                "calls_total": int(mm_total or 0),
                "daily_budget": mm_budget,
                "secondary_checks_today": int(sc_today or 0),
            },
            "ebay": {
                "calls_today": int(ebay_today or 0),
                "calls_total": int(ebay_total or 0),
            },
        },
        "funnel_today": {
            "scraped": int(scraped or 0),
            "rejected": int(rej or 0),
            "appraised": int(appr or 0),
            "over_threshold": int(over or 0),
            "notified": int(notif or 0),
            "pending_unsent": int(pending or 0),
            "threshold_used": funnel_threshold,
        },
        "tier": license_manager.tier(),
        "is_paid": license_manager.is_paid(),
    })


def _compute_poll_timer() -> dict:
    """Compute the user-visible 'when does the next FB poll happen?' state.

    SQLite port note: the Postgres version used EXTRACT(EPOCH FROM ...)
    + JSONB ->> operators inside one query. Here we fetch the rows and
    do the math (epoch deltas, JSON detail extraction) in Python. Same
    output shape as the personal tool's payload so the dashboard JS
    doesn't change.
    """
    BASE = 60
    MAX = 600
    WINDOW = 1800
    DEFAULT_TICK = 20
    DEFAULT_INITIAL = 60
    DEFAULT_FLOOR = 20

    with get_conn() as conn:
        rl_row = conn.execute(
            f"""SELECT
                   COUNT(*),
                   MAX(created_at)
               FROM scheduler_events
               WHERE event_type = 'fb_rate_limit'
                 AND created_at >= datetime('now','-{WINDOW} seconds')"""
        ).fetchone()
        rl_n = int(rl_row[0] or 0) if rl_row else 0
        rl_last = _parse_ts(rl_row[1]) if rl_row else None

        last_attempt_row = conn.execute(
            """SELECT MAX(created_at) FROM scheduler_events
               WHERE event_type IN ('poll','fb_rate_limit')"""
        ).fetchone()
        last_attempt = _parse_ts(last_attempt_row[0]) if last_attempt_row else None

        ss_row = conn.execute(
            """SELECT detail FROM scheduler_events
               WHERE event_type = 'slow_start_ramp'
               ORDER BY created_at DESC
               LIMIT 1"""
        ).fetchone()

        probe_row = conn.execute(
            """SELECT detail, created_at FROM scheduler_events
               WHERE event_type = 'fb_probe'
               ORDER BY created_at DESC
               LIMIT 1"""
        ).fetchone()

    now = _now_utc()

    cooldown_remaining = 0
    cooldown_total = 0
    if rl_n and rl_last is not None:
        nominal = min(BASE * (2 ** min(int(rl_n) - 1, 4)), MAX)
        import random as _r
        rl_last_epoch = rl_last.timestamp()
        jitter_seed = int((rl_last_epoch or 0) * 1000) ^ int(rl_n) << 8
        rng = _r.Random(jitter_seed)
        cooldown_total = int(nominal * rng.uniform(0.85, 1.15))
        elapsed = (now - rl_last).total_seconds()
        cooldown_remaining = max(0, int(cooldown_total - elapsed))

    slow_start_active = ss_row is not None
    if ss_row:
        detail = _json_loads(ss_row[0]) or {}
        try:
            slow_start_min = int(detail.get("to_s") or DEFAULT_INITIAL)
        except (TypeError, ValueError):
            slow_start_min = DEFAULT_INITIAL
    else:
        slow_start_min = DEFAULT_INITIAL

    seconds_since_last_attempt = (
        int((now - last_attempt).total_seconds()) if last_attempt else 999_999
    )

    if cooldown_remaining > 0:
        state = "cooldown"
        next_in = cooldown_remaining
    elif slow_start_active and seconds_since_last_attempt < slow_start_min:
        state = "slow_start"
        next_in = max(0, slow_start_min - seconds_since_last_attempt)
    else:
        state = "tick"
        if last_attempt is not None:
            into_tick = seconds_since_last_attempt % DEFAULT_TICK
            next_in = (DEFAULT_TICK - into_tick) if into_tick > 0 else 0
        else:
            next_in = DEFAULT_TICK

    probe_detail = _json_loads(probe_row[0]) if probe_row else None
    probe_result = (probe_detail or {}).get("result") if probe_detail else None
    probe_at_dt = _parse_ts(probe_row[1]) if probe_row else None
    probe_at = probe_at_dt.isoformat() if probe_at_dt else None

    rl_after_probe = (
        rl_last is not None and (probe_at_dt is None or rl_last > probe_at_dt)
    )
    very_recent_rl = rl_last is not None and (now - rl_last).total_seconds() < 300

    if probe_result == "blocked":
        fb_health = "blocked"
    elif probe_result == "down":
        fb_health = "fb_down"
    elif probe_result == "ok" and very_recent_rl and rl_after_probe:
        fb_health = "graphql_gated"
    elif probe_result == "ok":
        fb_health = "ok"
    else:
        fb_health = "graphql_gated" if very_recent_rl else "unknown"

    return {
        "state": state,
        "next_attempt_in_s": int(next_in),
        "last_attempt_iso": last_attempt.isoformat() if last_attempt else None,
        "fb_health": fb_health,
        "last_probe_at": probe_at,
        "cooldown": {
            "active": cooldown_remaining > 0,
            "remaining_s": int(cooldown_remaining),
            "total_s": int(cooldown_total),
            "rate_limits_in_window": int(rl_n or 0),
            "window_minutes": WINDOW // 60,
        },
        "slow_start": {
            "active": slow_start_active,
            "min_interval_s": int(slow_start_min),
            "elapsed_since_last_attempt_s": int(seconds_since_last_attempt)
                if last_attempt else None,
            "floor_s": DEFAULT_FLOOR,
            "initial_s": DEFAULT_INITIAL,
        },
        "coordinator_tick_s": DEFAULT_TICK,
    }


# ---------------------------------------------------------------------------
# /api/dashboard/events — live event tail. Pro-only? In the personal tool
#                          this was free; we keep it logged-in-only here.
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/events")
@login_required_api
def api_dashboard_events():
    try:
        since = int(request.args.get("since", 0))
    except (TypeError, ValueError):
        since = 0
    try:
        limit = int(request.args.get("limit", 80))
    except (TypeError, ValueError):
        limit = 80
    limit = max(1, min(limit, 200))

    rows = []
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT e.id, e.event_type,
                      json_extract(e.detail,'$.search_id') AS search_id,
                      json_extract(e.detail,'$.duration_ms') AS duration_ms,
                      e.detail, e.created_at,
                      us.keyword
               FROM scheduler_events e
               LEFT JOIN user_searches us
                    ON us.id = json_extract(e.detail,'$.search_id')
               WHERE e.id > ?
               ORDER BY e.id DESC
               LIMIT ?""",
            (since, limit),
        )
        for r in cur.fetchall():
            rows.append({
                "id": r[0],
                "event_type": r[1],
                "search_id": r[2],
                "duration_ms": r[3],
                "detail": _json_loads(r[4]),
                "created_at": _iso(r[5]),
                "keyword": r[6],
            })
    return jsonify({"events": rows})


# ---------------------------------------------------------------------------
# /api/dashboard/per-watch — paid only.
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/per-watch")
@paid_only_api
def api_dashboard_per_watch():
    """Per-watch performance table. Translated AVG((detail->>'raw_count')::int)
    into a Python-side reduce after pulling the raw rows for each watch."""
    rows = []
    with get_conn() as conn:
        # First: aggregate per-watch counts. AVG on the JSON detail field
        # is done in Python to avoid SQLite quirks with json_extract +
        # AVG on NULL casts.
        watches = conn.execute(
            """SELECT us.id, us.keyword, us.active
               FROM user_searches us
               ORDER BY us.active DESC, us.id"""
        ).fetchall()

        for w in watches:
            us_id, keyword, active = w[0], w[1], w[2]
            poll_row = conn.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN event_type='fb_rate_limit'
                                  AND created_at >= datetime('now','-24 hours')
                                  AND json_extract(detail,'$.search_id') = ?
                                  THEN 1 ELSE 0 END)
                   FROM scheduler_events
                   WHERE event_type='poll'
                     AND created_at >= datetime('now','-24 hours')
                     AND json_extract(detail,'$.search_id') = ?""",
                (us_id, us_id),
            ).fetchone()
            polls_24h = int(poll_row[0] or 0)

            rl_row = conn.execute(
                """SELECT COUNT(*) FROM scheduler_events
                   WHERE event_type='fb_rate_limit'
                     AND created_at >= datetime('now','-24 hours')
                     AND json_extract(detail,'$.search_id') = ?""",
                (us_id,),
            ).fetchone()
            rate_lims_24h = int(rl_row[0] or 0)

            avg_raw_rows = conn.execute(
                """SELECT json_extract(detail,'$.raw_count') FROM scheduler_events
                   WHERE event_type='poll'
                     AND created_at >= datetime('now','-24 hours')
                     AND json_extract(detail,'$.search_id') = ?""",
                (us_id,),
            ).fetchall()
            raw_counts = [
                int(rc[0]) for rc in avg_raw_rows if rc[0] is not None
            ]
            avg_raw = (sum(raw_counts) / len(raw_counts)) if raw_counts else None

            hits_row = conn.execute(
                """SELECT COUNT(*) FROM listings
                   WHERE search_id = ?
                     AND deal_score IS NOT NULL
                     AND deal_score >= 70
                     AND rejected = 0
                     AND scraped_at >= datetime('now','-24 hours')""",
                (us_id,),
            ).fetchone()
            hits_24h = int(hits_row[0] or 0)

            scraped_row = conn.execute(
                """SELECT COUNT(*) FROM listings
                   WHERE search_id = ?
                     AND scraped_at >= datetime('now','-24 hours')""",
                (us_id,),
            ).fetchone()
            scraped_24h = int(scraped_row[0] or 0)

            last_row = conn.execute(
                "SELECT MAX(scraped_at) FROM listings WHERE search_id = ?",
                (us_id,),
            ).fetchone()

            rows.append({
                "id": us_id,
                "keyword": keyword,
                "active": bool(active),
                "polls_24h": polls_24h,
                "rate_limits_24h": rate_lims_24h,
                "avg_raw": float(avg_raw) if avg_raw is not None else None,
                "hits_24h": hits_24h,
                "scraped_24h": scraped_24h,
                "last_scrape_iso": _iso(last_row[0]) if last_row else None,
            })
    return jsonify({"watches": rows})


# ---------------------------------------------------------------------------
# /api/dashboard/score-histogram — paid only.
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/score-histogram")
@paid_only_api
def api_dashboard_histogram():
    buckets = [0] * 11
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT deal_score FROM listings
               WHERE deal_score IS NOT NULL
                 AND rejected = 0
                 AND appraised_at >= datetime('now','-24 hours')"""
        )
        for (score,) in cur.fetchall():
            idx = min(int(score) // 10, 10)
            buckets[idx] += 1
    labels = [f"{i*10}-{i*10+9}" if i < 10 else "100" for i in range(11)]
    return jsonify({
        "buckets": [{"label": l, "count": c} for l, c in zip(labels, buckets)],
    })


# ---------------------------------------------------------------------------
# /api/dashboard/appraisal-feed — free tier sees this; the feed is the
#                                  acquisition surface.
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/appraisal-feed")
@login_required_api
def api_dashboard_appraisal_feed():
    """Per-listing live feed. SQLite port: the dynamic WHERE clause +
    `since_id` cursor logic carries over with `?` placeholders. The
    haversine-based distance calc happens Python-side for both PDP
    coords and city-geocache fallback (same as the original)."""
    try:
        limit = int(request.args.get("limit", 60))
    except (TypeError, ValueError):
        limit = 60
    limit = max(1, min(limit, 200))
    filt = (request.args.get("filter") or "all").lower()
    since_id = request.args.get("since_id") or None
    try:
        min_score_raw = request.args.get("min_score")
        min_score = int(min_score_raw) if min_score_raw not in (None, "") else None
    except (TypeError, ValueError):
        min_score = None
    if min_score is not None:
        min_score = max(0, min(100, min_score))

    where_clauses: list[str] = []
    params: list = []

    if min_score is not None:
        where_clauses.append(
            "l.appraised = 1 AND l.rejected = 0 AND l.deal_score >= ?"
        )
        params.append(min_score)
    elif filt == "passed":
        where_clauses.append(
            "l.appraised = 1 AND l.rejected = 0 AND l.deal_score >= ?"
        )
        params.append(_default_threshold())
    elif filt == "scored":
        where_clauses.append(
            "l.appraised = 1 AND l.rejected = 0 AND l.deal_score IS NOT NULL"
        )
    elif filt == "rejected":
        where_clauses.append("l.rejected = 1")
    elif filt == "unscoreable":
        where_clauses.append(
            "l.appraised = 1 AND l.deal_score IS NULL AND l.rejected = 0"
        )
    # 'all' adds no filter

    if since_id:
        where_clauses.append(
            "(l.scraped_at, l.id) > "
            "(SELECT scraped_at, id FROM listings WHERE id = ?)"
        )
        params.append(since_id)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    threshold = _default_threshold()

    rows: list[dict] = []
    with get_conn() as conn:
        cur = conn.execute(
            f"""SELECT l.id, l.title, l.price, l.deal_score, l.fair_value,
                      l.rejected, l.rejection_reason, l.appraisal_note,
                      l.listing_url, l.photo_url,
                      l.scraped_at, l.appraised_at,
                      l.notified, l.appraised,
                      l.comp_sample_size, l.comp_median,
                      l.comp_search_term, l.comp_source,
                      us.keyword,
                      l.seller_location,
                      us.latitude, us.longitude, us.radius_km,
                      l.detail_latitude, l.detail_longitude
               FROM listings l
               LEFT JOIN user_searches us ON us.id = l.search_id
               WHERE {where_sql}
               ORDER BY l.scraped_at DESC, l.id DESC
               LIMIT ?""",
            (*params, limit),
        )
        db_rows = cur.fetchall()

        from deal_finder.db.geo import haversine_km

        # Batch-resolve geocodes for rows that lack PDP coords.
        unique_locs = {
            r["seller_location"] for r in db_rows
            if r["seller_location"]
            and r["detail_latitude"] is None
            and r["detail_longitude"] is None
        }
        geocode_cache: dict[str, tuple[float, float] | None] = {}
        if unique_locs:
            placeholders = ",".join("?" * len(unique_locs))
            geo_cur = conn.execute(
                f"""SELECT label, latitude, longitude FROM city_geocache
                    WHERE label IN ({placeholders})""",
                tuple(unique_locs),
            )
            for label, lat, lng in geo_cur.fetchall():
                geocode_cache[label] = (
                    (float(lat), float(lng))
                    if lat is not None and lng is not None
                    else None
                )

        for r in db_rows:
            lid = r["id"]
            title = r["title"]
            price = r["price"]
            score = r["deal_score"]
            fair = r["fair_value"]
            rejected = bool(r["rejected"])
            rej_reason = r["rejection_reason"]
            appraisal_note = r["appraisal_note"]
            listing_url = r["listing_url"]
            photo_url = r["photo_url"]
            scraped_at = r["scraped_at"]
            appraised_at = r["appraised_at"]
            notified = bool(r["notified"])
            appraised = bool(r["appraised"])
            comp_n = r["comp_sample_size"]
            comp_median = r["comp_median"]
            comp_term = r["comp_search_term"]
            comp_source = r["comp_source"]
            keyword = r["keyword"]
            seller_location = r["seller_location"]
            watch_lat = r["latitude"]
            watch_lng = r["longitude"]
            watch_radius = r["radius_km"]
            detail_lat = r["detail_latitude"]
            detail_lng = r["detail_longitude"]

            distance_km = None
            if watch_lat is not None and watch_lng is not None:
                if detail_lat is not None and detail_lng is not None:
                    distance_km = round(
                        haversine_km(
                            float(watch_lat), float(watch_lng),
                            float(detail_lat), float(detail_lng),
                        ),
                        1,
                    )
                elif seller_location:
                    coords = geocode_cache.get(seller_location)
                    if coords is not None:
                        distance_km = round(
                            haversine_km(
                                float(watch_lat), float(watch_lng),
                                coords[0], coords[1],
                            ),
                            1,
                        )

            if rejected:
                status = "rejected"
            elif notified:
                status = "emailed"
            elif appraised and score is not None and score >= threshold:
                status = "passed"
            elif appraised and score is not None:
                status = "scored"
            elif appraised and score is None:
                status = "unscoreable"
            else:
                status = "pending"

            rows.append({
                "id": lid,
                "title": title,
                "price": float(price) if price is not None else None,
                "deal_score": int(score) if score is not None else None,
                "fair_value": float(fair) if fair is not None else None,
                "rejected": rejected,
                "rejection_reason": rej_reason,
                "appraisal_note": appraisal_note,
                "listing_url": listing_url,
                "photo_url": photo_url,
                "scraped_at": _iso(scraped_at),
                "appraised_at": _iso(appraised_at),
                "notified": notified,
                "appraised": appraised,
                "comp_sample_size": int(comp_n) if comp_n is not None else None,
                "comp_median": float(comp_median) if comp_median is not None else None,
                "comp_search_term": comp_term,
                "comp_source": comp_source,
                "keyword": keyword,
                "status": status,
                "seller_location": seller_location,
                "distance_km": distance_km,
                "watch_radius_km": int(watch_radius) if watch_radius is not None else None,
            })

    return jsonify({"listings": rows, "threshold": threshold, "filter": filt})


# ---------------------------------------------------------------------------
# /api/dashboard/breakdown/<id> — paid only.
# ---------------------------------------------------------------------------

@app.route("/api/dashboard/breakdown/<listing_id>")
@paid_only_api
def api_dashboard_breakdown(listing_id: str):
    """Full score breakdown for a listing. SQLite stores
    appraisal_breakdown as TEXT — we json.loads on the way out."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT l.id, l.title, l.price, l.deal_score, l.fair_value,
                      l.appraisal_note, l.appraisal_breakdown,
                      l.comp_median, l.comp_sample_size,
                      l.comp_search_term, l.comp_source,
                      l.rejected, l.rejection_reason, l.notified,
                      l.listing_url, l.seller_location,
                      us.keyword, us.latitude, us.longitude, us.radius_km
               FROM listings l
               LEFT JOIN user_searches us ON us.id = l.search_id
               WHERE l.id = ?""",
            (listing_id,),
        ).fetchone()

    if not row:
        return jsonify({"error": "listing not found"}), 404

    distance_km = None
    if row["seller_location"] and row["latitude"] is not None and row["longitude"] is not None:
        from deal_finder.db.geo import geocode_city, haversine_km
        try:
            coords = geocode_city(row["seller_location"])
        except Exception:  # noqa: BLE001
            coords = None
        if coords is not None:
            distance_km = round(
                haversine_km(
                    float(row["latitude"]), float(row["longitude"]),
                    coords[0], coords[1],
                ),
                1,
            )

    sec_check = None
    with get_conn() as conn:
        # detail->>'listing_id' -> json_extract(detail,'$.listing_id')
        sec_row = conn.execute(
            """SELECT detail FROM scheduler_events
               WHERE event_type = 'secondary_check'
                 AND json_extract(detail,'$.listing_id') = ?
               ORDER BY created_at DESC LIMIT 1""",
            (listing_id,),
        ).fetchone()
        if sec_row:
            sec_check = _json_loads(sec_row[0])

    breakdown = _json_loads(row["appraisal_breakdown"]) or {}

    return jsonify({
        "id": row["id"],
        "title": row["title"],
        "price": float(row["price"]) if row["price"] is not None else None,
        "deal_score": int(row["deal_score"]) if row["deal_score"] is not None else None,
        "fair_value": float(row["fair_value"]) if row["fair_value"] is not None else None,
        "appraisal_note": row["appraisal_note"],
        "breakdown": breakdown,
        "comp": {
            "search_term": row["comp_search_term"],
            "source": row["comp_source"],
            "sample_size": row["comp_sample_size"],
            "median": float(row["comp_median"]) if row["comp_median"] is not None else None,
            # SQLite schema doesn't track comp_mean/min/max separately.
            "mean": None,
            "min": None,
            "max": None,
        },
        "rejected": bool(row["rejected"]),
        "rejection_reason": row["rejection_reason"],
        "notified": bool(row["notified"]),
        "listing_url": row["listing_url"],
        "keyword": row["keyword"],
        "seller_location": row["seller_location"],
        "distance_km": distance_km,
        "watch_radius_km": int(row["radius_km"]) if row["radius_km"] is not None else None,
        "secondary_check": sec_check,
    })


# ---------------------------------------------------------------------------
# /api/dashboard/log/tail — log file. Pro-only since the log lives next to
#                            the scheduler which is itself a pro thing.
#                            Keep behind login_required only for now (free
#                            users won't see /dashboard but the API is
#                            harmless if they hit it directly).
# ---------------------------------------------------------------------------

_SCHEDULER_LOG_PATH = Path(
    os.path.expanduser("~/.bullseye/logs/scheduler.log")
)


@app.route("/api/dashboard/log/tail")
@login_required_api
def api_dashboard_log_tail():
    try:
        n = int(request.args.get("n", 200))
    except (TypeError, ValueError):
        n = 200
    n = max(1, min(n, 1000))

    if not _SCHEDULER_LOG_PATH.exists():
        return jsonify({
            "lines": [],
            "path": str(_SCHEDULER_LOG_PATH),
            "exists": False,
            "warning": "scheduler hasn't started yet — no log file found",
        })

    try:
        size = _SCHEDULER_LOG_PATH.stat().st_size
        read_window = min(size, 256 * 1024)
        with _SCHEDULER_LOG_PATH.open("rb") as f:
            f.seek(size - read_window)
            chunk = f.read().decode("utf-8", errors="replace")
        lines = chunk.splitlines()
        if size > read_window and lines:
            lines = lines[1:]
        lines = lines[-n:]
    except OSError as e:
        return jsonify({"lines": [], "error": str(e)}), 500

    return jsonify({
        "lines": lines,
        "path": str(_SCHEDULER_LOG_PATH),
        "exists": True,
        "total_returned": len(lines),
    })


# ---------------------------------------------------------------------------
# /api/searches — populate the subscribe dropdown
# ---------------------------------------------------------------------------

@app.route("/api/searches")
@login_required_api
def api_searches():
    rows = []
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT id, keyword, radius_km
               FROM user_searches
               WHERE active = 1
               ORDER BY id"""
        )
        for r in cur.fetchall():
            rows.append({
                "id": r["id"],
                "keyword": r["keyword"],
                "radius_km": r["radius_km"],
            })
    return jsonify({"searches": rows})


# ---------------------------------------------------------------------------
# /api/watches — list, create, patch, delete, bulk-update
# ---------------------------------------------------------------------------

@app.route("/api/watches", methods=["GET"])
@login_required_api
def api_watches_list():
    rows = []
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT
                   us.id, us.keyword, us.radius_km, us.price_min, us.price_max,
                   us.latitude, us.longitude, us.active, us.created_at,
                   us.must_include, us.must_exclude,
                   s.email, s.score_threshold, s.daily_summary_enabled,
                   (SELECT COUNT(*) FROM listings l
                       WHERE l.search_id = us.id) AS total_seen,
                   (SELECT COUNT(*) FROM listings l
                       WHERE l.search_id = us.id
                         AND l.deal_score IS NOT NULL
                         AND l.deal_score >= COALESCE(s.score_threshold, 70)
                         AND l.rejected = 0) AS hit_count,
                   (SELECT MAX(l.scraped_at) FROM listings l
                       WHERE l.search_id = us.id) AS last_scrape
               FROM user_searches us
               LEFT JOIN subscribers s ON s.search_id = us.id
               ORDER BY us.active DESC, us.id DESC"""
        )
        for r in cur.fetchall():
            rows.append({
                "id": r["id"],
                "keyword": r["keyword"],
                "radius_km": r["radius_km"],
                "price_min": r["price_min"],
                "price_max": r["price_max"],
                "latitude": float(r["latitude"]) if r["latitude"] is not None else None,
                "longitude": float(r["longitude"]) if r["longitude"] is not None else None,
                "active": bool(r["active"]),
                "created_at": _iso(r["created_at"]),
                "must_include": r["must_include"],
                "must_exclude": r["must_exclude"],
                "email": r["email"],
                "score_threshold": r["score_threshold"] if r["score_threshold"] is not None else None,
                "daily_summary_enabled": bool(r["daily_summary_enabled"]) if r["daily_summary_enabled"] is not None else False,
                "total_seen": int(r["total_seen"] or 0),
                "hit_count": int(r["hit_count"] or 0),
                "last_scrape": _iso(r["last_scrape"]),
            })
    return jsonify({"watches": rows})


@app.route("/api/watches", methods=["POST"])
@login_required_api
def api_watches_create():
    """Create a single watch. Subject to the watches_limit gate for
    free users — free tier caps at 3 active watches by default.

    The personal tool didn't have this endpoint (it created watches via
    /api/searches/bulk). We add it as the canonical single-watch
    creation surface so the limit check has one place to live.
    """
    limit = license_manager.watches_limit()
    if limit is not None:
        with get_conn() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM user_searches WHERE active = 1"
            ).fetchone()[0]
        if int(cnt or 0) >= limit:
            return jsonify({
                "ok": False,
                "error": "watches_limit_reached",
                "message": (
                    f"Your plan allows {limit} active watches. "
                    "Upgrade to Pro for unlimited."
                ),
                "limit": limit,
            }), 403

    data = request.form if request.form else (request.get_json(silent=True) or {})
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"ok": False, "error": "keyword required"}), 400
    try:
        lat = float(data.get("lat") or DEFAULT_LAT)
        lng = float(data.get("lng") or DEFAULT_LNG)
        radius_km = int(data.get("radius_km") or DEFAULT_RADIUS_KM)
        price_min_str = (str(data.get("price_min") or "")).strip()
        price_max_str = (str(data.get("price_max") or "")).strip()
        price_min = int(price_min_str) if price_min_str else None
        price_max = int(price_max_str) if price_max_str else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad numeric input"}), 400

    with get_conn() as conn:
        with conn:
            cur = conn.execute(
                """INSERT INTO user_searches
                   (keyword, latitude, longitude, radius_km,
                    price_min, price_max, active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)
                   RETURNING id""",
                (keyword, lat, lng, radius_km, price_min, price_max),
            )
            new_id = cur.fetchone()[0]
    return jsonify({"ok": True, "id": new_id, "keyword": keyword})


@app.route("/api/watches/<int:watch_id>", methods=["PATCH"])
@login_required_api
def api_watches_patch(watch_id: int):
    """Update one watch's prefs. Mirrors the personal tool's PATCH semantics."""
    data = request.form if request.form else (request.get_json(silent=True) or {})

    def _opt_int(name: str) -> int | None:
        v = data.get(name)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be an integer")

    def _opt_bool(name: str) -> bool | None:
        if name not in data:
            return None
        v = data.get(name)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    us_updates: dict[str, object] = {}
    sub_updates: dict[str, object] = {}

    try:
        if "active" in data:
            # Store SQLite booleans as INTEGER 0/1.
            us_updates["active"] = int(bool(_opt_bool("active")))
            sub_updates["active"] = us_updates["active"]
        if "radius_km" in data:
            r_km = _opt_int("radius_km")
            if r_km is None or not (1 <= r_km <= 500):
                return jsonify({"ok": False, "error": "radius_km must be 1-500"}), 400
            us_updates["radius_km"] = r_km
        if "price_min" in data:
            us_updates["price_min"] = _opt_int("price_min")
        if "price_max" in data:
            us_updates["price_max"] = _opt_int("price_max")
        if "must_include" in data:
            v = (data.get("must_include") or "").strip()
            us_updates["must_include"] = v if v else None
        if "must_exclude" in data:
            v = (data.get("must_exclude") or "").strip()
            us_updates["must_exclude"] = v if v else None
        if "score_threshold" in data:
            t = _opt_int("score_threshold")
            if t is None or not (0 <= t <= 100):
                return jsonify({"ok": False, "error": "score_threshold must be 0-100"}), 400
            sub_updates["score_threshold"] = t
        if "daily_summary_enabled" in data:
            sub_updates["daily_summary_enabled"] = int(bool(_opt_bool("daily_summary_enabled")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if not us_updates and not sub_updates:
        return jsonify({"ok": False, "error": "no valid fields to update"}), 400

    with get_conn() as conn:
        with conn:
            exists = conn.execute(
                "SELECT 1 FROM user_searches WHERE id = ?", (watch_id,),
            ).fetchone()
            if exists is None:
                return jsonify({"ok": False, "error": "watch not found"}), 404

            if us_updates:
                set_clause = ", ".join(f"{k} = ?" for k in us_updates)
                vals = list(us_updates.values()) + [watch_id]
                conn.execute(
                    f"UPDATE user_searches SET {set_clause} WHERE id = ?",
                    vals,
                )
            if sub_updates:
                set_clause = ", ".join(f"{k} = ?" for k in sub_updates)
                vals = list(sub_updates.values()) + [watch_id]
                conn.execute(
                    f"UPDATE subscribers SET {set_clause} WHERE search_id = ?",
                    vals,
                )

    return jsonify({"ok": True, "id": watch_id, "updated": {**us_updates, **sub_updates}})


@app.route("/api/watches/bulk-update", methods=["POST"])
@login_required_api
def api_watches_bulk_update():
    data = request.form if request.form else (request.get_json(silent=True) or {})

    def _opt_int(name: str) -> int | None:
        v = data.get(name)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be an integer")

    def _opt_bool(name: str) -> bool | None:
        if name not in data:
            return None
        v = data.get(name)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    us_updates: dict[str, object] = {}
    sub_updates: dict[str, object] = {}

    try:
        if "active" in data:
            us_updates["active"] = int(bool(_opt_bool("active")))
            sub_updates["active"] = us_updates["active"]
        if "radius_km" in data:
            r_km = _opt_int("radius_km")
            if r_km is None or not (1 <= r_km <= 500):
                return jsonify({"ok": False, "error": "radius_km must be 1-500"}), 400
            us_updates["radius_km"] = r_km
        if "price_min" in data:
            us_updates["price_min"] = _opt_int("price_min")
        if "price_max" in data:
            us_updates["price_max"] = _opt_int("price_max")
        if "must_include" in data:
            v = (data.get("must_include") or "").strip()
            us_updates["must_include"] = v if v else None
        if "must_exclude" in data:
            v = (data.get("must_exclude") or "").strip()
            us_updates["must_exclude"] = v if v else None
        if "score_threshold" in data:
            t = _opt_int("score_threshold")
            if t is None or not (0 <= t <= 100):
                return jsonify({"ok": False, "error": "score_threshold must be 0-100"}), 400
            sub_updates["score_threshold"] = t
        if "daily_summary_enabled" in data:
            sub_updates["daily_summary_enabled"] = int(bool(_opt_bool("daily_summary_enabled")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if not us_updates and not sub_updates:
        return jsonify({"ok": False, "error": "no valid fields to update"}), 400

    only_active = str(data.get("only_active", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )

    us_filter = " WHERE active = 1" if only_active else ""
    sub_filter = (
        " WHERE search_id IN (SELECT id FROM user_searches WHERE active = 1)"
        if only_active else ""
    )

    us_count = 0
    sub_count = 0
    with get_conn() as conn:
        with conn:
            if us_updates:
                set_clause = ", ".join(f"{k} = ?" for k in us_updates)
                cur = conn.execute(
                    f"UPDATE user_searches SET {set_clause}{us_filter}",
                    list(us_updates.values()),
                )
                us_count = cur.rowcount
            if sub_updates:
                set_clause = ", ".join(f"{k} = ?" for k in sub_updates)
                cur = conn.execute(
                    f"UPDATE subscribers SET {set_clause}{sub_filter}",
                    list(sub_updates.values()),
                )
                sub_count = cur.rowcount

    return jsonify({
        "ok": True,
        "watches_updated": us_count,
        "subscribers_updated": sub_count,
        "fields": {**us_updates, **sub_updates},
        "only_active": only_active,
    })


@app.route("/api/watches/<int:watch_id>", methods=["DELETE"])
@login_required_api
def api_watches_delete(watch_id: int):
    """Delete a watch. ON DELETE CASCADE on subscribers + ON DELETE SET NULL
    on listings carry over from the SQLite schema."""
    with get_conn() as conn:
        with conn:
            cur = conn.execute(
                "DELETE FROM user_searches WHERE id = ? RETURNING keyword",
                (watch_id,),
            )
            row = cur.fetchone()
            if row is None:
                return jsonify({"ok": False, "error": "watch not found"}), 404
    return jsonify({"ok": True, "id": watch_id, "keyword": row[0]})


# ---------------------------------------------------------------------------
# /api/searches/bulk — bulk-add watches. Free tier still hits watches_limit
#                       on the *active* count, applied in aggregate after
#                       the dedupe pass.
# ---------------------------------------------------------------------------

@app.route("/api/searches/bulk", methods=["POST"])
@login_required_api
def api_searches_bulk():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    raw = (data.get("keywords") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "keywords required"}), 400

    import re
    parts = [p.strip() for p in re.split(r"[\n,]+", raw) if p.strip()]
    seen = set()
    keywords: list[str] = []
    for p in parts:
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        keywords.append(p)
    if not keywords:
        return jsonify({"ok": False, "error": "no usable keywords"}), 400

    home_lat, home_lng = _resolve_home_location()
    try:
        lat = float(data.get("lat") or home_lat)
        lng = float(data.get("lng") or home_lng)
        radius_km = int(data.get("radius_km") or 40)
        pmin_str = (str(data.get("price_min") or "")).strip()
        pmax_str = (str(data.get("price_max") or "")).strip()
        price_min = int(pmin_str) if pmin_str else None
        price_max = int(pmax_str) if pmax_str else None
        sub_email = (str(data.get("email") or "")).strip()
        sub_name = (str(data.get("name") or "")).strip() or None
        sub_thresh_raw = str(data.get("score_threshold") or "").strip()
        sub_threshold = int(data.get("score_threshold") or 70) if sub_thresh_raw else 70
        sub_threshold = max(0, min(100, sub_threshold))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad numeric input"}), 400

    if sub_email and "@" not in sub_email:
        return jsonify({"ok": False, "error": "invalid email"}), 400

    # Free-tier watches_limit on the bulk path. Count current actives;
    # for each new (non-dup) keyword we'd add one more — bail before
    # writing any of them if the total would exceed the limit. Dedupes
    # don't count (they re-activate existing rows).
    limit = license_manager.watches_limit()

    created: list[dict] = []
    duplicate: list[dict] = []
    subscribed_to: list[int] = []
    with get_conn() as conn:
        with conn:
            current = conn.execute(
                "SELECT COUNT(*) FROM user_searches WHERE active = 1"
            ).fetchone()[0] or 0

            for kw in keywords:
                row = conn.execute(
                    """SELECT id FROM user_searches
                       WHERE LOWER(keyword) = LOWER(?)
                         AND radius_km = ?
                         AND ABS(latitude - ?) < 0.001
                         AND ABS(longitude - ?) < 0.001""",
                    (kw, radius_km, lat, lng),
                ).fetchone()
                if row:
                    duplicate.append({"keyword": kw, "id": row["id"]})
                    conn.execute(
                        "UPDATE user_searches SET active = 1 WHERE id = ?",
                        (row["id"],),
                    )
                    continue
                # New row — check the active-count gate first.
                if limit is not None and (current + len(created)) >= limit:
                    # Stop creating but still report what was made.
                    break
                cur = conn.execute(
                    """INSERT INTO user_searches
                       (keyword, latitude, longitude, radius_km,
                        price_min, price_max, active)
                       VALUES (?, ?, ?, ?, ?, ?, 1)
                       RETURNING id""",
                    (kw, lat, lng, radius_km, price_min, price_max),
                )
                new_id = cur.fetchone()[0]
                created.append({"keyword": kw, "id": new_id})

            # Inline subscribe — applies to ALL touched searches.
            if sub_email:
                all_ids = [c["id"] for c in created] + [d["id"] for d in duplicate]
                for sid in all_ids:
                    conn.execute(
                        """INSERT INTO subscribers
                           (name, email, search_id, score_threshold, active)
                           VALUES (?, ?, ?, ?, 1)
                           ON CONFLICT (email, search_id) DO UPDATE SET
                             name = COALESCE(excluded.name, subscribers.name),
                             score_threshold = excluded.score_threshold,
                             active = 1""",
                        (sub_name, sub_email, sid, sub_threshold),
                    )
                    subscribed_to.append(sid)

    truncated = (
        limit is not None
        and (len(keywords) - len(duplicate)) > len(created)
    )

    summary_bits = [f"{len(created)} new search(es) created"]
    if duplicate:
        summary_bits.append(f"{len(duplicate)} re-activated")
    if truncated:
        summary_bits.append(
            f"truncated at {limit}-watch free-tier limit — "
            "upgrade to Pro for unlimited"
        )
    if sub_email and subscribed_to:
        summary_bits.append(
            f"alerts to {sub_email} on {len(subscribed_to)} watch(es) "
            f"@ score >= {sub_threshold}"
        )

    # TODO: cloud.alerts.send_confirmation_email — step 7. The personal
    # tool fired an immediate "we're watching" email here; we'll route
    # that through the cloud alerts function once it exists.
    confirmation_status = None

    return jsonify({
        "ok": True,
        "created": created,
        "duplicate": duplicate,
        "subscribed_to": subscribed_to,
        "summary": " · ".join(summary_bits),
        "confirmation": confirmation_status,
        "truncated_at_limit": truncated,
        "limit": limit,
    })


# ---------------------------------------------------------------------------
# /api/subscribe — record a notification preference
# ---------------------------------------------------------------------------

@app.route("/api/subscribe", methods=["POST"])
@login_required_api
def api_subscribe():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    email = (data.get("email") or "").strip()
    search_id = data.get("search_id")
    name = (data.get("name") or "").strip() or None
    phone = (data.get("phone") or "").strip() or None
    threshold = data.get("score_threshold") or 70

    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "valid email required"}), 400
    try:
        search_id = int(search_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "search_id required"}), 400
    try:
        threshold = max(0, min(100, int(threshold)))
    except (TypeError, ValueError):
        threshold = 70

    with get_conn() as conn:
        with conn:
            cur = conn.execute(
                """INSERT INTO subscribers
                   (name, email, phone, search_id, score_threshold)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (email, search_id) DO UPDATE SET
                     name = excluded.name,
                     phone = excluded.phone,
                     score_threshold = excluded.score_threshold,
                     active = 1
                   RETURNING id""",
                (name, email, phone, search_id, threshold),
            )
            sub_id = cur.fetchone()[0]
    return jsonify({
        "ok": True,
        "subscriber_id": sub_id,
        "message": (
            f"Subscribed {email} for deals scoring {threshold}+ "
            f"on search {search_id}."
        ),
    })


# ---------------------------------------------------------------------------
# /api/comps — read-only comp viewer. SQLite uses placeholder for the
#              interval too (Python concatenation-safe).
# ---------------------------------------------------------------------------

@app.route("/api/comps")
@login_required_api
def api_comps():
    term = (request.args.get("term") or "").strip()
    source = (request.args.get("source") or "marketplace").strip()
    try:
        ttl_seconds = int(request.args.get("ttl") or 12 * 3600)
    except (TypeError, ValueError):
        ttl_seconds = 12 * 3600
    if not term:
        return jsonify({"error": "missing 'term'"}), 400

    rows = []
    with get_conn() as conn:
        cur = conn.execute(
            f"""SELECT price, title, listing_url, location, fetched_at
                FROM comps
                WHERE search_term = ? AND source = ?
                  AND fetched_at >= datetime('now','-{int(ttl_seconds)} seconds')
                ORDER BY price ASC""",
            (term, source),
        )
        for r in cur.fetchall():
            rows.append({
                "price": float(r["price"]) if r["price"] is not None else None,
                "title": r["title"],
                "listing_url": r["listing_url"],
                "location": r["location"],
                "fetched_at": _iso(r["fetched_at"]),
            })

    if not rows:
        return jsonify({"term": term, "source": source, "rows": []})

    prices = [r["price"] for r in rows if r["price"] is not None]
    if not prices:
        return jsonify({"term": term, "source": source, "rows": rows})
    return jsonify({
        "term": term,
        "source": source,
        "sample_size": len(prices),
        "median": statistics.median(prices),
        "mean": statistics.fmean(prices),
        "min": min(prices),
        "max": max(prices),
        "rows": rows,
    })


# ---------------------------------------------------------------------------
# /api/settings — single-user home location (+ telemetry opt-out hook)
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET", "POST"])
@login_required_api
def api_settings():
    if request.method == "GET":
        with get_conn() as conn:
            row = conn.execute(
                """SELECT home_label, home_latitude, home_longitude, updated_at,
                          telemetry_opt_out
                   FROM user_settings WHERE user_id = 1"""
            ).fetchone()
        if not row:
            return jsonify({
                "home_label": None,
                "home_latitude": None,
                "home_longitude": None,
                "updated_at": None,
                "telemetry_opt_out": False,
            })
        return jsonify({
            "home_label": row["home_label"],
            "home_latitude": float(row["home_latitude"]) if row["home_latitude"] is not None else None,
            "home_longitude": float(row["home_longitude"]) if row["home_longitude"] is not None else None,
            "updated_at": _iso(row["updated_at"]),
            "telemetry_opt_out": bool(row["telemetry_opt_out"]),
        })

    # POST — upsert
    data = request.form if request.form else (request.get_json(silent=True) or {})
    label = (data.get("home_label") or "").strip() or None
    try:
        lat = float(data.get("home_latitude")) if data.get("home_latitude") not in (None, "") else None
        lng = float(data.get("home_longitude")) if data.get("home_longitude") not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lat/lng must be numbers"}), 400
    if lat is None or lng is None:
        return jsonify({"ok": False, "error": "home_latitude and home_longitude required"}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"ok": False, "error": "lat/lng out of range"}), 400

    with get_conn() as conn:
        with conn:
            conn.execute(
                """INSERT INTO user_settings
                       (user_id, home_label, home_latitude, home_longitude)
                   VALUES (1, ?, ?, ?)
                   ON CONFLICT (user_id) DO UPDATE SET
                       home_label = excluded.home_label,
                       home_latitude = excluded.home_latitude,
                       home_longitude = excluded.home_longitude,
                       updated_at = CURRENT_TIMESTAMP""",
                (label, lat, lng),
            )
    return jsonify({
        "ok": True,
        "home_label": label,
        "home_latitude": lat,
        "home_longitude": lng,
    })


def _resolve_home_location() -> tuple[float, float]:
    """Resolve the configured home lat/lng with Vancouver as fallback."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT home_latitude, home_longitude FROM user_settings WHERE user_id = 1"
            ).fetchone()
            if row and row[0] is not None and row[1] is not None:
                return float(row[0]), float(row[1])
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_LAT, DEFAULT_LNG


def _resolve_home_label() -> str | None:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT home_label FROM user_settings WHERE user_id = 1"
            ).fetchone()
            if row and row[0]:
                parts = [p.strip() for p in row[0].split(",")]
                if len(parts) >= 3:
                    return f"{parts[0]}, {parts[-2]}, {parts[-1]}"
                return row[0]
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# /api/geocode — Nominatim proxy (unchanged from the personal tool)
# ---------------------------------------------------------------------------

_GEOCODE_CACHE: dict[str, list[dict]] = {}
_GEOCODE_CACHE_MAX = 500


@app.route("/api/geocode", methods=["GET"])
@login_required_api
def api_geocode():
    import requests

    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify({"results": []})

    if q in _GEOCODE_CACHE:
        return jsonify({"results": _GEOCODE_CACHE[q]})

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": q, "format": "json", "limit": 6, "addressdetails": 0,
            },
            headers={
                "User-Agent": "bullseye-deal-finder/0.1 (github.com/reubenlavin08/bullseye)",
                "Accept-Language": "en",
            },
            timeout=4.0,
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        return jsonify({"results": [], "error": f"geocoder unreachable: {e}"}), 502

    results = []
    for item in raw[:6]:
        try:
            results.append({
                "label": item.get("display_name") or "",
                "lat": float(item["lat"]),
                "lng": float(item["lon"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    _GEOCODE_CACHE[q] = results
    if len(_GEOCODE_CACHE) > _GEOCODE_CACHE_MAX:
        for k in list(_GEOCODE_CACHE.keys())[:50]:
            _GEOCODE_CACHE.pop(k, None)

    return jsonify({"results": results})


# ---------------------------------------------------------------------------
# /appraise — Test Appraiser. Public-product surface; requires login since
#              it calls cloud comps which needs a JWT.
# ---------------------------------------------------------------------------

@app.route("/appraise", methods=["POST"])
@login_required_api
def appraise():
    """Run a comp lookup + score for a search term + asking price.

    The personal tool's /appraise/<listing_id> hit FB to fetch a real
    listing's description, ran the rejection filter, and persisted the
    appraisal. The product version is simpler: takes a free-text title +
    asking price, calls cloud /comps, returns a stub score. Once the
    scheduler is wired up to write to listings, the front-page "Test
    appraiser" tool can layer the listing-fetch step back on. For now
    this is the surface that proves cloud comps are reachable and lets
    us draw the deal-score widget.

    Body (JSON or form-encoded):
        title          str, required
        asking_price   float, required (the price the seller is asking)
        region         str, optional (default EBAY-ENCA)
    """
    data = request.get_json(silent=True) or request.form
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    try:
        asking_price = float(data.get("asking_price"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "asking_price must be numeric"}), 400
    region = (data.get("region") or "EBAY-ENCA").strip() or "EBAY-ENCA"

    t0 = time.perf_counter()
    try:
        comp = get_comps(title, region=region)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"comp fetch: {e}"}), 502
    elapsed_s = time.perf_counter() - t0

    sample_size = int(comp.get("sample_size") or 0)
    median = float(comp.get("median") or 0) or None

    if sample_size < 3 or not median:
        return jsonify({
            "ok": True,
            "unscoreable": True,
            "reason": "insufficient comparable listings",
            "search_term": comp.get("search_term"),
            "sample_size": sample_size,
            "median": median,
            "comp_source": comp.get("source"),
            "raw_comps": comp.get("raw_comps") or [],
            "elapsed_s": round(elapsed_s, 3),
        })

    # Mirrors deal_finder.appraisal.formula at a high level: fair_value
    # is the trimmed median × 0.85, deal_score is 100 × (1 − pct_rank)
    # where pct_rank is where the asking price falls in the comp set.
    fair_value = median * 0.85
    raw_prices = sorted(
        float(c.get("price")) for c in (comp.get("raw_comps") or [])
        if c.get("price") is not None
    )
    if raw_prices:
        below = sum(1 for p in raw_prices if p < asking_price)
        pct_rank = below / len(raw_prices)
    else:
        pct_rank = 0.5
    deal_score = max(0, min(100, int(round(100 * (1 - pct_rank)))))

    return jsonify({
        "ok": True,
        "unscoreable": False,
        "deal_score": deal_score,
        "fair_value": round(fair_value, 2),
        "asking_price": asking_price,
        "search_term": comp.get("search_term"),
        "comp_source": comp.get("source"),
        "comp_median": median,
        "comp_sample_size": sample_size,
        "raw_comps": comp.get("raw_comps") or [],
        "elapsed_s": round(elapsed_s, 3),
    })


# ---------------------------------------------------------------------------
# /api/account/* — paid-only on delete? Account delete should always be
#                  available so a user can leave; same for export.
# ---------------------------------------------------------------------------

@app.route("/api/account/delete", methods=["POST"])
@login_required_api
def api_account_delete():
    """Trigger a server-side account delete. Cloud function clears the
    user's row in subscribers / user_searches across all devices and
    revokes their refresh token. Local data stays intact until the user
    uninstalls — we only nuke the keyring tokens here so subsequent
    cloud calls go anonymous."""
    from deal_finder.cloud.client import client as cloud_client, CloudError, CloudUnavailable
    try:
        resp = cloud_client.post("account-delete", {})
    except CloudUnavailable as e:
        return jsonify({"ok": False, "error": f"cloud unavailable: {e}"}), 503
    except CloudError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    token_store.clear()
    license_manager.invalidate()
    return jsonify({"ok": True, "cloud": resp})


@app.route("/api/account/export", methods=["GET"])
@login_required_api
def api_account_export():
    """Return the user's data dump from the cloud (GDPR/CCPA hook)."""
    from deal_finder.cloud.client import client as cloud_client, CloudError, CloudUnavailable
    try:
        resp = cloud_client.post("account-export", {})
    except CloudUnavailable as e:
        return jsonify({"ok": False, "error": f"cloud unavailable: {e}"}), 503
    except CloudError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(resp)


# ---------------------------------------------------------------------------
# /api/license/refresh — force a license re-check (used after Stripe
#                        checkout returns).
# ---------------------------------------------------------------------------

@app.route("/api/license/refresh", methods=["POST"])
@login_required_api
def api_license_refresh():
    license_manager.invalidate()
    data = license_manager.get(force_refresh=True)
    return jsonify({"ok": True, "license": data})


# ---------------------------------------------------------------------------
# Static fallback for /favicon.ico so we don't 404-spam the log.
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    return ("", 204)


# ---------------------------------------------------------------------------
# Server runner used by main.py. Defaults to a random port; main.py picks
# one and passes it in. We never expose to 0.0.0.0 — localhost only.
# ---------------------------------------------------------------------------

def run(*, port: int = 5000) -> None:
    """Boot the Flask app on the given port. Used by `main.py`'s thread
    starter. We disable the reloader so daemon-thread startup doesn't
    spawn a child and the WAL connection stays sane."""
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":  # pragma: no cover
    run()
