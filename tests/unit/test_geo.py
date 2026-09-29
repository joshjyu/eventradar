"""Tests for ZIP centroids, time zone lookup, and the Census geocoder."""

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings
from eventradar.geo.geocode.providers.census import CensusGeocoder
from eventradar.geo.postal import zip_centroid
from eventradar.geo.timezone import zone_at
from eventradar.http import HttpClient

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
