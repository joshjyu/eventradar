"""Contract tests every adapter must pass against its recorded fixtures."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.http import HttpClient
from eventradar.sources.base import ParseError, SourceContext
from eventradar.sources.registry import build_source

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 9, 26, tzinfo=UTC)
FEED_URL = "https://feeds.example.test/feed"

# (adapter, params, fixture file served at FEED_URL)
CASES = [
    ("ical", {"default_tz": "America/Los_Angeles"}, "ical/utc_feed.ics"),
    ("ical", {"default_tz": "America/Los_Angeles"}, "ical/edge_cases.ics"),
]


@pytest.mark.parametrize(("adapter", "params", "fixture"), CASES)
@respx.mock
async def test_adapter_contract(
    adapter: str, params: dict[str, object], fixture: str
) -> None:
    """
    Fetch yields unique records whose parse output is valid and attributed.

    Parameters:
      adapter: Registered adapter name.
      params: Adapter params, excluding the URL.
      fixture: Fixture path served as the upstream response.
    """
    respx.get(FEED_URL).mock(
        return_value=httpx.Response(
            200, content=(FIXTURES / fixture).read_bytes()
        )
    )
    config = SourceConfig(
        id="contract-src", adapter=adapter, params={"url": FEED_URL, **params}
    )
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
