"""SQLite migration runner. Reads numbered .sql files from migrations/
and applies any not yet recorded in `schema_version`.

Migration files are append-only. Never edit a migration after it's
been released — write a new one. The `schema_version` table records
which versions have been applied.

Why this is a custom runner instead of Alembic: the desktop app is a
single-user single-process SQLite store. Alembic is overkill, and its
dependency footprint inflates the PyInstaller bundle.
"""
from __future__ import annotations


def run_migrations() -> None:
    """Apply all pending migrations. Called once at app startup,
    before anything else touches the DB.

    Behavior:
        - Creates schema_version table if missing
        - Reads max(version) from schema_version
        - For each migration_NNN_*.sql file with version > current:
          executescript(content) + INSERT INTO schema_version (NNN)
        - Commits at the end of each migration so a partial-failure
          doesn't leave us in a half-state
    """
    raise NotImplementedError
