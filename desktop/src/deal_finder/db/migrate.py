"""SQLite migration runner.

Reads numbered .sql files from `migrations/` and applies any whose
version isn't yet recorded in `schema_version`. Runs once at app
startup, before any other module touches the DB.

File naming convention:
    NNN_short_description.sql

NNN is a zero-padded integer (3 digits is plenty for years of
migrations). The leading digits are the version. Anything after the
underscore is for human readability and ignored by the runner.

Migrations are append-only. Once a migration is shipped (i.e. some
user has run it), NEVER edit that file. Write a new migration that
does the correction.

Why a custom runner instead of Alembic:
    - Alembic adds ~5 MB to the PyInstaller bundle (sqlalchemy + deps)
    - Single-user single-process SQLite doesn't need Alembic's online
      migration / autogenerate / branch features
    - We want migrations to be plain .sql files anyone can audit, not
      Python ORM expressions
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .connection import get_connection

logger = logging.getLogger(__name__)

# Resolves to .../desktop/src/deal_finder/db/migrations regardless of
# whether we're running from source or from a PyInstaller bundle. In
# the bundle, `__file__` points into the unpacked temp dir.
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_VERSION_RE = re.compile(r"^(\d+)_.+\.sql$")


def _discover_migrations(directory: Path) -> list[tuple[int, Path]]:
    """Return sorted list of (version, path) for migration files."""
    if not directory.is_dir():
        return []
    out: list[tuple[int, Path]] = []
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        m = _VERSION_RE.match(entry.name)
        if not m:
            # Files that don't match the convention are silently
            # ignored — useful for README.md or .keep files in the
            # migrations directory.
            continue
        out.append((int(m.group(1)), entry))
    out.sort(key=lambda t: t[0])
    return out


def _ensure_version_table(conn) -> None:
    """Create schema_version if it doesn't exist. Idempotent — runs
    on every startup, no-op after first call."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _current_version(conn) -> int:
    row = conn.execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _apply_one(conn, version: int, path: Path) -> None:
    """Run one migration's SQL inside a transaction. If anything
    raises, the transaction rolls back so we never end up with a
    half-applied schema. The schema_version row is inserted in the
    SAME transaction as the schema changes, so partial-success is
    impossible: either everything in the file applied AND we recorded
    the version, or none of it did."""
    sql = path.read_text(encoding="utf-8")
    logger.info("applying migration %03d: %s", version, path.name)
    try:
        # `with conn` opens a transaction in autocommit-isolation_level=None mode
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (version,),
            )
    except Exception:
        logger.exception("migration %03d failed; rolled back", version)
        raise


def run_migrations(*, migrations_dir: os.PathLike[str] | None = None) -> int:
    """Apply all pending migrations. Returns the number of migrations
    applied (0 if everything was already up to date).

    Safe to call repeatedly. Called once at app startup before any
    other module touches the DB.
    """
    directory = Path(migrations_dir) if migrations_dir else _MIGRATIONS_DIR
    conn = get_connection()
    _ensure_version_table(conn)
    current = _current_version(conn)
    migrations = _discover_migrations(directory)

    pending = [(v, p) for v, p in migrations if v > current]
    if not pending:
        logger.debug("schema up to date at version %d", current)
        return 0

    logger.info(
        "applying %d migration(s): current=%d, target=%d",
        len(pending), current, pending[-1][0],
    )
    for version, path in pending:
        _apply_one(conn, version, path)

    new_version = _current_version(conn)
    logger.info("migrations complete; schema is at version %d", new_version)
    return len(pending)


def get_schema_version() -> int:
    """Return the current applied schema version. 0 if no migrations
    have run yet."""
    conn = get_connection()
    _ensure_version_table(conn)
    return _current_version(conn)
