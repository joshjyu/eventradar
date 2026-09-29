"""Classify stage: assign rule-based kinds to upcoming events."""

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime

from eventradar.classify.kinds import KindClassifier
from eventradar.domain.enums import EventKind
from eventradar.storage.repositories import EventRepository, transaction


def classify_kinds(
    conn: sqlite3.Connection,
    events: EventRepository,
    classifier: KindClassifier,
    now: datetime,
    defaults: Mapping[str, Iterable[EventKind]] | None = None,
) -> int:
    """
    Recompute rule kinds for every upcoming event.

    Title rules win; when they find nothing, the event's sources' default
    kinds apply.

    Parameters:
      conn: State database.
      events: Event repository.
      classifier: Kind rules.
      now: Run time.
      defaults: Source id to default kinds.
    Returns:
      Number of events with at least one rule kind.
    """
    defaults = defaults or {}
    classified = 0
    with transaction(conn):
        for event_id, title, sources in events.upcoming_titles(now):
            kinds = classifier.kinds(title) or frozenset(
                k for s in sources for k in defaults.get(s, ())
            )
            events.set_rule_kinds(event_id, kinds)
            classified += bool(kinds)
    return classified
