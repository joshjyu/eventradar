"""Eventbrite events through the official API v3, per organizer.

Eventbrite removed public event search, and its website refuses requests
from datacenter IPs, so this adapter lists the live events of configured
organizers with a personal OAuth token. The token is read from the
environment (`token_env`) and never logged or stored.
"""

import logging
import os
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from eventradar.config.schema import SourceConfig
from eventradar.config.types import HttpsUrl, IanaZone
from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.times import TimeParseError, parse_iso
from eventradar.http import HttpError
from eventradar.sources.base import ParseError, SourceContext

log = logging.getLogger(__name__)

_MAX_DESCRIPTION = 5000
_EVENT_KEYS = (
    "id",
    "name",
    "description",
    "url",
    "start",
    "end",
    "status",
    "online_event",
    "capacity",
    "venue",
    "organizer",
)


class MissingTokenError(RuntimeError):
    """The API token environment variable is not set."""


class EventbriteParams(BaseModel):
    """Parameters for an `eventbrite` source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: HttpsUrl = "https://www.eventbriteapi.com/v3"
    organizers: list[str] = Field(min_length=1)
    token_env: str = Field(
        default="EVENTBRITE_TOKEN", pattern=r"^[A-Z][A-Z0-9_]*$"
    )
    pages: int = Field(default=3, ge=1, le=20)
    default_tz: IanaZone = "UTC"
    max_events: int = Field(default=500, ge=1, le=5000)

    def token(self) -> SecretStr:
        """
        Read the API token from the environment.

        Returns:
          The token, wrapped so it cannot be printed by accident.
        """
        value = os.environ.get(self.token_env, "").strip()
        if not value:
            raise MissingTokenError(f"{self.token_env} is not set")
        return SecretStr(value)


class EventbriteSource:
    """Lists live events for each configured organizer."""

    def __init__(self, config: SourceConfig) -> None:
        """
        Bind to a configured source.

        Parameters:
          config: Source configuration with `EventbriteParams` params.
        """
        self.config = config
        self.params = EventbriteParams.model_validate(config.params)
        self._zone = ZoneInfo(self.params.default_tz)

    async def fetch(self, ctx: SourceContext) -> list[RawRecord]:
        """
        Page through each organizer's live events.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          One raw record per event.
        """
        token = self.params.token()
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"}
        found: dict[str, dict[str, Any]] = {}
        errors: list[HttpError] = []
        for organizer in self.params.organizers:
            try:
                await self._organizer_events(organizer, headers, ctx, found)
            except HttpError as exc:
                log.warning(
                    "eventbrite organizer %s failed: %s", organizer, exc
                )
                errors.append(exc)
            if len(found) >= self.params.max_events:
                break
        if not found and errors:
            raise errors[0]
        return [
            RawRecord(
                source_id=self.config.id,
                native_id=event_id,
                url=event.get("url"),
                payload=event,
                fetched_at=ctx.now,
            )
            for event_id, event in list(found.items())[: self.params.max_events]
        ]

    async def _organizer_events(
        self,
        organizer: str,
        headers: dict[str, str],
        ctx: SourceContext,
        found: dict[str, dict[str, Any]],
    ) -> None:
        """
        Collect one organizer's live events, following continuations.

        Parameters:
          organizer: Organizer id.
          headers: Request headers carrying the token.
          ctx: Run-scoped services.
          found: Accumulator keyed by event id.
        """
        continuation: str | None = None
        for _ in range(self.params.pages):
            query = {
                "status": "live",
                "order_by": "start_asc",
                "expand": "venue,organizer",
                "page_size": "50",
            }
            if continuation:
                query["continuation"] = continuation
            url = (
                f"{self.params.endpoint}/organizers/{organizer}/events/"
                f"?{urlencode(query)}"
            )
            data = (await ctx.http.get(url, headers=headers)).json()
            for event in data.get("events") or []:
                if event.get("id"):
                    found.setdefault(str(event["id"]), _slim(event))
            pagination = data.get("pagination") or {}
            continuation = pagination.get("continuation")
            if not pagination.get("has_more_items") or not continuation:
                break

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Map an API event to a draft.

        Parameters:
          raw: Record produced by `fetch`.
        Returns:
          A single draft.
        """
        event = raw.payload
        title = _text(event.get("name"))
        if not title:
            raise ParseError(f"{raw.native_id}: missing name")
        start_info = event.get("start") or {}
        end_info = event.get("end") or {}
        zone = _zone(start_info.get("timezone")) or self._zone
        try:
            start = parse_iso(start_info.get("utc"), zone)
            end = (
                parse_iso(end_info["utc"], zone)
                if end_info.get("utc")
                else None
            )
        except TimeParseError as exc:
            raise ParseError(f"{raw.native_id}: {exc}") from exc
        online = bool(event.get("online_event"))
        venue = event.get("venue") or {}
        lat, lon = (None, None) if online else _coordinates(venue)
        capacity = event.get("capacity")
        return [
            EventDraft(
                source_id=raw.source_id,
                native_id=raw.native_id,
                title=title,
                description=_text(event.get("description")),
                start_utc=start,
                end_utc=end if end and end >= start else None,
                tz=zone.key,
                venue=None if online else (venue.get("name") or None),
                address=None if online else _address(venue),
                lat=lat,
                lon=lon,
                attendance_mode=(
                    AttendanceMode.ONLINE
                    if online
                    else AttendanceMode.IN_PERSON
                ),
                status=(
                    EventStatus.CANCELLED
                    if event.get("status") == "canceled"
                    else EventStatus.SCHEDULED
                ),
                organizer=(event.get("organizer") or {}).get("name") or None,
                url=event.get("url"),
                size_signal=(
                    capacity
                    if isinstance(capacity, int) and capacity >= 0
                    else None
                ),
            )
        ]


def _slim(event: dict[str, Any]) -> dict[str, Any]:
    """
    Keep stable, useful fields and bound the description.

    Parameters:
      event: API event object.
    Returns:
      Trimmed copy.
    """
    slim = {k: event[k] for k in _EVENT_KEYS if k in event}
    description = slim.get("description")
    if isinstance(description, dict) and isinstance(
        description.get("text"), str
    ):
        slim["description"] = {"text": description["text"][:_MAX_DESCRIPTION]}
    if isinstance(slim.get("organizer"), dict):
        slim["organizer"] = {"name": slim["organizer"].get("name")}
    return slim


def _text(value: Any) -> str | None:
    """
    Read Eventbrite's `{"text": ...}` wrapper or a plain string.

    Parameters:
      value: Field value.
    Returns:
      Stripped text, or None.
    """
    if isinstance(value, dict):
        value = value.get("text")
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _zone(name: Any) -> ZoneInfo | None:
    """
    Resolve an IANA zone name from the API.

    Parameters:
      name: Zone name.
    Returns:
      Zone, or None when missing or unknown.
    """
    if not isinstance(name, str):
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _coordinates(venue: dict[str, Any]) -> tuple[float | None, float | None]:
    """
    Read venue coordinates, which the API returns as strings.

    Parameters:
      venue: Venue object.
    Returns:
      (lat, lon), or (None, None).
    """
    try:
        lat = float(venue.get("latitude") or "")
        lon = float(venue.get("longitude") or "")
    except ValueError:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def _address(venue: dict[str, Any]) -> str | None:
    """
    Format the venue address on one line.

    Parameters:
      venue: Venue object.
    Returns:
      Address, or None.
    """
    address = venue.get("address") or {}
    display = address.get("localized_address_display")
    if isinstance(display, str) and display.strip():
        return display.strip()
    parts = [
        address.get(k)
        for k in (
            "address_1",
            "address_2",
            "city",
            "region",
            "postal_code",
            "country",
        )
    ]
    kept = [str(p).strip() for p in parts if p and str(p).strip()]
    return ", ".join(kept) or None
