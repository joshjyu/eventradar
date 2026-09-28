"""Tests for JSON-LD extraction and schema.org Event mapping."""

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.urls import canonical_url
from eventradar.schemaorg.event import (
    EventMappingError,
    event_fields,
    slim_event,
)
from eventradar.schemaorg.extract import (
    events,
    item_list_entries,
    jsonld_documents,
)

LA = ZoneInfo("America/Los_Angeles")
PAGE = "https://events.example.test/e/robotics-day-123"


def _page(*docs: Any, raw: str = "") -> str:
    """
    Wrap JSON-LD documents in a minimal HTML page.

    Parameters:
      docs: JSON-serializable documents.
      raw: Extra raw HTML appended to the head.
    Returns:
      HTML text.
    """
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(d)}</script>'
        for d in docs
    )
    return f"<html><head>{scripts}{raw}</head><body></body></html>"


def test_extracts_graph_and_skips_malformed_blocks() -> None:
    """`@graph` members are found; broken JSON does not stop parsing."""
    html = _page(
        {"@graph": [{"@type": "WebPage"}, {"@type": "Event", "name": "A"}]},
        raw='<script type="application/ld+json">{broken</script>'
        '<script type="application/ld+json"><!-- {"@type": "SocialEvent",'
        ' "name": "B"} --></script>',
    )
    found = events(jsonld_documents(html))
    assert [e["name"] for e in found] == ["A", "B"]


def test_event_subtypes_and_nested_events() -> None:
    """Subtypes count as events; sub-events are not listed separately."""
    doc = {
        "@type": "BusinessEvent",
        "name": "Summit",
        "subEvent": [{"@type": "Event", "name": "Keynote"}],
    }
    found = events(jsonld_documents(_page(doc, {"@type": "FAQPage"})))
    assert [e["name"] for e in found] == ["Summit"]


def test_item_list_entries_resolve_relative_urls() -> None:
    """ItemList entries accept string items, objects, and relative URLs."""
    doc = {
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "item": {"@type": "Event", "url": "/e/1"}},
            {"@type": "ListItem", "item": "https://other.test/e/2"},
            {"@type": "ListItem", "position": 3},
        ],
    }
    entries = item_list_entries(
        jsonld_documents(_page(doc)), "https://hub.test/d/x"
    )
    assert [u for u, _ in entries] == [
        "https://hub.test/e/1",
        "https://other.test/e/2",
    ]
    assert entries[0][1] is not None


def _node(**overrides: Any) -> dict[str, Any]:
    """
    Build a schema.org Event node with defaults.

    Parameters:
      overrides: Key overrides.
    Returns:
      Event node.
    """
    node: dict[str, Any] = {
        "@type": "BusinessEvent",
        "name": "Robotics &amp; AI Day",
        "startDate": "2026-11-06T10:00:00-08:00",
        "endDate": "2026-11-06T18:00:00-08:00",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "eventStatus": "https://schema.org/EventScheduled",
        "location": {
            "@type": "Place",
            "name": "Hall A",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "1 Main St",
                "addressLocality": "Pasadena",
                "addressRegion": "CA",
            },
            "geo": {"latitude": "34.14", "longitude": "-118.14"},
        },
        "organizer": [
            {"@type": "Person", "name": "Pat Example"},
            {"@type": "Organization", "name": "Robot Club"},
        ],
        "image": "https://img.test/x.jpg?sig=rotating",
        "offers": [{"price": "10"}],
    }
    node.update(overrides)
    return node


def test_maps_core_fields() -> None:
    """Names, times, place, and organization map to draft fields."""
    fields = event_fields(_node(), PAGE, LA)
    assert fields["title"] == "Robotics & AI Day"
    assert fields["start_utc"] == datetime(2026, 11, 6, 18, tzinfo=UTC)
    assert fields["tz"] == "America/Los_Angeles"
    assert fields["venue"] == "Hall A"
    assert fields["address"] == "1 Main St, Pasadena, CA"
    assert (fields["lat"], fields["lon"]) == (34.14, -118.14)
    assert fields["attendance_mode"] is AttendanceMode.IN_PERSON
    assert fields["organizer"] == "Robot Club"
    assert fields["url"] == PAGE


def test_people_are_never_stored_as_organizers() -> None:
    """Only organizations are kept, for privacy."""
    node = _node(organizer={"@type": "Person", "name": "Pat Example"})
    assert event_fields(node, PAGE, LA)["organizer"] is None


def test_naive_and_date_only_times_use_zone() -> None:
    """Offset-less times are local to the source's zone."""
    naive = event_fields(
        _node(startDate="2026-11-06T10:00", endDate=None), PAGE, LA
    )
    dated = event_fields(_node(startDate="2026-11-06", endDate=None), PAGE, LA)
    assert naive["start_utc"] == datetime(2026, 11, 6, 18, tzinfo=UTC)
    assert dated["start_utc"] == datetime(2026, 11, 6, 8, tzinfo=UTC)


def test_foreign_offset_is_not_attributed_to_zone() -> None:
    """An offset that disagrees with the zone leaves tz unset."""
    fields = event_fields(
        _node(startDate="2026-11-06T10:00:00-05:00"), PAGE, LA
    )
    assert fields["tz"] is None


def test_status_mode_and_capacity() -> None:
    """Cancelled status, mixed locations, and capacity are mapped."""
    node = _node(
        eventStatus="https://schema.org/EventCancelled",
        eventAttendanceMode=None,
        location=[
            {"@type": "Place", "name": "Hall"},
            {"@type": "VirtualLocation", "url": "https://stream.test"},
        ],
        maximumAttendeeCapacity="500",
        url="/e/robotics-day-123?aff=x",
    )
    fields = event_fields(node, PAGE, LA)
    assert fields["status"] is EventStatus.CANCELLED
    assert fields["attendance_mode"] is AttendanceMode.MIXED
    assert fields["size_signal"] == 500
    assert (
        fields["url"] == "https://events.example.test/e/robotics-day-123?aff=x"
    )


@pytest.mark.parametrize(
    "overrides",
    [{"name": ""}, {"startDate": None}, {"startDate": "next friday"}],
)
def test_unusable_nodes_raise(overrides: dict[str, Any]) -> None:
    """
    Missing names or unparseable dates are rejected.

    Parameters:
      overrides: Field overrides that break the node.
    """
    with pytest.raises(EventMappingError):
        event_fields(_node(**overrides), PAGE, LA)


def test_slim_event_drops_volatile_fields() -> None:
    """Images and offers are removed; location and organizer are trimmed."""
    slim = slim_event(_node())
    assert "image" not in slim
    assert "offers" not in slim
    assert set(slim["location"]) <= {"@type", "name", "address", "geo"}


def test_canonical_url() -> None:
    """Tracking params, fragments, and default ports are removed."""
    url = "HTTPS://Events.Example.TEST:443/e/1?utm_source=x&b=2&aff=y&a=1#top"
    assert canonical_url(url) == "https://events.example.test/e/1?a=1&b=2"
