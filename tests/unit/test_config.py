"""Tests for config loading, cross-validation, and env expansion."""

import shutil
from pathlib import Path

import pytest

from eventradar.config import ConfigError, expand_env, load_config

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """
    Copy the repository config into a scratch directory.

    Parameters:
      tmp_path: Pytest temporary directory.
    Returns:
      Path to the copied config root.
    """
    return Path(shutil.copytree(REPO_CONFIG, tmp_path / "config"))


def test_repository_config_is_valid() -> None:
    """The committed config loads without errors."""
    bundle = load_config(REPO_CONFIG)
    assert "socal-tech" in bundle.profiles
    assert bundle.enabled_sources()


def test_unknown_adapter_is_reported(config_dir: Path) -> None:
    """A source naming an unregistered adapter fails validation."""
    (config_dir / "sources" / "bad.yaml").write_text(
        "version: 1\nsources:\n  - id: bad-one\n    adapter: nope\n"
    )
    with pytest.raises(ConfigError, match="unknown adapter 'nope'"):
        load_config(config_dir)


def test_duplicate_source_id_is_reported(config_dir: Path) -> None:
    """Source ids must be unique across files."""
    (config_dir / "sources" / "dup.yaml").write_text(
        "version: 1\nsources:\n  - id: luma-la-tech-week\n    adapter: ical\n"
    )
    with pytest.raises(ConfigError, match="duplicate source id"):
        load_config(config_dir)


def test_profile_id_must_match_file_name(config_dir: Path) -> None:
    """A profile file's stem must equal its id."""
    src = config_dir / "profiles" / "socal-tech.yaml"
    src.rename(src.with_name("other.yaml"))
    with pytest.raises(ConfigError, match="must match file name"):
        load_config(config_dir)


def test_profile_references_must_resolve(config_dir: Path) -> None:
    """Unknown region, topic, and source references are all reported."""
    (config_dir / "profiles" / "x-y.yaml").write_text(
        "version: 1\nid: x-y\nname: X\nregion: nowhere\ntopic: none\n"
        "sources: [missing-src]\n"
    )
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    text = str(exc.value)
    assert "unknown region" in text
    assert "unknown topic" in text
    assert "unknown source" in text


def test_invalid_id_format_is_rejected(config_dir: Path) -> None:
    """Config ids must be kebab-case."""
    (config_dir / "sources" / "bad.yaml").write_text(
        "version: 1\nsources:\n  - id: Bad_Id\n    adapter: ical\n"
    )
    with pytest.raises(ConfigError, match=r"bad\.yaml: sources\.0\.id"):
        load_config(config_dir)


def test_expand_env_substitutes_nested_values() -> None:
    """References resolve inside nested dicts and lists."""
    env = {"A": "1", "B": "two"}
    value = {"x": "pre-${A}", "y": ["${B}", 3]}
    assert expand_env(value, env) == {"x": "pre-1", "y": ["two", 3]}


def test_expand_env_reports_missing_names_only() -> None:
    """Missing variables are named without echoing any values."""
    with pytest.raises(ConfigError, match="MISSING_ONE"):
        expand_env("${MISSING_ONE}", {"OTHER": "secret"})


def test_unknown_keep_region_is_reported(config_dir: Path) -> None:
    """A source's keep_region must name an existing region."""
    (config_dir / "sources" / "kr.yaml").write_text(
        "version: 1\nsources:\n  - id: kr-one\n    adapter: ical\n"
        "    keep_region: atlantis\n"
    )
    with pytest.raises(ConfigError, match="unknown keep_region 'atlantis'"):
        load_config(config_dir)
