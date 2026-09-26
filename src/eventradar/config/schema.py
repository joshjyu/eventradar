"""Pydantic schemas for every file under `config/`."""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


class BlobSettings(_Strict):
    """Selects a blob backend plugin and its options."""

    backend: str
    options: dict[str, Any] = Field(default_factory=dict)


class EnvironmentSettings(_Strict):
    """Backends for one deployment environment."""

    state: BlobSettings
    publish: BlobSettings | None = None


class RunSettings(_Strict):
    """Per-run safety limits."""

    lock_ttl_s: int = Field(default=3600, ge=60)
    snapshot_retention: int = Field(default=14, ge=1)


class Settings(_Strict):
    """Top-level runtime settings (`config/settings.yaml`)."""

    version: Literal[1]
    http: HttpSettings
    run: RunSettings = RunSettings()
    environments: dict[str, EnvironmentSettings]


class SloSettings(_Strict):
    """Health thresholds for a source."""

    min_yield_ratio: float = Field(default=0.3, ge=0, le=1)
    window_days: int = Field(default=7, ge=1)


class SourceConfig(_Strict):
    """One configured source: an adapter plus its parameters."""

    id: ConfigId
    adapter: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    slo: SloSettings = SloSettings()


class SourceFile(_Strict):
    """A file under `config/sources/`."""

    version: Literal[1]
    sources: list[SourceConfig]


class ProfileConfig(_Strict):
    """A published view: region x topic, optionally scoped to sources."""

    version: Literal[1]
    id: ConfigId
    name: str
    region: ConfigId
    topic: ConfigId
    sources: list[ConfigId] | Literal["all"] = "all"
    queries: list[str] = Field(default_factory=list)


class TopicConfig(_Strict):
    """Topic taxonomy used for classification."""

    version: Literal[1]
    id: ConfigId
    name: str
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)


class ConfigBundle(_Strict):
    """Fully loaded and cross-validated configuration."""

    root: Path
    settings: Settings
    sources: dict[str, SourceConfig]
    profiles: dict[str, ProfileConfig]
    topics: dict[str, TopicConfig]
    regions: dict[str, Path]

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
