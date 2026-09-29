"""Enrich stage: locate events by address and fill in time zones."""

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from eventradar.config.loader import expand_env
from eventradar.config.schema import GeoSettings
from eventradar.geo.geocode.base import Geocoder, GeoPoint, Precision
from eventradar.geo.places import place_centroid
from eventradar.geo.postal import zip_centroid
from eventradar.geo.timezone import zone_at
from eventradar.http import HttpClient, HttpError
from eventradar.plugins import GEOCODER_GROUP, load_plugin
from eventradar.storage.repositories import (
    EventRepository,
    GeocodeCacheRepository,
    transaction,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichStats:
    """What the enrich stage changed."""

    located: int
    lookups: int
    zoned: int


def build_geocoder(settings: GeoSettings) -> Geocoder:
    """
    Instantiate the configured geocoder plugin.

    Parameters:
      settings: Geo settings.
    Returns:
      Geocoder instance.
    """
    provider = load_plugin(GEOCODER_GROUP, settings.geocoder.provider)
    return provider(**expand_env(settings.geocoder.options))


def address_key(address: str) -> str:
    """
    Normalize an address for caching.

    Parameters:
      address: One-line address.
    Returns:
      Lower-cased, whitespace- and comma-normalized key.
    """
    text = re.sub(r"\s*,\s*", ", ", address.strip().lower())
    return re.sub(r"\s+", " ", text)


def _offline(address: str) -> GeoPoint | None:
    """
    Locate an address without the network: its ZIP, else its city.

    Parameters:
      address: One-line address.
    Returns:
      The centroid, or None.
    """
    return zip_centroid(address) or place_centroid(address)


def _cached_precision(value: str | None) -> Precision:
    """
    Read a precision stored in the geocode cache.

    Parameters:
      value: Stored precision.
    Returns:
      The precision; unknown values count as an address match.
    """
    return value if value in ("postal", "place") else "address"


async def _locate(
    address: str,
    cache: GeocodeCacheRepository,
    geocoder: Geocoder,
    http: HttpClient,
    now: datetime,
    settings: GeoSettings,
    budget: list[int],
) -> GeoPoint | None:
    """
    Resolve one address from cache, the geocoder, or offline centroids.

    Parameters:
      address: One-line address.
      cache: Geocode cache.
      geocoder: Online geocoder.
      http: Shared HTTP client.
      now: Run time.
      settings: Geo settings.
      budget: Single-item list holding the remaining lookup budget.
    Returns:
      The point, or None.
    """
    key = address_key(address)
    cached = cache.get(key)
    retry_after = timedelta(days=settings.retry_misses_after_days)
    if cached and cached.lat is not None and cached.lon is not None:
        precision = _cached_precision(cached.precision)
        return GeoPoint(cached.lat, cached.lon, precision)
    # A recent miss skips the geocoder; offline tables may have grown.
    if cached and now - cached.looked_up_at < retry_after:
        return _offline(address)
    if budget[0] <= 0:
        return _offline(address)
    budget[0] -= 1
    try:
        point = await geocoder.geocode(address, http)
    except HttpError as exc:
        log.warning("geocoding failed, will retry next run: %s", exc)
        return _offline(address)
    point = point or _offline(address)
    as_tuple = (point.lat, point.lon, point.precision) if point else None
    cache.put(key, as_tuple, geocoder.name, now)
    return point


async def enrich(
    conn: sqlite3.Connection,
    events: EventRepository,
    geocoder: Geocoder,
    http: HttpClient,
    now: datetime,
    settings: GeoSettings,
) -> EnrichStats:
    """
    Geocode unlocated upcoming events, then derive missing time zones.

    Parameters:
      conn: State database.
      events: Event repository.
      geocoder: Online geocoder.
      http: Shared HTTP client.
      now: Run time.
      settings: Geo settings.
    Returns:
      Counts of changes.
    """
    cache = GeocodeCacheRepository(conn)
    budget = [settings.max_lookups_per_run]
    located: list[tuple[str, GeoPoint]] = []
    for event_id, address in events.missing_locations(now):
        point = await _locate(
            address, cache, geocoder, http, now, settings, budget
        )
        if point:
            located.append((event_id, point))
    with transaction(conn):
        for event_id, point in located:
            events.set_location(event_id, point.lat, point.lon, point.precision)
        zoned = 0
        for event_id, lat, lon in events.missing_zones(now):
            zone = zone_at(lat, lon)
            if zone:
                events.set_zone(event_id, zone)
                zoned += 1
    lookups = settings.max_lookups_per_run - budget[0]
    return EnrichStats(located=len(located), lookups=lookups, zoned=zoned)
