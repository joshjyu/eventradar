"""Map schema.org Event nodes to event draft fields."""

import html
import re
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.times import (
    TimeParseError,
    parse_iso,
    zone_for_offset,
)
from eventradar.schemaorg.extract import types_of

_MAX_DESCRIPTION = 5000
_TAG = re.compile(r"<[^>]+>")
_EVENT_KEYS = frozenset(
    {
        "@id",
        "@type",
        "description",
        "endDate",
        "eventAttendanceMode",
        "eventStatus",
        "location",
        "maximumAttendeeCapacity",
        "name",
        "organizer",
        "previousStartDate",
        "startDate",
        "url",
    }
)
_LOCATION_KEYS = frozenset(
    {"@type", "address", "geo", "latitude", "longitude", "name", "url"}
)
_ORGANIZER_KEYS = frozenset({"@type", "name", "url"})
_MODES = {
    "OfflineEventAttendanceMode": AttendanceMode.IN_PERSON,
    "OnlineEventAttendanceMode": AttendanceMode.ONLINE,
    "MixedEventAttendanceMode": AttendanceMode.MIXED,
}
_STATUSES = {
    "EventCancelled": EventStatus.CANCELLED,
    "EventPostponed": EventStatus.POSTPONED,
}


class EventMappingError(ValueError):
    """An event node lacks required fields or has unusable values."""


def _pick(node: Any, keys: frozenset[str]) -> Any:
    """
    Keep whitelisted keys of a node or each node in a list.

    Parameters:
      node: Object, list of objects, or scalar.
      keys: Keys to keep.
    Returns:
      Filtered copy; scalars unchanged.
    """
    if isinstance(node, list):
        return [_pick(n, keys) for n in node]
    if isinstance(node, dict):
        return {k: v for k, v in node.items() if k in keys}
    return node


def slim_event(node: dict[str, Any]) -> dict[str, Any]:
    """
    Reduce an event node to stable, useful fields.

    Volatile data (images, prices, availability) is dropped so it does not
    make stored records look changed.

    Parameters:
      node: schema.org Event node.
    Returns:
      Filtered copy.
    """
    slim = _pick(node, _EVENT_KEYS)
    if "location" in slim:
        slim["location"] = _pick(slim["location"], _LOCATION_KEYS)
    if "organizer" in slim:
        slim["organizer"] = _pick(slim["organizer"], _ORGANIZER_KEYS)
    return slim


def _as_list(value: Any) -> list[Any]:
    """
    Wrap scalars and objects in a list.

    Parameters:
      value: Any JSON value.
    Returns:
      A list (empty for None).
    """
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value: Any) -> str | None:
    """
    Clean a text value: unescape entities, strip tags and whitespace.

    Parameters:
      value: JSON value, usually a string.
    Returns:
      Clean text, or None when empty or not text.
    """
    if not isinstance(value, str):
        return None
    cleaned = _TAG.sub(" ", html.unescape(value))
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned or None


def _suffix(value: Any) -> str:
    """
    Take the last path segment of a schema.org enum URL.

    Parameters:
      value: e.g. `https://schema.org/EventScheduled`.
    Returns:
      e.g. `EventScheduled`, or an empty string.
    """
    return str(value).rsplit("/", 1)[-1] if value else ""


def _address(value: Any) -> str | None:
    """
    Format a PostalAddress or plain string.

    Parameters:
      value: `address` value.
    Returns:
      Single-line address.
    """
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return None
    parts = [
        _text(value.get(k))
        for k in (
            "streetAddress",
            "addressLocality",
            "addressRegion",
            "postalCode",
            "addressCountry",
        )
    ]
    # Some publishers put the full address in streetAddress; skip parts
    # already present as whole words.
    kept: list[str] = []
    for part in parts:
        pattern = rf"\b{re.escape(part)}\b" if part else ""
        if part and not re.search(pattern, ", ".join(kept), re.IGNORECASE):
            kept.append(part)
    return ", ".join(kept) or None


def _coordinate(place: dict[str, Any], key: str) -> float | None:
    """
    Read latitude or longitude from `geo` or the place itself.

    Parameters:
      place: Place node.
      key: `latitude` or `longitude`.
    Returns:
      The coordinate, or None.
    """
    for holder in (place.get("geo"), place):
        if isinstance(holder, dict) and holder.get(key) is not None:
            try:
                return float(holder[key])
            except (TypeError, ValueError):
                return None
    return None


def _location(node: dict[str, Any]) -> dict[str, Any]:
    """
    Extract venue, address, coordinates, and virtual flag.

    Parameters:
      node: Event node.
    Returns:
      Keys `venue`, `address`, `lat`, `lon`, `physical`, `virtual`.
    """
    out: dict[str, Any] = {
        "venue": None,
        "address": None,
        "lat": None,
        "lon": None,
        "physical": False,
        "virtual": False,
    }
    for loc in _as_list(node.get("location")):
        if isinstance(loc, str):
            out["address"] = out["address"] or _text(loc)
            out["physical"] = True
            continue
        if not isinstance(loc, dict):
            continue
        if "VirtualLocation" in types_of(loc):
            out["virtual"] = True
            continue
        out["physical"] = True
        out["venue"] = out["venue"] or _text(loc.get("name"))
        out["address"] = out["address"] or _address(loc.get("address"))
        lat, lon = _coordinate(loc, "latitude"), _coordinate(loc, "longitude")
        valid = (
            lat is not None
            and lon is not None
            and -90 <= lat <= 90
            and -180 <= lon <= 180
        )
        if out["lat"] is None and valid:
            out["lat"], out["lon"] = lat, lon
    return out


def _mode(node: dict[str, Any], loc: dict[str, Any]) -> AttendanceMode:
    """
    Read or infer the attendance mode.

    Parameters:
      node: Event node.
      loc: Output of `_location`.
    Returns:
      Attendance mode.
    """
    declared = _MODES.get(_suffix(node.get("eventAttendanceMode")))
    if declared:
        return declared
    if loc["physical"] and loc["virtual"]:
        return AttendanceMode.MIXED
    if loc["virtual"]:
        return AttendanceMode.ONLINE
    if loc["physical"]:
        return AttendanceMode.IN_PERSON
    return AttendanceMode.UNKNOWN


def _organizer(node: dict[str, Any]) -> str | None:
    """
    Name the first organizing organization; individuals are not stored.

    Parameters:
      node: Event node.
    Returns:
      Organization name, or None.
    """
    for org in _as_list(node.get("organizer")):
        if isinstance(org, dict) and "Organization" in types_of(org):
            name = _text(org.get("name"))
            if name:
                return name
    return None


def _capacity(node: dict[str, Any]) -> int | None:
    """
    Read `maximumAttendeeCapacity` as a size signal.

    Parameters:
      node: Event node.
    Returns:
      Non-negative capacity, or None.
    """
    value = node.get("maximumAttendeeCapacity")
    if not isinstance(value, int | str):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def _day(value: Any) -> Any:
    """
    Keep only the calendar date of a timestamp string.

    Parameters:
      value: `startDate` or `endDate` value.
    Returns:
      `YYYY-MM-DD` for strings, else the value unchanged.
    """
    return value.strip()[:10] if isinstance(value, str) else value


def event_fields(
    node: dict[str, Any],
    page_url: str,
    zone: ZoneInfo,
    dates_only: bool = False,
) -> dict[str, Any]:
    """
    Map an event node to `EventDraft` keyword arguments.

    Parameters:
      node: schema.org Event node.
      page_url: Page the node came from, for relative URLs.
      zone: Zone for offset-less times; attributed when offsets agree.
      dates_only: Read start and end as local calendar days, for
        publishers that stamp dates as midnight UTC.
    Returns:
      Fields for `EventDraft`, excluding `source_id` and `native_id`.
    """
    title = _text(node.get("name"))
    raw_start = node.get("startDate")
    raw_end = node.get("endDate")
    if dates_only:
        raw_start, raw_end = _day(raw_start), _day(raw_end)
    if not title:
        raise EventMappingError("missing name")
    try:
        start = parse_iso(raw_start, zone)
        end = parse_iso(raw_end, zone) if raw_end else None
    except TimeParseError as exc:
        raise EventMappingError(str(exc)) from exc
    # A one-day event's end date equals its start date; that says nothing
    # about when it ends.
    if end is not None and (end < start or (dates_only and end == start)):
        end = None
    loc = _location(node)
    url = node.get("url")
    description = _text(node.get("description"))
    return {
        "title": title,
        "description": description[:_MAX_DESCRIPTION] if description else None,
        "start_utc": start,
        "end_utc": end,
        "tz": zone_for_offset(str(raw_start), start, zone),
        "venue": loc["venue"],
        "address": loc["address"],
        "lat": loc["lat"],
        "lon": loc["lon"],
        "attendance_mode": _mode(node, loc),
        "status": _STATUSES.get(
            _suffix(node.get("eventStatus")), EventStatus.SCHEDULED
        ),
        "organizer": _organizer(node),
        "url": urljoin(page_url, url) if isinstance(url, str) else page_url,
        "size_signal": _capacity(node),
    }
