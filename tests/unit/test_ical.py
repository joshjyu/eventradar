"""Tests for the generic iCal adapter's parsing rules."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from eventradar.config.schema import SourceConfig
from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.models import EventDraft
from eventradar.sources.base import ParseError
from eventradar.sources.protocols.ical import IcalSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ical"
NOW = datetime(2026, 9, 26, tzinfo=UTC)


def _source(**params: object) -> IcalSource:
    """
    Build an adapter with test params.

    Parameters:
      params: Param overrides.
    Returns:
      Adapter instance.
    """
    merged: dict[str, object] = {
        "url": "https://feeds.example.test/cal.ics",
        "default_tz": "America/Los_Angeles",
    }
    merged.update(params)
    return IcalSource(
        SourceConfig(id="test-ical", adapter="ical", params=merged)
    )


def _drafts(name: str) -> dict[str, EventDraft]:
    """
    Parse every valid event in a fixture file.

    Parameters:
      name: Fixture file name.
    Returns:
      Drafts keyed by native id.
    """
    src = _source()
    out: dict[str, EventDraft] = {}
    for raw in src.split((FIXTURES / name).read_bytes(), NOW):
        try:
            [draft] = src.parse(raw)
        except ParseError:
            continue
        out[raw.native_id] = draft
    return out


def test_params_require_https_and_known_zone() -> None:
    """Plain HTTP feeds and unknown zones are rejected at config time."""
    with pytest.raises(ValidationError, match="https"):
        _source(url="http://feeds.example.test/cal.ics")
    with pytest.raises(ValidationError, match="unknown time zone"):
        _source(default_tz="Mars/Olympus")


def test_utc_feed_fields() -> None:
    """UTC events keep the configured locality and map core fields."""
    draft = _drafts("utc_feed.ics")["evt-aaa111@events.example.test"]
    assert draft.start_utc == datetime(2026, 10, 15, 1, tzinfo=UTC)
    assert draft.tz == "America/Los_Angeles"
    assert draft.attendance_mode is AttendanceMode.IN_PERSON
    assert draft.status is EventStatus.SCHEDULED
    assert draft.organizer == "Builders Guild"
    assert draft.url == "https://events.example.test/event/evt-aaa111"
    assert draft.address is None
    assert (draft.lat, draft.lon) == (34.0522, -118.2437)


def test_floating_time_uses_calendar_zone_and_duration() -> None:
    """Floating DTSTART is local to X-WR-TIMEZONE; DURATION sets the end."""
    draft = _drafts("edge_cases.ics")["floating-1"]
    assert draft.start_utc == datetime(2026, 10, 2, 1, tzinfo=UTC)
    assert draft.end_utc == datetime(2026, 10, 2, 3, tzinfo=UTC)
    assert draft.address == "123 Main St, Irvine, CA"
    assert draft.attendance_mode is AttendanceMode.IN_PERSON


def test_all_day_event_starts_at_local_midnight() -> None:
    """Date-only values become local midnight."""
    draft = _drafts("edge_cases.ics")["allday-1"]
    assert draft.start_utc == datetime(2026, 10, 3, 7, tzinfo=UTC)


def test_custom_vtimezone_is_applied() -> None:
    """Non-IANA TZIDs resolve through the embedded VTIMEZONE."""
    draft = _drafts("edge_cases.ics")["custom-tz-1"]
    assert draft.start_utc == datetime(2026, 12, 1, 18, tzinfo=UTC)


def test_cancelled_online_event() -> None:
    """STATUS:CANCELLED and meeting links map to status and mode."""
    draft = _drafts("edge_cases.ics")["cancelled-1"]
    assert draft.status is EventStatus.CANCELLED
    assert draft.attendance_mode is AttendanceMode.ONLINE


def test_missing_uid_gets_stable_hash_id() -> None:
    """Events without UID get a deterministic content-derived id."""
    first = set(_drafts("edge_cases.ics"))
    second = set(_drafts("edge_cases.ics"))
    hashed = [i for i in first if i.startswith("sha256:")]
    assert len(hashed) == 1
    assert first == second


def test_missing_summary_raises_parse_error() -> None:
    """Events without a title are rejected, not published."""
    src = _source()
    [bad] = [
        r
        for r in src.split((FIXTURES / "edge_cases.ics").read_bytes(), NOW)
        if r.native_id == "bad-1"
    ]
    with pytest.raises(ParseError):
        src.parse(bad)


def test_dtstamp_does_not_change_content_hash() -> None:
    """Regenerated DTSTAMP values do not make records look changed."""
    src = _source()
    body = (FIXTURES / "utc_feed.ics").read_bytes()
    later = body.replace(
        b"DTSTAMP:20260926T105934Z", b"DTSTAMP:20270101T000000Z"
    )
    first = [r.content_hash for r in src.split(body, NOW)]
    second = [r.content_hash for r in src.split(later, NOW)]
    assert first == second
