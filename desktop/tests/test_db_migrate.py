"""Smoke tests for the SQLite connection + migration runner.

Run from the desktop/ directory:
    pytest tests/test_db_migrate.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make `src/` importable when running pytest from desktop/.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from deal_finder.db import connection, migrate


@pytest.fixture
def tmp_db(tmp_path):
    """Per-test fresh DB. Sets the override path, clears the
    thread-local cached connection, restores afterward."""
    db_path = tmp_path / "test.db"
    connection.set_db_path(str(db_path))
    connection.reset_for_tests()
    yield db_path
    connection.close_thread_connection()
    connection.set_db_path(None)


def test_connection_creates_db_and_directory(tmp_path):
    nested = tmp_path / "nested" / "subdir" / "bullseye.db"
    connection.set_db_path(str(nested))
    connection.reset_for_tests()
    try:
        conn = connection.get_connection()
        assert nested.parent.is_dir(), "parent directory not auto-created"
        # Round-trip a query to make sure the connection is real.
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close_thread_connection()
        connection.set_db_path(None)


def test_pragmas_applied(tmp_db):
    """Verify WAL mode + FK enforcement + sane busy_timeout."""
    conn = connection.get_connection()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000


def test_row_factory_returns_named_rows(tmp_db):
    conn = connection.get_connection()
    conn.execute("CREATE TABLE t (a TEXT, b INT)")
    conn.execute("INSERT INTO t VALUES ('hi', 7)")
    row = conn.execute("SELECT a, b FROM t").fetchone()
    assert row["a"] == "hi"
    assert row["b"] == 7


def test_migrations_apply_from_scratch(tmp_db):
    applied = migrate.run_migrations()
    assert applied >= 1, "expected at least the initial migration to run"

    # All the tables the personal tool relies on should now exist.
    conn = connection.get_connection()
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    expected = {
        "schema_version", "user_searches", "listings",
        "scheduler_events", "comps", "comps_meta",
        "city_geocache", "app_state",
    }
    missing = expected - tables
    assert not missing, f"missing tables after migration: {missing}"


def test_migrations_idempotent(tmp_db):
    """Running migrations twice should be a no-op the second time."""
    first = migrate.run_migrations()
    second = migrate.run_migrations()
    assert first >= 1
    assert second == 0


def test_schema_version_advances(tmp_db):
    assert migrate.get_schema_version() == 0
    migrate.run_migrations()
    assert migrate.get_schema_version() >= 1


def test_foreign_key_cascade_works(tmp_db):
    """Sanity check: ON DELETE SET NULL on listings.search_id requires
    PRAGMA foreign_keys=ON. Without that PRAGMA, the cascade silently
    no-ops and we'd have orphan listings pointing at deleted searches."""
    migrate.run_migrations()
    conn = connection.get_connection()
    with conn:
        conn.execute(
            "INSERT INTO user_searches (id, keyword) VALUES (1, 'arduino')"
        )
        conn.execute(
            """INSERT INTO listings (id, search_id, title)
               VALUES ('test-listing-1', 1, 'arduino uno r3')"""
        )
    # Delete the search; FK should null out the listing's search_id.
    with conn:
        conn.execute("DELETE FROM user_searches WHERE id = 1")
    row = conn.execute(
        "SELECT search_id FROM listings WHERE id = 'test-listing-1'"
    ).fetchone()
    assert row["search_id"] is None, "FK cascade did not fire"


def test_unknown_files_in_migrations_dir_ignored(tmp_db, tmp_path):
    """Files that don't match NNN_*.sql are skipped — lets us put
    README.md or .keep files in the migrations directory."""
    fake_dir = tmp_path / "migs"
    fake_dir.mkdir()
    # One real migration
    (fake_dir / "001_init.sql").write_text(
        "CREATE TABLE only_one (x INTEGER);"
    )
    # Three things that should be ignored
    (fake_dir / "README.md").write_text("# hello")
    (fake_dir / "draft.sql").write_text(
        "CREATE TABLE should_not_apply (x INTEGER);"
    )
    (fake_dir / "999.txt").write_text("nope")

    applied = migrate.run_migrations(migrations_dir=fake_dir)
    assert applied == 1

    conn = connection.get_connection()
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "only_one" in tables
    assert "should_not_apply" not in tables
