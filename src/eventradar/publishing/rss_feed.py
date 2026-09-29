"""RSS 2.0 feed of events, newest additions first."""

from collections.abc import Sequence
from datetime import datetime
from email.utils import format_datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from zoneinfo import ZoneInfo

from eventradar.config.schema import ProfileConfig
from eventradar.domain.enums import EventStatus
from eventradar.domain.models import Event
from eventradar.publishing.artifacts import Artifact, profile_key

MAX_ITEMS = 100
_MAX_DESCRIPTION = 500


def _when(event: Event) -> str:
    """
    Format the start in the event's own time zone.

    Parameters:
      event: Event.
    Returns:
      e.g. `Tue, Oct 14 2026, 6:00 PM PDT`.
    """
    local = event.start_utc.astimezone(
        ZoneInfo(event.tz) if event.tz else event.start_utc.tzinfo
    )
    hour = local.hour % 12 or 12
    return f"{local:%a, %b %d %Y}, {hour}:{local:%M %p %Z}"


def _summary(event: Event) -> str:
    """
    Build the item's plain-text description.

    Parameters:
      event: Event.
    Returns:
      Status, time, place, and a description excerpt.
    """
    lines = []
    if event.status is EventStatus.CANCELLED:
        lines.append("CANCELLED")
    lines.append(_when(event))
    place = ", ".join(p for p in (event.venue, event.address) if p)
    if place:
        lines.append(place)
    if event.description:
        lines.append(event.description[:_MAX_DESCRIPTION])
    return "\n".join(lines)


def rss_artifact(
    profile: ProfileConfig,
    events: Sequence[Event],
    generated_at: datetime,
    site_url: str,
) -> Artifact:
    """
    Render `feed.xml` with the most recently added upcoming events.

    Parameters:
      profile: Profile being published.
      events: Upcoming events.
      generated_at: Publication time.
      site_url: Homepage for the channel link.
    Returns:
      The RSS artifact.
    """
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = profile.name
    SubElement(channel, "link").text = site_url
    SubElement(channel, "description").text = f"New events: {profile.name}"
    SubElement(channel, "lastBuildDate").text = format_datetime(generated_at)
    newest = sorted(
        events, key=lambda e: (e.first_seen, e.event_id), reverse=True
    )
    for event in newest[:MAX_ITEMS]:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = event.title
        if event.url:
            SubElement(item, "link").text = event.url
        SubElement(item, "guid", isPermaLink="false").text = event.event_id
        SubElement(item, "pubDate").text = format_datetime(event.first_seen)
        SubElement(item, "description").text = _summary(event)
        for kind in sorted(k.value for k in event.kinds):
            SubElement(item, "category").text = kind
    body = tostring(rss, encoding="utf-8", xml_declaration=True) + b"\n"
    return Artifact(
        key=profile_key(profile.id, "feed.xml"),
        body=body,
        content_type="application/rss+xml; charset=utf-8",
    )
