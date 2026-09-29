"""Case-insensitive phrase matching on word boundaries."""

import re
from collections.abc import Iterable


class PhraseSet:
    """Finds which of a set of phrases occur in text."""

    def __init__(self, phrases: Iterable[str]) -> None:
        """
        Compile the phrases.

        Spaces and hyphens inside a phrase match any run of spaces or
        hyphens, so "hack night" also matches "Hack-Night".

        Parameters:
          phrases: Phrases; matching ignores case.
        """
        self._phrases = sorted(
            {p.strip().lower() for p in phrases if p.strip()}
        )
        parts = [
            r"[\s\-]+".join(re.escape(w) for w in re.split(r"[\s\-]+", p))
            for p in self._phrases
        ]
        self._pattern = (
            re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.I)
            if parts
            else None
        )

    def found(self, text: str | None) -> set[str]:
        """
        List the distinct phrases present.

        Parameters:
          text: Text to search.
        Returns:
          Matched phrases, normalized to lower case with single spaces.
        """
        if not text or self._pattern is None:
            return set()
        return {
            re.sub(r"[\s\-]+", " ", m.group(1).lower())
            for m in self._pattern.finditer(text)
        }
