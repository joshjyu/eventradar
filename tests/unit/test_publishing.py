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


def test_ical_feed_round_trips() -> None:
    """Calendar apps can parse the feed; cancellations are marked."""
    from icalendar import Calendar

    from eventradar.domain.enums import EventStatus
    from eventradar.publishing.ical_feed import ical_artifact

    events = [
        _event("E1").model_copy(
            update={
                "venue": "Hall",
                "address": "1 Main St",
                "lat": 34.0,
                "lon": -118.0,
                "url": "https://events.example.test/e/1",
            }
        ),
        _event("E2").model_copy(update={"status": EventStatus.CANCELLED}),
    ]
    art = ical_artifact(PROFILE, events, NOW)
    assert art.key == "v1/test-profile/events.ics"
    assert art.content_type.startswith("text/calendar")
    parsed = Calendar.from_ical(art.body).events
    assert [str(e["UID"]) for e in parsed] == ["E1@eventradar", "E2@eventradar"]
    assert str(parsed[0]["LOCATION"]) == "Hall, 1 Main St"
    assert str(parsed[1]["STATUS"]) == "CANCELLED"
    assert ical_artifact(PROFILE, events, NOW).body == art.body


def test_rss_feed_is_valid_and_newest_first() -> None:
    """The feed parses as XML, orders by first_seen, and escapes text."""
    from datetime import timedelta
    from xml.etree.ElementTree import fromstring

    from eventradar.publishing.rss_feed import rss_artifact

    older = _event("E1").model_copy(update={"title": "Build & <Ship>"})
    newer = _event("E2").model_copy(
        update={"first_seen": NOW + timedelta(hours=1), "tz": "UTC"}
    )
    art = rss_artifact(PROFILE, [older, newer], NOW, "https://site.test")
    root = fromstring(art.body)
    titles = [i.findtext("title") for i in root.iter("item")]
    assert titles == ["Hack", "Build & <Ship>"]
    assert root.findtext("channel/link") == "https://site.test"
    assert b"&amp; &lt;Ship&gt;" in art.body
