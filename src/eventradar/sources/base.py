"""Adapter protocol shared by every source."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from eventradar.config.schema import SourceConfig
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.http import HttpClient


class ParseError(ValueError):
    """A raw record could not be turned into an event draft."""


@dataclass(frozen=True)
class SourceContext:
    """Run-scoped services handed to adapters."""

    http: HttpClient
    now: datetime


class Source(Protocol):
    """
    A retrieval adapter.

    `fetch` does network IO only; `parse` is pure so stored raw records can
    be replayed without refetching.
    """

    def __init__(self, config: SourceConfig) -> None:
        """
        Bind the adapter to one configured source; validate its params.

        Parameters:
          config: Source configuration.
        """
        ...

    async def fetch(self, ctx: SourceContext) -> list[RawRecord]:
        """
        Retrieve every current upstream record.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          Raw records, one per upstream event.
        """
        ...

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Convert one raw record into zero or more drafts.

        Parameters:
          raw: Record previously returned by `fetch`.
        Returns:
          Parsed drafts.
        """
        ...
