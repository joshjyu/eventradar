"""Generic iCalendar (RFC 5545) feed adapter."""

import re
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar
from icalendar import Event as VEvent
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator

from eventradar.config.schema import SourceConfig
from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.ids import content_hash
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.sources.base import ParseError, SourceContext

# Properties some providers regenerate on every download (Luma stamps
# SEQUENCE with a counter); hashing them makes every record look changed.
_VOLATILE = ("DTSTAMP", "SEQUENCE")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_ONLINE_HOSTS = ("zoom.us", "meet.google.com", "teams.microsoft.com")
_MAX_DESCRIPTION = 5000


class IcalParams(BaseModel):
    """Parameters for an `ical` source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: HttpUrl
    default_tz: str = "UTC"

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        """
        Require TLS for every feed.

        Parameters:
          value: Configured URL.
        Returns:
          The URL.
        """
        if value.scheme != "https":
            raise ValueError("feed url must use https")
        return value

    @field_validator("default_tz")
    @classmethod
    def _known_tz(cls, value: str) -> str:
        """
        Require an IANA time zone name.

        Parameters:
          value: Configured zone name.
        Returns:
          The zone name.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone: {value}") from exc
        return value


class IcalSource:
    """
    Reads one VCALENDAR feed; each VEVENT becomes one raw record.

    ORGANIZER is not used: calendar platforms often fill it with a host's
    personal name, and only organizations are published.
    """

    def __init__(self, config: SourceConfig) -> None:
        """
        Bind to a configured source.

        Parameters:
          config: Source configuration with `IcalParams` params.
        """
        self.config = config
        self.params = IcalParams.model_validate(config.params)

    async def fetch(self, ctx: SourceContext) -> list[RawRecord]:
        """
        Download the feed and split it into per-event records.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          One raw record per VEVENT.
        """
        response = await ctx.http.get(str(self.params.url))
        return self.split(response.content, ctx.now)

    def split(self, body: bytes, fetched_at: datetime) -> list[RawRecord]:
        """
        Split a VCALENDAR document into per-event raw records.

        Parameters:
          body: Raw feed bytes.
          fetched_at: Download time.
        Returns:
          One raw record per VEVENT.
        """
        cal = Calendar.from_ical(body)
        vtimezones = [tz.to_ical().decode() for tz in cal.walk("VTIMEZONE")]
        cal_tz = _text(cal.get("X-WR-TIMEZONE"))
        records = []
        for vevent in cal.events:
            for prop in _VOLATILE:
                vevent.pop(prop, None)
            text = vevent.to_ical().decode()
            payload: dict[str, Any] = {
                "vevent": text,
                "vtimezones": vtimezones,
                "calendar_tz": cal_tz,
            }
            records.append(
                RawRecord(
                    source_id=self.config.id,
                    native_id=_native_id(vevent, text),
                    url=str(self.params.url),
                    payload=payload,
                    fetched_at=fetched_at,
                )
            )
        return records

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Turn one stored VEVENT into a draft.

        Recurring events yield only their first occurrence.

        Parameters:
          raw: Record produced by `fetch`.
        Returns:
          A single draft.
        """
        vevent = _load_vevent(raw.payload)
        zone = _zone(raw.payload.get("calendar_tz"), self.params.default_tz)
        title = _text(vevent.get("SUMMARY"))
        start_prop = vevent.get("DTSTART")
        if not title or start_prop is None:
            raise ParseError(f"{raw.native_id}: missing SUMMARY or DTSTART")
        start = _to_utc(start_prop.dt, zone)
        end = _end(vevent, start, zone)
        location = _text(vevent.get("LOCATION"))
        location_url = location if location and _is_url(location) else None
        description = _text(vevent.get("DESCRIPTION"))
        lat, lon = _geo(vevent)
        return [
            EventDraft(
                source_id=raw.source_id,
                native_id=raw.native_id,
                title=title,
                description=(description or "")[:_MAX_DESCRIPTION] or None,
                start_utc=start,
                end_utc=end,
                tz=_tz_name(start_prop.dt, zone),
                address=None if location_url else location,
                lat=lat,
                lon=lon,
                attendance_mode=_mode(location, location_url, lat),
                status=_status(vevent),
                url=(
                    _text(vevent.get("URL"))
                    or location_url
                    or _first_url(description)
                ),
            )
        ]


def _native_id(vevent: VEvent, text: str) -> str:
    """
    Derive a stable id for a VEVENT.

    Parameters:
      vevent: Parsed event.
      text: Serialized event, used when UID is missing.
    Returns:
      UID, suffixed with RECURRENCE-ID for overridden instances.
    """
    uid = _text(vevent.get("UID")) or f"sha256:{content_hash({'v': text})}"
    rid = vevent.get("RECURRENCE-ID")
    return f"{uid}#{rid.dt.isoformat()}" if rid is not None else uid


def _load_vevent(payload: dict[str, Any]) -> VEvent:
    """
    Rebuild a VEVENT with its time zone definitions.

    Parameters:
      payload: Stored raw payload.
    Returns:
      The parsed VEVENT.
    """
    body = "".join(
        [
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n",
            *payload.get("vtimezones", []),
            payload["vevent"],
            "END:VCALENDAR\r\n",
        ]
    )
    events = Calendar.from_ical(body).events
    if not events:
        raise ParseError("payload has no VEVENT")
    return events[0]


def _zone(calendar_tz: str | None, default_tz: str) -> tzinfo:
    """
    Pick the zone used for floating times and all-day dates.

    Parameters:
      calendar_tz: X-WR-TIMEZONE from the feed, if any.
      default_tz: Configured fallback zone.
    Returns:
      A tzinfo.
    """
    if calendar_tz:
        with suppress(ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(calendar_tz)
    return ZoneInfo(default_tz)


def _to_utc(value: date | datetime, zone: tzinfo) -> datetime:
    """
    Convert an iCal date or datetime to aware UTC.

    Parameters:
      value: DTSTART/DTEND value; floating or date-only values use `zone`.
      zone: Fallback zone.
    Returns:
      Aware UTC datetime.
    """
    if not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC)


def _end(vevent: VEvent, start: datetime, zone: tzinfo) -> datetime | None:
    """
    Resolve the end from DTEND or DURATION.

    Parameters:
      vevent: Parsed event.
      start: Start in UTC.
      zone: Fallback zone.
    Returns:
      End in UTC, or None if unspecified or inconsistent.
    """
    if (prop := vevent.get("DTEND")) is not None:
        end = _to_utc(prop.dt, zone)
    elif (prop := vevent.get("DURATION")) is not None and isinstance(
        prop.dt, timedelta
    ):
        end = start + prop.dt
    else:
        return None
    return end if end >= start else None


def _tz_name(value: date | datetime, zone: tzinfo) -> str | None:
    """
    Name the IANA zone an event is local to.

    UTC-stamped times carry no locality, so they use the fallback zone.

    Parameters:
      value: DTSTART value.
      zone: Fallback zone.
    Returns:
      IANA name, or None when the zone is not an IANA zone.
    """
    tz = value.tzinfo if isinstance(value, datetime) else None
    if not isinstance(tz, ZoneInfo) or tz.key == "UTC":
        tz = zone
    return tz.key if isinstance(tz, ZoneInfo) else None


def _geo(vevent: VEvent) -> tuple[float | None, float | None]:
    """
    Read the GEO property.

    Parameters:
      vevent: Parsed event.
    Returns:
      (lat, lon), or (None, None) when absent or out of range.
    """
    geo = vevent.get("GEO")
    if geo is None:
        return None, None
    lat, lon = float(geo.latitude), float(geo.longitude)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def _mode(
    location: str | None, location_url: str | None, lat: float | None
) -> AttendanceMode:
    """
    Infer attendance mode from location data.

    Parameters:
      location: LOCATION text.
      location_url: LOCATION when it is a URL.
      lat: Latitude from GEO.
    Returns:
      Attendance mode.
    """
    if location_url and any(h in location_url for h in _ONLINE_HOSTS):
        return AttendanceMode.ONLINE
    if lat is not None or (location and not location_url):
        return AttendanceMode.IN_PERSON
    return AttendanceMode.UNKNOWN


def _status(vevent: VEvent) -> EventStatus:
    """
    Map iCal STATUS to the domain status.

    Parameters:
      vevent: Parsed event.
    Returns:
      Event status.
    """
    value = (_text(vevent.get("STATUS")) or "").upper()
    return (
        EventStatus.CANCELLED if value == "CANCELLED" else EventStatus.SCHEDULED
    )


def _text(value: object) -> str | None:
    """
    Normalize an iCal text value.

    Parameters:
      value: Property value or None.
    Returns:
      Stripped string, or None when empty.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_url(value: str) -> bool:
    """
    Check whether a string is a bare http(s) URL.

    Parameters:
      value: Candidate string.
    Returns:
      True if the whole value is a URL.
    """
    return _URL.fullmatch(value) is not None


def _first_url(text: str | None) -> str | None:
    """
    Find the first URL in free text.

    Parameters:
      text: Free text, e.g. DESCRIPTION.
    Returns:
      The URL, or None.
    """
    match = _URL.search(text or "")
    return match.group(0).rstrip(".,;") if match else None
