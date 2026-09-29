"""Classify stage: assign rule-based kinds to upcoming events."""

import sqlite3
from datetime import datetime

from eventradar.classify.kinds import KindClassifier
from eventradar.storage.repositories import EventRepository, transaction


def classify_kinds(
    conn: sqlite3.Connection,
    events: EventRepository,
    classifier: KindClassifier,
    now: datetime,
) -> int:
    """
    Recompute rule kinds for every upcoming event.

    Parameters:
      conn: State database.
      events: Event repository.
      classifier: Kind rules.
      now: Run time.
    Returns:
      Number of events with at least one rule kind.
    """
    classified = 0
    with transaction(conn):
        for event_id, title in events.upcoming_titles(now):
            kinds = classifier.kinds(title)
            events.set_rule_kinds(event_id, kinds)
            classified += bool(kinds)
    return classified
