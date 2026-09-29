"""Decide whether an event belongs to a topic."""

from eventradar.classify.phrases import PhraseSet
from eventradar.config.schema import TopicConfig


class TopicMatcher:
    """Keyword rules for one topic."""

    def __init__(self, topic: TopicConfig) -> None:
        """
        Compile the topic's phrases.

        Parameters:
          topic: Topic configuration.
        """
        self._keywords = PhraseSet(topic.keywords)
        self._excluded = PhraseSet(topic.exclude_keywords)
        self._min_description = topic.description_min_matches
        self._min_trusted = topic.trusted_description_min_matches

    def matches(
        self, title: str, description: str | None, trusted: bool = False
    ) -> bool:
        """
        Apply the rules: excluded title words veto; a title keyword
        suffices; otherwise the description needs enough distinct keywords
        (fewer for sources trusted for the topic).

        Parameters:
          title: Event title.
          description: Event description.
          trusted: Whether a source of the event is trusted for the topic.
        Returns:
          True if the event is on the topic.
        """
        if self._excluded.found(title):
            return False
        if self._keywords.found(title):
            return True
        found = self._keywords.found(description)
        need = self._min_trusted if trusted else self._min_description
        return len(found) >= need
