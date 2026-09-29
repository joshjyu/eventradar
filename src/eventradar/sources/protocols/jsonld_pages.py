"""Fetch event pages, keep their schema.org Event nodes, map them to drafts.

Shared by adapters that discover event page URLs in different ways (HTML
listing hubs, platform APIs). A page is refetched only when its listing
changes or on a staggered `refresh_days` cycle; otherwise the stored record
is reused. Live counters (`signals`) update without a refetch.
"""

import hashlib
import logging
from datetime import date
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.urls import canonical_url
from eventradar.http import HttpError
from eventradar.schemaorg.event import (
    EventMappingError,
    event_fields,
    slim_event,
)
from eventradar.schemaorg.extract import events, jsonld_documents
from eventradar.sources.base import ParseError, SourceContext

log = logging.getLogger(__name__)


def refresh_due(native_id: str, day: date, every: int) -> bool:
    """
    Spread refetches evenly: each id is due once every `every` days.

    Parameters:
      native_id: Record id.
      day: Current date.
      every: Cycle length in days.
    Returns:
      True on the id's day in the cycle.
    """
    digest = hashlib.sha256(native_id.encode()).digest()
    return day.toordinal() % every == int.from_bytes(digest[:8]) % every


class PageHarvester:
    """Turns event page URLs into raw records and records into drafts."""

    def __init__(self, source_id: str, zone: ZoneInfo, refresh_days: int):
        """
        Configure the harvester for one source.

        Parameters:
          source_id: Source that owns the records.
          zone: Zone for offset-less times.
          refresh_days: Cycle length for refetching unchanged pages.
        """
        self._source_id = source_id
        self._zone = zone
        self._refresh_days = refresh_days

    async def record(
        self,
        url: str,
        listing: dict[str, Any] | None,
        ctx: SourceContext,
        signals: dict[str, Any] | None = None,
    ) -> RawRecord | None:
        """
        Reuse the stored record when fresh, otherwise fetch the page.

        Parameters:
          url: Canonical event page URL (also the native id).
          listing: Stable listing data used for change detection.
          ctx: Run-scoped services.
          signals: Volatile counters stored without triggering a refetch.
        Returns:
          A raw record, or None if the page failed and nothing is stored.
        """
        previous = ctx.previous(self._source_id, url)
        fresh = (
            previous is not None
            and previous.payload.get("listing") == listing
            and not refresh_due(url, ctx.now.date(), self._refresh_days)
        )
        if fresh and previous is not None:
            if previous.payload.get("signals") == signals:
                return previous
            payload = {**previous.payload, "signals": signals}
            return previous.model_copy(
                update={"payload": payload, "fetched_at": ctx.now}
            )
        try:
            response = await ctx.http.get(url)
        except HttpError as exc:
            log.warning("%s: page failed, keeping stored copy: %s", url, exc)
            return previous
        nodes = [slim_event(n) for n in events(jsonld_documents(response.text))]
        payload = {"page_url": url, "listing": listing, "events": nodes}
        if signals is not None:
            payload["signals"] = signals
        return RawRecord(
            source_id=self._source_id,
            native_id=url,
            url=url,
            payload=payload,
            fetched_at=ctx.now,
        )

    def parse(self, raw: RawRecord) -> EventDraft:
        """
        Map the page's primary event node to a draft.

        The primary node is the one whose URL matches the page, else the
        first event on the page.

        Parameters:
          raw: Record produced by `record`.
        Returns:
          The draft.
        """
        nodes = raw.payload.get("events") or []
        if not nodes:
            raise ParseError(f"{raw.native_id}: no schema.org Event")
        page = raw.payload.get("page_url") or raw.native_id
        primary = next(
            (
                n
                for n in nodes
                if isinstance(n.get("url"), str)
                and canonical_url(urljoin(page, n["url"])) == raw.native_id
            ),
            nodes[0],
        )
        try:
            fields = event_fields(primary, page, self._zone)
        except EventMappingError as exc:
            raise ParseError(f"{raw.native_id}: {exc}") from exc
        return EventDraft(
            source_id=raw.source_id, native_id=raw.native_id, **fields
        )
