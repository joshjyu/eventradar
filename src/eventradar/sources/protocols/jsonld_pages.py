"""Fetch event pages, keep their schema.org Event nodes, map them to drafts.

Shared by adapters that discover event page URLs in different ways (HTML
listing hubs, platform APIs). A page is refetched only when its listing
changes or on a staggered `refresh_days` cycle; otherwise the stored record
is reused. Live counters (`signals`) update without a refetch.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
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


MIN_ATTEMPTS_TO_FAIL = 3
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


@dataclass(frozen=True)
class PageRequest:
    """One page to record."""

    url: str
    listing: dict[str, Any] | None = None
    signals: dict[str, Any] | None = None


@dataclass
class _Tally:
    """Fetch attempts and failures during one harvest."""

    attempted: int = 0
    errors: list[HttpError] = field(default_factory=list)


def _title(html: str) -> str:
    """
    Read a page's <title>, for diagnostics.

    Parameters:
      html: Page HTML.
    Returns:
      The title, whitespace-collapsed and shortened, or "".
    """
    match = _TITLE.search(html)
    return " ".join(match.group(1).split())[:80] if match else ""


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

    async def harvest(
        self, requests: Sequence[PageRequest], ctx: SourceContext
    ) -> list[RawRecord]:
        """
        Record every requested page, failing loudly if all fetches fail.

        When at least `MIN_ATTEMPTS_TO_FAIL` pages were fetched and every
        one failed (e.g. the site started blocking us), the source fails
        with the first error instead of quietly returning nothing.

        Parameters:
          requests: Pages to record.
          ctx: Run-scoped services.
        Returns:
          Raw records for pages that are stored or fetched.
        """
        tally = _Tally()
        results = await asyncio.gather(
            *(
                self.record(r.url, r.listing, ctx, r.signals, tally)
                for r in requests
            )
        )
        attempted, failed = tally.attempted, len(tally.errors)
        if attempted >= MIN_ATTEMPTS_TO_FAIL and failed == attempted:
            raise HttpError(
                f"all {attempted} event page fetches failed; first: "
                f"{tally.errors[0]}"
            )
        return [r for r in results if r is not None]

    async def record(
        self,
        url: str,
        listing: dict[str, Any] | None,
        ctx: SourceContext,
        signals: dict[str, Any] | None = None,
        tally: "_Tally | None" = None,
    ) -> RawRecord | None:
        """
        Reuse the stored record when fresh, otherwise fetch the page.

        Parameters:
          url: Canonical event page URL (also the native id).
          listing: Stable listing data used for change detection.
          ctx: Run-scoped services.
          signals: Volatile counters stored without triggering a refetch.
          tally: Collects fetch attempts and errors, when given.
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
        if tally is not None:
            tally.attempted += 1
        try:
            response = await ctx.http.get(url)
        except HttpError as exc:
            log.warning("%s: page failed, keeping stored copy: %s", url, exc)
            if tally is not None:
                tally.errors.append(exc)
            return previous
        nodes = [slim_event(n) for n in events(jsonld_documents(response.text))]
        if not nodes:
            log.warning(
                "%s: no schema.org Event (HTTP %s, %s, title %r)",
                url,
                response.status_code,
                response.headers.get("content-type", "?"),
                _title(response.text),
            )
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
