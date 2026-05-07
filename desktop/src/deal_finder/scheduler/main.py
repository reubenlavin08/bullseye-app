"""APScheduler entry point.

Runs the deal_finder pipeline autonomously:

  - One round-robin coordinator tick that picks the stalest active
    user_search and scrapes it. Replaces the per-watch interval jobs
    used by the personal tool — single-shared-rate-gate is the only
    pattern that survives FB's per-IP quota at high N.

  - One safety-net appraisal sweep every SAFETY_NET_INTERVAL_S seconds.
    No-op until the worker module lands in step 5 of the implementation
    plan.

  - Stub digest + daily-summary jobs that re-fire every interval but
    do nothing — those get rewritten against cloud.alerts in step 7.

Configuration knobs (env-overridable):

  POLL_INTERVAL_S          default 60   - legacy; ignored under coordinator
  SAFETY_NET_INTERVAL_S    default 600  - seconds between safety drains
  COORDINATOR_TICK_S       default 20   - seconds between coordinator ticks
  RELOAD_INTERVAL_S        default 20   - watch-count reload cadence
  DIGEST_INTERVAL_S        default 15   - instant-alert sweep cadence (stub)
  DAILY_SUMMARY_INTERVAL_S default 3600 - daily-summary sweep cadence (stub)

Restart-safe: on boot we ALWAYS query active searches fresh from the
DB and (re)schedule them.

Translation notes:
  - LLM warmup omitted (worker.warmup_models is in the deferred Pro
    feature set; condition_signals/normalizer load Ollama lazily at
    first-call time).
  - dotenv loading dropped — bullseye uses OS-level config + the
    license manager's cloud config rather than a .env file.
  - License kill-switch checked on every coordinator tick via jobs.py.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from ..db.events import record_event
from ..license.manager import license_manager
from .jobs import (
    coordinator_tick,
    drain_appraisal_safety_net,
    list_active_search_ids,
    send_daily_summary_emails,
    send_digest_emails,
)

logger = logging.getLogger(__name__)


POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "60"))  # legacy; ignored under coordinator
SAFETY_NET_INTERVAL_S = int(os.environ.get("SAFETY_NET_INTERVAL_S", "600"))
RELOAD_INTERVAL_S = int(os.environ.get("RELOAD_INTERVAL_S", "20"))
DIGEST_INTERVAL_S = int(os.environ.get("DIGEST_INTERVAL_S", "15"))
DAILY_SUMMARY_INTERVAL_S = int(os.environ.get("DAILY_SUMMARY_INTERVAL_S", "3600"))
COORDINATOR_TICK_S = int(os.environ.get("COORDINATOR_TICK_S", "20"))


# Module-level cache so we only emit a 'reload' event when the
# active-watch count actually CHANGES.
_last_reported_active_count: int | None = None


def _kill_switch_active() -> bool:
    """Wrapper that fails open when the license manager is a stub
    (NotImplementedError). Mirrors the helper in jobs.py."""
    try:
        return bool(license_manager.is_kill_switched())
    except NotImplementedError:
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("license kill-switch check raised: %s", e)
        return False


def _effective_coordinator_tick_s() -> int:
    """Coordinator tick — how often the scheduler picks ONE stale
    watch and polls it. The PER-WATCH poll cadence is naturally
    `tick * N` (round-robin), so we don't need to clamp the tick to
    the license minimum directly — we just need to ensure each
    watch's effective cadence respects it.

    Old behavior: `max(tick, license_min_s)` — for free tier with
    license_min_s=1800 (30 min), the FIRST watch didn't poll until 30
    minutes after boot. Newly-added watches showed "awaiting first
    poll" for half an hour, looking broken.

    New behavior: divide the licensed cadence across N watches. With
    1 watch and a 30-min floor, the tick is still 30 min (so we
    respect the license). With 5 watches, the tick drops to 6 min
    each — still gives every watch its 30-min cadence on average.
    Floor at COORDINATOR_TICK_S so we never go below the
    configured minimum (which is also our FB rate-gate spacing
    floor). Free user with 0 watches gets the configured tick.
    """
    configured = max(1, COORDINATOR_TICK_S)
    try:
        license_min_s = int(license_manager.poll_interval_min()) * 60
    except NotImplementedError:
        license_min_s = 0
    except Exception as e:  # noqa: BLE001
        logger.warning("license poll_interval_min raised: %s", e)
        license_min_s = 0
    if license_min_s <= 0:
        return configured
    n = max(1, len(list_active_search_ids()))
    # Per-watch cadence = tick * n.  We want tick * n >= license_min_s,
    # i.e. tick >= license_min_s / n.  Floor at configured.
    return max(configured, license_min_s // n)


def reload_searches(scheduler: BlockingScheduler) -> None:
    """Detect changes to the active-watch count and emit a 'reload'
    event only when something actually changed."""
    global _last_reported_active_count
    active = list_active_search_ids()
    n = len(active)
    tick_s = _effective_coordinator_tick_s()
    eff_per_watch_s = tick_s * max(n, 1)

    if _last_reported_active_count == n:
        return  # no change — silent

    if _last_reported_active_count is None:
        logger.info(
            "reload: %d active watch(es); coordinator tick %ds; "
            "effective per-watch poll cadence ~%d sec (~%.1f min)",
            n, tick_s, eff_per_watch_s, eff_per_watch_s / 60,
        )
        delta_msg = "boot"
    else:
        delta = n - _last_reported_active_count
        sign = "+" if delta > 0 else ""
        delta_msg = f"{sign}{delta}"
        logger.info(
            "reload: active watch count %d -> %d (%s)",
            _last_reported_active_count, n, delta_msg,
        )

    record_event(
        "reload",
        total_active=n,
        previous_active=_last_reported_active_count,
        delta=delta_msg,
        coordinator_tick_s=tick_s,
        effective_per_watch_s=eff_per_watch_s,
    )
    _last_reported_active_count = n


def _reload_tick(scheduler: BlockingScheduler) -> None:
    try:
        reload_searches(scheduler)
    except Exception as e:  # noqa: BLE001
        logger.exception("reload_searches failed: %s", e)


def _setup_logging() -> Path | None:
    """Install handlers for both stdout (live terminal feel) AND a
    rotating file at logs/scheduler.log.

    Returns the log file path or None if the file handler couldn't be
    installed.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)
    formatter = logging.Formatter(fmt)

    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setFormatter(formatter)
    root.addHandler(stdout_h)

    # Default log location: ~/.bullseye/logs/scheduler.log so it sits
    # next to the SQLite DB. Override-able for tests via BULLSEYE_LOG_DIR.
    log_dir = os.environ.get(
        "BULLSEYE_LOG_DIR",
        os.path.expanduser("~/.bullseye/logs"),
    )
    log_path = Path(log_dir) / "scheduler.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_h = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_h.setFormatter(formatter)
        root.addHandler(file_h)
        return log_path
    except OSError as e:
        logging.warning("could not open log file %s: %s (stdout only)", log_path, e)
        return None


def run_forever() -> int:
    log_path = _setup_logging()
    if log_path:
        logger.info("scheduler logging to %s", log_path)

    if _kill_switch_active():
        logger.error(
            "kill-switch active — refusing to start. Update Bullseye to "
            "the minimum supported version."
        )
        record_event("kill_switch_boot_refusal")
        return 1

    scheduler = BlockingScheduler(
        executors={"default": ThreadPoolExecutor(4)},
        timezone="UTC",
    )

    reload_searches(scheduler)

    tick_s = _effective_coordinator_tick_s()
    scheduler.add_job(
        coordinator_tick,
        trigger="interval",
        seconds=tick_s,
        id="coordinator",
        name="round-robin watch coordinator",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=2),
        misfire_grace_time=tick_s,
    )
    logger.info("coordinator tick every %ds", tick_s)

    scheduler.add_job(
        _reload_tick,
        args=[scheduler],
        trigger="interval",
        seconds=RELOAD_INTERVAL_S,
        id="reload_searches",
        name="reload active searches",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        drain_appraisal_safety_net,
        trigger="interval",
        seconds=SAFETY_NET_INTERVAL_S,
        id="safety_net",
        name="safety net appraisal drain",
        max_instances=1,
        coalesce=True,
    )

    # Stub jobs — see jobs.py. Keep them registered so the dashboard
    # can show "scheduled" + the rewrite in step 7 just swaps the
    # function bodies.
    scheduler.add_job(
        send_digest_emails,
        trigger="interval",
        seconds=DIGEST_INTERVAL_S,
        id="instant_alerts",
        name="send pending instant-alert emails",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        send_daily_summary_emails,
        trigger="interval",
        seconds=DAILY_SUMMARY_INTERVAL_S,
        id="daily_summary",
        name="send rolling daily-summary emails",
        max_instances=1,
        coalesce=True,
    )

    n_searches = len(list_active_search_ids())
    logger.info(
        "scheduler running: %d active search(es), tick=%ds, safety=%ds",
        n_searches, tick_s, SAFETY_NET_INTERVAL_S,
    )
    record_event(
        "scheduler_boot",
        pid=os.getpid(),
        n_active_searches=n_searches,
        coordinator_tick_s=tick_s,
        safety_net_interval_s=SAFETY_NET_INTERVAL_S,
        reload_interval_s=RELOAD_INTERVAL_S,
        digest_interval_s=DIGEST_INTERVAL_S,
    )
    if n_searches == 0:
        logger.warning(
            "NO active searches in user_searches table. "
            "Add some via the desktop UI's New Watch flow.",
        )

    def _shutdown(signum, frame):
        logger.info("received shutdown signal, stopping scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(run_forever())
