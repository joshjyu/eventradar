"""Geocoder protocol and result type."""

from dataclasses import dataclass
from typing import Literal, Protocol

from eventradar.http import HttpClient

type Precision = Literal["address", "postal"]


@dataclass(frozen=True)
class GeoPoint:
    """A located point and how precise it is."""

    lat: float
    lon: float
    precision: Precision


class Geocoder(Protocol):
    """Turns one-line addresses into coordinates."""

    name: str

    async def geocode(self, address: str, http: HttpClient) -> GeoPoint | None:
        """
        Locate an address.

        Parameters:
          address: One-line address.
          http: Shared HTTP client.
        Returns:
          The point, or None when the service finds no match.
        """
        ...
