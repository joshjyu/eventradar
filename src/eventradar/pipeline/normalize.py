"""Normalize stage: persist raw records, parse changes, upsert events."""

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.urls import canonical_url
from eventradar.geo.region import Region
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
    keep: Region | None = None,
) -> ParseCounts:
    """
    Parse records and write the resulting drafts.

    Parameters:
      source: Adapter that produced the records.
      records: Records to parse.
      events: Event repository.
      now: Observation time.
      keep: When set, drafts not located inside this region are dropped.
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
            if keep is not None and not _inside(draft, keep):
                continue
            events.upsert_draft(normalize_draft(draft), raw.content_hash, now)
            parsed += 1
    return ParseCounts(parsed=parsed, errors=errors)


def _inside(draft: EventDraft, region: Region) -> bool:
    """
    Check that a draft is located inside a region.

    Parameters:
      draft: Parsed draft.
      region: Region to test.
    Returns:
      True only for located drafts inside the region.
    """
    if draft.lat is None or draft.lon is None:
        return False
    return region.contains(draft.lat, draft.lon)


def normalize_draft(draft: EventDraft) -> EventDraft:
    """
    Apply source-independent cleanup before storage.

    Parameters:
      draft: Draft as parsed by an adapter.
    Returns:
      The draft with a canonical URL.
    """
    if not draft.url:
        return draft
    return draft.model_copy(update={"url": canonical_url(draft.url)})


def ingest(
    conn: sqlite3.Connection,
    result: FetchResult,
    run_id: str,
    now: datetime,
    events: EventRepository,
    keep: Region | None = None,
) -> SourceRunRow:
    """
    Store one source's fetch result atomically and record its metrics.

    Parameters:
      conn: State database.
      result: Fetch outcome for one source.
      run_id: Current run.
      now: Observation time.
      events: Event repository bound to `conn`.
      keep: The source's `keep_region`, if any.
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
            counts = parse_and_upsert(result.source, changed, events, now, keep)
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
