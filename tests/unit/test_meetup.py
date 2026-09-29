"""Tests for the Meetup GraphQL adapter."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.models import RawRecord
from eventradar.http import HttpClient, HttpError
from eventradar.sources.base import SourceContext
from eventradar.sources.platforms.meetup import MeetupSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "meetup"
ENDPOINT = "https://api.meetup.example.test/gql-ext"
NOW = datetime(2026, 9, 29, 13, tzinfo=UTC)


def _source(**params: object) -> MeetupSource:
    """
    Build an adapter with two areas and one keyword.

    Parameters:
      params: Param overrides.
    Returns:
      Adapter instance.
    """
    merged: dict[str, object] = {
        "endpoint": ENDPOINT,
        "areas": [
            {"label": "a1", "lat": 34.0, "lon": -118.0, "radius_mi": 10},
            {"label": "a2", "lat": 33.0, "lon": -117.0, "radius_mi": 10},
        ],
        "queries": ["tech"],
        "topic_category_id": "546",
        "default_tz": "America/Los_Angeles",
    }
    merged.update(params)
    config = SourceConfig(id="test-meetup", adapter="meetup", params=merged)
    return MeetupSource(config)


def _fixture(name: str) -> dict[str, Any]:
    """
    Load a GraphQL response fixture.

    Parameters:
      name: File name.
    Returns:
      Decoded JSON.
    """
    return json.loads((FIXTURES / name).read_text())


def _responder(request: httpx.Request) -> httpx.Response:
    """
    Serve fixtures by area latitude and pagination cursor.

    Parameters:
      request: Incoming GraphQL POST.
    Returns:
      Fixture response.
    """
    variables = json.loads(request.content)["variables"]
    assert variables["filter"]["topicCategoryId"] == "546"
    if variables["filter"]["lat"] == 33.0:
        return httpx.Response(200, json=_fixture("area2.json"))
    name = "page2.json" if variables["after"] == "Mg==" else "page1.json"
    return httpx.Response(200, json=_fixture(name))


async def _fetch(source: MeetupSource) -> list[RawRecord]:
    """
    Run `fetch` against the mocked endpoint.

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


@respx.mock
async def test_fetch_pages_dedups_and_skips_online() -> None:
    """Cursors are followed, duplicates merged, online events dropped."""
    route = respx.post(ENDPOINT).mock(side_effect=_responder)
    records = await _fetch(_source())
    assert sorted(r.native_id for r in records) == [
        "300000001",
        "300000003",
        "300000004",
        "300000005",
    ]
    assert route.call_count == 3


@respx.mock
async def test_include_online_keeps_online_events() -> None:
    """Online events are kept when configured."""
    respx.post(ENDPOINT).mock(side_effect=_responder)
    records = await _fetch(_source(include_online=True))
    assert "300000002" in {r.native_id for r in records}


@respx.mock
async def test_parse_maps_fields() -> None:
    """Times, venue, status, mode, group, and RSVPs map to the draft."""
    respx.post(ENDPOINT).mock(side_effect=_responder)
    source = _source(include_online=True)
    by_id = {r.native_id: r for r in await _fetch(source)}
    [rust] = source.parse(by_id["300000001"])
    assert rust.start_utc == datetime(2026, 10, 14, 1, tzinfo=UTC)
    assert rust.end_utc == datetime(2026, 10, 14, 3, tzinfo=UTC)
    assert rust.tz == "America/Los_Angeles"
    assert rust.address == "100 Example St, Irvine, CA, 92618, us"
    assert (rust.lat, rust.lon) == (33.68, -117.83)
    assert rust.organizer == "Example Group 1"
    assert rust.size_signal == 42
    assert rust.attendance_mode is AttendanceMode.IN_PERSON
    [online] = source.parse(by_id["300000002"])
    assert online.attendance_mode is AttendanceMode.ONLINE
    assert online.lat is None
    [cancelled] = source.parse(by_id["300000003"])
    assert cancelled.status is EventStatus.CANCELLED
    [hybrid] = source.parse(by_id["300000004"])
    assert hybrid.attendance_mode is AttendanceMode.MIXED
    assert hybrid.tz == "America/Los_Angeles"


@respx.mock
async def test_graphql_errors_fail_only_when_nothing_found() -> None:
    """One failing search is tolerated; all failing raises."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "x"}]})
    )
    with pytest.raises(Exception, match="graphql error"):
        await _fetch(_source())

    def _half(request: httpx.Request) -> httpx.Response:
        """
        Fail the second area only.

        Parameters:
          request: Incoming request.
        Returns:
          Fixture or error response.
        """
        lat = json.loads(request.content)["variables"]["filter"]["lat"]
        if lat == 33.0:
            return httpx.Response(500)
        return httpx.Response(200, json=_fixture("page2.json"))

    respx.post(ENDPOINT).mock(side_effect=_half)
    records = await _fetch(_source())
    assert {r.native_id for r in records} == {"300000001", "300000004"}


def test_params_are_validated() -> None:
    """Endpoints need TLS; areas need a positive radius."""
    with pytest.raises(ValueError, match="https"):
        _source(endpoint="http://api.meetup.example.test/gql-ext")
    with pytest.raises(ValueError, match="radius_mi"):
        _source(areas=[{"label": "x", "lat": 1, "lon": 1, "radius_mi": 0}])


@respx.mock
async def test_http_errors_surface_when_all_fail() -> None:
    """A dead endpoint fails the source."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
    with pytest.raises(HttpError):
        await _fetch(_source())
