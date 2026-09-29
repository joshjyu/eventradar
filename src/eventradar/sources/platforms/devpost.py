"""Devpost hackathons: API listing for discovery, JSON-LD pages for detail.

The listing API gives display-only dates and venue names; each hackathon
page carries a schema.org Event with exact times and a postal address.
Registration counts are stored as signals, so their daily changes update
the size signal without refetching pages.
"""

import logging
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from eventradar.config.schema import SourceConfig
from eventradar.config.types import HttpsUrl, IanaZone
from eventradar.domain.enums import EventKind
from eventradar.domain.models import EventDraft, RawRecord
from eventradar.domain.urls import canonical_url
from eventradar.http import HttpError
from eventradar.sources.base import SourceContext
from eventradar.sources.protocols.jsonld_pages import (
    PageHarvester,
    PageRequest,
)

log = logging.getLogger(__name__)

_LISTING_KEYS = (
    "title",
    "submission_period_dates",
    "organization_name",
    "invite_only",
)


class DevpostParams(BaseModel):
    """Parameters for a `devpost` source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: HttpsUrl = "https://devpost.com/api/hackathons"
    challenge_types: list[str] = Field(default=["in-person"], min_length=1)
    statuses: list[str] = Field(default=["upcoming", "open"], min_length=1)
    pages: int = Field(default=15, ge=1, le=50)
    default_tz: IanaZone = "UTC"
    refresh_days: int = Field(default=7, ge=1, le=60)
    max_events: int = Field(default=300, ge=1, le=2000)


class DevpostSource:
    """Lists hackathons through the API and harvests each page."""

    def __init__(self, config: SourceConfig) -> None:
        """
        Bind to a configured source.

        Parameters:
          config: Source configuration with `DevpostParams` params.
        """
        self.config = config
        self.params = DevpostParams.model_validate(config.params)
        self._pages = PageHarvester(
            config.id,
            ZoneInfo(self.params.default_tz),
            self.params.refresh_days,
        )

    async def fetch(self, ctx: SourceContext) -> list[RawRecord]:
        """
        List hackathons, then fetch pages that are new or due.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          One raw record per hackathon page.
        """
        listed = await self._list(ctx)
        return await self._pages.harvest(
            [
                PageRequest(
                    canonical_url(item["url"]),
                    {k: item.get(k) for k in _LISTING_KEYS},
                    {"registrations": item.get("registrations_count")},
                )
                for item in listed
            ],
            ctx,
        )

    async def _list(self, ctx: SourceContext) -> list[dict[str, Any]]:
        """
        Page through the listing API.

        Parameters:
          ctx: Run-scoped services.
        Returns:
          Listing items with a URL, deduplicated, capped at `max_events`.
        """
        found: dict[str, dict[str, Any]] = {}
        for page in range(1, self.params.pages + 1):
            query = [
                *(("challenge_type[]", t) for t in self.params.challenge_types),
                *(("status[]", s) for s in self.params.statuses),
                ("page", str(page)),
            ]
            url = f"{self.params.endpoint}?{urlencode(query)}"
            try:
                data = (await ctx.http.get(url)).json()
            except HttpError:
                if not found:
                    raise
                log.warning("devpost listing stopped at page %d", page)
                break
            items = data.get("hackathons") or []
            for item in items:
                if isinstance(item.get("url"), str):
                    found.setdefault(canonical_url(item["url"]), item)
            total = (data.get("meta") or {}).get("total_count") or 0
            if not items or len(found) >= min(total, self.params.max_events):
                break
        return list(found.values())[: self.params.max_events]

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Map the hackathon page to a draft, adding Devpost-only fields.

        Parameters:
          raw: Record produced by `fetch`.
        Returns:
          A single draft.
        """
        draft = self._pages.parse(raw)
        listing = raw.payload.get("listing") or {}
        signals = raw.payload.get("signals") or {}
        registrations = signals.get("registrations")
        updates: dict[str, Any] = {"kinds": draft.kinds | {EventKind.HACKATHON}}
        if draft.size_signal is None and isinstance(registrations, int):
            updates["size_signal"] = registrations
        if draft.organizer is None and listing.get("organization_name"):
            updates["organizer"] = listing["organization_name"]
        return [draft.model_copy(update=updates)]
