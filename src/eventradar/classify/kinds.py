"""Assign event kinds from title phrases."""

from eventradar.classify.phrases import PhraseSet
from eventradar.config.schema import KindRules
from eventradar.domain.enums import EventKind


class KindClassifier:
    """Maps title phrases to kinds; an event can have several."""

    def __init__(self, rules: KindRules) -> None:
        """
        Compile each kind's phrases.

        Parameters:
          rules: Kind rules from config.
        """
        self._rules = {
            kind: PhraseSet(phrases) for kind, phrases in rules.kinds.items()
        }

    def kinds(self, title: str) -> frozenset[EventKind]:
        """
        Classify a title.

        Parameters:
          title: Event title.
        Returns:
          Kinds whose phrases appear in the title.
        """
        return frozenset(k for k, p in self._rules.items() if p.found(title))
