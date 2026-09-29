"""Tests for the MLH adapter."""

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.domain.enums import AttendanceMode, EventKind
from eventradar.domain.models import RawRecord
from eventradar.geo.places import place_centroid
from eventradar.http import HttpClient, HttpError
from eventradar.sources.base import ParseError, SourceContext
from eventradar.sources.platforms.mlh import MlhSource, season_of

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mlh"
BASE = "https://mlh.example.test"
NOW = datetime(2026, 9, 29, 13, tzinfo=UTC)
LA_HACKS = "019fb896-86e4-8802-4f6d-ccb9cf7ba3bb"
CAL_HACKS = "01a0439b-7ccc-7e99-15a8-09bd3de3e722"
TECHNICA = "01a081a1-b6e3-49d7-50f4-fe11bdf9d419"
ONLINE = "00000000-0000-0000-0000-000000000001"


def season_url(season: int) -> str:
    """
    Build the season page URL the adapter requests.

    Parameters:
      season: Season year.
    Returns:
      Absolute URL.
    """
    return f"{BASE}/seasons/{season}/events"


def mock_seasons(next_status: int = 404) -> dict[int, respx.Route]:
    """
    Serve the fixture as season 2027; season 2028 answers `next_status`.

    Parameters:
      next_status: Status for the next season's page.
    Returns:
      Routes keyed by season.
    """
    body = (FIXTURE / "season.html").read_bytes()
    return {
        2027: respx.get(season_url(2027)).mock(
            return_value=httpx.Response(200, content=body)
        ),
        2028: respx.get(season_url(2028)).mock(
            return_value=httpx.Response(next_status)
        ),
    }


def _source(**params: object) -> MlhSource:
    """
    Build an adapter pointed at the test host.

    Parameters:
      params: Extra params.
    Returns:
      Adapter instance.
    """
    config = SourceConfig(
        id="test-mlh", adapter="mlh", params={"base_url": BASE, **params}
    )
    return MlhSource(config)


async def _fetch(source: MlhSource) -> list[RawRecord]:
    """
    Run `fetch` with a real client over mocked routes.

    Parameters:
      source: Adapter.
    Returns:
      Fetched records.
    """
    settings = HttpSettings(
        user_agent="test",
        per_host_min_interval_s=0,
        respect_robots=False,
        max_retries=0,
    )
    async with HttpClient(settings) as http:
        return await source.fetch(SourceContext(http=http, now=NOW))


@pytest.mark.parametrize(
    ("day", "season"),
    [
        (date(2026, 6, 30), 2026),
        (date(2026, 7, 1), 2027),
        (date(2026, 9, 29), 2027),
        (date(2027, 1, 15), 2027),
    ],
)
def test_season_of(day: date, season: int) -> None:
    """
    Seasons run July through June and are named for the year they end.

    Parameters:
      day: Calendar day.
      season: Expected season.
    """
    assert season_of(day) == season


@respx.mock
async def test_fetch_keeps_upcoming_in_person_events() -> None:
    """Online events and past events are dropped; a 404 next season is
    not an error."""
    routes = mock_seasons()
    records = await _fetch(_source())
    assert {r.native_id for r in records} == {LA_HACKS, CAL_HACKS, TECHNICA}
    assert routes[2028].called
    by_id = {r.native_id: r for r in records}
    assert by_id[LA_HACKS].url == "https://ai.lahacks.com"
    assert "backgroundUrl" not in by_id[LA_HACKS].payload


@respx.mock
async def test_include_online_keeps_digital_events() -> None:
    """Configured sources can keep online events."""
    mock_seasons()
    records = await _fetch(_source(include_online=True))
    assert ONLINE in {r.native_id for r in records}


@respx.mock
async def test_parse_maps_times_mode_and_city() -> None:
    """Drafts are hackathons at the venue's city with UTC times."""
    mock_seasons()
    source = _source()
    by_id = {r.native_id: r for r in await _fetch(source)}
    [la] = source.parse(by_id[LA_HACKS])
    assert la.title == "LA Hacks AI Hackathon 2026"
    assert la.start_utc == datetime(2026, 10, 17, 14, tzinfo=UTC)
    assert la.end_utc == datetime(2026, 10, 18, 20, tzinfo=UTC)
    assert la.address == "Los Angeles, CA, US"
    assert la.attendance_mode is AttendanceMode.IN_PERSON
    assert la.kinds == frozenset({EventKind.HACKATHON})
    assert la.organizer is None
    [cal] = source.parse(by_id[CAL_HACKS])
    assert cal.title == "Cal Hacks 13.0"
    [technica] = source.parse(by_id[TECHNICA])
    assert technica.attendance_mode is AttendanceMode.MIXED


@pytest.mark.parametrize(
    ("venue", "address"),
    [
        (
            {"city": "Toronto", "state": "Ontario", "country": "CA"},
            "Toronto, Ontario, Canada",
        ),
        (
            {"city": "Bhilai", "state": "Chhattisgarh", "country": "IN"},
            "Bhilai, Chhattisgarh, India",
        ),
        (
            {"city": "Tbilisi", "state": "Tbilisi", "country": "GE"},
            "Tbilisi, Tbilisi",
        ),
        (
            {"city": "Ithaca", "state": "New York", "country": "US"},
            "Ithaca, New York, US",
        ),
    ],
)
def test_country_codes_never_read_as_states(
    venue: dict[str, str], address: str
) -> None:
    """
    Country codes that are also state abbreviations are written out.

    Parameters:
      venue: Listing venue.
      address: Expected address.
    """
    raw = RawRecord(
        source_id="test-mlh",
        native_id="x",
        payload={
            "id": "x",
            "name": "Hack",
            "startsAt": "2026-10-17T14:00:00Z",
            "formatType": "physical",
            "venueAddress": venue,
        },
        fetched_at=NOW,
    )
    [draft] = _source().parse(raw)
    assert draft.address == address
    assert (place_centroid(address) is None) is (venue["country"] != "US")


@respx.mock
async def test_current_season_failure_fails_the_source() -> None:
    """Only a later season may be missing."""
    respx.get(season_url(2027)).mock(return_value=httpx.Response(404))
    with pytest.raises(HttpError, match="HTTP 404"):
        await _fetch(_source(seasons_ahead=0))


@respx.mock
async def test_next_season_errors_other_than_404_fail() -> None:
    """A server error on the next season is reported, not skipped."""
    mock_seasons(next_status=500)
    with pytest.raises(HttpError, match="HTTP 500"):
        await _fetch(_source())


@respx.mock
async def test_page_without_data_is_a_parse_error() -> None:
    """A redesign that drops the page data fails loudly."""
    respx.get(season_url(2027)).mock(
        return_value=httpx.Response(200, text="<html><body></body></html>")
    )
    with pytest.raises(ParseError, match="no page data"):
        await _fetch(_source(seasons_ahead=0))
