"""Tests for the official Eventbrite API adapter."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.models import RawRecord
from eventradar.http import HttpClient
from eventradar.sources.base import SourceContext
from eventradar.sources.platforms.eventbrite import (
    EventbriteSource,
    MissingTokenError,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eventbrite"
API = "https://api.eventbrite.example.test/v3"
ORG_URL = f"{API}/organizers/111/events/"
NOW = datetime(2026, 9, 29, 13, tzinfo=UTC)
TOKEN = "test-token-not-real"


def _source(**params: object) -> EventbriteSource:
    """
    Build an adapter with one organizer.

    Parameters:
      params: Param overrides.
    Returns:
      Adapter instance.
    """
    merged: dict[str, object] = {
        "endpoint": API,
        "organizers": ["111"],
        "token_env": "TEST_EVENTBRITE_TOKEN",
        "default_tz": "America/Los_Angeles",
    }
    merged.update(params)
    config = SourceConfig(id="test-eb", adapter="eventbrite", params=merged)
    return EventbriteSource(config)


def mock_organizer() -> respx.Route:
    """
    Serve two pages of organizer events, chained by continuation.

    Returns:
      The route.
    """

    def _page(request: httpx.Request) -> httpx.Response:
        """
        Pick the page by continuation token.

        Parameters:
          request: Incoming request.
        Returns:
          Fixture response.
        """
        second = request.url.params.get("continuation") == "abc123"
        name = "org_p2.json" if second else "org_p1.json"
        return httpx.Response(200, content=(FIXTURES / name).read_bytes())

    return respx.get(url__startswith=ORG_URL).mock(side_effect=_page)


async def _fetch(source: EventbriteSource) -> list[RawRecord]:
    """
    Run `fetch` against the mocked API.

    Parameters:
      source: Adapter.
    Returns:
      Fetched records.
    """
    settings = HttpSettings(
        user_agent="test", per_host_min_interval_s=0, respect_robots=False
    )
    async with HttpClient(settings) as http:
        return await source.fetch(SourceContext(http=http, now=NOW))


@respx.mock
async def test_fetch_follows_continuation_with_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Pages are chained and every request carries the token header.

    Parameters:
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("TEST_EVENTBRITE_TOKEN", TOKEN)
    route = mock_organizer()
    records = await _fetch(_source())
    assert [r.native_id for r in records] == [
        "90000000001",
        "90000000002",
        "90000000003",
    ]
    assert route.call_count == 2
    for call in route.calls:
        assert call.request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(call.request.url)
    assert "logo" not in records[0].payload
    assert records[0].payload["organizer"] == {"name": "Example Tech Org"}


async def test_missing_token_fails_without_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    An unset token fails the source with a clear, value-free message.

    Parameters:
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("TEST_EVENTBRITE_TOKEN", raising=False)
    with pytest.raises(MissingTokenError, match="TEST_EVENTBRITE_TOKEN"):
        await _fetch(_source())


@respx.mock
async def test_parse_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Times, venue, capacity, online, and cancellation map to drafts.

    Parameters:
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("TEST_EVENTBRITE_TOKEN", TOKEN)
    mock_organizer()
    source = _source()
    by_id = {r.native_id: r for r in await _fetch(source)}
    [demo] = source.parse(by_id["90000000001"])
    assert demo.title == "AI Demo Night"
    assert demo.start_utc == datetime(2026, 10, 15, 1, tzinfo=UTC)
    assert demo.tz == "America/Los_Angeles"
    assert demo.address == "100 Example Ave, Irvine, CA 92618"
    assert (demo.lat, demo.lon) == (33.6846, -117.8265)
    assert demo.size_signal == 150
    assert demo.organizer == "Example Tech Org"
    [online] = source.parse(by_id["90000000002"])
    assert online.attendance_mode is AttendanceMode.ONLINE
    assert online.address is None
    [cancelled] = source.parse(by_id["90000000003"])
    assert cancelled.status is EventStatus.CANCELLED


def test_token_env_name_is_validated() -> None:
    """Only upper-case environment variable names are accepted."""
    with pytest.raises(ValueError, match="token_env"):
        _source(token_env="lower; rm -rf")
