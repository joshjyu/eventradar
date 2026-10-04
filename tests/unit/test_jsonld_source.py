"""Tests for the JSON-LD adapter's discovery, refresh, and parsing."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.models import RawRecord
from eventradar.geo.places import place_centroid
from eventradar.http import HttpClient, HttpError
from eventradar.sources.base import (
    ParseError,
    PreviousLookup,
    SourceContext,
    no_previous,
)
from eventradar.sources.protocols.jsonld import JsonLdSource, refresh_due

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jsonld"
HUB = "https://hub.example.test/d/city/tech--events/"
EV = "https://events.example.test/e"
NOW = datetime(2026, 9, 28, 13, tzinfo=UTC)
PAGES = {
    f"{EV}/robotics-day-101": "event_robotics.html",
    f"{EV}/cloud-summit-102": "event_cloud.html",
    f"{EV}/empty-page-103": "event_empty.html",
}


def _source(**params: object) -> JsonLdSource:
    """
    Build a hub-discovery adapter with test params.

    Parameters:
      params: Param overrides.
    Returns:
      Adapter instance.
    """
    merged: dict[str, object] = {
        "discovery": {
            "type": "hub",
            "urls": [HUB + "?page={page}"],
            "pages": 5,
        },
        "url_pattern": r"^https://events\.example\.test/e/",
        "default_tz": "America/Los_Angeles",
    }
    merged.update(params)
    config = SourceConfig(id="test-jsonld", adapter="jsonld", params=merged)
    return JsonLdSource(config)


def _mock_site() -> dict[str, respx.Route]:
    """
    Serve hub pages 1-3 and every event page from fixtures.

    Returns:
      Routes keyed by URL, for call assertions.
    """
    routes = {}
    files = {f"{HUB}?page={p}": f"hub_p{p}.html" for p in (1, 2, 3)}
    files.update(PAGES)
    for url, name in files.items():
        body = (FIXTURES / name).read_text()
        routes[url] = respx.get(url).mock(
            return_value=httpx.Response(200, text=body)
        )
    return routes


async def _fetch(
    source: JsonLdSource, previous: PreviousLookup = no_previous
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
async def test_hub_discovery_dedups_filters_and_stops() -> None:
    """Links are canonicalized, filtered, deduped; paging stops early."""
    routes = _mock_site()
    records = await _fetch(_source())
    assert [r.native_id for r in records] == list(PAGES)
    assert routes[f"{HUB}?page=3"].called
    assert not respx.calls.last.request.url.params.get("aff")
    listing = records[0].payload["listing"]
    assert "image" not in listing


@respx.mock
async def test_max_events_caps_discovery() -> None:
    """No more than `max_events` pages are fetched."""
    routes = _mock_site()
    records = await _fetch(_source(max_events=1))
    assert len(records) == 1
    assert not routes[f"{EV}/cloud-summit-102"].called


@respx.mock
async def test_parse_maps_primary_event() -> None:
    """Each page's primary event becomes one draft."""
    _mock_site()
    source = _source()
    by_id = {r.native_id: r for r in await _fetch(source)}
    [robotics] = source.parse(by_id[f"{EV}/robotics-day-101"])
    assert robotics.title == "Robotics Day"
    assert robotics.description == "Build & demo robots."
    assert robotics.organizer == "Robot Club"
    assert robotics.venue == "Maker Hall"
    [cloud] = source.parse(by_id[f"{EV}/cloud-summit-102"])
    assert cloud.status is EventStatus.CANCELLED
    assert cloud.attendance_mode is AttendanceMode.MIXED
    assert cloud.organizer is None
    assert cloud.size_signal == 800
    with pytest.raises(ParseError, match=r"no schema\.org Event"):
        source.parse(by_id[f"{EV}/empty-page-103"])


@respx.mock
async def test_unchanged_listing_reuses_stored_record() -> None:
    """Fresh pages are not refetched; changed listings are."""
    routes = _mock_site()
    source = _source(refresh_days=60)
    first = {r.native_id: r for r in await _fetch(source)}
    stale = f"{EV}/cloud-summit-102"
    modified = first[stale].model_copy(
        update={"payload": {**first[stale].payload, "listing": {"old": 1}}}
    )
    stored = {**first, stale: modified}
    before = {u: routes[u].call_count for u in PAGES}
    second = await _fetch(source, lambda _sid, nid: stored.get(nid))
    refetched = {u for u in PAGES if routes[u].call_count > before[u]}
    due = {u for u in PAGES if refresh_due(u, NOW.date(), 60)}
    assert refetched == {stale} | due
    assert {r.native_id for r in second} == set(PAGES)


@respx.mock
async def test_failed_page_keeps_stored_copy() -> None:
    """A page error falls back to the stored record, or skips it."""
    routes = _mock_site()
    source = _source(refresh_days=1)
    first = {r.native_id: r for r in await _fetch(source)}
    routes[f"{EV}/robotics-day-101"].mock(return_value=httpx.Response(404))
    kept = await _fetch(source, lambda _sid, nid: first.get(nid))
    assert f"{EV}/robotics-day-101" in {r.native_id for r in kept}
    dropped = await _fetch(source)
    assert f"{EV}/robotics-day-101" not in {r.native_id for r in dropped}


@respx.mock
async def test_all_hubs_failing_raises() -> None:
    """With no candidates at all, the hub error fails the source."""
    respx.get(url__startswith=HUB).mock(return_value=httpx.Response(503))
    with pytest.raises(HttpError):
        await _fetch(_source())


def test_refresh_due_cycles_once_per_period() -> None:
    """Every id is due exactly once in each cycle."""
    start = NOW.date().toordinal()
    days = [
        refresh_due("id-1", datetime.fromordinal(start + i).date(), 7)
        for i in range(14)
    ]
    assert sum(days) == 2


def test_params_reject_bad_config() -> None:
    """Plain HTTP, bad regexes, and unknown zones fail at config time."""
    with pytest.raises(ValueError, match="https"):
        _source(discovery={"type": "urls", "urls": ["http://x.test/e/1"]})
    with pytest.raises(ValueError, match="invalid url_pattern"):
        _source(url_pattern="(")
    with pytest.raises(ValueError, match="unknown time zone"):
        _source(default_tz="Nowhere/Land")


@respx.mock
async def test_signals_update_without_refetch() -> None:
    """Changed counters refresh the stored record but not the page."""
    from zoneinfo import ZoneInfo

    from eventradar.sources.protocols.jsonld_pages import PageHarvester

    routes = _mock_site()
    url = f"{EV}/robotics-day-101"
    pages = PageHarvester("test-jsonld", ZoneInfo("UTC"), refresh_days=60)
    settings = HttpSettings(
        user_agent="test", per_host_min_interval_s=0, respect_robots=False
    )
    async with HttpClient(settings) as http:
        first_ctx = SourceContext(http=http, now=NOW)
        first = await pages.record(url, None, first_ctx, {"n": 1})
        assert first is not None
        later = NOW.replace(day=29)
        if refresh_due(url, later.date(), 60):
            pytest.skip("url happens to be due on the test date")
        ctx = SourceContext(
            http=http, now=later, previous=lambda _sid, _nid: first
        )
        same = await pages.record(url, None, ctx, {"n": 1})
        bumped = await pages.record(url, None, ctx, {"n": 2})
    assert same is first
    assert bumped is not None
    assert bumped.payload["signals"] == {"n": 2}
    assert bumped.payload["events"] == first.payload["events"]
    assert routes[url].call_count == 1


def test_page_links_resolves_and_dedups() -> None:
    """Links are absolute, http(s) only, and listed once."""
    from eventradar.sources.protocols.links import page_links

    html = (FIXTURES / "site_links.html").read_text()
    links = page_links(html, "https://site.example.test/")
    assert "https://site.example.test/e/ignored-relative" in links
    assert not any(link.startswith("mailto:") for link in links)
    assert len(links) == len(set(links))


@respx.mock
async def test_links_discovery_harvests_matching_event_pages() -> None:
    """Only pattern-matching links are fetched, each once."""
    routes = _mock_site()
    site = "https://site.example.test/schedule"
    respx.get(site).mock(
        return_value=httpx.Response(
            200, text=(FIXTURES / "site_links.html").read_text()
        )
    )
    source = _source(discovery={"type": "links", "urls": [site]})
    records = await _fetch(source)
    assert sorted(r.native_id for r in records) == [
        f"{EV}/cloud-summit-102",
        f"{EV}/robotics-day-101",
    ]
    assert routes[f"{EV}/robotics-day-101"].call_count == 1
    [robotics] = source.parse(
        next(r for r in records if r.native_id.endswith("101"))
    )
    assert robotics.title == "Robotics Day"


@respx.mock
async def test_date_only_listing_with_default_state() -> None:
    """Midnight-UTC dates become local days and city-only addresses gain
    the configured state, so the city's centroid can place them."""
    host = "https://devevents.example.test"
    served = {
        f"{host}/NA/US/CA/Los_Angeles": "listing.html",
        f"{host}/conferences/example-kubeconf-la-2026-ab12cd34": (
            "kubeconf.html"
        ),
        f"{host}/conferences/example-devopsday-la-2027-ef56gh78": (
            "devopsday.html"
        ),
    }
    fixtures = FIXTURES.parent / "devevents"
    for url, name in served.items():
        respx.get(url).mock(
            return_value=httpx.Response(200, text=(fixtures / name).read_text())
        )
    source = _source(
        discovery={"type": "links", "urls": [f"{host}/NA/US/CA/Los_Angeles"]},
        url_pattern=r"^https://devevents\.example\.test/conferences/[a-z0-9-]+$",
        default_tz="America/Los_Angeles",
        dates_only=True,
        default_state="California",
    )
    records = await _fetch(source)
    by_id = {r.native_id.rsplit("/", 1)[-1]: r for r in records}
    assert sorted(by_id) == [
        "example-devopsday-la-2027-ef56gh78",
        "example-kubeconf-la-2026-ab12cd34",
    ]
    [kube] = source.parse(by_id["example-kubeconf-la-2026-ab12cd34"])
    assert kube.start_utc == datetime(2026, 10, 26, 7, tzinfo=UTC)
    assert kube.end_utc == datetime(2026, 10, 29, 7, tzinfo=UTC)
    assert kube.address == "Los Angeles, CA, United States"
    assert place_centroid(kube.address) is not None
    [devops] = source.parse(by_id["example-devopsday-la-2027-ef56gh78"])
    assert devops.end_utc is None


def test_default_state_must_be_a_state() -> None:
    """A typo in default_state fails config validation."""
    with pytest.raises(ValueError, match=r"unknown U\.S\. state"):
        _source(default_state="Calfornia")


def test_links_discovery_requires_a_pattern() -> None:
    """Following every link on a page is refused at config time."""
    with pytest.raises(ValueError, match="requires url_pattern"):
        _source(
            discovery={"type": "links", "urls": ["https://site.test/"]},
            url_pattern=None,
        )
