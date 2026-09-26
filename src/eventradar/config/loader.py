"""Load, validate, and cross-reference the `config/` directory."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from eventradar.config.schema import (
    ConfigBundle,
    ProfileConfig,
    Settings,
    SourceConfig,
    SourceFile,
    TopicConfig,
)
from eventradar.plugins import available

SOURCE_GROUP = "eventradar.sources"
_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ConfigError(ValueError):
    """Aggregates every problem found while loading config."""

    def __init__(self, problems: list[str]) -> None:
        """
        Build the error from a list of problems.

        Parameters:
          problems: Human-readable problem descriptions.
        """
        self.problems = problems
        super().__init__("\n".join(problems))


def _read_yaml(path: Path) -> Any:
    """
    Parse a YAML file with the safe loader.

    Parameters:
      path: File to read.
    Returns:
      The parsed document.
    """
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse[M: BaseModel](
    model: type[M], path: Path, root: Path, problems: list[str]
) -> M | None:
    """
    Validate one file against a model, recording failures.

    Parameters:
      model: Pydantic model to validate against.
      path: File to read.
      root: Config root, for relative paths in messages.
      problems: Accumulator for error messages.
    Returns:
      The model instance, or None if the file is invalid.
    """
    rel = path.relative_to(root)
    try:
        return model.model_validate(_read_yaml(path))
    except yaml.YAMLError as exc:
        problems.append(f"{rel}: invalid YAML: {exc}")
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            problems.append(f"{rel}: {loc}: {err['msg']}")
    return None


def _load_named[M: ProfileConfig | TopicConfig](
    model: type[M], directory: Path, root: Path, problems: list[str]
) -> dict[str, M]:
    """
    Load `*.yaml` files whose stem must equal their `id`.

    Parameters:
      model: Model with an `id` field.
      directory: Directory to scan.
      root: Config root, for relative paths in messages.
      problems: Accumulator for error messages.
    Returns:
      Mapping of id to model.
    """
    loaded: dict[str, M] = {}
    for path in sorted(directory.glob("*.yaml")):
        item = _parse(model, path, root, problems)
        if item is None:
            continue
        if item.id != path.stem:
            problems.append(
                f"{path.relative_to(root)}: id '{item.id}' must match "
                f"file name '{path.stem}'"
            )
        loaded[item.id] = item
    return loaded


def _load_sources(
    directory: Path, root: Path, problems: list[str]
) -> dict[str, SourceConfig]:
    """
    Load every source file and enforce globally unique ids.

    Parameters:
      directory: `config/sources/`.
      root: Config root, for relative paths in messages.
      problems: Accumulator for error messages.
    Returns:
      Mapping of source id to source config.
    """
    sources: dict[str, SourceConfig] = {}
    adapters = set(available(SOURCE_GROUP))
    for path in sorted(directory.glob("*.yaml")):
        parsed = _parse(SourceFile, path, root, problems)
        rel = path.relative_to(root)
        for src in parsed.sources if parsed else []:
            if src.id in sources:
                problems.append(f"{rel}: duplicate source id '{src.id}'")
            if src.adapter not in adapters:
                problems.append(
                    f"{rel}: source '{src.id}' uses unknown adapter "
                    f"'{src.adapter}'"
                )
            sources[src.id] = src
    return sources


def _check_profiles(
    profiles: Mapping[str, ProfileConfig],
    sources: Mapping[str, SourceConfig],
    topics: Mapping[str, TopicConfig],
    regions: Mapping[str, Path],
    problems: list[str],
) -> None:
    """
    Verify that every profile reference resolves.

    Parameters:
      profiles: Loaded profiles.
      sources: Loaded sources.
      topics: Loaded topics.
      regions: Region id to GeoJSON path.
      problems: Accumulator for error messages.
    """
    for p in profiles.values():
        if p.region not in regions:
            problems.append(f"profile '{p.id}': unknown region '{p.region}'")
        if p.topic not in topics:
            problems.append(f"profile '{p.id}': unknown topic '{p.topic}'")
        missing = (
            []
            if p.sources == "all"
            else [s for s in p.sources if s not in sources]
        )
        problems.extend(
            f"profile '{p.id}': unknown source '{s}'" for s in missing
        )


def load_config(root: Path) -> ConfigBundle:
    """
    Load and cross-validate the whole config directory.

    Parameters:
      root: Path to the `config/` directory.
    Returns:
      The validated bundle.
    """
    problems: list[str] = []
    settings_path = root / "settings.yaml"
    settings = None
    if settings_path.is_file():
        settings = _parse(Settings, settings_path, root, problems)
    else:
        problems.append("settings.yaml: missing")
    sources = _load_sources(root / "sources", root, problems)
    profiles = _load_named(ProfileConfig, root / "profiles", root, problems)
    topics = _load_named(TopicConfig, root / "topics", root, problems)
    regions = {p.stem: p for p in sorted((root / "regions").glob("*.geojson"))}
    _check_profiles(profiles, sources, topics, regions, problems)
    if problems or settings is None:
        raise ConfigError(problems)
    return ConfigBundle(
        root=root,
        settings=settings,
        sources=sources,
        profiles=profiles,
        topics=topics,
        regions=regions,
    )


def expand_env(value: Any, env: Mapping[str, str] | None = None) -> Any:
    """
    Substitute `${VAR}` references in strings, recursively.

    Parameters:
      value: Scalar, list, or mapping from config.
      env: Variables to read; defaults to the process environment.
    Returns:
      The value with every reference replaced.
    """
    env = os.environ if env is None else env
    if isinstance(value, dict):
        return {k: expand_env(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v, env) for v in value]
    if not isinstance(value, str):
        return value
    missing = [n for n in _ENV_REF.findall(value) if not env.get(n)]
    if missing:
        raise ConfigError(
            [f"missing environment variable(s): {', '.join(missing)}"]
        )
    return _ENV_REF.sub(lambda m: env[m.group(1)], value)
