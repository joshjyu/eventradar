"""U.S. Census Bureau geocoder: free, keyless, public domain, U.S. only."""

from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict

from eventradar.config.types import HttpsUrl
from eventradar.geo.geocode.base import GeoPoint
from eventradar.http import HttpClient


class CensusOptions(BaseModel):
    """Options for the Census geocoder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: HttpsUrl = (
        "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    )
    benchmark: str = "Public_AR_Current"


class CensusGeocoder:
    """Matches street addresses against Census address ranges."""

    name = "census"

    def __init__(self, **options: Any) -> None:
        """
        Configure the geocoder.

        Parameters:
          options: Fields of `CensusOptions`.
        """
        self._opts = CensusOptions.model_validate(options)

    async def geocode(self, address: str, http: HttpClient) -> GeoPoint | None:
        """
        Locate a U.S. street address.

        Parameters:
          address: One-line address.
          http: Shared HTTP client.
        Returns:
          The best match, or None.
        """
        query = urlencode(
            {
                "address": address,
                "benchmark": self._opts.benchmark,
                "format": "json",
            }
        )
        data = (await http.get(f"{self._opts.endpoint}?{query}")).json()
        matches = (data.get("result") or {}).get("addressMatches") or []
        if not matches:
            return None
        coords = matches[0].get("coordinates") or {}
        lat, lon = coords.get("y"), coords.get("x")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            return None
        return GeoPoint(lat=float(lat), lon=float(lon), precision="address")
