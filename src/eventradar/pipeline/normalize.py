"""Normalize stage: persist raw records, parse changes, upsert events."""

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from eventradar.domain.models import RawRecord
from eventradar.pipeline.fetch import FetchResult
from eventradar.sources.base import Source
from eventradar.storage.repositories import (
    EventRepository,
    RawRecordRepository,
    RunRepository,
    SourceRunRow,
    transaction,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseCounts:
    """Tally of one parse pass."""

    parsed: int
    errors: int


def parse_and_upsert(
    source: Source,
    records: Iterable[RawRecord],
    events: EventRepository,
    now: datetime,
) -> ParseCounts:
    """
    Parse records and write the resulting drafts.

    Parameters:
      source: Adapter that produced the records.
      records: Records to parse.
      events: Event repository.
      now: Observation time.
    Returns:
      Counts of drafts written and records that failed to parse.
    """
    parsed = errors = 0
    for raw in records:
        # One bad record, or an adapter bug, must not stop the run; the raw
        # record is kept so `replay` can reparse it after a fix.
        try:
            drafts = source.parse(raw)
        except Exception as exc:
            errors += 1
            log.warning(
                "parse failed %s:%s: %s", raw.source_id, raw.native_id, exc
            )
            continue
        for draft in drafts:
            events.upsert_draft(draft, raw.content_hash, now)
            parsed += 1
    return ParseCounts(parsed=parsed, errors=errors)


def ingest(
    conn: sqlite3.Connection,
    result: FetchResult,
    run_id: str,
    now: datetime,
    events: EventRepository,
) -> SourceRunRow:
    """
    Store one source's fetch result atomically and record its metrics.

    Parameters:
      conn: State database.
      result: Fetch outcome for one source.
      run_id: Current run.
      now: Observation time.
      events: Event repository bound to `conn`.
    Returns:
      The stored metrics row.
    """
    if result.error:
        log.warning("source %s failed: %s", result.config.id, result.error)
    raws = RawRecordRepository(conn)
    changed: list[RawRecord] = []
    counts = ParseCounts(0, 0)
    with transaction(conn):
        if result.source is not None:
            unchanged = []
            for record in result.records:
                target = (
                    changed
                    if raws.insert_if_changed(record, run_id)
                    else unchanged
                )
                target.append(record)
            counts = parse_and_upsert(result.source, changed, events, now)
            events.mark_seen(
                result.config.id, (r.native_id for r in unchanged), now
            )
        row = SourceRunRow(
            run_id=run_id,
            source_id=result.config.id,
            started_at=result.started_at,
            finished_at=result.finished_at,
            status="failed" if result.error else "ok",
            fetched=len(result.records),
            changed=len(changed),
            parsed=counts.parsed,
            parse_errors=counts.errors,
            error=result.error,
        )
        RunRepository(conn).record_source(row)
    return row
