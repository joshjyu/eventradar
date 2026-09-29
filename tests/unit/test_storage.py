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


def test_prune_keeps_latest_and_recent_versions(
    conn: sqlite3.Connection,
) -> None:
    """Only superseded versions older than the cutoff are removed."""
    repo = RawRecordRepository(conn)
    base = RawRecord(
        source_id="src-a", native_id="1", payload={"v": 0}, fetched_at=NOW
    )
    for day in range(4):
        repo.insert_if_changed(
            base.model_copy(
                update={
                    "payload": {"v": day},
                    "fetched_at": NOW + timedelta(days=day),
                }
            ),
            "run-1",
        )
    lonely = base.model_copy(update={"native_id": "2"})
    repo.insert_if_changed(lonely, "run-1")
    deleted = repo.prune(NOW + timedelta(days=2))
    assert deleted == 2
    remaining = conn.execute(
        "SELECT native_id, payload FROM raw_records ORDER BY 1, fetched_at"
    ).fetchall()
    assert [(r[0], r[1]) for r in remaining] == [
        ("1", '{"v": 2}'),
        ("1", '{"v": 3}'),
        ("2", '{"v": 0}'),
    ]


def test_higher_priority_source_leads_and_gaps_are_filled(
    conn: sqlite3.Connection,
) -> None:
    """The leading source wins conflicts; others only fill missing values."""
    repo = EventRepository(conn, priorities={"src-a": 40, "src-b": 70})
    url = "https://events.example.test/e/1"
    low = _draft("x", url=url, title="Low Title", venue="Hall", tz=None)
    event = repo.upsert_draft(low, "h", NOW).event_id
    high = _draft(
        "y", source_id="src-b", url=url, title="High Title", venue=None
    )
    repo.upsert_draft(high, "h", NOW)
    repo.upsert_draft(
        _draft("x", url=url, title="Low Again", venue="Hall", organizer="Org"),
        "h2",
        NOW,
    )
    repo.link_profile(event, "p", NOW)
    [merged] = repo.upcoming("p", NOW)
    assert merged.title == "High Title"
    assert merged.venue == "Hall"
    assert merged.organizer == "Org"


def test_merge_moves_sources_profiles_and_records_alias(
    conn: sqlite3.Connection,
) -> None:
    """The survivor gains provenance, gaps, and the earliest first_seen."""
    ids = iter(["E1", "E2"])
    repo = EventRepository(conn, id_factory=lambda: next(ids))
    repo.upsert_draft(_draft("a", venue=None), "h", NOW + timedelta(hours=1))
    repo.upsert_draft(
        _draft("b", source_id="src-b", venue="Hall", size_signal=30), "h", NOW
    )
    repo.link_profile("E1", "p", NOW + timedelta(hours=1))
    repo.link_profile("E2", "p", NOW)
    repo.merge("E2", "E1", NOW)
    [event] = repo.upcoming("p", NOW)
    assert event.event_id == "E1"
    assert event.venue == "Hall"
    assert event.size_signal == 30
    assert event.first_seen == NOW
    assert {s.source_id for s in event.sources} == {"src-a", "src-b"}
    alias = conn.execute(
        "SELECT event_id FROM event_aliases WHERE alias_id = 'E2'"
    ).fetchone()
    assert alias[0] == "E1"
    added = repo.added_between("p", NOW, NOW + timedelta(days=1), NOW)
    assert [e.event_id for e in added] == ["E1"]


def test_geocode_cache_and_missing_locations(
    conn: sqlite3.Connection,
) -> None:
    """Misses are cached; only upcoming, unlocated, in-person events list."""
    from eventradar.storage.repositories import GeocodeCacheRepository

    cache = GeocodeCacheRepository(conn)
    cache.put("a st", (33.1, -117.2, "address"), "census", NOW)
    cache.put("nowhere", None, "census", NOW)
    hit, miss = cache.get("a st"), cache.get("nowhere")
    assert hit is not None
    assert (hit.lat, hit.precision) == (33.1, "address")
    assert miss is not None
    assert miss.lat is None
    assert cache.get("unknown") is None
    repo = EventRepository(conn)
    repo.upsert_draft(_draft("1", address="1 Main St"), "h", NOW)
    repo.upsert_draft(
        _draft("2", address="2 Main St", lat=33.0, lon=-117.0), "h", NOW
    )
    repo.upsert_draft(
        _draft("3", address="3 Main St", start_utc=NOW - timedelta(days=5)),
        "h",
        NOW,
    )
    assert [a for _, a in repo.missing_locations(NOW)] == ["1 Main St"]


def test_cancellation_from_any_source_sticks(
    conn: sqlite3.Connection,
) -> None:
    """A lower-priority source or a merged duplicate can cancel an event."""
    from eventradar.domain.enums import EventStatus

    ids = iter(["E1", "E2", "E3"])
    repo = EventRepository(
        conn, id_factory=lambda: next(ids), priorities={"src-b": 90}
    )
    url = "https://events.example.test/e/1"
    repo.upsert_draft(_draft("x", source_id="src-b", url=url), "h", NOW)
    repo.upsert_draft(
        _draft("x", url=url, status=EventStatus.CANCELLED), "h", NOW
    )
    repo.upsert_draft(_draft("y"), "h", NOW)
    repo.upsert_draft(
        _draft("z", source_id="src-c", status=EventStatus.CANCELLED),
        "h",
        NOW,
    )
    repo.merge("E3", "E2", NOW)
    for event_id in ("E1", "E2"):
        repo.link_profile(event_id, "p", NOW)
    statuses = {e.event_id: e.status for e in repo.upcoming("p", NOW)}
    assert statuses == {
        "E1": EventStatus.CANCELLED,
        "E2": EventStatus.CANCELLED,
    }


def test_source_state_round_trip(conn: sqlite3.Connection) -> None:
    """Unknown sources are healthy; stored state reads back intact."""
    from eventradar.storage.repositories import (
        SourceState,
        SourceStateRepository,
    )

    repo = SourceStateRepository(conn)
    assert repo.get("new").status == "healthy"
    state = SourceState(
        source_id="s",
        status="disabled",
        reason="fetch failed",
        unhealthy_runs=7,
        alerted=True,
        since=NOW,
        last_probe=NOW,
    )
    repo.put(state, NOW)
    assert repo.get("s") == state
    assert [s.source_id for s in repo.disabled()] == ["s"]


def test_run_history_window(conn: sqlite3.Connection) -> None:
    """History covers the window and excludes the current run."""
    from eventradar.storage.repositories import SourceRunRow

    runs = RunRepository(conn)
    for i in range(3):
        run_id = f"h{i}"
        runs.start(run_id, NOW + timedelta(days=i))
        runs.record_source(
            SourceRunRow(
                run_id=run_id,
                source_id="s",
                started_at=NOW + timedelta(days=i),
                finished_at=NOW + timedelta(days=i),
                status="ok",
                fetched=10 + i,
                changed=0,
                parsed=0,
                parse_errors=0,
            )
        )
    rows = runs.history("s", NOW + timedelta(days=1), before_run="h2")
    assert [r.fetched for r in rows] == [11]
