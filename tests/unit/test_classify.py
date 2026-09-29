"""Tests for phrase matching, topic rules, and kind rules."""

from pathlib import Path

from eventradar.classify.kinds import KindClassifier
from eventradar.classify.phrases import PhraseSet
from eventradar.classify.topic import TopicMatcher
from eventradar.config.schema import KindRules, TopicConfig
from eventradar.domain.enums import EventKind


def test_phrases_respect_word_boundaries_and_separators() -> None:
    """Phrases match whole words; spaces and hyphens are interchangeable."""
    phrases = PhraseSet(["ai", "hack night", "c++"])
    assert phrases.found("AI meetup") == {"ai"}
    assert phrases.found("Hawaii night") == set()
    assert phrases.found("Hack-Night in LA") == {"hack night"}
    assert phrases.found("Modern C++ talk") == {"c++"}
    assert PhraseSet([]).found("anything") == set()


def test_topic_rules() -> None:
    """Title keywords qualify; descriptions need more unless trusted."""
    topic = TopicConfig(
        version=1,
        id="t",
        name="T",
        keywords=["python", "ai", "startup"],
        exclude_keywords=["real estate"],
        description_min_matches=2,
        trusted_description_min_matches=1,
    )
    matcher = TopicMatcher(topic)
    assert matcher.matches("Python night", None)
    assert not matcher.matches("Mixer", "Talk about python.")
    assert matcher.matches("Mixer", "Talk about python.", trusted=True)
    assert matcher.matches("Mixer", "Python and AI demos.")
    assert not matcher.matches("AI for real estate", None)


def test_kinds_from_title() -> None:
    """Titles can carry several kinds, or none."""
    rules = KindRules(
        version=1,
        kinds={
            EventKind.HACKATHON: ["hackathon", "hacks"],
            EventKind.MEETUP: ["meetup", "mixer"],
        },
    )
    kinds = KindClassifier(rules)
    assert kinds.kinds("OCC Hacks kickoff mixer") == {
        EventKind.HACKATHON,
        EventKind.MEETUP,
    }
    assert kinds.kinds("Quarterly review") == frozenset()


def test_source_default_kinds_apply_only_without_rule_matches(
    tmp_path: Path,
) -> None:
    """
    Title rules win; source defaults fill in when rules find nothing.

    Parameters:
      tmp_path: Pytest temporary directory.
    """
    from datetime import UTC, datetime, timedelta

    from eventradar.domain.models import EventDraft
    from eventradar.pipeline.classify import classify_kinds
    from eventradar.storage.db import connect, migrate
    from eventradar.storage.repositories import EventRepository

    now = datetime(2026, 10, 1, tzinfo=UTC)
    conn = connect(tmp_path / "s.db")
    migrate(conn)
    ids = iter(["E1", "E2"])
    repo = EventRepository(conn, id_factory=lambda: next(ids))
    for native, title in (("1", "Python Workshop"), ("2", "Monthly Gathering")):
        repo.upsert_draft(
            EventDraft(
                source_id="src",
                native_id=native,
                title=title,
                start_utc=now + timedelta(days=1),
            ),
            "h",
            now,
        )
    rules = KindRules(version=1, kinds={EventKind.WORKSHOP: ["workshop"]})
    classify_kinds(
        conn, repo, KindClassifier(rules), now, {"src": [EventKind.MEETUP]}
    )
    for event_id in ("E1", "E2"):
        repo.link_profile(event_id, "p", now)
    kinds = {e.event_id: e.kinds for e in repo.upcoming("p", now)}
    assert kinds == {
        "E1": frozenset({EventKind.WORKSHOP}),
        "E2": frozenset({EventKind.MEETUP}),
    }
