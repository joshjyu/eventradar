"""iCalendar feed for calendar apps."""

from collections.abc import Sequence
from datetime import datetime

from icalendar import Calendar
from icalendar import Event as VEvent

from eventradar.config.schema import ProfileConfig
from eventradar.domain.enums import EventStatus
from eventradar.domain.models import Event
from eventradar.publishing.artifacts import Artifact, profile_key

_MAX_DESCRIPTION = 1000


def _vevent(event: Event, generated_at: datetime) -> VEvent:
    """
    Render one event.

    Parameters:
      event: Event to render.
      generated_at: Publication time, used as DTSTAMP.
    Returns:
      The VEVENT component.
    """
    vevent = VEvent()
    vevent.add("uid", f"{event.event_id}@eventradar")
    vevent.add("dtstamp", generated_at)
    vevent.add("dtstart", event.start_utc)
    if event.end_utc:
        vevent.add("dtend", event.end_utc)
    vevent.add("summary", event.title)
    location = ", ".join(p for p in (event.venue, event.address) if p)
    if location:
        vevent.add("location", location)
    if event.lat is not None and event.lon is not None:
        vevent.add("geo", (event.lat, event.lon))
    if event.url:
        vevent.add("url", event.url)
    body = (event.description or "")[:_MAX_DESCRIPTION]
    details = "\n\n".join(p for p in (body, event.url) if p)
    if details:
        vevent.add("description", details)
    cancelled = event.status is EventStatus.CANCELLED
    vevent.add("status", "CANCELLED" if cancelled else "CONFIRMED")
    if event.kinds:
        vevent.add("categories", sorted(k.value for k in event.kinds))
    return vevent


def ical_artifact(
    profile: ProfileConfig, events: Sequence[Event], generated_at: datetime
) -> Artifact:
    """
    Render `events.ics` with every upcoming event.

    Parameters:
      profile: Profile being published.
      events: Events in output order.
      generated_at: Publication time.
    Returns:
      The calendar artifact.
    """
    calendar = Calendar()
    calendar.add("prodid", "-//eventradar//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", profile.name)
    calendar.add("x-published-ttl", "PT12H")
    for event in events:
        calendar.add_component(_vevent(event, generated_at))
    return Artifact(
        key=profile_key(profile.id, "events.ics"),
        body=calendar.to_ical(),
        content_type="text/calendar; charset=utf-8",
    )
