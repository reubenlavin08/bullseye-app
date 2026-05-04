"""SQLite connection helper.

Replaces the existing psycopg2-based connection pool from the personal
tool. SQLite handles single-process concurrency fine via WAL mode.

Path: `~/.bullseye/bullseye.db` (resolves to `%USERPROFILE%/.bullseye/`
on Windows). Created on first call.
"""
from __future__ import annotations

import os

DB_PATH = os.path.expanduser("~/.bullseye/bullseye.db")


def get_connection():
    """Return a new sqlite3 connection. Caller is responsible for
    closing or using `with` semantics. Row factory is sqlite3.Row so
    rows act like dicts.
    """
    # TODO:
    # import sqlite3
    # os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    # conn.row_factory = sqlite3.Row
    # conn.execute('PRAGMA journal_mode=WAL')
    # conn.execute('PRAGMA foreign_keys=ON')
    # return conn
    raise NotImplementedError


# Optional context-manager wrapper for `with get_conn() as conn:` ergonomics.
def get_conn():
    """Same as get_connection() but supports `with` blocks."""
    raise NotImplementedError
