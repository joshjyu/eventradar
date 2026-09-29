"""Meetup events via the public GraphQL `eventSearch` query.

Searches combine an area (lat, lon, radius in miles) with a keyword and an
optional topic category; the keyword alone is a loose semantic match, so the
category is what keeps results on topic. The endpoint answers public
queries without authentication, and robots.txt on the API host allows
`/gql*`. Results are deduplicated by event id.
"""

import logging
from itertools import product
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from eventradar.config.schema import SourceConfig
from eventradar.config.types import HttpsUrl, IanaZone
from eventradar.domain.enums import AttendanceMode, EventStatus
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.times import TimeParseError, parse_iso, zone_for_offset
from eventradar.http import HttpError
from eventradar.sources.base import ParseError, SourceContext

log = logging.getLogger(__name__)

_MAX_DESCRIPTION = 5000
_CANCELLED = frozenset({"CANCELLED", "CANCELLED_PERM", "AUTOSCHED_CANCELLED"})
_MODES = {
    "PHYSICAL": AttendanceMode.IN_PERSON,
    "ONLINE": AttendanceMode.ONLINE,
    "HYBRID": AttendanceMode.MIXED,
}
SEARCH_QUERY = """
query($filter: EventSearchFilter!, $first: Int!, $after: String) {
  eventSearch(filter: $filter, first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id title description dateTime endTime eventType status eventUrl
        rsvps { totalCount }
        venue { name address city state postalCode country lat lon }
        group { name urlname timezone }
      }
    }
  }
}
"""


class MeetupArea(BaseModel):
    """A circle to search."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_mi: float = Field(gt=0, le=100)


class MeetupParams(BaseModel):
    """Parameters for a `meetup` source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: HttpsUrl = "https://api.meetup.com/gql-ext"
    areas: list[MeetupArea] = Field(min_length=1)
    queries: list[str] = Field(min_length=1)
    topic_category_id: str | None = None
    pages: int = Field(default=2, ge=1, le=10)
    page_size: int = Field(default=50, ge=1, le=50)
    include_online: bool = False
    default_tz: IanaZone = "UTC"
    max_events: int = Field(default=500, ge=1, le=5000)


class MeetupSource:
    """Runs every (area, keyword) search and keeps one record per event."""

    def __init__(self, config: SourceConfig) -> None:
        """
        Bind to a configured source.

        Parameters:
          config: Source configuration with `MeetupParams` params.
        """
        self.config = config
        self.params = MeetupParams.model_validate(config.params)
        self._zone = ZoneInfo(self.params.default_tz)

    async def fetch(self, ctx: SourceContext) -> list[RawRecord]:
        """
        Run the searches, paging up to the configured limit.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          One raw record per distinct event.
        """
        found: dict[str, dict[str, Any]] = {}
        errors: list[Exception] = []
        pairs = product(self.params.areas, self.params.queries)
        for area, query in pairs:
            try:
                await self._search(area, query, ctx, found)
            except (HttpError, ParseError) as exc:
                log.warning("meetup %s/%s failed: %s", area.label, query, exc)
                errors.append(exc)
            if len(found) >= self.params.max_events:
                break
        if not found and errors:
            raise errors[0]
        nodes = list(found.values())[: self.params.max_events]
        return [
            RawRecord(
                source_id=self.config.id,
                native_id=str(node["id"]),
                url=node.get("eventUrl"),
                payload=node,
                fetched_at=ctx.now,
            )
            for node in nodes
        ]

    async def _search(
        self,
        area: MeetupArea,
        query: str,
        ctx: SourceContext,
        found: dict[str, dict[str, Any]],
    ) -> None:
        """
        Page through one search, adding wanted events to `found`.

        Parameters:
          area: Area to search.
          query: Keyword.
          ctx: Run-scoped services.
          found: Accumulator keyed by event id.
        """
        search: dict[str, Any] = {
            "lat": area.lat,
            "lon": area.lon,
            "radius": area.radius_mi,
            "query": query,
        }
        if self.params.topic_category_id:
            search["topicCategoryId"] = self.params.topic_category_id
        cursor: str | None = None
        for _ in range(self.params.pages):
            body = {
                "query": SEARCH_QUERY,
                "variables": {
                    "filter": search,
                    "first": self.params.page_size,
                    "after": cursor,
                },
            }
            response = await ctx.http.post_json(self.params.endpoint, body)
            data = response.json()
            if data.get("errors"):
                raise ParseError(f"graphql error: {data['errors'][0]}")
            result = (data.get("data") or {}).get("eventSearch") or {}
            for edge in result.get("edges") or []:
                node = _slim(edge.get("node") or {})
                if node and self._wanted(node):
                    found.setdefault(str(node["id"]), node)
            page = result.get("pageInfo") or {}
            cursor = page.get("endCursor")
            if not page.get("hasNextPage") or not cursor:
                break

    def _wanted(self, node: dict[str, Any]) -> bool:
        """
        Drop online-only events unless configured to keep them.

        Parameters:
          node: Event node.
        Returns:
          True if the event should be stored.
        """
        return self.params.include_online or node.get("eventType") != "ONLINE"

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Map a Meetup event node to a draft.

        Parameters:
          raw: Record produced by `fetch`.
        Returns:
          A single draft.
        """
        node = raw.payload
        title = (node.get("title") or "").strip()
        if not title:
            raise ParseError(f"{raw.native_id}: missing title")
        zone = _group_zone(node) or self._zone
        try:
            start = parse_iso(node.get("dateTime"), zone)
            end_raw = node.get("endTime")
            end = parse_iso(end_raw, zone) if end_raw else None
        except TimeParseError as exc:
            raise ParseError(f"{raw.native_id}: {exc}") from exc
        venue = node.get("venue") or {}
        mode = _MODES.get(node.get("eventType") or "", AttendanceMode.UNKNOWN)
        lat, lon = _coordinates(venue, mode)
        group = node.get("group") or {}
        rsvps = (node.get("rsvps") or {}).get("totalCount")
        return [
            EventDraft(
                source_id=raw.source_id,
                native_id=raw.native_id,
                title=title,
                description=(node.get("description") or "").strip() or None,
                start_utc=start,
                end_utc=end if end and end >= start else None,
                tz=_group_zone_name(node)
                or zone_for_offset(node["dateTime"], start, self._zone),
                venue=(venue.get("name") or "").strip() or None,
                address=_address(venue),
                lat=lat,
                lon=lon,
                attendance_mode=mode,
                status=(
                    EventStatus.CANCELLED
                    if node.get("status") in _CANCELLED
                    else EventStatus.SCHEDULED
                ),
                organizer=(group.get("name") or "").strip() or None,
                url=node.get("eventUrl"),
                size_signal=rsvps if isinstance(rsvps, int) else None,
            )
        ]


def _slim(node: dict[str, Any]) -> dict[str, Any]:
    """
    Keep only the fields used for parsing, with a bounded description.

    Parameters:
      node: Raw GraphQL event node.
    Returns:
      The trimmed node, or an empty dict when it has no id.
    """
    if not node.get("id"):
        return {}
    slim = dict(node)
    description = slim.get("description")
    if isinstance(description, str):
        slim["description"] = description[:_MAX_DESCRIPTION]
    return slim


def _group_zone_name(node: dict[str, Any]) -> str | None:
    """
    Read the group's IANA time zone.

    Parameters:
      node: Event node.
    Returns:
      Zone name, or None when absent or unknown.
    """
    name = (node.get("group") or {}).get("timezone")
    if not isinstance(name, str):
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def _group_zone(node: dict[str, Any]) -> ZoneInfo | None:
    """
    Resolve the group's time zone for offset-less times.

    Parameters:
      node: Event node.
    Returns:
      Zone, or None.
    """
    name = _group_zone_name(node)
    return ZoneInfo(name) if name else None


def _coordinates(
    venue: dict[str, Any], mode: AttendanceMode
) -> tuple[float | None, float | None]:
    """
    Read venue coordinates; online events carry a placeholder, so skip them.

    Parameters:
      venue: Venue node.
      mode: Attendance mode.
    Returns:
      (lat, lon), or (None, None).
    """
    if mode is AttendanceMode.ONLINE:
        return None, None
    lat, lon = venue.get("lat"), venue.get("lon")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return float(lat), float(lon)


def _address(venue: dict[str, Any]) -> str | None:
    """
    Format a venue address on one line.

    Parameters:
      venue: Venue node.
    Returns:
      Address, or None.
    """
    parts = [
        venue.get(k)
        for k in ("address", "city", "state", "postalCode", "country")
    ]
    kept = [str(p).strip() for p in parts if p and str(p).strip()]
    return ", ".join(kept) or None
