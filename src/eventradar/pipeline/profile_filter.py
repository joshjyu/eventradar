"""Profile stage: decide which events belong to which profile."""

from dataclasses import dataclass
from datetime import datetime

from eventradar.classify.topic import TopicMatcher
from eventradar.config.schema import ConfigBundle, ProfileConfig
from eventradar.domain.enums import AttendanceMode
from eventradar.geo.region import Region, load_region
from eventradar.storage.repositories import EventRepository, ProfileCandidate


@dataclass(frozen=True)
class ProfileFilter:
    """Region, topic, and attendance rules for one profile."""

    profile: ProfileConfig
    region: Region
    topic: TopicMatcher

    def admits(self, event: ProfileCandidate) -> bool:
        """
        Decide membership.

        Online events need `include_online`. Located events must fall in
        the region; unlocated ones need a region-trusted source. Events
        from a `topic_always` source are on the topic; others must match
        it, with weaker evidence required when a source is topic-trusted.

        Parameters:
          event: Candidate event.
        Returns:
          True if the event belongs in the profile.
        """
        trust = self.profile.trust
        online = event.attendance_mode is AttendanceMode.ONLINE
        if online and not self.profile.include_online:
            return False
        if not online:
            if event.lat is not None and event.lon is not None:
                if not self.region.contains(event.lat, event.lon):
                    return False
            elif not event.sources & set(trust.region):
                return False
        if event.sources & set(trust.topic_always):
            return True
        trusted = bool(event.sources & set(trust.topic))
        return self.topic.matches(event.title, event.description, trusted)


def assign_profile(
    bundle: ConfigBundle,
    profile: ProfileConfig,
    events: EventRepository,
    now: datetime,
) -> tuple[int, int]:
    """
    Recompute a profile's upcoming members.

    Parameters:
      bundle: Loaded config.
      profile: Profile to populate.
      events: Event repository.
      now: Time new members joined.
    Returns:
      (added, removed) counts.
    """
    rules = ProfileFilter(
        profile=profile,
        region=load_region(bundle.regions[profile.region]),
        topic=TopicMatcher(bundle.topics[profile.topic]),
    )
    candidates = events.profile_candidates(now, bundle.profile_sources(profile))
    return events.sync_profile(
        profile.id,
        [c.event_id for c in candidates],
        [c.event_id for c in candidates if rules.admits(c)],
        now,
    )
