"""Pydantic schemas for every file under `config/`."""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from eventradar.domain.enums import EventKind

type ConfigId = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
]


class _Strict(BaseModel):
    """Base for config models: immutable, unknown keys rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class HttpSettings(_Strict):
    """Outbound HTTP behavior shared by all adapters."""

    user_agent: str
    timeout_s: float = Field(default=20.0, gt=0)
    max_concurrency: int = Field(default=8, ge=1)
    per_host_min_interval_s: float = Field(default=1.0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    respect_robots: bool = True
    max_crawl_delay_s: float = Field(default=30.0, ge=0)


class BlobSettings(_Strict):
    """Selects a blob backend plugin and its options."""

    backend: str
    options: dict[str, Any] = Field(default_factory=dict)


class ProviderSettings(_Strict):
    """Selects a plugin implementation and its options."""

    provider: str
    options: dict[str, Any] = Field(default_factory=dict)


class EnvironmentSettings(_Strict):
    """Backends for one deployment environment."""

    state: BlobSettings
    publish: BlobSettings | None = None
    alerts: ProviderSettings = ProviderSettings(provider="log")


class RunSettings(_Strict):
    """Per-run safety limits."""

    lock_ttl_s: int = Field(default=3600, ge=60)
    snapshot_retention: int = Field(default=14, ge=1)
    raw_history_days: int = Field(default=30, ge=1)


class GeoSettings(_Strict):
    """Geocoding behavior for the enrich stage."""

    geocoder: ProviderSettings = ProviderSettings(provider="census")
    max_lookups_per_run: int = Field(default=200, ge=0)
    retry_misses_after_days: int = Field(default=30, ge=1)


class Settings(_Strict):
    """Top-level runtime settings (`config/settings.yaml`)."""

    version: Literal[1]
    http: HttpSettings
    run: RunSettings = RunSettings()
    geo: GeoSettings = GeoSettings()
    # Homepage linked from published feeds.
    site_url: str = "https://github.com/joshjyu/eventradar"
    environments: dict[str, EnvironmentSettings]


class SloSettings(_Strict):
    """Health thresholds for a source."""

    # Unhealthy when a run fetches fewer records than this share of the
    # median over the window.
    min_yield_ratio: float = Field(default=0.3, ge=0, le=1)
    window_days: int = Field(default=7, ge=1)
    # Runs needed in the window before the yield rule applies.
    min_history_runs: int = Field(default=3, ge=1)
    # Unhealthy when more than this share of changed records fail to parse.
    max_parse_error_ratio: float = Field(default=0.5, ge=0, le=1)
    # Consecutive unhealthy runs before alerting, and before disabling.
    alert_after_runs: int = Field(default=2, ge=1)
    disable_after_runs: int = Field(default=7, ge=1)


class SourceConfig(_Strict):
    """One configured source: an adapter plus its parameters."""

    id: ConfigId
    adapter: str
    enabled: bool = True
    # Higher wins when sources describe the same event differently.
    priority: int = Field(default=50, ge=0, le=100)
    # Kinds for this source's events when title rules find none.
    default_kinds: list[EventKind] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    slo: SloSettings = SloSettings()


class SourceFile(_Strict):
    """A file under `config/sources/`."""

    version: Literal[1]
    sources: list[SourceConfig]


class ProfileTrust(_Strict):
    """Sources whose events skip a filter because they are curated."""

    # Unlocated events from these sources count as in the region.
    region: list[ConfigId] = Field(default_factory=list)
    # Events from these sources need weaker keyword evidence for the topic.
    topic: list[ConfigId] = Field(default_factory=list)
    # Events from these sources are on the topic without keyword evidence.
    topic_always: list[ConfigId] = Field(default_factory=list)


class ProfileConfig(_Strict):
    """A published view: region x topic, optionally scoped to sources."""

    version: Literal[1]
    id: ConfigId
    name: str
    region: ConfigId
    topic: ConfigId
    sources: list[ConfigId] | Literal["all"] = "all"
    trust: ProfileTrust = ProfileTrust()
    include_online: bool = False
    queries: list[str] = Field(default_factory=list)


class TopicConfig(_Strict):
    """Topic taxonomy used for classification."""

    version: Literal[1]
    id: ConfigId
    name: str
    # Phrases matched case-insensitively on word boundaries.
    keywords: list[str] = Field(default_factory=list)
    # A title containing any of these is never on the topic.
    exclude_keywords: list[str] = Field(default_factory=list)
    # Distinct keywords a description needs when the title has none.
    description_min_matches: int = Field(default=3, ge=1)
    # The same, for events from sources a profile trusts for the topic.
    trusted_description_min_matches: int = Field(default=1, ge=1)


class KindRules(_Strict):
    """Title phrases that mark event kinds (`config/kinds.yaml`)."""

    version: Literal[1]
    kinds: dict[EventKind, list[str]] = Field(default_factory=dict)


class ConfigBundle(_Strict):
    """Fully loaded and cross-validated configuration."""

    root: Path
    settings: Settings
    sources: dict[str, SourceConfig]
    profiles: dict[str, ProfileConfig]
    topics: dict[str, TopicConfig]
    regions: dict[str, Path]
    kinds: KindRules = KindRules(version=1)

    def enabled_sources(self) -> list[SourceConfig]:
        """
        List sources that are enabled in config.

        Returns:
          Enabled sources in id order.
        """
        return [s for _, s in sorted(self.sources.items()) if s.enabled]

    def profile_sources(self, profile: ProfileConfig) -> set[str]:
        """
        Resolve which source ids feed a profile.

        Parameters:
          profile: Profile to resolve.
        Returns:
          Set of source ids.
        """
        if profile.sources == "all":
            return set(self.sources)
        return set(profile.sources)
