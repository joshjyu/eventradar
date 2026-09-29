"""Resolve stage: merge events that different postings describe twice."""

import sqlite3
from datetime import datetime

from eventradar.resolve.match import Thresholds, clusters
from eventradar.storage.repositories import EventRepository, transaction


def resolve(
    conn: sqlite3.Connection,
    events: EventRepository,
    now: datetime,
    limits: Thresholds | None = None,
) -> int:
    """
    Merge each group of duplicates into its oldest event.

    Parameters:
      conn: State database.
      events: Event repository.
      now: Run time.
      limits: Matching thresholds; defaults when None.
    Returns:
      Number of events merged away.
    """
    groups = clusters(events.resolve_candidates(now), limits or Thresholds())
    merged = 0
    with transaction(conn):
        for group in groups:
            survivor, *losers = group
            for loser in losers:
                events.merge(loser, survivor, now)
                merged += 1
    return merged
