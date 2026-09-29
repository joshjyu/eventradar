"""Typed access to the state database. Callers never write raw SQL."""

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
class ResolveCandidate:
    """The fields dedup compares."""

    event_id: str
    title: str
    start_utc: datetime
    lat: float | None
    lon: float | None
    url: str | None


@dataclass(frozen=True)
class ProfileCandidate:
    """The fields profile filtering needs."""

    event_id: str
    title: str
    description: str | None
    lat: float | None
    lon: float | None
    attendance_mode: AttendanceMode
    sources: frozenset[str]


@dataclass(frozen=True)
class CachedGeocode:
    """A stored geocoding result; `lat` is None for a recorded miss."""

    lat: float | None
    lon: float | None
    precision: str | None
    looked_up_at: datetime


class GeocodeCacheRepository:
    """Geocoding results keyed by normalized address."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """
        Bind to a connection.

        Parameters:
          conn: Open connection.
        """
        self._conn = conn

    def get(self, key: str) -> CachedGeocode | None:
        """
        Read a cached result.

        Parameters:
          key: Normalized address.
        Returns:
          The cached result, or None if never looked up.
        """
        row = self._conn.execute(
            "SELECT * FROM geocode_cache WHERE address_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return CachedGeocode(
            lat=row["lat"],
            lon=row["lon"],
            precision=row["precision"],
            looked_up_at=from_db(row["looked_up_at"]),
        )

    def put(
        self,
        key: str,
        point: tuple[float, float, str] | None,
        provider: str,
        now: datetime,
    ) -> None:
        """
        Store a result or a miss.

        Parameters:
          key: Normalized address.
          point: (lat, lon, precision), or None for a miss.
          provider: Service that answered.
          now: Lookup time.
        """
        lat, lon, precision = point or (None, None, None)
        self._conn.execute(
            "INSERT OR REPLACE INTO geocode_cache VALUES (?, ?, ?, ?, ?, ?)",
            (key, lat, lon, precision, provider, to_db(now)),
        )


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

    def prune(self, before: datetime) -> int:
        """
        Delete superseded versions fetched before a cutoff.

        The latest version of every record is always kept, so `replay`
        still has everything it needs.

        Parameters:
          before: Versions fetched earlier than this may be deleted.
        Returns:
          Number of rows deleted.
        """
        cur = self._conn.execute(
            "DELETE FROM raw_records AS r WHERE fetched_at < ? "
            "AND fetched_at < (SELECT MAX(fetched_at) FROM raw_records x "
            "WHERE x.source_id = r.source_id AND x.native_id = r.native_id)",
            (to_db(before),),
        )
        return cur.rowcount

    def latest_one(self, source_id: str, native_id: str) -> RawRecord | None:
        """
        Return the most recent payload for one source record.

        Parameters:
          source_id: Source id.
          native_id: Upstream id.
        Returns:
          The record, or None if never stored.
        """
        row = self._conn.execute(
            "SELECT * FROM raw_records WHERE source_id = ? AND native_id = ? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (source_id, native_id),
        ).fetchone()
        return _row_to_raw(row) if row else None

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
        return [_row_to_raw(r) for r in rows]


class EventRepository:
    """Resolved events, their provenance, and profile membership."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        id_factory: IdFactory = new_event_id,
        priorities: Mapping[str, int] | None = None,
    ) -> None:
        """
        Bind to a connection.

        Parameters:
          conn: Open connection.
          id_factory: Generates ids for new events.
          priorities: Source id to precedence; higher wins field conflicts.
        """
        self._conn = conn
        self._new_id = id_factory
        self._priorities = dict(priorities or {})

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
        existing = self._conn.execute(
            "SELECT event_id FROM event_sources "
            "WHERE source_id = ? AND native_id = ?",
            (draft.source_id, draft.native_id),
        ).fetchone()
        if existing:
            event_id = existing["event_id"]
            self._conn.execute(
                "UPDATE event_sources SET url = ?, content_hash = ? "
                "WHERE source_id = ? AND native_id = ?",
                (draft.url, content_hash, draft.source_id, draft.native_id),
            )
            self._apply(draft, event_id, now)
            return UpsertResult(event_id, created=False)
        match = self._same_page(draft)
        if match:
            self._link_source(draft, content_hash, match)
            self._apply(draft, match, now)
            return UpsertResult(match, created=False)
        event_id = self._new_id()
        columns = ", ".join(("event_id", *_EVENT_COLUMNS))
        marks = ", ".join("?" * (len(_EVENT_COLUMNS) + 4))
        self._conn.execute(
            f"INSERT INTO events ({columns}, first_seen, last_seen, "  # noqa: S608
            f"primary_source) VALUES ({marks})",
            (
                event_id,
                *_draft_values(draft),
                to_db(now),
                to_db(now),
                draft.source_id,
            ),
        )
        self._link_source(draft, content_hash, event_id)
        return UpsertResult(event_id, created=True)

    def _priority(self, source_id: str | None) -> int:
        """
        Look up a source's precedence.

        Parameters:
          source_id: Source id.
        Returns:
          Configured priority, 50 when unset.
        """
        return self._priorities.get(source_id or "", 50)

    def _apply(self, draft: EventDraft, event_id: str, now: datetime) -> None:
        """
        Write a draft into an existing event.

        The leading source (the current primary, or a higher-priority one)
        replaces values but never erases a known value with a missing one;
        other sources only fill gaps, except that any source can mark the
        event cancelled.

        Parameters:
          draft: Parsed draft.
          event_id: Event to update.
          now: Observation time.
        """
        row = self._conn.execute(
            "SELECT primary_source FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        primary = row["primary_source"] if row else None
        leads = primary in (None, draft.source_id) or self._priority(
            draft.source_id
        ) > self._priority(primary)
        values = dict(zip(_EVENT_COLUMNS, _draft_values(draft), strict=True))
        sets: list[str] = []
        params: list[Any] = []
        for column, value in values.items():
            if column == "kinds":
                keep_new = "? != '[]'" if leads else "kinds = '[]'"
                sets.append(
                    f"kinds = CASE WHEN {keep_new} THEN ? ELSE kinds END"
                )
                params.extend([value, value] if leads else [value])
            elif leads:
                sets.append(f"{column} = COALESCE(?, {column})")
                params.append(value)
            elif column == "status":
                # Any source reporting a cancellation is believed.
                sets.append(
                    "status = CASE WHEN ? = 'cancelled' THEN 'cancelled' "
                    "ELSE status END"
                )
                params.append(value)
            else:
                sets.append(f"{column} = COALESCE({column}, ?)")
                params.append(value)
        if leads:
            sets.append("primary_source = ?")
            params.append(draft.source_id)
        self._conn.execute(
            f"UPDATE events SET {', '.join(sets)}, last_seen = ? "  # noqa: S608
            "WHERE event_id = ?",
            (*params, to_db(now), event_id),
        )

    def _same_page(self, draft: EventDraft) -> str | None:
        """
        Find an event already known under the same page and start date.

        Matching on both avoids merging distinct events that share a
        generic link. Fuzzy cross-source matching happens in `resolve`.

        Parameters:
          draft: Draft whose URL is already canonical.
        Returns:
          The matching event id, or None.
        """
        if not draft.url:
            return None
        row = self._conn.execute(
            "SELECT event_id FROM events WHERE url = ? "
            "AND substr(start_utc, 1, 10) = ? ORDER BY event_id LIMIT 1",
            (draft.url, to_db(draft.start_utc)[:10]),
        ).fetchone()
        return row["event_id"] if row else None

    def _link_source(
        self, draft: EventDraft, content_hash: str, event_id: str
    ) -> None:
        """
        Record that a source record describes an event.

        Parameters:
          draft: Draft from the source record.
          content_hash: Hash of the raw record.
          event_id: Event the record belongs to.
        """
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

    def missing_locations(self, now: datetime) -> list[tuple[str, str]]:
        """
        List upcoming in-person events with an address but no coordinates.

        Parameters:
          now: Reference time.
        Returns:
          (event_id, address) pairs.
        """
        rows = self._conn.execute(
            "SELECT event_id, address FROM events WHERE lat IS NULL "
            "AND address IS NOT NULL AND attendance_mode != 'online' "
            "AND COALESCE(end_utc, start_utc) >= ? ORDER BY start_utc",
            (to_db(now),),
        )
        return [(r["event_id"], r["address"]) for r in rows]

    def set_location(self, event_id: str, lat: float, lon: float) -> None:
        """
        Store coordinates found by geocoding.

        Parameters:
          event_id: Event id.
          lat: Latitude.
          lon: Longitude.
        """
        self._conn.execute(
            "UPDATE events SET lat = ?, lon = ? WHERE event_id = ?",
            (lat, lon, event_id),
        )

    def missing_zones(self, now: datetime) -> list[tuple[str, float, float]]:
        """
        List upcoming located events without a time zone.

        Parameters:
          now: Reference time.
        Returns:
          (event_id, lat, lon) triples.
        """
        rows = self._conn.execute(
            "SELECT event_id, lat, lon FROM events WHERE tz IS NULL "
            "AND lat IS NOT NULL AND COALESCE(end_utc, start_utc) >= ?",
            (to_db(now),),
        )
        return [(r["event_id"], r["lat"], r["lon"]) for r in rows]

    def set_zone(self, event_id: str, tz: str) -> None:
        """
        Store a time zone derived from coordinates.

        Parameters:
          event_id: Event id.
          tz: IANA zone name.
        """
        self._conn.execute(
            "UPDATE events SET tz = ? WHERE event_id = ?", (tz, event_id)
        )

    def resolve_candidates(self, now: datetime) -> list[ResolveCandidate]:
        """
        Load the upcoming events that dedup compares.

        Parameters:
          now: Reference time; events ended more than a day ago are skipped.
        Returns:
          Candidates ordered by start time, then id.
        """
        rows = self._conn.execute(
            "SELECT event_id, title, start_utc, lat, lon, url, status "
            "FROM events WHERE COALESCE(end_utc, start_utc) >= ? "
            "ORDER BY start_utc, event_id",
            (to_db(now - timedelta(days=1)),),
        )
        return [
            ResolveCandidate(
                event_id=r["event_id"],
                title=r["title"],
                start_utc=from_db(r["start_utc"]),
                lat=r["lat"],
                lon=r["lon"],
                url=r["url"],
            )
            for r in rows
        ]

    def merge(self, loser: str, survivor: str, now: datetime) -> None:
        """
        Fold one event into another; the loser's id becomes an alias.

        Provenance and profile membership move to the survivor, the
        survivor's gaps are filled from the loser (or its values win, if its
        source has higher priority), a cancellation from either side sticks,
        and the earliest `first_seen` is kept.

        Parameters:
          loser: Event id to retire.
          survivor: Event id to keep.
          now: Merge time.
        """
        primaries = dict(
            self._conn.execute(
                "SELECT event_id, primary_source FROM events "
                "WHERE event_id IN (?, ?)",
                (loser, survivor),
            ).fetchall()
        )
        # A higher-priority loser supplies the values; otherwise it only
        # fills the survivor's gaps. Column names come from a constant.
        loser_leads = self._priority(primaries.get(loser)) > self._priority(
            primaries.get(survivor)
        )
        columns = [c for c in _EVENT_COLUMNS if c != "kinds"]
        if not loser_leads:
            columns = [c for c in columns if c not in {"title", "start_utc"}]
        pick = (
            "COALESCE((SELECT {c} FROM events WHERE event_id = ?), {c})"
            if loser_leads
            else "COALESCE({c}, (SELECT {c} FROM events WHERE event_id = ?))"
        )
        fills = ", ".join(f"{c} = " + pick.format(c=c) for c in columns)
        primary = primaries.get(loser) if loser_leads else None
        self._conn.execute(
            f"UPDATE events SET {fills}, "  # noqa: S608
            "primary_source = COALESCE(?, primary_source), "
            "status = CASE WHEN (SELECT status FROM events "
            "WHERE event_id = ?) = 'cancelled' THEN 'cancelled' "
            "ELSE status END, "
            "first_seen = MIN(first_seen, "
            "(SELECT first_seen FROM events WHERE event_id = ?)), "
            "last_seen = MAX(last_seen, "
            "(SELECT last_seen FROM events WHERE event_id = ?)) "
            "WHERE event_id = ?",
            (
                *([loser] * len(columns)),
                primary,
                loser,
                loser,
                loser,
                survivor,
            ),
        )
        self._conn.execute(
            "UPDATE event_sources SET event_id = ? WHERE event_id = ?",
            (survivor, loser),
        )
        self._conn.execute(
            "INSERT INTO event_profiles (event_id, profile_id, first_seen) "
            "SELECT ?, profile_id, first_seen FROM event_profiles "
            "WHERE event_id = ? AND true "
            "ON CONFLICT (event_id, profile_id) DO UPDATE SET "
            "first_seen = MIN(first_seen, excluded.first_seen)",
            (survivor, loser),
        )
        self._conn.execute(
            "DELETE FROM event_profiles WHERE event_id = ?", (loser,)
        )
        self._conn.execute(
            "UPDATE event_aliases SET event_id = ? WHERE event_id = ?",
            (survivor, loser),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO event_aliases VALUES (?, ?, ?)",
            (loser, survivor, to_db(now)),
        )
        self._conn.execute("DELETE FROM events WHERE event_id = ?", (loser,))

    def profile_candidates(
        self, now: datetime, source_ids: Iterable[str]
    ) -> list[ProfileCandidate]:
        """
        Load upcoming events that have a record from any given source.

        Parameters:
          now: Reference time.
          source_ids: Sources a profile draws from.
        Returns:
          Candidates with every source that describes them.
        """
        ids = sorted(set(source_ids))
        marks = ",".join("?" * len(ids))
        rows = self._conn.execute(
            "SELECT e.event_id, e.title, e.description, e.lat, e.lon, "  # noqa: S608
            "e.attendance_mode, group_concat(s.source_id) AS sources "
            "FROM events e JOIN event_sources s USING (event_id) "
            "WHERE COALESCE(e.end_utc, e.start_utc) >= ? "
            "AND e.event_id IN (SELECT event_id FROM event_sources "
            f"WHERE source_id IN ({marks})) "
            "GROUP BY e.event_id ORDER BY e.start_utc, e.event_id",
            (to_db(now), *ids),
        )
        return [
            ProfileCandidate(
                event_id=r["event_id"],
                title=r["title"],
                description=r["description"],
                lat=r["lat"],
                lon=r["lon"],
                attendance_mode=AttendanceMode(r["attendance_mode"]),
                sources=frozenset(r["sources"].split(",")),
            )
            for r in rows
        ]

    def sync_profile(
        self,
        profile_id: str,
        considered: Iterable[str],
        qualifying: Iterable[str],
        now: datetime,
    ) -> tuple[int, int]:
        """
        Make profile membership match the latest filter decisions.

        Only events in `considered` are touched, so past members stay.

        Parameters:
          profile_id: Profile id.
          considered: Events the filter evaluated.
          qualifying: Events that passed.
          now: Time new members joined.
        Returns:
          (added, removed) counts.
        """
        keep = set(qualifying)
        added = sum(self.link_profile(e, profile_id, now) for e in sorted(keep))
        drop = sorted(set(considered) - keep)
        removed = 0
        for chunk_start in range(0, len(drop), 500):
            chunk = drop[chunk_start : chunk_start + 500]
            cur = self._conn.execute(
                "DELETE FROM event_profiles WHERE profile_id = ? "  # noqa: S608
                f"AND event_id IN ({','.join('?' * len(chunk))})",
                (profile_id, *chunk),
            )
            removed += cur.rowcount
        return added, removed

    def upcoming_titles(self, now: datetime) -> list[tuple[str, str]]:
        """
        List upcoming events for classification.

        Parameters:
          now: Reference time.
        Returns:
          (event_id, title) pairs.
        """
        rows = self._conn.execute(
            "SELECT event_id, title FROM events "
            "WHERE COALESCE(end_utc, start_utc) >= ?",
            (to_db(now),),
        )
        return [(r["event_id"], r["title"]) for r in rows]

    def set_rule_kinds(self, event_id: str, kinds: Iterable[EventKind]) -> None:
        """
        Store kinds assigned by title rules.

        Parameters:
          event_id: Event id.
          kinds: Kinds.
        """
        self._conn.execute(
            "UPDATE events SET rule_kinds = ? WHERE event_id = ?",
            (json.dumps(sorted(k.value for k in kinds)), event_id),
        )

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
          Non-cancelled events ordered by start time, then id.
        """
        return self._query(
            "p.first_seen >= ? AND p.first_seen < ? AND e.start_utc > ? "
            "AND e.status != 'cancelled'",
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


def _row_to_raw(row: sqlite3.Row) -> RawRecord:
    """
    Rebuild a `RawRecord` from a raw_records row.

    Parameters:
      row: Row from the raw_records table.
    Returns:
      The record model.
    """
    return RawRecord(
        source_id=row["source_id"],
        native_id=row["native_id"],
        url=row["url"],
        payload=json.loads(row["payload"]),
        fetched_at=from_db(row["fetched_at"]),
    )


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
        kinds=frozenset(
            EventKind(k)
            for k in (*json.loads(row["kinds"]), *json.loads(row["rule_kinds"]))
        ),
        size_signal=row["size_signal"],
        first_seen=from_db(row["first_seen"]),
        last_seen=from_db(row["last_seen"]),
        sources=sources,
    )
