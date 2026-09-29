"""Tests for the SQLite state database and repositories."""

import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import pytest

from eventradar.domain.models import EventDraft, RawRecord
from eventradar.storage.db import check_integrity, connect, migrate
from eventradar.storage.repositories import (
    EventRepository,
    RawRecordRepository,
    RunRepository,
    transaction,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """
    Open a migrated database with one started run.

    Parameters:
      tmp_path: Pytest temporary directory.
    Returns:
      Open connection.
    """
    db = connect(tmp_path / "state.db")
    migrate(db)
    RunRepository(db).start("run-1", NOW)
    return db


def _draft(native_id: str = "a", **overrides: object) -> EventDraft:
    """
    Build a draft with sensible defaults.

    Parameters:
      native_id: Upstream id.
      overrides: Field overrides.
    Returns:
      Draft model.
    """
    fields: dict[str, object] = {
        "source_id": "src-a",
        "native_id": native_id,
        "title": "Hack Night",
        "start_utc": NOW + timedelta(days=3),
    }
    fields.update(overrides)
    return EventDraft.model_validate(fields)


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    """A second migrate applies nothing and the DB stays healthy."""
    assert migrate(conn) == []
    check_integrity(conn)


def test_raw_insert_skips_unchanged_content(conn: sqlite3.Connection) -> None:
    """Identical payloads are stored once; changes are kept as history."""
    repo = RawRecordRepository(conn)
    rec = RawRecord(
        source_id="src-a", native_id="1", payload={"v": 1}, fetched_at=NOW
    )
    assert repo.insert_if_changed(rec, "run-1")
    assert not repo.insert_if_changed(rec, "run-1")
    newer = rec.model_copy(
        update={"payload": {"v": 2}, "fetched_at": NOW + timedelta(hours=1)}
    )
    assert repo.insert_if_changed(newer, "run-1")
    assert [r.payload for r in repo.latest()] == [{"v": 2}]


def test_upsert_creates_then_updates(conn: sqlite3.Connection) -> None:
    """The same source record maps to one stable event id."""
    ids = count(1)
    repo = EventRepository(conn, id_factory=lambda: f"E{next(ids)}")
    first = repo.upsert_draft(_draft(), "h1", NOW)
    second = repo.upsert_draft(_draft(title="Renamed"), "h2", NOW)
    assert first.created
    assert not second.created
    assert first.event_id == second.event_id == "E1"
    repo.link_profile("E1", "p", NOW)
    [event] = repo.upcoming("p", NOW)
    assert event.title == "Renamed"
    assert event.sources[0].source_id == "src-a"


def test_profile_windows(conn: sqlite3.Connection) -> None:
    """Only events added in the window and not yet started are new."""
    repo = EventRepository(conn)
    past = repo.upsert_draft(
        _draft("old", start_utc=NOW - timedelta(days=1)), "h", NOW
    )
    future = repo.upsert_draft(_draft("new"), "h", NOW)
    for r in (past, future):
        assert repo.link_profile(r.event_id, "p", NOW)
    assert not repo.link_profile(future.event_id, "p", NOW)
    added = repo.added_between("p", NOW, NOW + timedelta(days=1), NOW)
    assert [e.event_id for e in added] == [future.event_id]
    assert [e.event_id for e in repo.upcoming("p", NOW)] == [future.event_id]


def test_transaction_rolls_back_on_error(conn: sqlite3.Connection) -> None:
    """A failing block leaves no partial writes."""
    repo = EventRepository(conn)
    with pytest.raises(RuntimeError), transaction(conn):
        repo.upsert_draft(_draft(), "h", NOW)
        raise RuntimeError
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_raw_insert_detects_reverted_content(
    conn: sqlite3.Connection,
) -> None:
    """Content that reverts to an earlier version is a change."""
    repo = RawRecordRepository(conn)
    base = RawRecord(
        source_id="src-a", native_id="1", payload={"v": 1}, fetched_at=NOW
    )
    changed = base.model_copy(
        update={"payload": {"v": 2}, "fetched_at": NOW + timedelta(hours=1)}
    )
    reverted = base.model_copy(update={"fetched_at": NOW + timedelta(hours=2)})
    assert repo.insert_if_changed(base, "run-1")
    assert repo.insert_if_changed(changed, "run-1")
    assert repo.insert_if_changed(reverted, "run-1")
    assert [r.payload for r in repo.latest()] == [{"v": 1}]


def test_latest_one_returns_newest_payload(conn: sqlite3.Connection) -> None:
    """Adapters can look up the newest stored version of one record."""
    repo = RawRecordRepository(conn)
    old = RawRecord(
        source_id="src-a", native_id="1", payload={"v": 1}, fetched_at=NOW
    )
    repo.insert_if_changed(old, "run-1")
    repo.insert_if_changed(
        old.model_copy(
            update={"payload": {"v": 2}, "fetched_at": NOW + timedelta(1)}
        ),
        "run-1",
    )
    latest = repo.latest_one("src-a", "1")
    assert latest is not None
    assert latest.payload == {"v": 2}
    assert repo.latest_one("src-a", "missing") is None


def test_same_page_from_two_sources_is_one_event(
    conn: sqlite3.Connection,
) -> None:
    """Matching URL and start date merge; a different date does not."""
    repo = EventRepository(conn)
    url = "https://events.example.test/e/1"
    first = repo.upsert_draft(_draft("x", url=url), "h", NOW)
    second = repo.upsert_draft(
        _draft("x", source_id="src-b", url=url), "h", NOW
    )
    other_day = repo.upsert_draft(
        _draft(
            "y",
            source_id="src-b",
            url=url,
            start_utc=NOW + timedelta(days=9),
        ),
        "h",
        NOW,
    )
    assert second.event_id == first.event_id
    assert not second.created
    assert other_day.event_id != first.event_id
    repo.link_profile(first.event_id, "p", NOW)
    [event] = repo.upcoming("p", NOW)
    assert {s.source_id for s in event.sources} == {"src-a", "src-b"}
