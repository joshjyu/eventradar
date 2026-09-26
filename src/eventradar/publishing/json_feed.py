"""JSON event feeds: all upcoming events and the daily delta."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from eventradar.config.schema import ProfileConfig
from eventradar.domain.models import SCHEMA_VERSION, Event
from eventradar.publishing.artifacts import Artifact, dump_json, profile_key


def _feed(
    profile: ProfileConfig, events: Sequence[Event], generated_at: datetime
) -> dict[str, Any]:
    """
    Build the feed document shared by both outputs.

    Parameters:
      profile: Profile being published.
      events: Events in output order.
      generated_at: Publication time.
    Returns:
      JSON-ready document.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": {"id": profile.id, "name": profile.name},
        "generated_at": generated_at.isoformat(),
        "count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


def feed_artifacts(
    profile: ProfileConfig,
    upcoming: Sequence[Event],
    added: Sequence[Event],
    run_date: date,
    generated_at: datetime,
) -> list[Artifact]:
    """
    Render `events.json` and `new/{date}.json` for a profile.

    Parameters:
      profile: Profile being published.
      upcoming: Every event that has not ended.
      added: Events that joined the profile on `run_date`.
      run_date: Date the delta covers.
      generated_at: Publication time.
    Returns:
      Artifacts to write.
    """
    return [
        Artifact(
            key=profile_key(profile.id, "events.json"),
            body=dump_json(_feed(profile, upcoming, generated_at)),
        ),
        Artifact(
            key=profile_key(profile.id, f"new/{run_date.isoformat()}.json"),
            body=dump_json(_feed(profile, added, generated_at)),
            cache_control="public, max-age=86400",
        ),
    ]
