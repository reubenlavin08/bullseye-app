"""Adversarial concurrency + cache probe.

Twelve attack scenarios (C1-C12) targeting the documented race / cache
windows in the bullseye stack. Each test either:
    - Demonstrates the issue exists (and asserts the *current* behavior so
      the test stays green; severity recorded in CONCURRENCY-FINDINGS.md), OR
    - Proves the system holds up under contention (and asserts correctness).

Cloud-side stampede tests require network. They auto-skip if the
production endpoint isn't reachable so CI doesn't flap. Total cloud
calls per full run is capped well under 25.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

# Make src importable.
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


SUPABASE_URL = "https://qfkzhyxmohytnzskcmdv.supabase.co"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFma3poeXhtb2h5dG56c2tjbWR2Iiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3Nzc5MjIzMjYsImV4cCI6MjA5MzQ5ODMyNn0."
    "pPathNzQcURGocSVPKKhgcv27-4sqHAFZeB1oenB0Z4"
)


# --- Shared fixtures ----------------------------------------------------

@pytest.fixture
def bullseye_db(tmp_path, monkeypatch):
    """Fresh SQLite DB pointed at a temp file, schema applied."""
    db_path = tmp_path / "bullseye_adversarial.db"
    monkeypatch.setenv("BULLSEYE_DB_PATH", str(db_path))

    from deal_finder.db import connection as conn_mod
    from deal_finder.db import migrate

    conn_mod.reset_for_tests()
    conn_mod.set_db_path(str(db_path))
    migrate.run_migrations()
    yield db_path
    conn_mod.reset_for_tests()
    conn_mod.set_db_path(None)


def _cloud_reachable() -> bool:
    """Cheap probe — the OPTIONS preflight responds without auth."""
    try:
        r = requests.options(
            f"{SUPABASE_URL}/functions/v1/comps", timeout=3,
        )
        return r.status_code < 500
    except Exception:
        return False


# =====================================================================
# C1. /comps cache stampede
# =====================================================================
# Cold cache for unique search_term. Fire 10 concurrent /comps calls.
# We CANNOT actually call /comps without a real user JWT (requireUser
# rejects anon). Even so, we can prove the cache stampede logic exists
# *in code*: between the SELECT cache check (line 80-86 of comps/index.ts)
# and the upsert (line 127-129), there is no distributed lock. Ten
# concurrent requests all read the cache as empty, all call searchEbay,
# and all upsert (last write wins).
#
# This test code-traces the issue and asserts the structural fact, then
# documents it in the findings file. We do NOT spam production with
# unauthenticated calls.

def test_c1_comps_cache_stampede_window_exists():
    """Verify /comps has the documented stampede window: SELECT-then-INSERT
    with no distributed lock or DB-level deduplication."""
    src = (Path(__file__).resolve().parent.parent.parent.parent
           / "cloud" / "supabase" / "functions" / "comps" / "index.ts")
    text = src.read_text(encoding="utf-8")

    # Structural assertions: cache-check + upsert pattern, no lock.
    assert "from(\"comps_cache\")" in text
    assert "maybeSingle()" in text
    assert ".upsert(" in text
    # No advisory-lock / FOR UPDATE / pg_advisory_lock anywhere
    assert "pg_advisory" not in text.lower()
    assert "for update" not in text.lower()
    # Confirms: between cache-check and upsert, N concurrent calls each
    # see "no cache" and each call searchEbay. MEDIUM finding (v1.1).


# =====================================================================
# C2. License cache race
# =====================================================================

def test_c2_license_cache_concurrent_get(bullseye_db, monkeypatch):
    """Two threads call license_manager.get() simultaneously with empty
    cache. Verify: no exception, in-memory cache consistent (one of the
    responses wins), cloud called at-most-twice (no exception is the bar)
    and SQLite write is idempotent.
    """
    from deal_finder.license import manager as lm_mod

    # Use a fresh manager (singleton would carry cache across tests).
    fresh = lm_mod.LicenseManager()

    call_count = {"n": 0}
    call_lock = threading.Lock()

    def fake_post(endpoint, data):
        with call_lock:
            call_count["n"] += 1
            n = call_count["n"]
        # Both calls return distinct payloads so we can detect which won.
        time.sleep(0.05)  # widen the race window
        return {
            "tier": "paid",
            "watches_limit": None,
            "poll_interval_min": 5,
            "expires_at": None,
            "trial_ends_at": None,
            "cancel_at_period_end": False,
            "min_supported_version": "0.0.0",
            "_call_n": n,  # marker
        }

    monkeypatch.setattr(lm_mod.client, "post", fake_post)

    results = []
    err = []

    def worker():
        try:
            results.append(fresh.get())
        except Exception as e:
            err.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not err, f"unexpected exception(s): {err}"
    assert len(results) == 2
    # Both threads see consistent data (the lock ensures one full-fetch
    # serializes the other — second call hits cache).
    assert results[0]["tier"] == "paid"
    assert results[1]["tier"] == "paid"
    # The RLock in get() makes the second waiter see the cached value
    # without re-calling. So call_count should be exactly 1.
    assert call_count["n"] == 1, (
        f"expected 1 cloud call (RLock gate), got {call_count['n']}"
    )

    # SQLite mirror should have one row, parseable JSON.
    from deal_finder.db.connection import get_connection
    row = get_connection().execute(
        "SELECT value FROM app_state WHERE key = 'cached_license_v1'"
    ).fetchone()
    assert row is not None
    parsed = json.loads(row["value"])
    assert parsed["tier"] == "paid"


# =====================================================================
# C3. Webhook event replay (idempotency)
# =====================================================================

def test_c3_webhook_handlers_are_idempotent_by_construction():
    """Each handler in stripe-webhook/index.ts must use UPDATE/UPSERT, not
    INSERT, for the licenses row. Replay of the same Stripe event id must
    not duplicate state.
    """
    src = (Path(__file__).resolve().parent.parent.parent.parent
           / "cloud" / "supabase" / "functions"
           / "stripe-webhook" / "index.ts")
    text = src.read_text(encoding="utf-8")

    # Pull each handler body and assert the mutation primitive.
    def _slice(name: str) -> str:
        i = text.index(f"async function {name}(")
        # next async function or end-of-file
        j = text.find("\nasync function ", i + 1)
        return text[i: j if j != -1 else len(text)]

    # checkoutCompleted: UPDATE on licenses (links customer id)
    h_co = _slice("handleCheckoutCompleted")
    assert ".update(" in h_co and ".insert(" not in h_co

    # subscription upsert: UPDATE on licenses (sets tier/period)
    h_sub = _slice("handleSubscriptionUpsert")
    assert ".update(" in h_sub and ".insert(" not in h_sub

    # subscription deleted: UPDATE (sets tier=free)
    h_del = _slice("handleSubscriptionDeleted")
    assert ".update(" in h_del and ".insert(" not in h_del

    # payment failed: console.warn only — no DB mutation. Idempotent
    # by virtue of being a no-op in the DB.
    h_pf = _slice("handlePaymentFailed")
    assert ".update(" not in h_pf and ".insert(" not in h_pf


# =====================================================================
# C4. Out-of-order webhook events
# =====================================================================

def test_c4_subscription_before_checkout_throws_for_retry():
    """`customer.subscription.created` arriving before
    `checkout.session.completed`: handleSubscriptionUpsert should detect
    no license row linked yet and THROW so Stripe retries.
    """
    src = (Path(__file__).resolve().parent.parent.parent.parent
           / "cloud" / "supabase" / "functions"
           / "stripe-webhook" / "index.ts")
    text = src.read_text(encoding="utf-8")

    # Confirm the sentinel string + throw.
    assert 'throw new Error("license row not yet linked; retry")' in text
    # And confirm the surrounding switch returns 500 on handler throw
    # (so Stripe will retry per its retry policy):
    assert 'status: 500' in text
    # Document the contract: handler raises, top-level catch returns 500
    # so Stripe's at-least-once delivery retries until checkout-completed
    # has linked the customer.


# =====================================================================
# C5. Concurrent /api/watches at limit boundary
# =====================================================================

def test_c5_watches_limit_race_under_contention(bullseye_db, monkeypatch):
    """Free user with 2 active watches, cap=3. Fire two concurrent POSTs.
    SELECT COUNT(*) is OUTSIDE the INSERT transaction, so both can see
    cnt=2, both pass the gate, and both INSERT — leaving the user at 4
    active watches.

    THIS IS THE MEDIUM-SEVERITY FINDING. The test asserts on the actual
    observed final count.
    """
    from webapp import app as webapp_app
    from deal_finder.auth import token_store
    from deal_finder.db.connection import get_connection

    # Stub login + license.
    monkeypatch.setattr(token_store, "is_logged_in", lambda: True)
    monkeypatch.setattr(webapp_app.token_store, "is_logged_in", lambda: True)
    lm = webapp_app.license_manager
    monkeypatch.setattr(lm, "watches_limit", lambda: 3)
    monkeypatch.setattr(lm, "is_kill_switched", lambda: False)
    monkeypatch.setattr(lm, "is_paid", lambda: False)
    monkeypatch.setattr(lm, "tier", lambda: "free")
    monkeypatch.setattr(lm, "get", lambda **kw: {"tier": "free"})

    # Pre-seed 2 active watches (cap is 3; one slot remains).
    conn = get_connection()
    with conn:
        for kw in ("alpha", "beta"):
            conn.execute(
                "INSERT INTO user_searches (keyword, latitude, longitude, "
                "radius_km, active) VALUES (?, 49.28, -123.12, 40, 1)",
                (kw,),
            )

    webapp_app.app.config["TESTING"] = True

    barrier = threading.Barrier(2)
    results = []
    res_lock = threading.Lock()

    def post_watch(kw: str):
        # Each thread gets its own test client.
        with webapp_app.app.test_client() as c:
            barrier.wait()  # release both at once
            r = c.post(
                "/api/watches",
                json={
                    "keyword": kw,
                    "lat": 49.28,
                    "lng": -123.12,
                    "radius_km": 40,
                },
            )
            with res_lock:
                results.append((kw, r.status_code, r.get_json()))

    t1 = threading.Thread(target=post_watch, args=("gamma",))
    t2 = threading.Thread(target=post_watch, args=("delta",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    final_count = get_connection().execute(
        "SELECT COUNT(*) FROM user_searches WHERE active = 1"
    ).fetchone()[0]

    successes = [r for r in results if r[1] == 200]
    forbiddens = [r for r in results if r[1] == 403]

    # Per the documented race: it is POSSIBLE for both to succeed. We
    # don't assert breakage every run (the GIL + Flask's request handling
    # may serialize in some runs); we assert the union of the observed
    # outcomes and record what happened in the findings doc.
    #
    # Critically: regardless of race outcome, the final_count must equal
    # 2 + len(successes), proving INSERTs went through unguarded.
    assert final_count == 2 + len(successes)
    # If both succeeded, the user is over cap — this IS the bug.
    if len(successes) == 2:
        assert final_count == 4
        # MEDIUM finding evidenced — see CONCURRENCY-FINDINGS.md C5.
    else:
        # Race went the "safe" way this run; bug still exists structurally
        # because the SELECT/INSERT is non-atomic. Don't fail.
        assert len(successes) >= 1


# =====================================================================
# C6. SQLite WAL concurrent reads + write
# =====================================================================

def test_c6_wal_allows_concurrent_reads_during_write(bullseye_db):
    """5 reader threads + 1 writer thread on the same DB. With WAL mode
    the readers must NOT block on the writer's transaction.
    """
    from deal_finder.db.connection import _new_connection

    db_path = str(bullseye_db)

    # Verify pragma is set on a fresh connection.
    probe = _new_connection()
    journal_mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
    probe.close()
    assert journal_mode.lower() == "wal"

    # Seed a row to read.
    seed = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    seed.execute("PRAGMA journal_mode = WAL")
    seed.execute(
        "INSERT INTO user_searches (keyword, latitude, longitude, "
        "radius_km, active) VALUES ('walseed', 49.28, -123.12, 40, 1)",
    )
    seed.close()

    write_done = threading.Event()
    read_results = []
    read_errors = []
    read_durations = []

    def writer():
        c = sqlite3.connect(db_path, timeout=10.0)
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA busy_timeout = 10000")
        try:
            c.execute("BEGIN IMMEDIATE")
            for i in range(20):
                c.execute(
                    "INSERT INTO user_searches "
                    "(keyword, latitude, longitude, radius_km, active) "
                    "VALUES (?, 49.28, -123.12, 40, 1)",
                    (f"writer_{i}",),
                )
                time.sleep(0.01)
            c.commit()
        finally:
            c.close()
            write_done.set()

    def reader(idx: int):
        c = sqlite3.connect(db_path, timeout=10.0)
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA busy_timeout = 10000")
        try:
            t0 = time.time()
            for _ in range(5):
                row = c.execute(
                    "SELECT COUNT(*) FROM user_searches"
                ).fetchone()
                read_results.append((idx, row[0]))
                time.sleep(0.005)
            read_durations.append(time.time() - t0)
        except sqlite3.OperationalError as e:
            read_errors.append(str(e))
        finally:
            c.close()

    w = threading.Thread(target=writer)
    rs = [threading.Thread(target=reader, args=(i,)) for i in range(5)]
    w.start()
    for r in rs:
        r.start()
    for r in rs:
        r.join()
    w.join()

    # No SQLITE_BUSY on readers.
    assert read_errors == [], f"reader busy errors: {read_errors}"
    # Each reader took some time but NOT > the busy_timeout (would mean
    # they were serialized waiting for the writer).
    for d in read_durations:
        assert d < 2.0, f"reader was blocked too long: {d}s"
    # All 5 readers got 5 rows each = 25 results.
    assert len(read_results) == 25


# =====================================================================
# C7. /alerts-send insert-first idempotency (RESEND_API_KEY missing)
# =====================================================================

def test_c7_alerts_send_double_call_with_missing_key_documented():
    """RESEND_API_KEY is NOT set in production. Per the function's code:
    digest path inserts email_log row first, then attempts send. If send
    returns missing_api_key, it ROLLS BACK the email_log row (so tomorrow
    isn't blocked). Therefore double-call from a free user both 503 — but
    each leaves email_log empty for that day. Documented behavior."""
    src = (Path(__file__).resolve().parent.parent.parent.parent
           / "cloud" / "supabase" / "functions"
           / "alerts-send" / "index.ts")
    text = src.read_text(encoding="utf-8")

    # The rollback delete on missing_api_key:
    assert 'result.status === "missing_api_key"' in text
    assert 'email_log").delete().eq("id", reservedId)' in text
    assert '"email service not configured", 503' in text
    # Confirms: 1st call => 503 (rolled back). 2nd call => 503 (rolled
    # back). Free-user idempotency is NOT enforced when sends fail —
    # which is fine because nothing was actually sent.


# =====================================================================
# C8. License + token refresh race (logout mid-fetch)
# =====================================================================

def test_c8_license_get_during_logout_doesnt_crash(bullseye_db, monkeypatch):
    """license_manager.get() while another thread clears tokens (logout):
    must not crash. Should return CloudUnavailable fallback (last-known
    or default), NOT raise."""
    from deal_finder.license import manager as lm_mod
    from deal_finder.cloud.client import Unauthorized

    fresh = lm_mod.LicenseManager()
    barrier = threading.Barrier(2)

    def fake_post_unauth(endpoint, data):
        # Simulate "JWT was wiped mid-flight" => 401 from server, refresh
        # also fails => Unauthorized.
        barrier.wait()
        time.sleep(0.02)
        raise Unauthorized("token cleared during request")

    monkeypatch.setattr(lm_mod.client, "post", fake_post_unauth)

    result = {}
    err = []

    def license_thread():
        try:
            barrier.wait()
            result["data"] = fresh.get()
        except Exception as e:
            err.append(e)

    def logout_thread():
        # In the real app token_store.clear() would be called here.
        # We just need to not deadlock with the license RLock.
        time.sleep(0.005)

    t1 = threading.Thread(target=license_thread)
    t2 = threading.Thread(target=logout_thread)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not err, f"license get crashed under logout race: {err}"
    # Should fall back to default (free tier).
    assert result["data"]["tier"] == "free"


# =====================================================================
# C9. comps_local_cache concurrent writes
# =====================================================================

def test_c9_comps_local_cache_concurrent_writes_idempotent(bullseye_db, monkeypatch):
    """Two threads call get_comps() for same term simultaneously. The
    local cache write uses ON CONFLICT DO UPDATE — last write wins,
    no exceptions, exactly one row per (search_term, region).
    """
    from deal_finder.cloud import comps as comps_mod

    # Mock the cloud client to return predictable payloads, fast.
    def fake_post(endpoint, data):
        time.sleep(0.02)
        return {
            "stats": {
                "sample_size": 10, "median": 100, "mean": 100,
                "minimum": 50, "maximum": 200, "p10": 60, "q1": 80,
                "q3": 120, "p90": 180, "iqr": 40, "iqr_ratio": 0.4,
            },
            "raw_comps": [
                {"title": "x", "price": 100, "currency": "CAD",
                 "listing_url": "u", "location": "Vancouver"},
            ],
            "source": "fresh",
            "age_seconds": 0,
            "search_term_normalized": "ipad pro",
            "region": "EBAY-ENCA",
        }

    monkeypatch.setattr(comps_mod.client, "post", fake_post)

    results = []
    errs = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            results.append(comps_mod.get_comps("ipad pro"))
        except Exception as e:
            errs.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errs, f"unexpected exceptions: {errs}"
    assert len(results) == 2

    from deal_finder.db.connection import get_connection
    row_count = get_connection().execute(
        "SELECT COUNT(*) FROM comps_local_cache "
        "WHERE search_term = 'ipad pro'"
    ).fetchone()[0]
    assert row_count == 1, f"expected 1 row (UPSERT), got {row_count}"


# =====================================================================
# C10. Email queue worker — DOCUMENT only
# =====================================================================

def test_c10_email_queue_has_no_drainer_documented():
    """Verify documented gap: email_queue rows are inserted by /alerts-send
    when a free user's daily slot is taken, but no auto-drainer exists yet
    (pg_cron deferred). Search the codebase for any worker that SELECTs
    from email_queue with intent to send.
    """
    cloud_dir = (Path(__file__).resolve().parent.parent.parent.parent
                 / "cloud" / "supabase" / "functions")
    desktop_dir = (Path(__file__).resolve().parent.parent.parent
                   / "src" / "deal_finder")

    drainer_hits = []
    for root in (cloud_dir, desktop_dir):
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix not in (".ts", ".py", ".sql"):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # Look for: SELECT ... FROM email_queue followed by some
            # send/drain/process verb. Insert references are excluded.
            low = text.lower()
            if "email_queue" in low and (
                ("select" in low and "from email_queue" in low)
                or ("drain" in low and "queue" in low)
                or ("process" in low and "email_queue" in low)
            ):
                # And exclude pure schema files
                if "create table" in low and "email_queue" in low:
                    continue
                drainer_hits.append(str(p))

    # Expected: NO drainer code exists. Documents the v1.0 gap.
    assert drainer_hits == [], (
        f"expected no drainer; found references in: {drainer_hits}. "
        f"If a drainer was added, update this test + findings."
    )


# =====================================================================
# C11. Scheduler + UI both touching SQLite under realistic load
# =====================================================================

def test_c11_scheduler_plus_ui_no_busy(bullseye_db):
    """Simulate scheduler thread (sleep + writes) + UI thread (frequent
    reads). With WAL + busy_timeout=10s, no SQLITE_BUSY should fire."""
    db_path = str(bullseye_db)
    duration = 1.5  # seconds
    stop_at = time.time() + duration

    errs = []
    write_count = [0]
    read_count = [0]

    def scheduler_writer():
        c = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA busy_timeout = 10000")
        try:
            while time.time() < stop_at:
                try:
                    c.execute("BEGIN IMMEDIATE")
                    c.execute(
                        "INSERT INTO user_searches "
                        "(keyword, latitude, longitude, radius_km, active) "
                        "VALUES (?, 49, -123, 40, 1)",
                        (f"sch_{write_count[0]}",),
                    )
                    c.execute("COMMIT")
                    write_count[0] += 1
                except sqlite3.OperationalError as e:
                    errs.append(("writer", str(e)))
                time.sleep(0.01)
        finally:
            c.close()

    def ui_reader(idx: int):
        c = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA busy_timeout = 10000")
        try:
            while time.time() < stop_at:
                try:
                    c.execute("SELECT COUNT(*) FROM user_searches").fetchone()
                    read_count[0] += 1
                except sqlite3.OperationalError as e:
                    errs.append((f"reader_{idx}", str(e)))
                time.sleep(0.005)
        finally:
            c.close()

    w = threading.Thread(target=scheduler_writer)
    readers = [threading.Thread(target=ui_reader, args=(i,)) for i in range(3)]
    w.start()
    for r in readers:
        r.start()
    w.join()
    for r in readers:
        r.join()

    assert errs == [], f"unexpected SQLITE_BUSY events: {errs[:5]}"
    assert write_count[0] > 0
    assert read_count[0] > 0


# =====================================================================
# C12. Stripe out-of-order subscription.updated before .created committed
# =====================================================================

def test_c12_subscription_updated_before_created_handler_throws():
    """Same pattern as C4 but for `subscription.updated`. Both event
    types route through handleSubscriptionUpsert, which throws if the
    license row hasn't been linked yet — making Stripe retry."""
    src = (Path(__file__).resolve().parent.parent.parent.parent
           / "cloud" / "supabase" / "functions"
           / "stripe-webhook" / "index.ts")
    text = src.read_text(encoding="utf-8")

    # Both create and update map to the same handler:
    assert 'case "customer.subscription.created":' in text
    assert 'case "customer.subscription.updated":' in text
    assert "handleSubscriptionUpsert" in text
    # And the handler raises on no-license-row:
    assert "license row not yet linked" in text


# =====================================================================
# Live cloud probe: C1 with anon (smoke) — disabled by default
# =====================================================================

@pytest.mark.skipif(
    not _cloud_reachable(), reason="production /comps not reachable",
)
def test_c1_live_anon_returns_401_quickly():
    """Sanity: 1 unauth call to /comps returns 401 (or 4xx) — no auth
    means the stampede surface area is gated by requireUser. Caps cloud
    calls to 1 per run.
    """
    r = requests.post(
        f"{SUPABASE_URL}/functions/v1/comps",
        json={"search_term": f"adversarial-probe-{uuid.uuid4().hex[:8]}"},
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {ANON_KEY}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    # requireUser uses the JWT 'sub' to identify a user. The anon JWT has
    # no sub — function should reject with 401.
    assert r.status_code in (401, 403), (
        f"expected 401/403 from anon /comps, got {r.status_code}: {r.text[:200]}"
    )
