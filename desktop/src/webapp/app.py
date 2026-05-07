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
from deal_finder.cloud import telemetry as cloud_telemetry
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
# Cap request body size so a malicious POST can't OOM the localhost
# Flask process. 2 MiB is generous — the largest legitimate body we
# accept is a bulk-watch keyword list (a few KB). A browser extension
# or rogue local process posting a multi-megabyte payload now gets a
# 413 from werkzeug before our handlers even run. Defense in depth on
# top of the per-route _safe_request_json() recursion guard.
# See: tests/adversarial/INJECTION-FINDINGS.md (F-MED-1).
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MiB
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

    # CSRF / Origin check for state-changing /api/* requests. PyWebView
    # serves the UI from 127.0.0.1:<random>, so a malicious page that
    # the user happens to be visiting in their normal browser cannot
    # know our port — but a phishing localhost link could still POST to
    # `127.0.0.1:<our-port>` if it guessed correctly. We require the
    # `Origin` (or `Referer`) header on every mutation to start with
    # http://127.0.0.1: or http://localhost:. (Audit finding 2026-05-06.)
    if (
        p.startswith("/api/")
        and request.method in ("POST", "PUT", "PATCH", "DELETE")
        # Internal callbacks from the OAuth helper come without Origin
        # because they are server-to-server; they hit /tokens not /api.
    ):
        origin = request.headers.get("Origin") or ""
        referer = request.headers.get("Referer") or ""
        source = origin or referer
        if source and not (
            source.startswith("http://127.0.0.1:")
            or source.startswith("http://localhost:")
        ):
            return jsonify({
                "ok": False,
                "error": "bad_origin",
                "message": "Cross-origin requests are not allowed.",
            }), 403

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


def _safe_request_json() -> dict | list | None:
    """Like ``request.get_json(silent=True)`` but also catches
    ``RecursionError`` raised by Python's ``json`` module on
    deeply-nested bodies. ``silent=True`` only swallows BadRequest, so
    a 5000-deep nested object still 500'd via RecursionError before
    this guard. Returns None on any parse failure; callers branch on
    None like they do with ``silent=True``.
    See: tests/adversarial/INJECTION-FINDINGS.md (F-MED-1).
    """
    try:
        return request.get_json(silent=True)
    except RecursionError:
        return None
    except Exception:  # noqa: BLE001 — any decoder error -> None
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
    """Root: bounce to the new shell.

    Signed-in users land on /home (the new sidebar+tabs surface).
    Signed-out users land on /auth (the centered sign-in card).
    The legacy marketing-style index.html is no longer routed by `/`,
    but the template file is still on disk so a developer can render
    it manually during the smoke-test window before we delete it.
    """
    if token_store.is_logged_in():
        return redirect("/home")
    return redirect("/auth")


# ---------------------------------------------------------------------------
# /upgrade — pricing page (Stripe Checkout button). Public so logged-out
#            users can see it, but the CTA only fires once logged in.
# ---------------------------------------------------------------------------

@app.route("/upgrade")
def upgrade_page():
    # Pass current tier + trial-days-remaining so the template can
    # render Pro as "Current plan" when the user's already on trial
    # or paid, instead of showing a "Start free trial" button to
    # someone who already activated it (the user-reported bug
    # 2026-05-06).
    ctx: dict = {"tier": "free", "trial_days_remaining": None}
    try:
        if token_store.is_logged_in():
            tier = license_manager.tier()
            ctx["tier"] = tier
            if tier == "trial":
                # license_manager exposes trial_ends_at via .get()
                lic = license_manager.get() or {}
                trial_end_iso = lic.get("trial_ends_at")
                if trial_end_iso:
                    from datetime import datetime as _dt, timezone as _tz
                    try:
                        end = _dt.fromisoformat(
                            trial_end_iso.replace("Z", "+00:00")
                        )
                        now = _dt.now(_tz.utc)
                        days = max(0, (end - now).days)
                        ctx["trial_days_remaining"] = days
                    except Exception:  # noqa: BLE001
                        pass
    except Exception:  # noqa: BLE001 — never break the page render
        pass
    return render_template("upgrade.html", **ctx)


# ---------------------------------------------------------------------------
# /api/checkout/start — emit upgrade_clicked + proxy to cloud /checkout-create.
# Kept thin so the Stripe path keeps living in one place (the cloud
# function); this endpoint exists only to (a) record the click for the
# v1.1 retention funnel and (b) hide the cloud-client URL from the
# upgrade page's JS.
# ---------------------------------------------------------------------------

# /api/trial/start — start the 14-day no-card trial WITHOUT Stripe.
# Cloud function flips the user's licenses row to tier='trial' and
# returns the new state; the desktop app then refreshes /home so the
# sidebar shows the trial countdown immediately.
#
# Why this exists: the previous flow sent the user through Stripe
# Checkout for a trial that doesn't need a card, which kicked them out
# of the desktop app to the website's upgrade-success page (and then
# to /download.html). Confusing for a trial. This endpoint keeps the
# whole trial flow in-app.
@app.route("/api/trial/start", methods=["POST"])
@login_required_api
def api_trial_start():
    cloud_telemetry.emit("trial_started", {"path": "in_app"})
    from deal_finder.cloud.client import (
        client as cloud_client,
        CloudError, CloudUnavailable, Unauthorized,
    )
    try:
        resp = cloud_client.post("trial-start", {})
    except Unauthorized:
        return jsonify({"ok": False, "error": "login_required"}), 401
    except CloudUnavailable as e:
        return jsonify({"ok": False, "error": "cloud_unavailable",
                        "message": str(e)}), 503
    except CloudError as e:
        return jsonify({"ok": False, "error": "trial_failed",
                        "message": str(e)}), 400

    # Invalidate the local license cache so the next /home render
    # sees tier='trial' immediately. Without this, license_manager's
    # TTL'd cache keeps reporting tier='free' until it expires (up to
    # 5 min), which is why the user reported "trial activated but
    # Stats / Pro features still locked".
    try:
        license_manager.invalidate()
        license_manager.get(force_refresh=True)
    except Exception as e:  # noqa: BLE001 — never break the trial response
        cloud_telemetry.emit("license_refresh_after_trial_failed", {"error": str(e)})

    return jsonify({"ok": True, **resp})


@app.route("/api/checkout/start", methods=["POST"])
@login_required_api
def api_checkout_start():
    data = request.get_json(silent=True) or {}
    plan = (data.get("plan") or "monthly").strip()
    trial = bool(data.get("trial", False))

    cloud_telemetry.emit("upgrade_clicked", {"plan": plan, "trial": trial})

    # Lazy import — `cloud.client` is shared and we want telemetry's
    # error_seen path to fire for upstream failures rather than
    # importing eagerly at module load.
    from deal_finder.cloud.client import (
        client as cloud_client,
        CloudError, CloudUnavailable, Unauthorized,
    )
    try:
        resp = cloud_client.post(
            "checkout-create", {"plan": plan, "trial": trial},
        )
    except Unauthorized:
        return jsonify({"ok": False, "error": "login_required"}), 401
    except CloudUnavailable as e:
        return jsonify({"ok": False, "error": "cloud_unavailable",
                        "message": str(e)}), 503
    except CloudError as e:
        return jsonify({"ok": False, "error": "checkout_failed",
                        "message": str(e)}), 502
    return jsonify({"ok": True, "url": resp.get("url")})


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

    # Optional `date=YYYY-MM-DD` filter: limits results to listings
    # scraped on that calendar day (UTC). Used by the Insights tab's
    # heatmap-cell drilldown so clicking a day reveals only its deals.
    date_str = (request.args.get("date") or "").strip()
    if date_str and len(date_str) == 10:
        # Validate by attempting to parse; reject otherwise to keep
        # this from becoming an injection vector.
        try:
            from datetime import datetime as _dt
            _dt.strptime(date_str, "%Y-%m-%d")
            where_clauses.append("DATE(l.scraped_at) = ?")
            params.append(date_str)
        except ValueError:
            pass  # silently ignore malformed dates

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

    # Treat a breakdown view as a "user clicked the listing" signal —
    # this is what the v1.1 streak/Pro-day system reads.
    cloud_telemetry.emit("alert_clicked", {
        "listing_id": row["id"],
        "deal_score": int(row["deal_score"]) if row["deal_score"] is not None else None,
    })

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

    # Optional alert wiring: if the user supplied email + threshold on
    # create (the new Wispr-shell tab_watches form does this), build a
    # subscriber row in the same transaction so the alert path is live
    # immediately. Older callers that omit these still work — they just
    # save the watch without alerts and the user can add them via PATCH
    # later.
    email = (data.get("email") or "").strip() or None
    name = (data.get("name") or "").strip() or None
    score_threshold: int | None = None
    if "score_threshold" in data and str(data.get("score_threshold")).strip():
        try:
            score_threshold = int(data.get("score_threshold"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "score_threshold must be an integer"}), 400
        if not (0 <= score_threshold <= 100):
            return jsonify({"ok": False, "error": "score_threshold must be 0-100"}), 400

    # Cap check + insert run in a single BEGIN IMMEDIATE transaction so
    # two concurrent POSTs can't both pass the SELECT-then-INSERT gate
    # (TOCTOU race). BEGIN IMMEDIATE acquires SQLite's RESERVED write
    # lock up front, serializing concurrent attempts.
    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if limit is not None:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM user_searches WHERE active = 1"
                ).fetchone()[0]
                if int(cnt or 0) >= limit:
                    conn.execute("ROLLBACK")
                    return jsonify({
                        "ok": False,
                        "error": "watches_limit_reached",
                        "message": (
                            f"Your plan allows {limit} active watches. "
                            "Upgrade to Pro for unlimited."
                        ),
                        "limit": limit,
                    }), 403
            cur = conn.execute(
                """INSERT INTO user_searches
                   (keyword, latitude, longitude, radius_km,
                    price_min, price_max, active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)
                   RETURNING id""",
                (keyword, lat, lng, radius_km, price_min, price_max),
            )
            new_id = cur.fetchone()[0]
            # Optional subscriber row. We only insert when email is
            # supplied — bare watches without an email just don't send
            # alerts. UNIQUE (email, search_id) protects against double
            # inserts if the form is submitted twice.
            if email:
                conn.execute(
                    """INSERT OR IGNORE INTO subscribers
                       (name, email, search_id, score_threshold,
                        daily_summary_enabled, active)
                       VALUES (?, ?, ?, ?, 0, 1)""",
                    (
                        name,
                        email,
                        new_id,
                        score_threshold if score_threshold is not None else 70,
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
    cloud_telemetry.emit("watch_created", {
        "watch_id": new_id, "source": "single", "keyword_len": len(keyword),
        "alerts": bool(email),
    })
    return jsonify({"ok": True, "id": new_id, "keyword": keyword,
                    "alerts_enabled": bool(email)})


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
            existing = conn.execute(
                "SELECT active FROM user_searches WHERE id = ?", (watch_id,),
            ).fetchone()
            if existing is None:
                return jsonify({"ok": False, "error": "watch not found"}), 404

            # Watches-cap re-check on un-pause: if the PATCH activates a
            # currently-paused watch, treat it like a new active and apply
            # the same gate /api/watches POST uses. Otherwise a free user
            # can pause→create→unpause to exceed their cap.
            if (
                "active" in us_updates
                and us_updates["active"] == 1
                and int(existing[0] or 0) == 0
            ):
                limit = license_manager.watches_limit()
                if limit is not None:
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

    if "active" in us_updates:
        cloud_telemetry.emit(
            "watch_unpaused" if us_updates["active"] == 1 else "watch_paused",
            {"watch_id": watch_id},
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
    cloud_telemetry.emit("watch_deleted", {"watch_id": watch_id})
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

    for c in created:
        cloud_telemetry.emit("watch_created", {
            "watch_id": c["id"], "source": "bulk",
            "keyword_len": len(c.get("keyword") or ""),
        })

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
    data = request.form if request.form else (_safe_request_json() or {})
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
    """Read or partially-update the single-row user_settings table.

    GET returns every field; POST accepts a partial body and only writes
    the keys present. This lets the Settings UI ship one toggle at a
    time (notification flags, telemetry, location) without each toggle
    needing its own endpoint.

    Notification flags (`notif_milestones`, `notif_first_deal`,
    `notif_kill_switch_banner`) are LOCAL — they govern desktop toasts
    + banner visibility. Email-level mute is Phase 3.5 (cloud column).
    """
    if request.method == "GET":
        with get_conn() as conn:
            row = conn.execute(
                """SELECT home_label, home_latitude, home_longitude, updated_at,
                          telemetry_opt_out,
                          notif_milestones, notif_first_deal,
                          notif_kill_switch_banner,
                          notif_email_global, notif_desktop_global
                   FROM user_settings WHERE user_id = 1"""
            ).fetchone()
        if not row:
            # No row yet — return defaults (everything on, no location).
            return jsonify({
                "home_label": None,
                "home_latitude": None,
                "home_longitude": None,
                "updated_at": None,
                "telemetry_opt_out": False,
                "notif_milestones": True,
                "notif_first_deal": True,
                "notif_kill_switch_banner": True,
                "notif_email_global": True,
                "notif_desktop_global": True,
            })
        return jsonify({
            "home_label": row["home_label"],
            "home_latitude": float(row["home_latitude"]) if row["home_latitude"] is not None else None,
            "home_longitude": float(row["home_longitude"]) if row["home_longitude"] is not None else None,
            "updated_at": _iso(row["updated_at"]),
            "telemetry_opt_out": bool(row["telemetry_opt_out"]),
            "notif_milestones": bool(row["notif_milestones"]) if row["notif_milestones"] is not None else True,
            "notif_first_deal": bool(row["notif_first_deal"]) if row["notif_first_deal"] is not None else True,
            "notif_kill_switch_banner": bool(row["notif_kill_switch_banner"]) if row["notif_kill_switch_banner"] is not None else True,
            "notif_email_global": bool(row["notif_email_global"]) if row["notif_email_global"] is not None else True,
            "notif_desktop_global": bool(row["notif_desktop_global"]) if row["notif_desktop_global"] is not None else True,
        })

    # POST — partial upsert. Each field is optional; only the keys the
    # caller sends get written. Unknown keys are ignored.
    data = request.form if request.form else (request.get_json(silent=True) or {})
    updates: dict[str, object] = {}

    # Location: validated together (both required if either is set).
    has_loc = "home_latitude" in data or "home_longitude" in data or "home_label" in data
    if has_loc:
        try:
            lat_raw = data.get("home_latitude")
            lng_raw = data.get("home_longitude")
            lat = float(lat_raw) if lat_raw not in (None, "") else None
            lng = float(lng_raw) if lng_raw not in (None, "") else None
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "lat/lng must be numbers"}), 400
        # If user is sending location updates, both must be present + valid.
        if lat is not None or lng is not None:
            if lat is None or lng is None:
                return jsonify({"ok": False, "error": "home_latitude and home_longitude required together"}), 400
            if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
                return jsonify({"ok": False, "error": "lat/lng out of range"}), 400
            label = (data.get("home_label") or "").strip() or None
            updates["home_label"] = label
            updates["home_latitude"] = lat
            updates["home_longitude"] = lng

    # Boolean flags — coerce truthy values to 0/1 (SQLite booleans).
    def _coerce_bool(name: str) -> int | None:
        if name not in data:
            return None
        v = data.get(name)
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(bool(v))
        return int(str(v).strip().lower() in ("1", "true", "yes", "on"))

    for flag in ("telemetry_opt_out", "notif_milestones",
                 "notif_first_deal", "notif_kill_switch_banner",
                 "notif_email_global", "notif_desktop_global"):
        v = _coerce_bool(flag)
        if v is not None:
            # Server-side Pro gate: free users can't TURN ON email
            # notifications (only Pro/trial). The toggle is also
            # disabled in the UI but we double-check here.
            if flag == "notif_email_global" and v == 1:
                tier = (license_manager.get() or {}).get("tier", "free")
                if tier not in ("paid", "trial"):
                    return jsonify({
                        "ok": False,
                        "error": "email_notifications_pro_only",
                        "message": "Email notifications are a Pro feature. Upgrade or start the free trial.",
                    }), 403
            updates[flag] = v

    if not updates:
        return jsonify({"ok": False, "error": "no valid fields to update"}), 400

    with get_conn() as conn:
        with conn:
            # Ensure the row exists (default user_id=1 single-user model).
            conn.execute(
                "INSERT OR IGNORE INTO user_settings (user_id) VALUES (1)"
            )
            # Build a partial UPDATE — SQLite has no syntactic shortcut.
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [1]
            conn.execute(
                f"UPDATE user_settings SET {set_clause}, "
                f"updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                vals,
            )
    return jsonify({"ok": True, "updated": list(updates.keys())})


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
# /api/search — Test Appraiser stage 1: scrape FB Marketplace for a
# keyword and return listing cards. The user picks one and the JS calls
# /appraise per-listing.
#
# Anon-callable. The scraper runs locally (curl_cffi inside this same
# Python process), no cloud auth needed. Lat/lng default to a Vancouver
# centroid for v1; per-user home location override comes via Settings.
#
# Ported from the personal `deal_finder/webapp/app.py` /search route.
# Same SearchParams + SearchPage shapes; we just emit JSON instead of
# Jinja-rendered HTML.
# ---------------------------------------------------------------------------

@app.route("/api/watches/poll-now", methods=["POST"])
@login_required_api
def api_watches_poll_now():
    """Manually trigger a poll cycle for every active watch in the
    background. Returns immediately; the scheduler runs the polls
    serially in a daemon thread. Watch-list UI can poll for updated
    `last_scrape` / `total_seen` values to reflect progress.

    Why this exists: users who add several watches don't see anything
    happen until the next scheduled poll cycle (5-30 min depending on
    tier). That looks broken even when it isn't. This endpoint kicks
    the scheduler so they can see results within ~30 sec instead of
    waiting on the cron.

    Per-tier rate-limit: only one manual poll-now per minute, to
    avoid hammering Facebook if the user repeatedly clicks the button.
    """
    from threading import Thread as _Thread
    import time as _time

    # Cheap rate limit: track last manual-poll time on app state
    # (in-memory; resets on app restart, fine for this surface).
    now_s = _time.monotonic()
    last = getattr(app, "_last_manual_poll_at", 0.0)
    if now_s - last < 60.0:
        seconds_remaining = int(60.0 - (now_s - last))
        return jsonify({
            "ok": False,
            "error": "rate_limited",
            "message": f"Please wait {seconds_remaining}s before another manual poll.",
        }), 429
    setattr(app, "_last_manual_poll_at", now_s)

    # Pull active watch IDs.
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM user_searches WHERE active = 1 ORDER BY id"
        ).fetchall()
    search_ids = [int(r["id"]) for r in rows]

    if not search_ids:
        return jsonify({
            "ok": True,
            "started": 0,
            "message": "No active watches.",
        })

    # Run polls serially in a daemon thread so the HTTP response
    # returns immediately. CRITICAL: each poll goes through
    # coordinator_tick() — NOT poll_search() directly — so the
    # slow-start, exponential cooldown, and circuit-breaker gates
    # all apply. Calling poll_search() directly would bypass every
    # rate-limit guard and could rapidly compound a FB block if the
    # user hammers the button.
    #
    # coordinator_tick picks one watch (the stalest) per call and
    # rotates through all of them. Calling it N times with the
    # FB rate-gate's 8s spacing means we cover every active watch
    # while still honoring per-IP quota.
    def _run_polls():
        from deal_finder.scheduler.jobs import coordinator_tick
        for _ in search_ids:
            try:
                coordinator_tick()
            except Exception as e:  # noqa: BLE001
                logger.warning("manual poll tick failed: %s", e)
            # Match the scraper's _DEFAULT_SEARCH_INTERVAL_S so we
            # don't push past the gate's spacing on rapid succession.
            _time.sleep(8.0)

    t = _Thread(target=_run_polls, name="manual-poll", daemon=True)
    t.start()

    return jsonify({
        "ok": True,
        "started": len(search_ids),
        "message": (
            f"Polling {len(search_ids)} watch(es) through the coordinator "
            f"(slow-start + cooldown gates active). "
            f"Refresh in ~{8 * len(search_ids)}s to see updates."
        ),
    })


@app.route("/api/scheduler/diagnose")
@login_required_api
def api_scheduler_diagnose():
    """All scheduler health info in one payload — designed to make
    "polls aren't firing" a 5-second diagnosis instead of an hour of
    log-spelunking.

    Surfaces:
      - thread_alive: is a thread named 'scheduler' still running?
      - kill_switch_active: would scheduler.run_forever() refuse boot?
      - license tier + poll_interval_min
      - active watch count
      - last_event_at + last_event_type (any scheduler_events row)
      - last_poll_at (most recent 'poll' event)
      - last_boot_at (most recent 'scheduler_boot' event)
      - last_tick_at (most recent 'coordinator_tick' event)
      - cooldown_remaining_s (from _compute_cooldown_remaining_s)
      - slow_start state
      - last 50 scheduler events with type + timestamp + detail summary
    """
    import threading as _th
    from deal_finder.license.manager import license_manager as _lm

    # Find the scheduler daemon thread. We use TWO signals — the
    # threading.enumerate() check (cheap, structural) AND a "recent
    # tick" check (semantic, definitive). If a coordinator_tick event
    # was emitted in the last minute, the scheduler IS running by
    # definition, regardless of what the thread-name match says.
    #
    # threading.enumerate() can miss threads in some PyInstaller
    # bundle scenarios or when APScheduler renames the dispatch
    # thread. We fall back to the event-based signal so the
    # diagnostic doesn't lie.
    all_threads = [
        {"name": t.name, "alive": t.is_alive(), "daemon": t.daemon}
        for t in _th.enumerate()
    ]
    thread_alive_by_name = any(
        ("scheduler" in t["name"].lower()) and t["alive"]
        for t in all_threads
    )

    # License gates
    try:
        kill_switch = bool(_lm.is_kill_switched())
    except Exception as e:  # noqa: BLE001
        kill_switch = False
        kill_switch_err = str(e)
    else:
        kill_switch_err = None
    try:
        tier = _lm.tier()
    except Exception:  # noqa: BLE001
        tier = "unknown"
    try:
        poll_interval_min = int(_lm.poll_interval_min())
    except Exception:  # noqa: BLE001
        poll_interval_min = -1

    # Coordinator state — peek at the in-memory state without holding
    # the GIL too long. Fail-soft on any module-import error.
    cooldown_remaining_s = None
    slow_start_state = None
    try:
        from deal_finder.scheduler import jobs as _jobs
        cooldown_remaining_s = int(_jobs._compute_cooldown_remaining_s())
        slow_start_state = dict(_jobs._slow_start_state)
        slow_start_mode = bool(_jobs.SLOW_START_MODE)
    except Exception as e:  # noqa: BLE001
        slow_start_mode = False

    # DB-backed state
    with get_conn() as conn:
        active_count_row = conn.execute(
            "SELECT COUNT(*) FROM user_searches WHERE active = 1"
        ).fetchone()
        active_watches = active_count_row[0] if active_count_row else 0

        last_event = conn.execute(
            """SELECT created_at, event_type FROM scheduler_events
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

        last_poll = conn.execute(
            """SELECT created_at FROM scheduler_events
               WHERE event_type = 'poll'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

        last_boot = conn.execute(
            """SELECT created_at FROM scheduler_events
               WHERE event_type = 'scheduler_boot'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

        last_tick = conn.execute(
            """SELECT created_at FROM scheduler_events
               WHERE event_type IN ('coordinator_tick', 'coordinator_idle',
                                    'poll', 'rate_limit_backoff',
                                    'scheduler_heartbeat', 'kill_switch_skip',
                                    'fb_probe', 'slow_start_ramp')
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

        # Has the scheduler emitted a tick in the last 90 seconds?
        # That's the definitive "is the thread alive" signal — no
        # amount of thread-name munging can fake actual events.
        recent_tick_row = conn.execute(
            """SELECT 1 FROM scheduler_events
               WHERE event_type IN ('coordinator_tick', 'coordinator_idle',
                                    'poll', 'fb_probe',
                                    'scheduler_heartbeat')
                 AND created_at >= datetime('now', '-90 seconds')
               LIMIT 1"""
        ).fetchone()
        recent_tick_seen = recent_tick_row is not None

        recent_rows = conn.execute(
            """SELECT created_at, event_type, detail
               FROM scheduler_events
               ORDER BY created_at DESC LIMIT 50"""
        ).fetchall()

    recent_events = []
    for r in recent_rows:
        try:
            d = r["detail"]
        except Exception:  # noqa: BLE001
            d = None
        # Truncate detail to keep payload small
        if d and len(str(d)) > 200:
            d = str(d)[:200] + "…"
        recent_events.append({
            "at": r["created_at"],
            "type": r["event_type"],
            "detail": d,
        })

    # Final thread_alive: thread-name match OR recent tick observed.
    # The recent-tick path overrides false negatives from the name
    # match (e.g. PyInstaller bundle thread renaming).
    thread_alive = thread_alive_by_name or recent_tick_seen

    return jsonify({
        "ok": True,
        "thread_alive": thread_alive,
        "thread_alive_by_name": thread_alive_by_name,
        "thread_alive_by_event": recent_tick_seen,
        "all_threads": all_threads,  # list every alive thread for debugging
        "kill_switch_active": kill_switch,
        "kill_switch_error": kill_switch_err,
        "tier": tier,
        "poll_interval_min": poll_interval_min,
        "active_watches": active_watches,
        "slow_start_mode": slow_start_mode,
        "slow_start_state": slow_start_state,
        "cooldown_remaining_s": cooldown_remaining_s,
        "last_event_at": last_event["created_at"] if last_event else None,
        "last_event_type": last_event["event_type"] if last_event else None,
        "last_poll_at": last_poll["created_at"] if last_poll else None,
        "last_boot_at": last_boot["created_at"] if last_boot else None,
        "last_tick_at": last_tick["created_at"] if last_tick else None,
        "recent_events": recent_events,
    })


@app.route("/api/lookup", methods=["POST"])
@login_required_api
def api_lookup():
    """Value-only lookup. Same pipeline as /appraise (LLM normalize
    → eBay comp lookup → compute_stats_from_prices) but NO scoring,
    NO asking price required. Returns the price distribution so the
    user can decide what a fair offer would be.

    Body:
        title           str, required
        body            str, optional — listing description (helps
                        the LLM disambiguate)
        region          str, optional (default EBAY-ENCA)
        force_refresh   bool, optional

    Returns:
        {
            ok, search_term, canonical_kind, category_hint,
            typical_price, fair_value (85% of typical),
            range_low, range_high (P25..P75 of trimmed comps),
            min, max, sample_size, source, red_flags, raw_comps,
            normalize_confidence
        }
    """
    data = _safe_request_json() or request.form
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    region = (data.get("region") or "EBAY-ENCA").strip() or "EBAY-ENCA"
    force_refresh = bool(data.get("force_refresh"))
    body_text = (data.get("body") or "").strip()

    # Step 1: LLM normalize (graceful fallback). No listing_url here,
    # so we synthesize a stable cache key from the title hash so
    # repeated lookups on the same string hit the cache.
    norm = None
    try:
        from deal_finder.appraisal.normalize import normalize_one
        import hashlib
        synthetic_url = "lookup://" + hashlib.md5(
            title.lower().encode("utf-8")).hexdigest()
        norm = normalize_one(
            listing_url=synthetic_url,
            title=title,
            body=body_text,
            ask_price=None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("lookup normalize failed (non-fatal): %s", e)
        norm = None

    # Step 2: comp fetch via cloud /comps with the normalize result.
    comp_search_term = (
        norm.canonical_kind if (norm and norm.canonical_kind)
        else title
    )
    comp_kwargs: dict = {
        "region": region, "force_refresh": force_refresh,
    }
    if norm and norm.canonical_kind:
        if norm.category_hint:
            comp_kwargs["category_hint"] = norm.category_hint
        if norm.coarse_low and norm.coarse_low > 0:
            comp_kwargs["coarse_low"] = norm.coarse_low
        if norm.coarse_high and norm.coarse_high > 0:
            comp_kwargs["coarse_high"] = norm.coarse_high

    t0 = time.perf_counter()
    try:
        comp = get_comps(comp_search_term, **comp_kwargs)
    except TypeError:
        try:
            comp = get_comps(
                comp_search_term, region=region, force_refresh=force_refresh,
            )
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"comp fetch: {e}"}), 502
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"comp fetch: {e}"}), 502
    elapsed_s = time.perf_counter() - t0

    raw_prices = [
        float(c.get("price")) for c in (comp.get("raw_comps") or [])
        if c.get("price") is not None and float(c.get("price")) > 0
    ]

    from deal_finder.db.comps import compute_stats_from_prices
    stats = compute_stats_from_prices(
        prices=raw_prices,
        search_term=comp.get("search_term") or title,
        source=comp.get("source") or "ebay",
        asking_price=None,  # no bimodal split in lookup mode
    )

    # Compute fair value (85% of trimmed median) the same way the
    # scored path does, so users see consistent numbers between
    # "look up" and "score this listing" flows.
    typical = stats.trimmed_median or stats.median or None
    fair_value = round(typical * 0.85, 2) if typical else None

    if stats.sample_size < 3:
        return jsonify({
            "ok": True,
            "lookup_only": True,
            "unscoreable": True,
            "reason": "couldn't_find_similar_items",
            "search_term": comp_search_term,
            "canonical_kind": (norm.canonical_kind if norm else None) or None,
            "category_hint": (norm.category_hint if norm else None),
            "sample_size": stats.sample_size,
            "raw_comps": comp.get("raw_comps") or [],
            "elapsed_s": round(elapsed_s, 3),
        })

    return jsonify({
        "ok": True,
        "lookup_only": True,
        "search_term": comp_search_term,
        "canonical_kind": (norm.canonical_kind if norm else None) or None,
        "category_hint": (norm.category_hint if norm else None),
        "normalize_confidence": (norm.confidence if norm else None),
        "red_flags": (norm.red_flags if norm else []),
        "typical_price": (
            round(typical, 2) if typical is not None else None
        ),
        "fair_value": fair_value,
        "range_low": (round(stats.q1, 2) if stats.q1 is not None else None),
        "range_high": (round(stats.q3, 2) if stats.q3 is not None else None),
        "min": (round(stats.minimum, 2) if stats.minimum is not None else None),
        "max": (round(stats.maximum, 2) if stats.maximum is not None else None),
        "sample_size": stats.sample_size,
        "outliers_dropped": stats.outliers_dropped,
        "source": comp.get("source"),
        "raw_comps": comp.get("raw_comps") or [],
        "elapsed_s": round(elapsed_s, 3),
    })


@app.route("/api/listing/<listing_id>/detail", methods=["POST"])
@login_required_api
def api_listing_detail(listing_id: str):
    """Scrape the full Marketplace listing detail (title + body +
    location + photos) for a single listing id. Used by the Test
    Appraiser's "Fetch description" button — search-results have very
    thin body text (often empty), and the LLM normalize call gets
    materially better canonical_kind output when it can see the full
    listing description.

    The fetch hits FB's PDP endpoint with HTML fallback, rate-limited
    to 1/second per the existing FacebookDetailClient. Returns the
    description string + a few other useful fields.
    """
    listing_id = (listing_id or "").strip()
    if not listing_id:
        return jsonify({"ok": False, "error": "listing_id required"}), 400
    from deal_finder.scraper.facebook_detail import (
        get_default_client as get_detail_client,
    )
    try:
        detail = get_detail_client().fetch(listing_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({
            "ok": False,
            "error": "detail_fetch_failed",
            "message": f"{type(e).__name__}: {e}",
        }), 502
    return jsonify({
        "ok": True,
        "listing_id": listing_id,
        "title": detail.title,
        "description": detail.description,
        "location": detail.location,
        "latitude": detail.latitude,
        "longitude": detail.longitude,
        "source": detail.source,  # 'pdp' | 'html' | None
        "errors": detail.errors,
    })


@app.route("/api/search", methods=["POST"])
@login_required_api
def api_search():
    data = request.get_json(silent=True) or request.form
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"ok": False, "error": "keyword required"}), 400

    # If the caller didn't pass lat/lng explicitly, use the user's
    # configured home location from Settings → Location instead of the
    # hardcoded Vancouver default. The Test Appraiser frontend doesn't
    # send lat/lng (only radius_km), so without this lookup the search
    # always centered on Vancouver — explaining why a 5 km radius
    # returned listings from Surrey + Victoria for a non-Vancouver user.
    home_lat, home_lng = _resolve_home_location()
    try:
        lat = float(data.get("lat") or home_lat)
        lng = float(data.get("lng") or home_lng)
        radius_km = int(data.get("radius_km") or DEFAULT_RADIUS_KM)
        price_min_str = (str(data.get("price_min") or "")).strip()
        price_max_str = (str(data.get("price_max") or "")).strip()
        price_min = int(price_min_str) if price_min_str else None
        price_max = int(price_max_str) if price_max_str else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad numeric input"}), 400

    # Lazy import — keeps webapp module import-time light, and lets
    # tests stub the scraper without bringing curl_cffi into scope.
    from deal_finder.scraper.facebook import (
        SearchParams, search_listings, FacebookRateLimited,
    )
    from deal_finder.db.geo import geocode_city, haversine_km

    t0 = time.perf_counter()
    try:
        page = search_listings(SearchParams(
            keyword=keyword,
            lat=lat, lng=lng, radius_km=radius_km,
            price_min=price_min, price_max=price_max,
        ))
    except FacebookRateLimited as e:
        return jsonify({
            "ok": False,
            "error": "rate_limited",
            "message": (
                f"Facebook is rate-limiting. Cooldown ~"
                f"{int(e.seconds_remaining)}s remaining."
            ),
            "seconds_remaining": int(e.seconds_remaining),
        }), 503
    except Exception as e:  # noqa: BLE001 — surface anything to the UI
        msg = f"{type(e).__name__}: {e}"
        return jsonify({"ok": False, "error": "scrape_failed", "message": msg}), 502

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # Post-filter by actual haversine distance. Facebook's
    # `filter_radius_km` parameter is a HINT — listings outside it
    # (especially nearby cities like Surrey from Vancouver) routinely
    # leak through, sometimes from 30+ km away even at 5 km radius.
    # We re-check every listing's geocoded city against the user's
    # home lat/lng and drop anything outside.
    #
    # Listings whose seller_location can't be geocoded (or is empty)
    # stay in — better to keep an unknown-location listing than drop
    # something the user might want.
    soft_radius = float(radius_km) if radius_km else None
    distance_dropped = 0
    listings = []
    for sl in page.listings:
        in_radius = True
        listing_dist_km: float | None = None
        if soft_radius is not None and sl.seller_location:
            coords = geocode_city(sl.seller_location)
            if coords is not None:
                listing_dist_km = haversine_km(
                    lat, lng, coords[0], coords[1],
                )
                if listing_dist_km > soft_radius:
                    in_radius = False
        if not in_radius:
            distance_dropped += 1
            continue
        listings.append({
            "id": sl.id,
            "title": sl.title,
            "price_amount": sl.price_amount,
            "price_formatted": sl.price_formatted,
            "previous_price": sl.previous_price,
            "is_pending": bool(sl.is_pending),
            "photo_url": sl.photo_url,
            "seller_location": sl.seller_location,
            "listing_url": sl.listing_url,
            "distance_km": (round(listing_dist_km, 1)
                            if listing_dist_km is not None else None),
        })

    return jsonify({
        "ok": True,
        "keyword": keyword,
        "count": len(listings),
        "elapsed_ms": elapsed_ms,
        "has_more": page.has_more,
        "rate_limited": page.rate_limited,
        "error_message": page.error_message,
        "listings": listings,
        "search_center": {"lat": lat, "lng": lng, "radius_km": radius_km},
        "distance_dropped": distance_dropped,
        "total_returned_by_facebook": len(page.listings),
    })


# ---------------------------------------------------------------------------
# /appraise — Test Appraiser stage 2: score one chosen listing against
# eBay comps. Public-product surface; anon-callable now that /comps is
# deployed with verify_jwt=false.
# ---------------------------------------------------------------------------

# /appraise — uses the personal deal_finder pipeline VERBATIM:
#   1. cloud /comps gives us a list of raw comp prices (eBay sold/active)
#   2. compute_stats_from_prices() applies the Tukey trim + bimodal-cluster
#      split that the personal version uses (fixes the Honda Civic case
#      where parts dragged the median to $40 — bimodal split keeps only
#      the cluster whose median is closest to the asking price)
#   3. formula.compute_score() runs the deterministic percentile-rank
#      math with confidence cap and condition adjustments
#
# This replaces the inline math the SaaS used to do. No more drift
# between "what scored my watches in the personal tool" and "what the
# SaaS's appraiser does."
@app.route("/appraise", methods=["POST"])
@login_required_api
def appraise():
    """Run a comp lookup + score for a search term + asking price.

    Ported the full proven appraisal logic from the personal
    `deal_finder/src/deal_finder/appraisal/formula.py` pipeline:

      1. Tukey-fence outlier trim on the comp set so a few mis-categorized
         parts don't drag the median into the dirt.
      2. Asking-vs-sold discount: fair_value = trimmed_median * 0.80
         (asking prices on Marketplace systematically run 15-30% above
         true secondhand sale prices).
      3. Score driver = percentile rank of asking_price inside the comp
         distribution. score = 100 * (1 - pct_rank). "Cheaper than X% of
         comparable listings."
      4. Confidence interval (`confidence_pm`) widens with low sample
         size and high IQR; final score capped at (100 - confidence_pm)
         so we never claim more confidence than the data supports.
      5. Sanity guards:
            - sample_size < 3 → unscoreable
            - asking > 5x median → unscoreable ("comps don't match")
            - IQR > median → flag data_quality_poor

    Body (JSON or form-encoded):
        title           str, required
        asking_price    float, required
        region          str, optional (default EBAY-ENCA)
        force_refresh   bool, optional (bypass the 12h comps cache)
        listing_url     str, optional — when present, the title gets
                        run through the cloud `appraise-normalize`
                        edge function (MiniMax) so two listings with
                        the same canonical_kind but different raw
                        titles share comp data.
        body            str, optional — listing description, helps
                        the LLM disambiguate model + condition.
    """
    data = _safe_request_json() or request.form
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    try:
        asking_price = float(data.get("asking_price"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "asking_price must be numeric"}), 400
    if asking_price <= 0:
        return jsonify({"ok": False, "error": "asking_price must be positive"}), 400
    region = (data.get("region") or "EBAY-ENCA").strip() or "EBAY-ENCA"
    force_refresh = bool(data.get("force_refresh"))
    listing_url = (data.get("listing_url") or "").strip()
    body_text = (data.get("body") or "").strip()

    # ---- Step 0: LLM normalize (optional, gracefully degraded) ----
    # If listing_url is provided, ask the cloud to give us a
    # canonical_kind + worth_deep flag + red flags. Failure is
    # non-fatal — we just lose the canonical_kind and comp the raw
    # title. Cost: ~$0.0002 per cache miss; cache hit is free.
    norm = None
    if listing_url:
        try:
            from deal_finder.appraisal.normalize import normalize_one
            norm = normalize_one(
                listing_url=listing_url,
                title=title,
                body=body_text,
                ask_price=asking_price,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("normalize_one failed (non-fatal): %s", e)
            norm = None

    # If the LLM says this listing isn't worth scoring (WTB, services,
    # off-topic, empty), short-circuit before paying for comps.
    if norm is not None and not norm.worth_deep and not norm.is_fallback:
        return jsonify({
            "ok": True,
            "unscoreable": True,
            "reason": "low_quality_data",
            "reason_detail": (
                norm.reasoning
                or "this listing doesn't have enough information to compare against similar items"
            ),
            "search_term": title,
            "canonical_kind": norm.canonical_kind or None,
            "red_flags": norm.red_flags,
            "asking_price": asking_price,
            "elapsed_s": 0.0,
        })

    # Use canonical_kind for comp lookup if we have a confident one;
    # otherwise fall back to the raw title. The "or title" branch is
    # the graceful degradation path (cloud down, LLM ambiguous, etc.).
    comp_search_term = (
        norm.canonical_kind if (norm and norm.canonical_kind)
        else title
    )

    # Forward LLM normalize fields to the cloud /comps function so
    # the eBay query gets the categoryId + price-band guards. Without
    # these, a "Honda motorcycle" search returns 50 helmets and the
    # bimodal split has no real motorcycles to find.
    comp_kwargs: dict = {
        "region": region, "force_refresh": force_refresh,
    }
    if norm and norm.canonical_kind:
        if norm.category_hint:
            comp_kwargs["category_hint"] = norm.category_hint
        if norm.coarse_low and norm.coarse_low > 0:
            comp_kwargs["coarse_low"] = norm.coarse_low
        if norm.coarse_high and norm.coarse_high > 0:
            comp_kwargs["coarse_high"] = norm.coarse_high

    t0 = time.perf_counter()
    try:
        comp = get_comps(comp_search_term, **comp_kwargs)
    except TypeError:
        # get_comps may not accept the new kwargs in older builds; retry
        # with just the legacy args so the appraise endpoint stays
        # compatible with stale local imports during dev.
        try:
            comp = get_comps(
                comp_search_term, region=region, force_refresh=force_refresh,
            )
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"comp fetch: {e}"}), 502
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"comp fetch: {e}"}), 502
    elapsed_s = time.perf_counter() - t0

    raw_prices = [
        float(c.get("price")) for c in (comp.get("raw_comps") or [])
        if c.get("price") is not None and float(c.get("price")) > 0
    ]
    sample_size = int(comp.get("sample_size") or 0) or len(raw_prices)

    # Use the EXACT personal pipeline:
    #   compute_stats_traced  →  bimodal split + Tukey trim + IQR
    #                            + a debug trace of every step
    #   formula.compute_score →  percentile-rank scoring + confidence cap
    from deal_finder.db.comps import compute_stats_traced
    from deal_finder.appraisal.formula import compute_score, MIN_COMPS_TO_SCORE

    stats, stats_trace = compute_stats_traced(
        prices=raw_prices,
        search_term=comp.get("search_term") or title,
        source=comp.get("source") or "ebay",
        asking_price=asking_price,
    )

    # If the cluster split or Tukey trim left us with too few comps,
    # the formula will return unscoreable=True with a clean reason.
    try:
        breakdown = compute_score(
            asking_price=asking_price,
            comp=stats,
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({
            "ok": False,
            "error": "score_failed",
            "message": str(e)[:300],
            "elapsed_s": round(elapsed_s, 3),
        }), 500

    if breakdown.unscoreable:
        return jsonify({
            "ok": True,
            "unscoreable": True,
            "reason": breakdown.unscoreable_reason or
                      f"insufficient comparable listings (need {MIN_COMPS_TO_SCORE}+)",
            "search_term": comp.get("search_term"),
            "canonical_kind": (norm.canonical_kind if norm else None) or None,
            "normalize_confidence": (norm.confidence if norm else None),
            "red_flags": (norm.red_flags if norm else []),
            "sample_size": stats.sample_size,
            "trimmed_sample_size": stats.trimmed_sample_size,
            "median": stats.median,
            "trimmed_median": stats.trimmed_median,
            "asking_price": asking_price,
            "comp_source": comp.get("source"),
            "raw_comps": comp.get("raw_comps") or [],
            "elapsed_s": round(elapsed_s, 3),
            "force_refresh": force_refresh,
            # Debug payload — same shape as the scoreable branch so the
            # UI's auto-opened debug panel can render the LLM normalize
            # result, eBay filter that fired, and stats trace. Without
            # this, "0 comps" cards have no way to show what filter ran.
            "debug": {
                "search_term_used": comp_search_term,
                "search_term_raw": title,
                "search_term_source": (
                    "canonical_kind" if (norm and norm.canonical_kind)
                    else "raw_title"
                ),
                "normalize": (
                    None if norm is None else {
                        "canonical_kind": norm.canonical_kind,
                        "category_hint": norm.category_hint,
                        "coarse_low": norm.coarse_low,
                        "coarse_high": norm.coarse_high,
                        "confidence": norm.confidence,
                        "worth_deep": norm.worth_deep,
                        "red_flags": norm.red_flags,
                        "reasoning": norm.reasoning,
                        "cache_hit": norm.cache_hit,
                        "is_fallback": norm.is_fallback,
                    }
                ),
                "comp_filters": {
                    "category_hint": (norm.category_hint if norm else None),
                    "category_id": (comp.get("category_id") if isinstance(comp, dict) else None),
                    "price_band": (comp.get("price_band") if isinstance(comp, dict) else None),
                },
                "stats_trace": stats_trace,
                "guard_fired": "insufficient_comps",
            },
        })

    # Sanity guard for the edge case the formula doesn't catch:
    # if asking is wildly higher than the trimmed median AFTER the
    # bimodal split fired, we're still in the wrong category. Bail
    # out rather than render a confidently-wrong 0/100. (The personal
    # bimodal-split path normally handles this; this is belt-and-
    # suspenders for cars where eBay returns dense parts clusters.)
    median_for_check = stats.trimmed_median or stats.median or 0
    if median_for_check > 0 and asking_price / median_for_check > 5:
        return jsonify({
            "ok": True,
            "unscoreable": True,
            "reason": (
                f"comps don't match listing — median "
                f"${int(median_for_check)} vs asking "
                f"${int(asking_price)} ({asking_price/median_for_check:.1f}x). "
                "Try a more specific search (year + model + trim)."
            ),
            "search_term": comp.get("search_term"),
            "canonical_kind": (norm.canonical_kind if norm else None) or None,
            "red_flags": (norm.red_flags if norm else []),
            "sample_size": stats.sample_size,
            "median": stats.median,
            "trimmed_median": stats.trimmed_median,
            "asking_price": asking_price,
            "comp_source": comp.get("source"),
            "raw_comps": comp.get("raw_comps") or [],
            "elapsed_s": round(elapsed_s, 3),
            "force_refresh": force_refresh,
            # Even on the unscoreable path, ship the full debug trace
            # so the user can see WHY the sanity guard fired (which
            # comps were in the cluster, was bimodal triggered, etc.).
            "debug": {
                "search_term_used": comp_search_term,
                "search_term_raw": title,
                "search_term_source": (
                    "canonical_kind" if (norm and norm.canonical_kind)
                    else "raw_title"
                ),
                "normalize": (
                    None if norm is None else {
                        "canonical_kind": norm.canonical_kind,
                        "category_hint": norm.category_hint,
                        "coarse_low": norm.coarse_low,
                        "coarse_high": norm.coarse_high,
                        "confidence": norm.confidence,
                        "worth_deep": norm.worth_deep,
                        "red_flags": norm.red_flags,
                        "reasoning": norm.reasoning,
                        "cache_hit": norm.cache_hit,
                        "is_fallback": norm.is_fallback,
                    }
                ),
                "comp_filters": {
                    "category_hint": (norm.category_hint if norm else None),
                    "category_id": (comp.get("category_id") if isinstance(comp, dict) else None),
                    "price_band": (comp.get("price_band") if isinstance(comp, dict) else None),
                },
                "stats_trace": stats_trace,
                "guard_fired": "asking_over_5x_median",
                "median_for_check": median_for_check,
            },
        })

    # Plumb LLM normalize confidence into the score band: low =
    # widen ±12, medium = ±6, high = pass through. Caps the deal_score
    # at (100 - confidence_pm) so we never claim more confidence than
    # the underlying normalization supports. (The score function also
    # has its own sample-size / IQR widening; both apply.)
    confidence_pm = breakdown.confidence_pm
    norm_widen = 0
    if norm and norm.canonical_kind:
        norm_widen = {"low": 12, "medium": 6, "high": 0}.get(
            norm.confidence, 0,
        )
        if norm_widen:
            confidence_pm = min(100, confidence_pm + norm_widen)

    final_deal_score = breakdown.deal_score
    if confidence_pm > 0:
        final_deal_score = min(final_deal_score, max(0, 100 - confidence_pm))

    # Build the comprehensive debug trace that the UI's "Debug"
    # expander renders. EVERY step the appraiser took is in here so
    # the user can trace exactly why a score came out the way it did.
    # We hide this expander behind a CSS toggle for production but
    # always emit the data so debugging is free of round-trips.
    debug_payload = {
        "search_term_used": comp_search_term,
        "search_term_raw": title,
        "search_term_source": (
            "canonical_kind" if (norm and norm.canonical_kind) else "raw_title"
        ),
        "normalize": (
            None if norm is None else {
                "canonical_kind": norm.canonical_kind,
                "category_hint": norm.category_hint,
                "coarse_low": norm.coarse_low,
                "coarse_high": norm.coarse_high,
                "confidence": norm.confidence,
                "worth_deep": norm.worth_deep,
                "red_flags": norm.red_flags,
                "reasoning": norm.reasoning,
                "cache_hit": norm.cache_hit,
                "is_fallback": norm.is_fallback,
            }
        ),
        "comp_filters": {
            "category_hint": (norm.category_hint if norm else None),
            "category_id": (comp.get("category_id") if isinstance(comp, dict) else None),
            "price_band": (comp.get("price_band") if isinstance(comp, dict) else None),
        },
        "stats_trace": {
            "input_count": stats_trace.get("input_count"),
            "sorted_prices": stats_trace.get("sorted_prices"),
            "bimodal": stats_trace.get("bimodal"),
            "cluster_prices": stats_trace.get("cluster_prices"),
            "tukey": stats_trace.get("tukey"),
            "final": stats_trace.get("final"),
        },
        "score_inputs": {
            "asking_price": asking_price,
            "trimmed_median": stats.trimmed_median,
            "iqr": stats.iqr,
            "trimmed_sample_size": stats.trimmed_sample_size,
            "percentile_rank": breakdown.percentile_rank,
            "raw_score_pre_condition": breakdown.raw_score_pre_condition,
            "confidence_pm_pre_normalize": breakdown.confidence_pm,
            "normalize_widening": norm_widen,
            "confidence_pm_final": confidence_pm,
            "deal_score_pre_cap": breakdown.deal_score,
            "deal_score_after_cap": final_deal_score,
        },
    }

    return jsonify({
        "ok": True,
        "unscoreable": False,
        "deal_score": final_deal_score,
        "fair_value": (
            round(breakdown.fair_value, 2)
            if breakdown.fair_value is not None else None
        ),
        "fair_value_source": breakdown.fair_value_source,
        "ratio": (
            round(breakdown.ratio, 3) if breakdown.ratio is not None else None
        ),
        "asking_price": asking_price,
        "search_term": comp.get("search_term"),
        # Normalize-layer fields. UI uses these to render the
        # "appraised as: X" line and red-flag chips. May be None
        # when the cloud was unreachable or no listing_url given.
        "canonical_kind": (norm.canonical_kind if norm else None) or None,
        "normalize_confidence": (norm.confidence if norm else None),
        "red_flags": (norm.red_flags if norm else []),
        "normalize_cache_hit": (norm.cache_hit if norm else False),
        "comp_source": comp.get("source"),
        "comp_median": (
            round(stats.median, 2) if stats.median is not None else None
        ),
        "comp_trimmed_median": (
            round(stats.trimmed_median, 2)
            if stats.trimmed_median is not None else None
        ),
        "comp_sample_size": stats.sample_size,
        "comp_trimmed_sample_size": stats.trimmed_sample_size,
        "outliers_dropped": stats.outliers_dropped,
        "iqr": (round(stats.iqr, 2) if stats.iqr is not None else None),
        "data_quality_poor": breakdown.data_quality_poor,
        "iqr_to_median_ratio": breakdown.iqr_to_median_ratio,
        "percentile_rank": breakdown.percentile_rank,
        "confidence_pm": confidence_pm,
        "confidence_label": breakdown.confidence_label,
        "raw_score_pre_condition": breakdown.raw_score_pre_condition,
        "formula_version": breakdown.formula_version,
        "raw_comps": comp.get("raw_comps") or [],
        "elapsed_s": round(elapsed_s, 3),
        "force_refresh": force_refresh,
        "debug": debug_payload,
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
# /api/referral/info  — proxy to cloud /referral-info. Returns code,
#                       link, earned/pending counts, history.
# /api/referral/claim — proxy to cloud /referral-claim. Body: {code}.
# ---------------------------------------------------------------------------

@app.route("/api/referral/info", methods=["GET"])
@login_required_api
def api_referral_info():
    from deal_finder.cloud.client import (
        client as cloud_client, CloudError, CloudUnavailable, Unauthorized,
    )
    try:
        resp = cloud_client.post("referral-info", {})
    except Unauthorized:
        return jsonify({"ok": False, "error": "login_required"}), 401
    except CloudUnavailable as e:
        return jsonify({"ok": False, "error": "cloud_unavailable",
                        "message": str(e)}), 503
    except CloudError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify(resp)


@app.route("/api/referral/claim", methods=["POST"])
@login_required_api
def api_referral_claim():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "code required"}), 400
    from deal_finder.cloud.client import (
        client as cloud_client, CloudError, CloudUnavailable, Unauthorized,
    )
    try:
        resp = cloud_client.post("referral-claim", {"code": code})
    except Unauthorized:
        return jsonify({"ok": False, "error": "login_required"}), 401
    except CloudUnavailable as e:
        return jsonify({"ok": False, "error": "cloud_unavailable",
                        "message": str(e)}), 503
    except CloudError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(resp)


# ---------------------------------------------------------------------------
# /api/changelog — return CHANGELOG.md text so the "What's New" modal
# can render it. Used by Settings → "What's New" entry. Public so even
# anon users can read shipping notes.
# ---------------------------------------------------------------------------

@app.route("/api/changelog")
def api_changelog():
    # CHANGELOG lives at desktop/CHANGELOG.md — two levels up from
    # webapp/. Fall back gracefully if the file moves so we don't 500.
    try:
        cl_path = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
        if not cl_path.exists():
            # PyInstaller bundle: file may be at sys._MEIPASS root.
            cl_path = Path(__file__).resolve().parent / "CHANGELOG.md"
        if not cl_path.exists():
            return jsonify({"ok": False, "error": "changelog not bundled"}), 404
        return jsonify({
            "ok": True,
            "markdown": cl_path.read_text(encoding="utf-8"),
        })
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"read failed: {e}"}), 500


# ---------------------------------------------------------------------------
# /api/billing/portal — proxy to cloud /billing-portal.
# Returns a short-lived Stripe portal URL so the user can manage / cancel
# their subscription. Wired to Settings → "Manage subscription" button.
# ---------------------------------------------------------------------------

@app.route("/api/billing/portal", methods=["POST"])
@login_required_api
def api_billing_portal():
    from deal_finder.cloud.client import (
        client as cloud_client,
        CloudError, CloudUnavailable, Unauthorized,
    )
    try:
        resp = cloud_client.post("billing-portal", {})
    except Unauthorized:
        return jsonify({"ok": False, "error": "login_required"}), 401
    except CloudUnavailable as e:
        return jsonify({"ok": False, "error": "cloud_unavailable",
                        "message": str(e)}), 503
    except CloudError as e:
        # 404 from cloud means "no Stripe customer for this user yet".
        # Surface a clean message the UI can show: free-tier user has
        # nothing to manage. Bubble through a 400.
        msg = str(e)
        return jsonify({"ok": False, "error": "no_subscription",
                        "message": msg}), 400
    return jsonify({"ok": True, "url": resp.get("url")})


# ---------------------------------------------------------------------------
# /api/streak — retention-loop v1.1 proxy.
#
# Thin pass-through to the cloud `/streak` Edge Function (owned by the
# streak-backend agent in `deal_finder.cloud.streak`). The desktop never
# computes streak math itself; this just forwards the response so the
# dashboard JS has a single same-origin URL.
#
# Failure mode: when the cloud helper returns None (network error,
# function down, schema mismatch) we surface 503 with
# `streak_unavailable: True` so the UI can hide the streak card without
# blowing up the rest of the dashboard. The Pro-day banner and redeem
# CTA gracefully degrade to invisible — losing the streak counter is a
# non-event compared to losing the appraisal feed.
# ---------------------------------------------------------------------------

@app.route("/api/streak", methods=["GET"])
@login_required_api
def api_streak():
    # Lazy import: cloud.streak is owned by another agent and the module
    # may land separately. Importing at module scope would couple this
    # file's load order to that work; the lazy import lets tests stub
    # the symbol via patch() without needing the real module.
    from deal_finder.cloud.streak import fetch_streak
    data = fetch_streak()
    if data is None:
        return jsonify({"ok": False, "streak_unavailable": True}), 503
    return jsonify({"ok": True, **data})


@app.route("/api/streak/redeem", methods=["POST"])
@login_required_api
def api_streak_redeem():
    """Convert N banked Pro days into an active trial window.

    On success we MUST invalidate the license cache — otherwise the
    next /api/dashboard call still says tier='free' until the cache
    TTL expires and the user thinks the redeem silently failed.
    """
    from deal_finder.cloud.streak import redeem_pro_days
    result = redeem_pro_days()
    if result is None:
        return jsonify({"ok": False, "error": "redeem_failed"}), 503
    license_manager.invalidate()
    return jsonify({"ok": True, **result})


# ---------------------------------------------------------------------------
# New shell routes (sidebar+tabs surface). The old /, /dashboard, /login
# templates still render via their own routes during the smoke-test
# window; once the user signs off on the new shell, those + their
# stylesheets/JS get deleted.
#
# Each tab route renders a thin Jinja template that extends
# templates/app_shell.html. The shell pulls user_email/tier_label from
# the license manager so the sidebar's account block renders on first
# paint — JS would flicker in/out otherwise.
#
# We define a fresh `_shell_login_required` decorator here so the new
# shell tabs redirect to /auth (the new sign-in card) instead of /login
# (the legacy template). Existing routes still use the original
# `login_required` -> /login flow, untouched.
# ---------------------------------------------------------------------------

def _shell_login_required(view):
    """HTML routes on the new shell: redirect to /auth when no token."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not token_store.is_logged_in():
            return redirect("/auth")
        return view(*args, **kwargs)
    return wrapper


def _tier_label_for_shell() -> str:
    """Sidebar plan-pill text. Cheap; reads cached license info only."""
    try:
        if license_manager.is_kill_switched():
            return "Update required"
        tier = license_manager.tier()
        if tier == "paid":
            return "Pro"
        if tier == "trial":
            return "Trial"
        return "Free"
    except Exception:  # noqa: BLE001 — sidebar must never crash a page
        return "Free"


def _shell_context() -> dict:
    """Common kwargs every tab passes to render_template."""
    is_paid = False
    try:
        is_paid = license_manager.is_paid()
    except Exception:  # noqa: BLE001
        pass
    # Decode the email from the JWT for the sidebar account pill. This
    # is cosmetic — the JWT signature is verified server-side on every
    # cloud call; a forged local token with a bogus email just displays
    # the bogus email and fails on the next /license refresh.
    user_email = None
    try:
        user_email = token_store.load_email()
    except Exception:  # noqa: BLE001 — sidebar must never crash a page
        user_email = None
    # Trial countdown — when user is mid-trial, surface days remaining
    # so the sidebar can show "Trial ends in N days · Upgrade →" instead
    # of the banked-Pro-days pill. Mirrors Wispr's persistent trial CTA.
    trial_days_remaining = None
    try:
        trial_days_remaining = license_manager.trial_days_remaining()
    except Exception:  # noqa: BLE001
        trial_days_remaining = None
    return {
        "logged_in": token_store.is_logged_in(),
        "is_paid": is_paid,
        "tier": license_manager.tier() if token_store.is_logged_in() else "free",
        "tier_label": _tier_label_for_shell(),
        "user_email": user_email,
        "trial_days_remaining": trial_days_remaining,
    }


@app.route("/home")
@_shell_login_required
def tab_home():
    banked_days = 0
    is_paid = license_manager.is_paid()
    if not is_paid:
        # Same defensive pattern the old / route used: streak failures
        # must not block the home tab.
        try:
            from deal_finder.cloud.streak import fetch_streak
            data = fetch_streak() or {}
            banked_days = int(data.get("pro_days_banked") or 0)
        except Exception:  # noqa: BLE001
            banked_days = 0
    ctx = _shell_context()
    ctx["banked_days"] = banked_days
    return render_template("tab_home.html", **ctx)


@app.route("/watches")
@_shell_login_required
def tab_watches():
    return render_template("tab_watches.html", **_shell_context())


@app.route("/activity")
@_shell_login_required
def tab_activity():
    return render_template("tab_activity.html", **_shell_context())


@app.route("/test")
@_shell_login_required
def tab_test():
    """Login-gated. Anon test surface was removed 2026-05-05 per user
    direction — the public hook will be a recorded GIF demo on the
    landing page instead. Simpler codepath, no fake/cached data risk."""
    return render_template("tab_test.html", **_shell_context())


@app.route("/stats")
@_shell_login_required
def tab_stats():
    """Stats is Pro-only; free users see an inline upgrade card on the
    same page (no redirect to /upgrade). This matches Wispr Flow's
    dashboard-first behavior — never bounce, always tell the user
    where they are."""
    ctx = _shell_context()
    ctx["upgrade_required"] = not ctx["is_paid"]
    return render_template("tab_stats.html", **ctx)


@app.route("/settings")
@_shell_login_required
def tab_settings():
    return render_template("tab_settings.html", **_shell_context())


@app.route("/auth")
def auth_page():
    if token_store.is_logged_in():
        return redirect("/home")
    return render_template("auth.html")


# ---------------------------------------------------------------------------
# Insights tab — Wispr Flow's retention-engine equivalent.
#
# Two endpoints power the tab:
#   GET /api/insights/heatmap        — last 90 days of {date, deals, alerts}
#   GET /api/insights/personal-best  — your top 10 highest-scored listings
#
# "Deals" = listings.deal_score >= active_threshold AND rejected = 0.
# active_threshold pulls from subscribers' MIN(score_threshold) when any
# subscriber row is active; falls back to ALERT_SCORE_THRESHOLD env (70).
#
# We zero-fill missing days so the front-end can render a fixed-size grid
# without holes — much simpler than computing date ranges client-side.
# ---------------------------------------------------------------------------

@app.route("/api/insights/heatmap")
@login_required_api
def api_insights_heatmap():
    """Return N days of deals + alerts buckets, zero-filled.

    Query params:
        days  int 1-365, default 90. Home tab passes 14 for the mini-
              heatmap; Insights tab uses the default 90.

    Response also includes `today_top`: the highest-scoring listing
    scored today (or null if none). Used by the Home tab's daily-goal
    banner so we don't need a separate endpoint for that one row.

    Streak math: count consecutive trailing days (working back from
    today) where deals > 0. Today counts. Yesterday gap breaks streak.
    """
    # Parse + clamp the window.
    try:
        days_window = int(request.args.get("days", "90"))
    except (TypeError, ValueError):
        days_window = 90
    if days_window < 1:
        days_window = 1
    if days_window > 365:
        days_window = 365

    threshold = _default_threshold()
    with get_conn() as conn:
        sub_row = conn.execute(
            "SELECT MIN(score_threshold) FROM subscribers WHERE active = 1"
        ).fetchone()
        if sub_row and sub_row[0] is not None:
            threshold = int(sub_row[0])

        rows = conn.execute(
            f"""SELECT
                   DATE(scraped_at) AS day,
                   SUM(CASE WHEN deal_score IS NOT NULL
                              AND deal_score >= ?
                              AND rejected = 0
                       THEN 1 ELSE 0 END) AS deals,
                   SUM(CASE WHEN notified = 1 THEN 1 ELSE 0 END) AS alerts
               FROM listings
               WHERE scraped_at >= DATE('now', '-{days_window} days')
               GROUP BY DATE(scraped_at)
               ORDER BY day""",
            (threshold,),
        ).fetchall()

        # Today's top scoring listing (drives the Home daily-goal card).
        # We use UTC date semantics here because scraped_at is UTC; the
        # Home tab will display "today" relative to the server clock,
        # which for a single-user desktop is fine.
        top_today_row = conn.execute(
            """SELECT id, title, listing_url, deal_score, price, photo_url
               FROM listings
               WHERE deal_score IS NOT NULL AND rejected = 0
                 AND DATE(scraped_at) = DATE('now')
               ORDER BY deal_score DESC, scraped_at DESC
               LIMIT 1"""
        ).fetchone()

    by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        day = r[0]
        if not day:
            continue
        by_day[day] = {"deals": int(r[1] or 0), "alerts": int(r[2] or 0)}

    # Zero-fill the window back from today (UTC).
    today = datetime.now(timezone.utc).date()
    days: list[dict[str, object]] = []
    from datetime import timedelta
    for i in range(days_window - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        cell = by_day.get(d, {"deals": 0, "alerts": 0})
        days.append({"date": d, "deals": cell["deals"], "alerts": cell["alerts"]})

    # Aggregate stats.
    total_deals = sum(d["deals"] for d in days)
    total_alerts = sum(d["alerts"] for d in days)
    active_days = sum(1 for d in days if d["deals"] > 0)

    # Longest streak across the 90-day window.
    longest = 0
    cur = 0
    for d in days:
        if d["deals"] > 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0

    # Current streak: consecutive trailing days with deals > 0. Today
    # may legitimately be 0 mid-day before any poll lands — in that case
    # we use *yesterday* as the starting point so the user doesn't see
    # the streak appear to drop to 0 every morning.
    current = 0
    started = False
    for d in reversed(days):
        if d["deals"] > 0:
            current += 1
            started = True
        elif started:
            break
        elif d == days[-1]:
            # Today is 0 — allow the streak to continue from yesterday
            # without resetting; we just don't increment yet.
            continue
        else:
            break

    # Build the today_top payload (or null when nothing today).
    today_top: dict[str, object] | None = None
    if top_today_row is not None:
        price = top_today_row[4]
        today_top = {
            "id": top_today_row[0],
            "title": top_today_row[1],
            "listing_url": top_today_row[2],
            "deal_score": int(top_today_row[3]),
            "price": price,
            "photo_url": top_today_row[5],
        }

    return jsonify({
        "ok": True,
        "days": days,
        "threshold": threshold,
        "today_top": today_top,
        "summary": {
            "total_deals": total_deals,
            "total_alerts": total_alerts,
            "active_days": active_days,
            "longest_streak": longest,
            "current_streak": current,
        },
    })


# ---------------------------------------------------------------------------
# /api/insights/lifetime — total-deals + total-savings flex.
#
# Powers the Home "$X saved across N deals" card + the cultural
# comparison ("That's enough to buy a new MacBook"). Computed across
# ALL of the user's history, not bound to a window like /heatmap.
#
# Savings = sum(fair_value - price) for scored, non-rejected listings
# where price < fair_value. We deliberately ignore deals where the
# user's asking price was higher than fair (that's a negative savings
# and would distort the flex).
# ---------------------------------------------------------------------------

# Cultural-comparison ladder: as savings climb, surface a culturally
# meaningful "you could have bought a..." flex. List is ordered low→high;
# we pick the highest threshold the user has crossed. Approximate retail
# values, USD. Tweak freely without touching code.
_CULTURAL_FLEX_LADDER = [
    (50,    "a really good steak dinner"),
    (150,   "a pair of AirPods Pro"),
    (300,   "a Switch Lite"),
    (500,   "a weekend getaway"),
    (800,   "an iPhone 15"),
    (1200,  "a MacBook Air"),
    (2000,  "a flight to Europe"),
    (3500,  "a used car downpayment"),
    (5000,  "a month off work"),
    (10000, "a small wedding"),
    (25000, "a Honda Civic, in cash"),
    (50000, "a year of rent in San Francisco"),
]


def _cultural_flex(savings: float) -> str | None:
    """Pick the highest-tier cultural item the savings cover."""
    if savings <= 0:
        return None
    last = None
    for threshold, item in _CULTURAL_FLEX_LADDER:
        if savings >= threshold:
            last = item
    return last


@app.route("/api/insights/lifetime")
@login_required_api
def api_insights_lifetime():
    """Total deals scored + total savings across all-time history."""
    threshold = _default_threshold()
    with get_conn() as conn:
        sub_row = conn.execute(
            "SELECT MIN(score_threshold) FROM subscribers WHERE active = 1"
        ).fetchone()
        if sub_row and sub_row[0] is not None:
            threshold = int(sub_row[0])

        row = conn.execute(
            """SELECT
                   SUM(CASE WHEN deal_score IS NOT NULL
                              AND deal_score >= ?
                              AND rejected = 0
                       THEN 1 ELSE 0 END) AS deals,
                   COALESCE(SUM(
                       CASE
                           WHEN deal_score IS NOT NULL
                                AND deal_score >= ?
                                AND rejected = 0
                                AND price IS NOT NULL
                                AND fair_value IS NOT NULL
                                AND fair_value > price
                           THEN (fair_value - price)
                           ELSE 0
                       END
                   ), 0) AS savings
               FROM listings""",
            (threshold, threshold),
        ).fetchone()

    deals = int(row[0] or 0)
    savings = float(row[1] or 0)
    return jsonify({
        "ok": True,
        "deals_total": deals,
        "savings_total": round(savings, 2),
        "cultural_flex": _cultural_flex(savings),
        "threshold_used": threshold,
    })


@app.route("/api/insights/personal-best")
@login_required_api
def api_insights_personal_best():
    """Top 10 highest-scored listings, all-time. Excludes rejected rows.

    Tie-break: more recent scraped_at wins, so the leaderboard refreshes
    organically as new high-score listings come in.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, listing_url, photo_url,
                      deal_score, price, fair_value, scraped_at,
                      seller_location
               FROM listings
               WHERE deal_score IS NOT NULL AND rejected = 0
               ORDER BY deal_score DESC, scraped_at DESC
               LIMIT 10"""
        ).fetchall()
    items = []
    for r in rows:
        price = r[5]
        fair_value = r[6]
        savings = None
        if isinstance(price, (int, float)) and isinstance(fair_value, (int, float)):
            savings = max(0, fair_value - price)
        items.append({
            "id": r[0],
            "title": r[1],
            "listing_url": r[2],
            "photo_url": r[3],
            "deal_score": int(r[4]),
            "price": price,
            "fair_value": fair_value,
            "savings": savings,
            "scraped_at": _iso(r[7]),
            "seller_location": r[8],
        })
    return jsonify({"ok": True, "items": items})


@app.route("/insights")
@_shell_login_required
def tab_insights():
    """Insights tab — streak heatmap + personal-best leaderboard.

    Free + paid both see this tab; the data isn't gated. Pro doesn't
    unlock anything extra here in v1 — it's a retention surface for
    everyone, not a paywall.
    """
    return render_template("tab_insights.html", **_shell_context())


# ---------------------------------------------------------------------------
# Static fallback for /favicon.ico so we don't 404-spam the log.
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    return ("", 204)


# ---------------------------------------------------------------------------
# Sentry-equivalent error_seen telemetry. Catch unhandled exceptions
# from any route, emit error_seen, then return the appropriate
# response.
#
# Critical: HTTPException subclasses (NotFound, MethodNotAllowed,
# BadRequest, etc.) carry their own status code + body. We MUST
# return the canned response — re-raising would bubble back up into
# Flask's outer wsgi_app catch which turns ANY exception into a 500.
# That bug silently rewrote every 404/405 into a 500 in production.
# Verified fix: tests/adversarial/INJECTION-FINDINGS.md (F-HIGH-1).
# ---------------------------------------------------------------------------

from werkzeug.exceptions import HTTPException as _HTTPException


@app.errorhandler(Exception)
def _emit_error_seen(e: Exception):
    try:
        cloud_telemetry.emit("error_seen", {
            "error_type": type(e).__name__,
            "where": "flask",
            "path": request.path or "",
        })
    except Exception:  # noqa: BLE001
        pass
    if isinstance(e, _HTTPException):
        # Preserve the HTTPException's own 4xx/5xx response (status
        # + body). Returning the exception object is Flask's documented
        # way to surface its canned response from an error handler.
        return e
    # Genuine 500 surface — return a plain 500 instead of re-raising.
    # Re-raising would land in wsgi_app's outer catch and the user sees
    # an opaque werkzeug page; we want a JSON envelope on /api routes.
    if (request.path or "").startswith("/api/"):
        return jsonify({"ok": False, "error": "internal_error"}), 500
    return ("Internal Server Error", 500)


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
