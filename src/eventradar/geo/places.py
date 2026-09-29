"""Offline U.S. city/place centroids, for addresses that name only a city."""

import csv
import gzip
import io
import re
from functools import cache
from importlib.resources import files

from eventradar.geo.geocode.base import GeoPoint
from eventradar.geo.states import state_abbr

_COUNTRIES = frozenset({"us", "usa", "u.s.", "u.s.a.", "united states"})
# A trailing ZIP after the state ("CA 90012") is not part of the state.
_ZIP_TAIL = re.compile(r"\s+\d{5}(?:-\d{4})?$")


def _key(state: str, place: str) -> tuple[str, str]:
    """
    Build a lookup key.

    Parameters:
      state: Postal abbreviation.
      place: Place name.
    Returns:
      (state, lower-cased place with single spaces).
    """
    return state, " ".join(place.lower().split())


@cache
def _table() -> dict[tuple[str, str], tuple[float, float]]:
    """
    Load the packaged place centroid table.

    Returns:
      (state, place key) to (lat, lon).
    """
    raw = (files("eventradar.geo.data") / "place_centroids.csv.gz").read_bytes()
    text = gzip.decompress(raw).decode("utf-8")
    return {
        _key(row["state"], row["place"]): (float(row["lat"]), float(row["lon"]))
        for row in csv.DictReader(io.StringIO(text))
    }


def place_centroid(address: str) -> GeoPoint | None:
    """
    Locate an American address by the centroid of its city.

    The address must end with `city, state`, optionally followed by a ZIP
    code or a U.S. country name.

    Parameters:
      address: One-line address, e.g. `Los Angeles, CA, US`.
    Returns:
      The centroid, or None when the address names no known U.S. place.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if parts and parts[-1].lower() in _COUNTRIES:
        parts.pop()
    if len(parts) < 2:
        return None
    state = state_abbr(_ZIP_TAIL.sub("", parts[-1]))
    point = _table().get(_key(state, parts[-2])) if state else None
    if not point:
        return None
    return GeoPoint(lat=point[0], lon=point[1], precision="place")
