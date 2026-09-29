"""Tests for offline centroids, time zones, geocoding, and enrichment."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from eventradar.config.schema import GeoSettings, HttpSettings
from eventradar.domain.models import EventDraft
from eventradar.geo.geocode.base import GeoPoint
from eventradar.geo.geocode.providers.census import CensusGeocoder
from eventradar.geo.places import place_centroid
from eventradar.geo.postal import zip_centroid
from eventradar.geo.timezone import zone_at
from eventradar.http import HttpClient
from eventradar.pipeline.enrich import enrich
from eventradar.storage.db import connect, migrate
from eventradar.storage.repositories import (
    EventRepository,
    GeocodeCacheRepository,
    RunRepository,
)

CENSUS = "https://census.example.test/geocoder"


@pytest.mark.parametrize(
    ("address", "found"),
    [
        ("900 University Ave, Riverside, CA 92521, USA", True),
        ("Maker Hall, Irvine, California 92618", True),
        ("1 Main St, Irvine, ca 92618-1234", True),
        ("Hauptstr. 5, 10115 Berlin", False),
        ("Room 92618, Example Tower", False),
        ("Nowhere, CA 00000", False),
    ],
)
def test_zip_centroid_requires_us_state(address: str, found: bool) -> None:
    """
    Only a state followed by a known ZIP counts as a U.S. address.

    Parameters:
      address: One-line address.
      found: Whether a centroid is expected.
    """
    point = zip_centroid(address)
    assert (point is not None) is found
    if point:
        assert point.precision == "postal"


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("Los Angeles, CA, US", (34.019, -118.411)),
        ("Los Angeles, California", (34.019, -118.411)),
        ("Pauley Pavilion, los angeles,  ca", (34.019, -118.411)),
        ("1 Main St, Irvine, CA 92618, United States", (33.678, -117.771)),
        ("East Los Angeles, CA", (34.032, -118.169)),
        ("Irvine", None),
        ("London, GB", None),
        ("Springfield, Narnia", None),
        ("Nowhereville, CA", None),
    ],
)
def test_place_centroid(
    address: str, expected: tuple[float, float] | None
) -> None:
    """
    Addresses ending in a U.S. city and state resolve to its centroid.

    Parameters:
      address: One-line address.
      expected: Rounded (lat, lon), or None for no match.
    """
    point = place_centroid(address)
    if expected is None:
        assert point is None
        return
    assert point is not None
    assert (round(point.lat, 3), round(point.lon, 3)) == expected
    assert point.precision == "place"


class _NoMatch:
    """A geocoder that finds nothing and counts its lookups."""

    name = "stub"

    def __init__(self) -> None:
        """Start with no lookups."""
        self.lookups = 0

    async def geocode(self, address: str, http: HttpClient) -> GeoPoint | None:
        """
        Record the lookup and miss.

        Parameters:
          address: One-line address.
          http: Shared HTTP client.
        Returns:
          Always None.
        """
        self.lookups += 1


async def test_enrich_falls_back_to_city_centroid(tmp_path: Path) -> None:
    """
    City-only addresses are placed at the city, even after a cached miss.

    Parameters:
      tmp_path: Pytest temporary directory.
    """
    now = datetime(2026, 9, 29, tzinfo=UTC)
    conn = connect(tmp_path / "state.db")
    migrate(conn)
    RunRepository(conn).start("run-1", now)
    events = EventRepository(conn, id_factory=iter(["E1", "E2"]).__next__)
    for native_id, address in (
        ("a", "Los Angeles, CA, US"),
        ("b", "Irvine, CA"),
    ):
        draft = EventDraft(
            source_id="src",
            native_id=native_id,
            title=f"Hack {native_id}",
            start_utc=datetime(2026, 10, 17, 14, tzinfo=UTC),
            address=address,
        )
        events.upsert_draft(draft, "h", now)
    cache = GeocodeCacheRepository(conn)
    cache.put("irvine, ca", None, "census", now)
    geocoder = _NoMatch()
    async with HttpClient(_settings()) as http:
        stats = await enrich(conn, events, geocoder, http, now, GeoSettings())
    assert (stats.located, geocoder.lookups) == (2, 1)
    located = dict(conn.execute("SELECT event_id, tz FROM events"))
    assert located == {
        "E1": "America/Los_Angeles",
        "E2": "America/Los_Angeles",
    }
    stored = cache.get("los angeles, ca, us")
    assert stored is not None
    assert stored.precision == "place"


def test_zone_at() -> None:
    """Land points resolve to IANA zones; open ocean does not."""
    assert zone_at(33.99, -117.31) == "America/Los_Angeles"
    assert zone_at(40.71, -74.0) == "America/New_York"
    assert zone_at(0.0, -140.0) is None


def _settings() -> HttpSettings:
    """
    Build fast test settings.

    Returns:
      HTTP settings.
    """
    return HttpSettings(
        user_agent="test", per_host_min_interval_s=0, respect_robots=False
    )


@respx.mock
async def test_census_match_and_miss() -> None:
    """A match returns address precision; no match returns None."""
    route = respx.get(url__startswith=CENSUS).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "result": {
                        "addressMatches": [
                            {"coordinates": {"x": -117.33, "y": 33.97}}
                        ]
                    }
                },
            ),
            httpx.Response(200, json={"result": {"addressMatches": []}}),
        ]
    )
    geocoder = CensusGeocoder(endpoint=CENSUS)
    async with HttpClient(_settings()) as http:
        hit = await geocoder.geocode("900 University Ave, Riverside", http)
        miss = await geocoder.geocode("Nowhere", http)
    assert hit is not None
    assert (hit.lat, hit.lon, hit.precision) == (33.97, -117.33, "address")
    assert miss is None
    sent = route.calls[0].request.url.params
    assert sent["address"] == "900 University Ave, Riverside"
    assert sent["format"] == "json"
