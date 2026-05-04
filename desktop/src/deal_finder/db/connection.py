"""SQLite connection helper.

Replaces the Postgres connection pool from the personal tool. SQLite
in WAL mode handles concurrent reads from multiple threads + one
writer, which is exactly the scheduler's access pattern (one writer
appending listings, multiple readers — webapp, dashboard, digest
worker).

Path: `~/.bullseye/bullseye.db`
    - Windows: `C:\\Users\\<user>\\.bullseye\\bullseye.db`
    - macOS:   `~/.bullseye/bullseye.db`
    - Linux:   `~/.bullseye/bullseye.db`

WAL mode trade-offs:
    + Concurrent reads + one writer (vs. default journal which
      serializes everything)
    + Less write fsync overhead
    + Crash-safe
    - Three files instead of one (.db, .db-wal, .db-shm). Fine for
      our use case — installer can ignore the wal/shm files.

`foreign_keys=ON` is critical for the ON DELETE CASCADE behavior
the migrations depend on. SQLite ships with FKs OFF by default
(historical compatibility quirk).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

# DB location — overridable for tests + dev.
_DB_PATH_OVERRIDE: str | None = None
_DEFAULT_REL_PATH = ".bullseye/bullseye.db"


def get_db_path() -> str:
    """Resolve the DB path. Honors BULLSEYE_DB_PATH env var first,
    then any test override set via `set_db_path`, then defaults to
    `~/.bullseye/bullseye.db`. Creates the parent directory on first
    call so `sqlite3.connect` doesn't fail on a missing folder."""
    if env_override := os.environ.get("BULLSEYE_DB_PATH"):
        path = env_override
    elif _DB_PATH_OVERRIDE is not None:
        path = _DB_PATH_OVERRIDE
    else:
        path = os.path.expanduser(f"~/{_DEFAULT_REL_PATH}")
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def set_db_path(path: str | None) -> None:
    """Test hook: override the DB path globally. Pass None to clear."""
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


# Per-thread connection cache. SQLite connections are not safe to
# share across threads even with check_same_thread=False (it just
# disables the check, doesn't make it safe). One conn per thread is
# the correct model.
_thread_local = threading.local()


def _new_connection() -> sqlite3.Connection:
    """Open a fresh connection with our standard PRAGMAs applied."""
    path = get_db_path()
    conn = sqlite3.connect(
        path,
        timeout=10.0,            # wait up to 10s for the writer's lock
        isolation_level=None,    # autocommit mode; we manage txns
                                  #   explicitly via `with conn:` blocks
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")  # WAL-safe + faster than FULL
    conn.execute("PRAGMA busy_timeout = 10000")  # 10s before SQLITE_BUSY
    return conn


def get_connection() -> sqlite3.Connection:
    """Return THIS THREAD's connection. Creates one on first call.
    Caller does not close it — connections live as long as the thread."""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = _new_connection()
        _thread_local.conn = conn
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Context-manager flavor for symmetry with the personal tool's API.

    Usage:
        with get_conn() as conn:
            with conn:                    # inner = transaction
                conn.execute("INSERT ...")

    The outer `with get_conn()` does NOT commit/rollback (since the
    connection is shared across the thread). The inner `with conn`
    is a transaction that auto-commits on exit, rolls back on
    exception. This matches the existing `with get_conn() as conn: with conn:`
    idiom used throughout the personal tool's codebase.
    """
    yield get_connection()


def close_thread_connection() -> None:
    """Explicitly close THIS THREAD's connection. Used in tests + on
    app shutdown. Idempotent."""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        conn.close()
        _thread_local.conn = None


def reset_for_tests() -> None:
    """Test hook: drop the cached connection so the next `get_connection`
    call opens a fresh one against whatever path is currently set."""
    close_thread_connection()
