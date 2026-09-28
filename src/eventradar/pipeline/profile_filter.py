"""Profile stage: decide which events belong to which profile."""

from datetime import datetime

from eventradar.config.schema import ConfigBundle, ProfileConfig
from eventradar.storage.repositories import EventRepository


def assign_profile(
    bundle: ConfigBundle,
    profile: ProfileConfig,
    events: EventRepository,
    now: datetime,
) -> int:
    """
    Link every event from the profile's sources to the profile.

    Region and topic filtering are applied here once implemented.

    Parameters:
      bundle: Loaded config.
      profile: Profile to populate.
      events: Event repository.
      now: Time new members joined.
    Returns:
      Number of events newly linked.
    """
    source_ids = sorted(bundle.profile_sources(profile))
    return sum(
        events.link_profile(event_id, profile.id, now)
        for event_id in events.event_ids_for_sources(source_ids)
    )
