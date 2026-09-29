"""Tests for the Devpost adapter."""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
import respx

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.domain.enums import EventKind
from eventradar.domain.models import RawRecord
from eventradar.http import HttpClient
from eventradar.sources.base import PreviousLookup, SourceContext, no_previous
from eventradar.sources.platforms.devpost import DevpostSource
from eventradar.sources.protocols.jsonld_pages import refresh_due

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "devpost"
API = "https://devpost.example.test/api/hackathons"
NOW = datetime(2026, 9, 29, 13, tzinfo=UTC)
PAGES = {
    "https://alpha-hack.devpost.example.test/": "alpha.html",
    "https://beta-hack.devpost.example.test/": "beta.html",
    "https://gamma-hack.devpost.example.test/": "gamma.html",
}


def listing_url(page: int) -> str:
    """
    Build the listing URL the adapter requests for a page.

    Parameters:
      page: Page number.
    Returns:
      Absolute URL.
    """
    query = [
        ("challenge_type[]", "in-person"),
        ("status[]", "upcoming"),
        ("status[]", "open"),
        ("page", str(page)),
    ]
    return f"{API}?{urlencode(query)}"


def mock_devpost() -> dict[str, respx.Route]:
    """
    Serve two listing pages and three hackathon pages.

    Returns:
      Routes keyed by URL.
    """
    served = {listing_url(1): "list_p1.json", listing_url(2): "list_p2.json"}
    served.update(PAGES)
    return {
        url: respx.get(url).mock(
            return_value=httpx.Response(
                200, content=(FIXTURES / name).read_bytes()
            )
        )
        for url, name in served.items()
    }


def _source() -> DevpostSource:
    """
    Build an adapter pointed at the test API.

    Returns:
      Adapter instance.
    """
    params = {"endpoint": API, "default_tz": "America/Los_Angeles"}
    config = SourceConfig(id="test-devpost", adapter="devpost", params=params)
    return DevpostSource(config)


async def _fetch(
    source: DevpostSource, previous: PreviousLookup = no_previous
) -> list[RawRecord]:
    """
    Run `fetch` with a real client over mocked routes.

    Parameters:
      source: Adapter.
      previous: Previous-record lookup.
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
        ctx = SourceContext(http=http, now=NOW, previous=previous)
        return await source.fetch(ctx)


@respx.mock
async def test_listing_pages_until_total_count() -> None:
    """Listing stops once `total_count` items are collected."""
    routes = mock_devpost()
    records = await _fetch(_source())
    assert sorted(r.native_id for r in records) == sorted(PAGES)
    assert routes[listing_url(2)].called
    assert all(c.request.url.params.get("page") != "3" for c in respx.calls)


@respx.mock
async def test_parse_adds_hackathon_kind_size_and_organizer() -> None:
    """Drafts are hackathons sized by registrations."""
    mock_devpost()
    source = _source()
    by_url = {r.native_id: r for r in await _fetch(source)}
    [alpha] = source.parse(by_url["https://alpha-hack.devpost.example.test/"])
    assert alpha.kinds == frozenset({EventKind.HACKATHON})
    assert alpha.size_signal == 120
    assert alpha.organizer == "Alpha ACM"
    assert alpha.start_utc == datetime(2026, 11, 21, 16, tzinfo=UTC)
    assert alpha.tz == "America/Los_Angeles"
    [gamma] = source.parse(by_url["https://gamma-hack.devpost.example.test/"])
    assert gamma.organizer == "Gamma Club"


@respx.mock
async def test_registration_changes_do_not_refetch_pages() -> None:
    """New counts update records without page requests when not due."""
    routes = mock_devpost()
    source = _source()
    first = {r.native_id: r for r in await _fetch(source)}
    before = {u: routes[u].call_count for u in PAGES}
    stored = {
        k: v.model_copy(update={"payload": {**v.payload, "signals": {}}})
        for k, v in first.items()
    }
    second = await _fetch(source, lambda _sid, nid: stored.get(nid))
    refetched = {u for u in PAGES if routes[u].call_count > before[u]}
    due = {u for u in PAGES if refresh_due(u, NOW.date(), 7)}
    assert refetched == due
    counts = {
        r.native_id: r.payload["signals"]["registrations"] for r in second
    }
    assert counts["https://alpha-hack.devpost.example.test/"] == 120


@respx.mock
async def test_blocked_pages_fail_the_source() -> None:
    """If every page fetch is a bot challenge, the source reports it."""
    import pytest

    from eventradar.http import HttpError

    routes = mock_devpost()
    for url in PAGES:
        routes[url].mock(return_value=httpx.Response(202, text="<html>"))
    with pytest.raises(HttpError, match="all 3 event page fetches failed"):
        await _fetch(_source())


@respx.mock
async def test_one_failed_page_does_not_fail_the_source() -> None:
    """Isolated page failures are tolerated."""
    routes = mock_devpost()
    first = next(iter(PAGES))
    routes[first].mock(return_value=httpx.Response(500))
    records = await _fetch(_source())
    assert first not in {r.native_id for r in records}
    assert len(records) == len(PAGES) - 1
