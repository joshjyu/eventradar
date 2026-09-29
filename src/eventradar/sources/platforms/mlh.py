"""Major League Hacking (MLH) member hackathons from the season listing.

Each season page (`/seasons/{year}/events`) embeds its data as Inertia page
JSON: upcoming events with UTC start and end times, a format, and the
venue's city, state, and country (no street address). robots.txt allows
these pages. A season runs from July to the next June and is named after
the year it ends, so the next season is also read once it is published.
"""

import json
import logging
import re
from datetime import UTC, date
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

from eventradar.config.schema import SourceConfig
from eventradar.config.types import HttpsUrl
from eventradar.domain.enums import AttendanceMode, EventKind
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.times import TimeParseError, parse_iso
from eventradar.http import HttpError
from eventradar.sources.base import ParseError, SourceContext

log = logging.getLogger(__name__)

_PAGE_DATA = re.compile(
    r"<script\b[^>]*\bdata-page=\"app\"[^>]*>(.*?)</script>", re.DOTALL
)
_MODES = {
    "physical": AttendanceMode.IN_PERSON,
    "digital": AttendanceMode.ONLINE,
    "hybrid_physical": AttendanceMode.MIXED,
}
# ISO codes of countries MLH lists, as addresses write them.
_COUNTRIES = {
    "US": "US",
    "CA": "Canada",
    "MX": "Mexico",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "FR": "France",
    "DE": "Germany",
    "NL": "Netherlands",
    "ES": "Spain",
    "IT": "Italy",
    "CH": "Switzerland",
    "IN": "India",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "SG": "Singapore",
    "MY": "Malaysia",
    "PH": "Philippines",
    "ID": "Indonesia",
    "AU": "Australia",
    "NZ": "New Zealand",
    "NG": "Nigeria",
    "KE": "Kenya",
    "GH": "Ghana",
    "ZA": "South Africa",
    "BR": "Brazil",
}
# Fields kept in raw records; image URLs change without the event changing.
_KEYS = (
    "id",
    "name",
    "startsAt",
    "endsAt",
    "url",
    "location",
    "formatType",
    "websiteUrl",
    "venueAddress",
)


class MlhParams(BaseModel):
    """Parameters for an `mlh` source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: HttpsUrl = "https://www.mlh.com"
    seasons_ahead: int = Field(default=1, ge=0, le=2)
    include_online: bool = False
    max_events: int = Field(default=500, ge=1, le=2000)


def season_of(day: date) -> int:
    """
    Name the MLH season a day falls in.

    Parameters:
      day: Calendar day.
    Returns:
      The season's year, e.g. 2027 for 2026-09-29.
    """
    return day.year + 1 if day.month >= 7 else day.year


class MlhSource:
    """Reads the current (and next) season's upcoming hackathons."""

    def __init__(self, config: SourceConfig) -> None:
        """
        Bind to a configured source.

        Parameters:
          config: Source configuration with `MlhParams` params.
        """
        self.config = config
        self.params = MlhParams.model_validate(config.params)

    async def fetch(self, ctx: SourceContext) -> list[RawRecord]:
        """
        Read each season page and keep its upcoming events.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          One raw record per event.
        """
        found: dict[str, dict[str, Any]] = {}
        current = season_of(ctx.now.date())
        for offset in range(self.params.seasons_ahead + 1):
            url = f"{self.params.base_url}/seasons/{current + offset}/events"
            try:
                response = await ctx.http.get(url)
            except HttpError as exc:
                # Later seasons 404 until MLH publishes them.
                if offset and exc.status == 404:
                    continue
                raise
            for event in _upcoming(response.text, url):
                if self._wanted(event):
                    found.setdefault(str(event["id"]), event)
        events = list(found.values())[: self.params.max_events]
        return [
            RawRecord(
                source_id=self.config.id,
                native_id=str(event["id"]),
                url=self._url(event),
                payload=event,
                fetched_at=ctx.now,
            )
            for event in events
        ]

    def _wanted(self, event: dict[str, Any]) -> bool:
        """
        Drop online-only events unless configured to keep them.

        Parameters:
          event: Listing event.
        Returns:
          True if the event should be stored.
        """
        online = event.get("formatType") == "digital"
        return self.params.include_online or not online

    def _url(self, event: dict[str, Any]) -> str | None:
        """
        Link to the hackathon's own site, else its MLH page.

        Parameters:
          event: Listing event.
        Returns:
          Absolute URL, or None.
        """
        website = event.get("websiteUrl")
        if isinstance(website, str) and website.startswith("https://"):
            return website
        path = event.get("url")
        return urljoin(self.params.base_url, path) if path else None

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Map a listing event to a draft.

        Parameters:
          raw: Record produced by `fetch`.
        Returns:
          A single draft.
        """
        event = raw.payload
        title = str(event.get("name") or "").strip()
        if not title:
            raise ParseError(f"{raw.native_id}: missing name")
        try:
            # MLH times are UTC ("...Z").
            start = parse_iso(event.get("startsAt"), UTC)
            end_raw = event.get("endsAt")
            end = parse_iso(end_raw, UTC) if end_raw else None
        except TimeParseError as exc:
            raise ParseError(f"{raw.native_id}: {exc}") from exc
        mode = _MODES.get(event.get("formatType") or "", AttendanceMode.UNKNOWN)
        online = mode is AttendanceMode.ONLINE
        return [
            EventDraft(
                source_id=raw.source_id,
                native_id=raw.native_id,
                title=title,
                start_utc=start,
                end_utc=end if end and end >= start else None,
                address=None if online else _address(event),
                attendance_mode=mode,
                url=raw.url,
                kinds=frozenset({EventKind.HACKATHON}),
            )
        ]


def _upcoming(html: str, url: str) -> list[dict[str, Any]]:
    """
    Extract upcoming events from a season page's embedded page data.

    Parameters:
      html: Season page.
      url: Page URL, for error messages.
    Returns:
      Events with an id, trimmed to the fields parsing uses.
    """
    match = _PAGE_DATA.search(html)
    if not match:
        raise ParseError(f"{url}: no page data")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ParseError(f"{url}: page data is not JSON: {exc}") from exc
    events = (data.get("props") or {}).get("upcomingEvents")
    if not isinstance(events, list):
        raise ParseError(f"{url}: no upcomingEvents in page data")
    return [
        {k: e.get(k) for k in _KEYS}
        for e in events
        if isinstance(e, dict) and e.get("id")
    ]


def _address(event: dict[str, Any]) -> str | None:
    """
    Format the venue's city, state, and country on one line.

    Country codes are written out, since many (CA, IN, GA) are also U.S.
    state abbreviations; an unknown code is left out rather than risk
    "Toronto, Ontario, CA" reading as a California address.

    Parameters:
      event: Listing event.
    Returns:
      Address, or the listing's display location, or None.
    """
    venue = event.get("venueAddress") or {}
    code = str(venue.get("country") or "").strip().upper()
    parts = [venue.get("city"), venue.get("state"), _COUNTRIES.get(code)]
    kept = [str(p).strip() for p in parts if p and str(p).strip()]
    location = str(event.get("location") or "").strip()
    return ", ".join(kept) or location or None
