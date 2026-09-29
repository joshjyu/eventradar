"""Tests for phrase matching, topic rules, and kind rules."""

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
