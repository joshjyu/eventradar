"""Parsing ISO 8601 timestamps and attributing IANA zones."""

from datetime import UTC, date, datetime, time, tzinfo
from typing import Any
from zoneinfo import ZoneInfo


class TimeParseError(ValueError):
    """A timestamp could not be parsed."""


def parse_iso(value: Any, zone: tzinfo) -> datetime:
    """
    Parse an ISO 8601 date or datetime into aware UTC.

    Date-only and offset-less values are local to `zone`.

    Parameters:
      value: Timestamp string.
      zone: Fallback zone.
    Returns:
      Aware UTC datetime.
    """
    if not isinstance(value, str) or not value.strip():
        raise TimeParseError(f"invalid date: {value!r}")
    text = value.strip()
    try:
        parsed = (
            datetime.combine(date.fromisoformat(text), time.min)
            if len(text) == 10
            else datetime.fromisoformat(text)
        )
    except ValueError as exc:
        raise TimeParseError(f"invalid date: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(UTC)


def zone_for_offset(raw: str, start: datetime, zone: ZoneInfo) -> str | None:
    """
    Attribute an IANA zone when the published offset agrees with it.

    Parameters:
      raw: Original timestamp string.
      start: Parsed instant in UTC.
      zone: Candidate zone, usually the source's default.
    Returns:
      The zone name, or None when the offset points elsewhere.
    """
    text = raw.strip()
    if len(text) <= 10:
        return zone.key
    offset = datetime.fromisoformat(text).utcoffset()
    if offset is None:
        return zone.key
    matches = start.astimezone(zone).utcoffset() == offset
    return zone.key if matches else None
