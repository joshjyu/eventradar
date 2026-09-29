"""Upgrading databases created by earlier releases."""

import sqlite3
from pathlib import Path

from eventradar.storage.db import _migration_scripts, connect, migrate


def _database_at(path: Path, version: int) -> sqlite3.Connection:
    """
    Create a database as an older release left it.

    Parameters:
      path: Database file.
      version: Last migration to apply.
    Returns:
      Open connection.
    """
    conn = connect(path)
    conn.execute(
        "CREATE TABLE schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    )
    for number, name, sql in _migration_scripts():
        if number <= version:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)", (number, name)
            )
    return conn


def test_0003_backfills_primary_source(tmp_path: Path) -> None:
    """Events from before 0003 get a primary source from provenance."""
    conn = _database_at(tmp_path / "old.db", 2)
    conn.execute(
        "INSERT INTO runs VALUES ('r', '2026-09-01T00:00:00+00:00', NULL, 'ok')"
    )
    conn.execute(
        "INSERT INTO events (event_id, title, start_utc, attendance_mode, "
        "status, kinds, first_seen, last_seen) VALUES ('E1', 'T', "
        "'2026-10-01T00:00:00.000000+00:00', 'unknown', 'scheduled', '[]', "
        "'2026-09-01T00:00:00.000000+00:00', "
        "'2026-09-01T00:00:00.000000+00:00')"
    )
    for source in ("src-b", "src-a"):
        conn.execute(
            "INSERT INTO event_sources VALUES (?, 'n', 'E1', NULL, 'h')",
            (source,),
        )
    assert migrate(conn)[0] == "0003_geocode_priority_aliases.sql"
    primary = conn.execute(
        "SELECT primary_source FROM events WHERE event_id = 'E1'"
    ).fetchone()[0]
    assert primary == "src-a"
    assert migrate(conn) == []
