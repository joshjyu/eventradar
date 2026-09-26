"""SQLite connection handling and the migration runner."""

import re
import sqlite3
from importlib.resources import files
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def connect(path: Path) -> sqlite3.Connection:
    """
    Open the state database with safe defaults.

    Parameters:
      path: Database file; created if absent.
    Returns:
      An open connection with foreign keys enforced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn


def _migration_scripts() -> list[tuple[int, str, str]]:
    """
    Read packaged migration scripts in version order.

    Returns:
      Tuples of (version, file name, SQL text).
    """
    scripts = []
    for entry in files("eventradar.storage.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            text = entry.read_text(encoding="utf-8")
            scripts.append((int(match.group(1)), entry.name, text))
    return sorted(scripts)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """
    Apply every migration not yet recorded, each in its own transaction.

    Parameters:
      conn: Open connection.
    Returns:
      File names of the migrations applied by this call.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, name, sql in _migration_scripts():
        if version in done:
            continue
        # Names are regex-validated, so interpolation cannot inject SQL.
        try:
            conn.executescript(
                f"BEGIN;\n{sql}\n"
                f"INSERT INTO schema_migrations VALUES ({version}, '{name}');"
                "\nCOMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(name)
    return applied


def check_integrity(conn: sqlite3.Connection) -> None:
    """
    Fail loudly if SQLite reports corruption.

    Parameters:
      conn: Open connection.
    """
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise sqlite3.DatabaseError(f"integrity check failed: {result}")
