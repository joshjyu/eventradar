"""Tests for profile membership rules."""

import pytest

from eventradar.classify.topic import TopicMatcher
from eventradar.config.schema import ProfileConfig, TopicConfig
from eventradar.domain.enums import AttendanceMode
from eventradar.geo.region import Region
from eventradar.pipeline.profile_filter import ProfileFilter
from eventradar.storage.repositories import ProfileCandidate

SQUARE = Region(
    {
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
    }
)


def _rules(**overrides: object) -> ProfileFilter:
    """
    Build a filter with a square region and a one-keyword topic.

    Parameters:
      overrides: Profile field overrides.
    Returns:
      Profile filter.
    """
    fields: dict[str, object] = {
        "version": 1,
        "id": "p",
        "name": "P",
        "region": "r",
        "topic": "t",
        "trust": {
            "region": ["near"],
            "topic": ["mostly"],
            "topic_always": ["curated"],
        },
    }
    fields.update(overrides)
    topic = TopicConfig(
        version=1,
        id="t",
        name="T",
        keywords=["python"],
        description_min_matches=2,
        trusted_description_min_matches=1,
    )
    return ProfileFilter(
        ProfileConfig.model_validate(fields), SQUARE, TopicMatcher(topic)
    )


def _event(
    title: str = "Python night",
    description: str | None = None,
    lat: float | None = 5,
    lon: float | None = 5,
    mode: AttendanceMode = AttendanceMode.IN_PERSON,
    sources: tuple[str, ...] = ("other",),
) -> ProfileCandidate:
    """
    Build a candidate.

    Parameters:
      title: Title.
      description: Description.
      lat: Latitude.
      lon: Longitude.
      mode: Attendance mode.
      sources: Source ids.
    Returns:
      Candidate.
    """
    return ProfileCandidate(
        "E", title, description, lat, lon, mode, frozenset(sources)
    )


@pytest.mark.parametrize(
    ("event", "admitted"),
    [
        (_event(), True),
        (_event(lat=50, lon=50), False),
        (_event(lat=None, lon=None), False),
        (_event(lat=None, lon=None, sources=("near",)), True),
        (_event(mode=AttendanceMode.ONLINE), False),
        (_event(title="Mixer"), False),
        (_event(title="Mixer", description="python"), False),
        (
            _event(title="Mixer", description="python", sources=("mostly",)),
            True,
        ),
        (_event(title="Mixer", sources=("curated",)), True),
        (_event(title="Mixer", lat=50, lon=50, sources=("curated",)), False),
    ],
)
def test_admission_rules(event: ProfileCandidate, admitted: bool) -> None:
    """
    Region, attendance, and topic rules combine as documented.

    Parameters:
      event: Candidate.
      admitted: Expected decision.
    """
    assert _rules().admits(event) is admitted


def test_online_events_can_be_included() -> None:
    """With `include_online`, online events skip the region check."""
    online = _event(mode=AttendanceMode.ONLINE, lat=None, lon=None)
    assert _rules(include_online=True).admits(online)
