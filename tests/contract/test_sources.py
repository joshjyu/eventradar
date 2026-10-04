"""Contract tests every adapter must pass against its recorded fixtures."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.http import HttpClient
from eventradar.sources.base import ParseError, SourceContext
from eventradar.sources.registry import build_source
from tests.unit.test_devpost import API as DEVPOST_API
from tests.unit.test_devpost import listing_url

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 9, 26, tzinfo=UTC)
FEED = "https://feeds.example.test/feed"
HUB = "https://hub.example.test/d/city/tech--events/"
EV = "https://events.example.test/e"
LA = "America/Los_Angeles"

# (case id, adapter, params, {served URL: fixture path})
CASES: list[tuple[str, str, dict[str, Any], dict[str, str]]] = [
    (
        "ical-utc",
        "ical",
        {"url": FEED, "default_tz": LA},
        {FEED: "ical/utc_feed.ics"},
    ),
    (
        "ical-edge",
        "ical",
        {"url": FEED, "default_tz": LA},
        {FEED: "ical/edge_cases.ics"},
    ),
    (
        "jsonld-hub",
        "jsonld",
        {
            "discovery": {
                "type": "hub",
                "urls": [HUB + "?page={page}"],
                "pages": 3,
            },
            "url_pattern": r"^https://events\.example\.test/e/",
            "default_tz": LA,
        },
        {
            f"{HUB}?page=1": "jsonld/hub_p1.html",
            f"{HUB}?page=2": "jsonld/hub_p2.html",
            f"{HUB}?page=3": "jsonld/hub_p3.html",
            f"{EV}/robotics-day-101": "jsonld/event_robotics.html",
            f"{EV}/cloud-summit-102": "jsonld/event_cloud.html",
            f"{EV}/empty-page-103": "jsonld/event_empty.html",
        },
    ),
    (
        "meetup",
        "meetup",
        {
            "endpoint": "https://api.meetup.example.test/gql-ext",
            "areas": [{"label": "x", "lat": 34, "lon": -118, "radius_mi": 5}],
            "queries": ["tech"],
            "default_tz": LA,
        },
        {"https://api.meetup.example.test/gql-ext": "meetup/page2.json"},
    ),
    (
        "devpost",
        "devpost",
        {"endpoint": DEVPOST_API, "default_tz": LA},
        {
            listing_url(1): "devpost/list_p1.json",
            listing_url(2): "devpost/list_p2.json",
            "https://alpha-hack.devpost.example.test/": "devpost/alpha.html",
            "https://beta-hack.devpost.example.test/": "devpost/beta.html",
            "https://gamma-hack.devpost.example.test/": "devpost/gamma.html",
        },
    ),
    (
        "mlh",
        "mlh",
        {"base_url": "https://mlh.example.test", "seasons_ahead": 0},
        {"https://mlh.example.test/seasons/2027/events": "mlh/season.html"},
    ),
    (
        "jsonld-dates-only",
        "jsonld",
        {
            "discovery": {
                "type": "links",
                "urls": ["https://devevents.example.test/NA/US/CA/Los_Angeles"],
            },
            "url_pattern": (
                r"^https://devevents\.example\.test/conferences/[a-z0-9-]+$"
            ),
            "default_tz": LA,
            "dates_only": True,
            "default_state": "CA",
        },
        {
            "https://devevents.example.test/NA/US/CA/Los_Angeles": (
                "devevents/listing.html"
            ),
            "https://devevents.example.test/conferences/"
            "example-kubeconf-la-2026-ab12cd34": "devevents/kubeconf.html",
            "https://devevents.example.test/conferences/"
            "example-devopsday-la-2027-ef56gh78": "devevents/devopsday.html",
        },
    ),
    (
        "jsonld-links",
        "jsonld",
        {
            "discovery": {
                "type": "links",
                "urls": ["https://site.example.test/schedule"],
            },
            "url_pattern": r"^https://events\.example\.test/e/",
            "default_tz": LA,
        },
        {
            "https://site.example.test/schedule": "jsonld/site_links.html",
            f"{EV}/robotics-day-101": "jsonld/event_robotics.html",
            f"{EV}/cloud-summit-102": "jsonld/event_cloud.html",
        },
    ),
    (
        "jsonld-urls",
        "jsonld",
        {
            "discovery": {
                "type": "urls",
                "urls": [f"{EV}/robotics-day-101"],
            },
            "default_tz": LA,
        },
        {f"{EV}/robotics-day-101": "jsonld/event_robotics.html"},
    ),
]


@pytest.mark.parametrize(
    ("adapter", "params", "routes"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
@respx.mock
async def test_adapter_contract(
    adapter: str, params: dict[str, Any], routes: dict[str, str]
) -> None:
    """
    Fetch yields unique records whose parse output is valid and attributed.

    Parameters:
      adapter: Registered adapter name.
      params: Adapter params.
      routes: URLs to serve and the fixtures served at each.
    """
    for url, fixture in routes.items():
        body = (FIXTURES / fixture).read_bytes()
        respx.route(url=url).mock(
            return_value=httpx.Response(200, content=body)
        )
    config = SourceConfig(id="contract-src", adapter=adapter, params=params)
    source = build_source(config)
    settings = HttpSettings(
        user_agent="test", per_host_min_interval_s=0, respect_robots=False
    )
    async with HttpClient(settings) as http:
        records = await source.fetch(SourceContext(http=http, now=NOW))
    assert records
    keys = [r.native_id for r in records]
    assert len(keys) == len(set(keys))
    parsed = 0
    for raw in records:
        assert raw.source_id == "contract-src"
        try:
            drafts = source.parse(raw)
        except ParseError:
            continue
        for draft in drafts:
            assert (draft.source_id, draft.native_id) == (
                raw.source_id,
                raw.native_id,
            )
            parsed += 1
    assert parsed
