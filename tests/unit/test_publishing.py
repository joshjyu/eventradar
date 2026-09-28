"""Tests for published artifact rendering."""

import json
from datetime import UTC, date, datetime

from eventradar.config.schema import ProfileConfig
from eventradar.domain.enums import EventKind
from eventradar.domain.models import Event
from eventradar.publishing.json_feed import feed_artifacts
from eventradar.publishing.jsonschema import schema_artifact
from eventradar.publishing.manifest import manifest_artifact
from eventradar.storage.repositories import SourceRunRow

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
PROFILE = ProfileConfig(
    version=1, id="test-profile", name="Test", region="r", topic="t"
)


def _event(event_id: str) -> Event:
    """
    Build a minimal published event.

    Parameters:
      event_id: Event id.
    Returns:
      Event model.
    """
    return Event(
        event_id=event_id,
        title="Hack",
        start_utc=NOW,
        first_seen=NOW,
        last_seen=NOW,
        kinds=frozenset({EventKind.MEETUP, EventKind.HACKATHON}),
    )


def test_feed_keys_and_determinism() -> None:
    """Feeds land under versioned keys and render byte-identically."""
    args = (PROFILE, [_event("E1")], [], date(2026, 10, 1), NOW)
    first = feed_artifacts(*args)
    second = feed_artifacts(*args)
    assert [a.key for a in first] == [
        "v1/test-profile/events.json",
        "v1/test-profile/new/2026-10-01.json",
    ]
    assert [a.body for a in first] == [a.body for a in second]
    doc = json.loads(first[0].body)
    assert doc["count"] == 1
    assert doc["events"][0]["kinds"] == ["hackathon", "meetup"]


def test_manifest_omits_error_details() -> None:
    """Private error text never reaches the public manifest."""
    row = SourceRunRow(
        run_id="r",
        source_id="s",
        started_at=NOW,
        finished_at=NOW,
        status="failed",
        fetched=0,
        changed=0,
        parsed=0,
        parse_errors=0,
        error="internal detail",
    )
    art = manifest_artifact(PROFILE, "r", NOW, {"upcoming": 0}, [row])
    assert b"internal detail" not in art.body
    assert json.loads(art.body)["sources"][0]["status"] == "failed"


def test_schema_artifact_describes_event() -> None:
    """The published schema covers the Event contract."""
    art = schema_artifact()
    assert art.key == "v1/schema/event.json"
    schema = json.loads(art.body)
    assert {"event_id", "start_utc", "schema_version"} <= set(
        schema["properties"]
    )
