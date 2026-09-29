"""Offline U.S. ZIP code centroids, used when an address will not geocode."""

import csv
import gzip
import io
import re
from functools import cache
from importlib.resources import files

from eventradar.geo.geocode.base import GeoPoint
from eventradar.geo.states import STATE_NAMES

# "CA 92507" or "California 92507": a state right before the ZIP marks the
# address as American, so five-digit postal codes elsewhere are ignored.
_STATE_ZIP = re.compile(r"\b([A-Za-z]{2}|California)\s+(\d{5})(?:-\d{4})?\b")


@cache
def _table() -> dict[str, tuple[float, float]]:
    """
    Load the packaged ZIP centroid table.

    Returns:
      ZIP code to (lat, lon).
    """
    raw = (files("eventradar.geo.data") / "zip_centroids.csv.gz").read_bytes()
    text = gzip.decompress(raw).decode("utf-8")
    return {
        row["zip"]: (float(row["lat"]), float(row["lon"]))
        for row in csv.DictReader(io.StringIO(text))
    }


def zip_centroid(address: str) -> GeoPoint | None:
    """
    Locate an American address by its ZIP code's centroid.

    Parameters:
      address: One-line address.
    Returns:
      The centroid, or None when no U.S. state + ZIP pair is present.
    """
    for state, code in reversed(_STATE_ZIP.findall(address)):
        is_state = state.upper() in STATE_NAMES or state == "California"
        if is_state and code in _table():
            lat, lon = _table()[code]
            return GeoPoint(lat=lat, lon=lon, precision="postal")
    return None
