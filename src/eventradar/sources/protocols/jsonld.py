"""Generic schema.org JSON-LD adapter: discover event pages, read JSON-LD.

Discovery modes:
  hub   Listing pages whose JSON-LD `ItemList` links to event pages; a
        `{page}` placeholder in the URL enables pagination.
  urls  A fixed list of event pages.

Event pages are refetched only when their hub listing changes or on a
staggered cycle of `refresh_days`, so a daily run mostly fetches new events.
"""

import asyncio
import hashlib
import logging
import re
from datetime import date
from typing import Annotated, Any, Literal
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from eventradar.config.schema import SourceConfig
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.urls import canonical_url
from eventradar.http import HttpError
from eventradar.schemaorg.event import (
    EventMappingError,
    event_fields,
    slim_event,
)
from eventradar.schemaorg.extract import (
    events,
    item_list_entries,
    jsonld_documents,
)
from eventradar.sources.base import ParseError, SourceContext

log = logging.getLogger(__name__)


def _require_https(urls: list[str]) -> list[str]:
    """
    Reject non-TLS URLs.

    Parameters:
      urls: Configured URLs.
    Returns:
      The URLs.
    """
    bad = [u for u in urls if not u.startswith("https://")]
    if bad:
        raise ValueError(f"urls must use https: {', '.join(bad)}")
    return urls


class HubDiscovery(BaseModel):
    """Paginated listing pages with a JSON-LD ItemList."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["hub"]
    urls: list[str] = Field(min_length=1)
    pages: int = Field(default=1, ge=1, le=20)

    _https = field_validator("urls")(_require_https)


class UrlDiscovery(BaseModel):
    """A fixed list of event pages."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["urls"]
    urls: list[str] = Field(min_length=1)

    _https = field_validator("urls")(_require_https)


class JsonLdParams(BaseModel):
    """Parameters for a `jsonld` source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    discovery: Annotated[
        HubDiscovery | UrlDiscovery, Field(discriminator="type")
    ]
    url_pattern: str | None = None
    default_tz: str = "UTC"
    max_events: int = Field(default=200, ge=1, le=2000)
    refresh_days: int = Field(default=7, ge=1, le=60)

    @field_validator("url_pattern")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        """
        Require a compilable regex.

        Parameters:
          value: Pattern from config.
        Returns:
          The pattern.
        """
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid url_pattern: {exc}") from exc
        return value

    @field_validator("default_tz")
    @classmethod
    def _known_tz(cls, value: str) -> str:
        """
        Require an IANA time zone name.

        Parameters:
          value: Configured zone name.
        Returns:
          The zone name.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone: {value}") from exc
        return value


type Candidate = tuple[str, dict[str, Any] | None]


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


class JsonLdSource:
    """Discovers event pages and stores their schema.org Event nodes."""

    def __init__(self, config: SourceConfig) -> None:
        """
        Bind to a configured source.

        Parameters:
          config: Source configuration with `JsonLdParams` params.
        """
        self.config = config
        self.params = JsonLdParams.model_validate(config.params)
        self._zone = ZoneInfo(self.params.default_tz)
        self._pattern = (
            re.compile(self.params.url_pattern)
            if self.params.url_pattern
            else None
        )

    async def fetch(self, ctx: SourceContext) -> list[RawRecord]:
        """
        Discover event pages and fetch those that are new or due.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          One raw record per event page.
        """
        candidates = await self._discover(ctx)
        results = await asyncio.gather(
            *(self._record(url, listing, ctx) for url, listing in candidates)
        )
        return [r for r in results if r is not None]

    async def _discover(self, ctx: SourceContext) -> list[Candidate]:
        """
        Collect candidate event URLs, deduplicated and capped.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          (canonical URL, hub listing or None) pairs.
        """
        discovery = self.params.discovery
        if isinstance(discovery, UrlDiscovery):
            found = {canonical_url(u): None for u in discovery.urls}
        else:
            found = await self._crawl_hubs(discovery, ctx)
        return list(found.items())[: self.params.max_events]

    async def _crawl_hubs(
        self, hub: HubDiscovery, ctx: SourceContext
    ) -> dict[str, dict[str, Any] | None]:
        """
        Walk hub pages until a page adds nothing new or the cap is hit.

        Parameters:
          hub: Hub discovery settings.
          ctx: Run-scoped services.
        Returns:
          Canonical URL to slimmed listing item, in discovery order.
        """
        found: dict[str, dict[str, Any] | None] = {}
        errors: list[HttpError] = []
        for template in hub.urls:
            pages = hub.pages if "{page}" in template else 1
            for page in range(1, pages + 1):
                url = template.replace("{page}", str(page))
                try:
                    response = await ctx.http.get(url)
                except HttpError as exc:
                    errors.append(exc)
                    break
                docs = jsonld_documents(response.text)
                added = 0
                for link, item in item_list_entries(docs, url):
                    key = canonical_url(link)
                    if key in found or not self._wanted(key):
                        continue
                    found[key] = slim_event(item) if item else None
                    added += 1
                if not added or len(found) >= self.params.max_events:
                    break
        if not found and errors:
            raise errors[0]
        return found

    def _wanted(self, url: str) -> bool:
        """
        Apply the optional URL filter.

        Parameters:
          url: Canonical candidate URL.
        Returns:
          True if the URL should be fetched.
        """
        return self._pattern is None or bool(self._pattern.search(url))

    async def _record(
        self, url: str, listing: dict[str, Any] | None, ctx: SourceContext
    ) -> RawRecord | None:
        """
        Reuse the stored record when fresh, otherwise fetch the page.

        Parameters:
          url: Canonical event page URL (also the native id).
          listing: Hub listing for change detection.
          ctx: Run-scoped services.
        Returns:
          A raw record, or None if the page failed and nothing is stored.
        """
        previous = ctx.previous(self.config.id, url)
        fresh = (
            previous is not None
            and previous.payload.get("listing") == listing
            and not refresh_due(url, ctx.now.date(), self.params.refresh_days)
        )
        if fresh:
            return previous
        try:
            response = await ctx.http.get(url)
        except HttpError as exc:
            log.warning("%s: page failed, keeping stored copy: %s", url, exc)
            return previous
        nodes = [slim_event(n) for n in events(jsonld_documents(response.text))]
        return RawRecord(
            source_id=self.config.id,
            native_id=url,
            url=url,
            payload={"page_url": url, "listing": listing, "events": nodes},
            fetched_at=ctx.now,
        )

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Map the page's primary event node to a draft.

        The primary node is the one whose URL matches the page, else the
        first event on the page.

        Parameters:
          raw: Record produced by `fetch`.
        Returns:
          A single draft.
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
        return [
            EventDraft(
                source_id=raw.source_id, native_id=raw.native_id, **fields
            )
        ]
