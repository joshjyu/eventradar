"""Time zone lookup from coordinates, offline."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzfpy


def zone_at(lat: float, lon: float) -> str | None:
    """
    Name the IANA time zone at a point.

    Parameters:
      lat: Latitude.
      lon: Longitude.
    Returns:
      Zone name, or None over open water or when unknown locally.
    """
    name = tzfpy.get_tz(lon, lat)
    if not name or name.startswith("Etc/"):
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name
