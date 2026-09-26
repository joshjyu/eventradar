"""Typed access to the state database. Callers never write raw SQL."""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from eventradar.domain.enums import AttendanceMode, EventKind, EventStatus
from eventradar.domain.ids import IdFactory, new_event_id
from eventradar.domain.models import (
    Event,
    EventDraft,
    EventSourceRef,
    RawRecord,
)

_EVENT_COLUMNS = (
    "title",
    "description",
    "start_utc",
    "end_utc",
    "tz",
    "venue",
    "address",
    "lat",
    "lon",
    "attendance_mode",
    "status",
    "organizer",
    "url",
    "kinds",
    "size_signal",
)


def to_db(value: datetime) -> str:
    """
    Serialize a timezone-aware datetime to a sortable UTC string.

    Parameters:
      value: Aware datetime.
    Returns:
      ISO 8601 string with fixed microsecond precision.
    """
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def from_db(value: str) -> datetime:
    """
    Parse a UTC string written by `to_db`.

    Parameters:
      value: ISO 8601 string.
    Returns:
      Aware datetime in UTC.
    """
    return datetime.fromisoformat(value)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """
    Run a block atomically on an autocommit connection.

    Parameters:
      conn: Connection opened with `isolation_level=None`.
    Returns:
      A context manager that commits on success and rolls back on error.
    """
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@dataclass(frozen=True)
class SourceRunRow:
    """Outcome of one source within one run."""

    run_id: str
    source_id: str
    started_at: datetime
    finished_at: datetime
    status: str
    fetched: int
    changed: int
    parsed: int
    parse_errors: int
    error: str | None = None


@dataclass(frozen=True)
class UpsertResult:
    """Result of writing a draft into the events table."""

    event_id: str
    created: bool


class RunRepository:
    """Pipeline run bookkeeping."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """
        Bind to a connection.

        Parameters:
          conn: Open connection.
        """
        self._conn = conn

    def start(self, run_id: str, started_at: datetime) -> None:
        """
        Record the start of a run.

        Parameters:
          run_id: Unique run id.
          started_at: Start time.
        """
        self._conn.execute(
            "INSERT INTO runs (run_id, started_at, status) "
            "VALUES (?, ?, 'running')",
            (run_id, to_db(started_at)),
        )

    def finish(self, run_id: str, finished_at: datetime, status: str) -> None:
        """
        Record the end of a run.

        Parameters:
          run_id: Run id passed to `start`.
          finished_at: End time.
          status: Final status, e.g. `ok` or `failed`.
        """
        self._conn.execute(
            "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
            (to_db(finished_at), status, run_id),
        )

    def record_source(self, row: SourceRunRow) -> None:
        """
        Store per-source metrics for a run.

        Parameters:
          row: Metrics to store.
        """
        self._conn.execute(
            "INSERT INTO source_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.run_id,
                row.source_id,
                to_db(row.started_at),
                to_db(row.finished_at),
                row.status,
                row.fetched,
                row.changed,
                row.parsed,
                row.parse_errors,
                row.error,
            ),
        )

    def source_rows(self, run_id: str) -> list[SourceRunRow]:
        """
        Read per-source metrics for a run.

        Parameters:
          run_id: Run to read.
        Returns:
          Rows ordered by source id.
        """
        rows = self._conn.execute(
            "SELECT * FROM source_runs WHERE run_id = ? ORDER BY source_id",
            (run_id,),
        )
        return [
            SourceRunRow(
                **{
                    **dict(r),
                    "started_at": from_db(r["started_at"]),
                    "finished_at": from_db(r["finished_at"]),
                }
            )
            for r in rows
        ]


class RawRecordRepository:
    """Append-only store of upstream payloads."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """
        Bind to a connection.

        Parameters:
          conn: Open connection.
        """
        self._conn = conn

    def insert_if_changed(self, record: RawRecord, run_id: str) -> bool:
        """
        Store a record unless identical content was stored before.

        Parameters:
          record: Record to store.
          run_id: Run that fetched it.
        Returns:
          True if the content differs from the latest stored version.
        """
        latest = self._conn.execute(
            "SELECT content_hash FROM raw_records "
            "WHERE source_id = ? AND native_id = ? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (record.source_id, record.native_id),
        ).fetchone()
        if latest and latest[0] == record.content_hash:
            return False
        # Content can revert to an earlier version; refresh that row so it
        # becomes the latest again.
        self._conn.execute(
            "INSERT OR REPLACE INTO raw_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.source_id,
                record.native_id,
                record.content_hash,
                record.url,
                json.dumps(record.payload, sort_keys=True),
                to_db(record.fetched_at),
                run_id,
            ),
        )
        return True

    def latest(
        self, source_ids: Iterable[str] | None = None
    ) -> list[RawRecord]:
        """
        Return the most recent payload per source record.

        Parameters:
          source_ids: Restrict to these sources; all when None.
        Returns:
          Records ordered by source id and native id.
        """
        sql = (
            "SELECT * FROM raw_records r WHERE fetched_at = ("
            "SELECT MAX(fetched_at) FROM raw_records x "
            "WHERE x.source_id = r.source_id AND x.native_id = r.native_id)"
        )
        params: list[str] = []
        if source_ids is not None:
            ids = list(source_ids)
            sql += f" AND source_id IN ({','.join('?' * len(ids))})"
            params.extend(ids)
        rows = self._conn.execute(
            sql + " ORDER BY source_id, native_id", params
        )
        return [
            RawRecord(
                source_id=r["source_id"],
                native_id=r["native_id"],
                url=r["url"],
                payload=json.loads(r["payload"]),
                fetched_at=from_db(r["fetched_at"]),
            )
            for r in rows
        ]


class EventRepository:
    """Resolved events, their provenance, and profile membership."""

    def __init__(
        self, conn: sqlite3.Connection, id_factory: IdFactory = new_event_id
    ) -> None:
        """
        Bind to a connection.

        Parameters:
          conn: Open connection.
          id_factory: Generates ids for new events.
        """
        self._conn = conn
        self._new_id = id_factory

    def upsert_draft(
        self, draft: EventDraft, content_hash: str, now: datetime
    ) -> UpsertResult:
        """
        Create or update the event linked to a draft's source record.

        Parameters:
          draft: Parsed draft.
          content_hash: Hash of the raw record the draft came from.
          now: Observation time.
        Returns:
          The event id and whether it was created.
        """
        values = _draft_values(draft)
        existing = self._conn.execute(
            "SELECT event_id FROM event_sources "
            "WHERE source_id = ? AND native_id = ?",
            (draft.source_id, draft.native_id),
        ).fetchone()
        if existing:
            event_id = existing["event_id"]
            assignments = ", ".join(f"{c} = ?" for c in _EVENT_COLUMNS)
            self._conn.execute(
                f"UPDATE events SET {assignments}, last_seen = ? "  # noqa: S608
                "WHERE event_id = ?",
                (*values, to_db(now), event_id),
            )
            self._conn.execute(
                "UPDATE event_sources SET url = ?, content_hash = ? "
                "WHERE source_id = ? AND native_id = ?",
                (draft.url, content_hash, draft.source_id, draft.native_id),
            )
            return UpsertResult(event_id, created=False)
        event_id = self._new_id()
        columns = ", ".join(("event_id", *_EVENT_COLUMNS))
        marks = ", ".join("?" * (len(_EVENT_COLUMNS) + 3))
        self._conn.execute(
            f"INSERT INTO events ({columns}, first_seen, last_seen) "  # noqa: S608
            f"VALUES ({marks})",
            (event_id, *values, to_db(now), to_db(now)),
        )
        self._conn.execute(
            "INSERT INTO event_sources VALUES (?, ?, ?, ?, ?)",
            (
                draft.source_id,
                draft.native_id,
                event_id,
                draft.url,
                content_hash,
            ),
        )
        return UpsertResult(event_id, created=True)

    def mark_seen(
        self, source_id: str, native_ids: Iterable[str], now: datetime
    ) -> None:
        """
        Refresh `last_seen` for events whose records were unchanged.

        Parameters:
          source_id: Source that re-served the records.
          native_ids: Native ids seen in this run.
          now: Observation time.
        """
        self._conn.executemany(
            "UPDATE events SET last_seen = ? WHERE event_id = ("
            "SELECT event_id FROM event_sources "
            "WHERE source_id = ? AND native_id = ?)",
            [(to_db(now), source_id, n) for n in native_ids],
        )

    def link_profile(
        self, event_id: str, profile_id: str, now: datetime
    ) -> bool:
        """
        Add an event to a profile if it is not already a member.

        Parameters:
          event_id: Event to link.
          profile_id: Profile id.
          now: Time the event joined the profile.
        Returns:
          True if the link is new.
        """
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO event_profiles VALUES (?, ?, ?)",
            (event_id, profile_id, to_db(now)),
        )
        return cur.rowcount == 1

    def event_ids_for_sources(self, source_ids: Iterable[str]) -> list[str]:
        """
        List events that have at least one record from the given sources.

        Parameters:
          source_ids: Source ids.
        Returns:
          Distinct event ids.
        """
        ids = list(source_ids)
        rows = self._conn.execute(
            "SELECT DISTINCT event_id FROM event_sources "  # noqa: S608
            f"WHERE source_id IN ({','.join('?' * len(ids))}) "
            "ORDER BY event_id",
            ids,
        )
        return [r[0] for r in rows]

    def upcoming(self, profile_id: str, now: datetime) -> list[Event]:
        """
        List a profile's events that have not yet ended.

        Parameters:
          profile_id: Profile id.
          now: Reference time.
        Returns:
          Events ordered by start time, then id.
        """
        return self._query(
            "COALESCE(e.end_utc, e.start_utc) >= ?", profile_id, (to_db(now),)
        )

    def added_between(
        self, profile_id: str, start: datetime, end: datetime, now: datetime
    ) -> list[Event]:
        """
        List upcoming events that joined a profile within a window.

        Parameters:
          profile_id: Profile id.
          start: Inclusive window start.
          end: Exclusive window end.
          now: Events that already started are excluded.
        Returns:
          Events ordered by start time, then id.
        """
        return self._query(
            "p.first_seen >= ? AND p.first_seen < ? AND e.start_utc > ?",
            profile_id,
            (to_db(start), to_db(end), to_db(now)),
        )

    def _query(
        self, where: str, profile_id: str, params: tuple[Any, ...]
    ) -> list[Event]:
        """
        Fetch events in a profile matching an internal filter.

        Parameters:
          where: Trusted SQL predicate over aliases `e` and `p`.
          profile_id: Profile id.
          params: Values bound to the predicate.
        Returns:
          Events ordered by start time, then id.
        """
        rows = self._conn.execute(
            "SELECT e.* FROM events e JOIN event_profiles p "  # noqa: S608
            "ON p.event_id = e.event_id "
            f"WHERE p.profile_id = ? AND {where} "
            "ORDER BY e.start_utc, e.event_id",
            (profile_id, *params),
        ).fetchall()
        refs = self._sources_for([r["event_id"] for r in rows])
        return [_row_to_event(r, refs.get(r["event_id"], ())) for r in rows]

    def _sources_for(
        self, event_ids: list[str]
    ) -> dict[str, tuple[EventSourceRef, ...]]:
        """
        Load provenance for a batch of events.

        Parameters:
          event_ids: Events to load.
        Returns:
          Mapping of event id to its source references.
        """
        grouped: dict[str, list[EventSourceRef]] = {}
        for chunk_start in range(0, len(event_ids), 500):
            chunk = event_ids[chunk_start : chunk_start + 500]
            rows = self._conn.execute(
                "SELECT * FROM event_sources "  # noqa: S608
                f"WHERE event_id IN ({','.join('?' * len(chunk))}) "
                "ORDER BY source_id, native_id",
                chunk,
            )
            for r in rows:
                grouped.setdefault(r["event_id"], []).append(
                    EventSourceRef(
                        source_id=r["source_id"],
                        native_id=r["native_id"],
                        url=r["url"],
                    )
                )
        return {k: tuple(v) for k, v in grouped.items()}


def _draft_values(draft: EventDraft) -> tuple[Any, ...]:
    """
    Flatten a draft into column order for `_EVENT_COLUMNS`.

    Parameters:
      draft: Draft to flatten.
    Returns:
      Values aligned with `_EVENT_COLUMNS`.
    """
    return (
        draft.title,
        draft.description,
        to_db(draft.start_utc),
        to_db(draft.end_utc) if draft.end_utc else None,
        draft.tz,
        draft.venue,
        draft.address,
        draft.lat,
        draft.lon,
        draft.attendance_mode.value,
        draft.status.value,
        draft.organizer,
        draft.url,
        json.dumps(sorted(k.value for k in draft.kinds)),
        draft.size_signal,
    )


def _row_to_event(
    row: sqlite3.Row, sources: tuple[EventSourceRef, ...]
) -> Event:
    """
    Rebuild an `Event` from an events row.

    Parameters:
      row: Row from the events table.
      sources: Provenance for the event.
    Returns:
      The event model.
    """
    return Event(
        event_id=row["event_id"],
        title=row["title"],
        description=row["description"],
        start_utc=from_db(row["start_utc"]),
        end_utc=from_db(row["end_utc"]) if row["end_utc"] else None,
        tz=row["tz"],
        venue=row["venue"],
        address=row["address"],
        lat=row["lat"],
        lon=row["lon"],
        attendance_mode=AttendanceMode(row["attendance_mode"]),
        status=EventStatus(row["status"]),
        organizer=row["organizer"],
        url=row["url"],
        kinds=frozenset(EventKind(k) for k in json.loads(row["kinds"])),
        size_signal=row["size_signal"],
        first_seen=from_db(row["first_seen"]),
        last_seen=from_db(row["last_seen"]),
        sources=sources,
    )
