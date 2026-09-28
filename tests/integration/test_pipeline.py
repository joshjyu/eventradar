"""End-to-end pipeline runs against fixtures and local blob stores."""

import json
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import httpx
import pytest
import respx

from eventradar.config import ConfigBundle, load_config
from eventradar.pipeline.runner import RunOptions, replay, run

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures" / "ical"
SNAPSHOTS = HERE.parent / "golden" / "snapshots"
NOW = datetime(2026, 9, 26, 13, 17, tzinfo=UTC)


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigBundle:
    """
    Load the integration config with blob roots in a scratch directory.

    Parameters:
      tmp_path: Pytest temporary directory.
      monkeypatch: Pytest monkeypatch fixture.
    Returns:
      Loaded config bundle.
    """
    monkeypatch.setenv("EVENTRADAR_TEST_ROOT", str(tmp_path))
    return load_config(HERE / "config")


def _mock_feeds() -> None:
    """Serve fixture feeds; one source is permanently broken."""
    respx.get("https://feeds.example.test/utc.ics").mock(
        return_value=httpx.Response(
            200, content=(FIXTURES / "utc_feed.ics").read_bytes()
        )
    )
    respx.get("https://feeds.example.test/edge.ics").mock(
        return_value=httpx.Response(
            200, content=(FIXTURES / "edge_cases.ics").read_bytes()
        )
    )
    respx.get("https://feeds.example.test/broken.ics").mock(
        return_value=httpx.Response(404)
    )


async def _run(bundle: ConfigBundle, now: datetime, run_id: str) -> dict:
    """
    Run the pipeline with deterministic ids and time.

    Parameters:
      bundle: Loaded config.
      now: Run time.
      run_id: Run id.
    Returns:
      Per-source (fetched, changed, status) and profile counts.
    """
    ids = count(1)
    summary = await run(
        bundle,
        RunOptions(env="test", now=now, run_id=run_id),
        id_factory=lambda: f"EVT{next(ids):04d}",
    )
    return {
        "status": summary.status,
        "sources": {
            r.source_id: (r.status, r.fetched, r.changed)
            for r in summary.sources
        },
        "profiles": summary.profiles,
    }


def _published(root: Path) -> dict[str, str]:
    """
    Read every published file.

    Parameters:
      root: Public blob root.
    Returns:
      Relative path to text.
    """
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@respx.mock
async def test_run_matches_snapshots(
    bundle: ConfigBundle, tmp_path: Path, update_golden: bool
) -> None:
    """
    Published outputs match the reviewed snapshots.

    Parameters:
      bundle: Loaded config.
      tmp_path: Pytest temporary directory.
      update_golden: Rewrite snapshots instead of comparing.
    """
    _mock_feeds()
    result = await _run(bundle, NOW, "RUN1")
    assert result["status"] == "degraded"
    assert result["sources"]["fixture-broken"][0] == "failed"
    published = _published(tmp_path / "public")
    if update_golden:
        for rel, text in published.items():
            dest = SNAPSHOTS / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
    expected = {
        p.relative_to(SNAPSHOTS).as_posix(): p.read_text()
        for p in sorted(SNAPSHOTS.rglob("*.json"))
    }
    assert published == expected


@respx.mock
async def test_second_run_is_idempotent(
    bundle: ConfigBundle, tmp_path: Path
) -> None:
    """
    Re-running with unchanged feeds changes nothing and adds nothing.

    Parameters:
      bundle: Loaded config.
      tmp_path: Pytest temporary directory.
    """
    _mock_feeds()
    await _run(bundle, NOW, "RUN1")
    later = NOW + timedelta(days=1)
    second = await _run(bundle, later, "RUN2")
    assert second["sources"]["fixture-utc"][2] == 0
    assert second["sources"]["fixture-edge"][2] == 0
    assert second["profiles"]["test-all"]["new"] == 0
    feed = json.loads((tmp_path / "public/v1/test-all/events.json").read_text())
    ids = [e["event_id"] for e in feed["events"]]
    assert len(ids) == len(set(ids))


@respx.mock
async def test_all_sources_failing_keeps_published_data(
    bundle: ConfigBundle, tmp_path: Path
) -> None:
    """
    A run where every source fails never overwrites published outputs.

    Parameters:
      bundle: Loaded config.
      tmp_path: Pytest temporary directory.
    """
    _mock_feeds()
    await _run(bundle, NOW, "RUN1")
    events = tmp_path / "public/v1/test-all/events.json"
    before = events.read_bytes()
    respx.routes.clear()
    respx.get(url__startswith="https://feeds.example.test/").mock(
        return_value=httpx.Response(500)
    )
    result = await _run(bundle, NOW + timedelta(hours=1), "RUN2")
    assert result["status"] == "failed"
    assert events.read_bytes() == before


@respx.mock
async def test_replay_needs_no_network(
    bundle: ConfigBundle, tmp_path: Path
) -> None:
    """
    Replay reparses stored raw records with the network unavailable.

    Parameters:
      bundle: Loaded config.
      tmp_path: Pytest temporary directory.
    """
    _mock_feeds()
    await _run(bundle, NOW, "RUN1")
    respx.routes.clear()
    respx.route().mock(side_effect=httpx.ConnectError("offline"))
    written = replay(bundle, "test", now=NOW + timedelta(hours=1))
    assert written["fixture-utc"] == 2
    assert written["fixture-edge"] == 5
    assert written["fixture-broken"] == 0
