"""Generic schema.org JSON-LD adapter: discover event pages, read JSON-LD.

Discovery modes:
  hub    Listing pages whose JSON-LD `ItemList` links to event pages; a
         `{page}` placeholder in the URL enables pagination.
  links  Pages whose plain `<a>` links point to event pages; `url_pattern`
         selects which links count.
  urls   A fixed list of event pages.

Event pages are refetched only when their hub listing changes or on a
staggered cycle of `refresh_days`, so a daily run mostly fetches new events.
"""

import re
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from eventradar.config.schema import SourceConfig
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.urls import canonical_url
from eventradar.geo.states import state_abbr, with_state
from eventradar.http import HttpError
from eventradar.schemaorg.event import slim_event
from eventradar.schemaorg.extract import item_list_entries, jsonld_documents
from eventradar.sources.base import SourceContext
from eventradar.sources.protocols.jsonld_pages import (
    PageHarvester,
    PageRequest,
    refresh_due,
)
from eventradar.sources.protocols.links import page_links

__all__ = ["JsonLdParams", "JsonLdSource", "refresh_due"]


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


class LinksDiscovery(BaseModel):
    """Pages that link to event pages, e.g. an event's own website."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["links"]
    urls: list[str] = Field(min_length=1)

    _https = field_validator("urls")(_require_https)


class JsonLdParams(BaseModel):
    """Parameters for a `jsonld` source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    discovery: Annotated[
        HubDiscovery | LinksDiscovery | UrlDiscovery,
        Field(discriminator="type"),
    ]
    url_pattern: str | None = None
    default_tz: str = "UTC"
    max_events: int = Field(default=200, ge=1, le=2000)
    refresh_days: int = Field(default=7, ge=1, le=60)
    # Publishers that stamp dates as midnight UTC: read local days instead.
    dates_only: bool = False
    # U.S. state for addresses that name none (listings scoped to a state).
    default_state: str | None = None

    @model_validator(mode="after")
    def _links_need_pattern(self) -> Self:
        """
        Require `url_pattern` with links discovery, which would otherwise
        follow every link on the page.

        Returns:
          The validated params.
        """
        if isinstance(self.discovery, LinksDiscovery) and not self.url_pattern:
            raise ValueError("links discovery requires url_pattern")
        return self

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

    @field_validator("default_state")
    @classmethod
    def _known_state(cls, value: str | None) -> str | None:
        """
        Require a U.S. state, stored as its postal abbreviation.

        Parameters:
          value: Configured state.
        Returns:
          The abbreviation.
        """
        if value is None:
            return None
        abbr = state_abbr(value)
        if abbr is None:
            raise ValueError(f"unknown U.S. state: {value}")
        return abbr

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
        self._pages = PageHarvester(
            config.id,
            ZoneInfo(self.params.default_tz),
            self.params.refresh_days,
            self.params.dates_only,
        )
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
        return await self._pages.harvest(
            [PageRequest(url, listing) for url, listing in candidates], ctx
        )

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
        elif isinstance(discovery, LinksDiscovery):
            found = await self._follow_links(discovery, ctx)
        else:
            found = await self._crawl_hubs(discovery, ctx)
        return list(found.items())[: self.params.max_events]

    async def _follow_links(
        self, pages: LinksDiscovery, ctx: SourceContext
    ) -> dict[str, dict[str, Any] | None]:
        """
        Collect matching links from each listing page.

        Parameters:
          pages: Links discovery settings.
          ctx: Run-scoped services.
        Returns:
          Canonical URL to None (plain links carry no listing data).
        """
        found: dict[str, dict[str, Any] | None] = {}
        errors: list[HttpError] = []
        for url in pages.urls:
            try:
                response = await ctx.http.get(url)
            except HttpError as exc:
                errors.append(exc)
                continue
            for link in page_links(response.text, url):
                key = canonical_url(link)
                if key != canonical_url(url) and self._wanted(key):
                    found.setdefault(key, None)
        if not found and errors:
            raise errors[0]
        return found

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

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Map the page's primary event node to a draft.

        Parameters:
          raw: Record produced by `fetch`.
        Returns:
          A single draft.
        """
        draft = self._pages.parse(raw)
        state = self.params.default_state
        if state and draft.address:
            address = with_state(draft.address, state)
            draft = draft.model_copy(update={"address": address})
        return [draft]
