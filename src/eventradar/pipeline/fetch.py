"""Fetch stage: run every enabled adapter concurrently."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from eventradar.config.schema import SourceConfig
from eventradar.domain.models import RawRecord
from eventradar.sources.base import Source, SourceContext
from eventradar.sources.registry import build_source


@dataclass
class FetchResult:
    """Outcome of fetching one source."""

    config: SourceConfig
    started_at: datetime
    finished_at: datetime
    source: Source | None = None
    records: list[RawRecord] = field(default_factory=list)
    error: str | None = None


async def _fetch_one(config: SourceConfig, ctx: SourceContext) -> FetchResult:
    """
    Build and run one adapter, capturing any failure.

    Parameters:
      config: Source to fetch.
      ctx: Run-scoped services.
    Returns:
      The fetch result; `error` is set on failure.
    """
    started = datetime.now(UTC)
    try:
        source = build_source(config)
        records = await source.fetch(ctx)
    except Exception as exc:
        return FetchResult(
            config=config,
            started_at=started,
            finished_at=datetime.now(UTC),
            error=f"{type(exc).__name__}: {exc}",
        )
    return FetchResult(
        config=config,
        started_at=started,
        finished_at=datetime.now(UTC),
        source=source,
        records=records,
    )


async def fetch_all(
    configs: Sequence[SourceConfig], ctx: SourceContext
) -> list[FetchResult]:
    """
    Fetch all sources; one failure never affects the others.

    Parameters:
      configs: Sources to fetch.
      ctx: Run-scoped services.
    Returns:
      Results in the same order as `configs`.
    """
    return list(await asyncio.gather(*(_fetch_one(c, ctx) for c in configs)))
