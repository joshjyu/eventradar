"""Tests for health transitions, alerts, probes, and drift capture."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eventradar.config.schema import (
    ConfigBundle,
    HttpSettings,
    Settings,
    SloSettings,
    SourceConfig,
)
from eventradar.health.monitor import probe_due, update_health
from eventradar.http import HttpClient
from eventradar.pipeline.runner import _select_sources
from eventradar.storage.db import connect, migrate
from eventradar.storage.repositories import (
    RunRepository,
    SourceRunRow,
    SourceStateRepository,
)

START = datetime(2026, 10, 1, 13, tzinfo=UTC)


class FakeAlerter:
    """Records alert calls instead of sending them."""

    name = "fake"

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    async def raise_alert(
        self, key: str, title: str, body: str, http: HttpClient
    ) -> None:
        """
        Record a raised alert.

        Parameters:
          key: Alert key.
          title: Summary.
          body: Details.
          http: Unused.
        """
        if self.fail:
            raise RuntimeError("delivery down")
        self.calls.append(("raise", title))

    async def resolve(self, key: str, body: str, http: HttpClient) -> None:
        """
        Record a resolution.

        Parameters:
          key: Alert key.
          body: Closing note.
          http: Unused.
        """
        self.calls.append(("resolve", key))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """
    Open a migrated database.

    Parameters:
      tmp_path: Pytest temporary directory.
    Returns:
      Connection.
    """
    db = connect(tmp_path / "s.db")
    migrate(db)
    return db


def _bundle(tmp_path: Path) -> ConfigBundle:
    """
    Build a bundle with one source and fast SLOs.

    Parameters:
      tmp_path: Pytest temporary directory.
    Returns:
      Config bundle.
    """
    slo = SloSettings(alert_after_runs=2, disable_after_runs=4)
    source = SourceConfig(
        id="src", adapter="ical", params={"url": "https://x.test"}, slo=slo
    )
    settings = Settings(
        version=1,
        http=HttpSettings(user_agent="t"),
        environments={},
    )
    return ConfigBundle(
        root=tmp_path,
        settings=settings,
        sources={"src": source},
        profiles={},
        topics={},
        regions={},
    )


async def _day(
    conn: sqlite3.Connection,
    bundle: ConfigBundle,
    alerter: FakeAlerter,
    day: int,
    ok: bool,
    artifacts: Path | None = None,
) -> str:
    """
    Simulate one run of the source and update health.

    Parameters:
      conn: State database.
      bundle: Config.
      alerter: Fake alerter.
      day: Days after START.
      ok: Whether the fetch succeeds.
      artifacts: Drift capture directory.
    Returns:
      The source's status afterwards.
    """
    now = START + timedelta(days=day)
    run_id = f"run{day}"
    runs = RunRepository(conn)
    runs.start(run_id, now)
    row = SourceRunRow(
        run_id=run_id,
        source_id="src",
        started_at=now,
        finished_at=now,
        status="ok" if ok else "failed",
        fetched=100 if ok else 0,
        changed=0,
        parsed=0,
        parse_errors=0,
        error=None if ok else "HTTP 405",
    )
    runs.record_source(row)
    async with HttpClient(HttpSettings(user_agent="t")) as http:
        await update_health(conn, bundle, [row], now, alerter, http, artifacts)
    return SourceStateRepository(conn).get("src").status


async def test_lifecycle_alerts_disables_probes_and_recovers(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """
    One bad run is noise; repeats alert, then disable; recovery resolves.

    Parameters:
      conn: State database.
      tmp_path: Pytest temporary directory.
    """
    bundle, alerter = _bundle(tmp_path), FakeAlerter()
    assert await _day(conn, bundle, alerter, 0, ok=False) == "unhealthy"
    assert alerter.calls == []
    assert await _day(conn, bundle, alerter, 1, ok=False) == "unhealthy"
    assert alerter.calls == [("raise", "source unhealthy")]
    assert await _day(conn, bundle, alerter, 2, ok=False) == "unhealthy"
    assert len(alerter.calls) == 1
    assert await _day(conn, bundle, alerter, 3, ok=False) == "disabled"
    assert alerter.calls[-1] == ("raise", "source disabled")
    state = SourceStateRepository(conn).get("src")
    assert state.reason == "fetch failed: HTTP 405"
    assert not probe_due(state, START + timedelta(days=5))
    assert probe_due(state, START + timedelta(days=10))
    active, skipped = _select_sources(bundle, conn, START + timedelta(days=5))
    assert (active, skipped) == ([], ["src"])
    assert await _day(conn, bundle, alerter, 10, ok=True) == "healthy"
    assert alerter.calls[-1] == ("resolve", "src")
    assert not SourceStateRepository(conn).get("src").alerted


async def test_failed_delivery_is_retried_next_run(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """
    An alert that cannot be delivered is attempted again.

    Parameters:
      conn: State database.
      tmp_path: Pytest temporary directory.
    """
    bundle, alerter = _bundle(tmp_path), FakeAlerter()
    alerter.fail = True
    await _day(conn, bundle, alerter, 0, ok=False)
    await _day(conn, bundle, alerter, 1, ok=False)
    assert not SourceStateRepository(conn).get("src").alerted
    alerter.fail = False
    await _day(conn, bundle, alerter, 2, ok=False)
    assert alerter.calls == [("raise", "source unhealthy")]


async def test_unhealthy_runs_write_drift_capture(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """
    A capture file with metrics and reasons is written for triage.

    Parameters:
      conn: State database.
      tmp_path: Pytest temporary directory.
    """
    out = tmp_path / "artifacts"
    await _day(
        conn, _bundle(tmp_path), FakeAlerter(), 0, ok=False, artifacts=out
    )
    doc = json.loads((out / "src.json").read_text())
    assert doc["reasons"] == ["fetch failed: HTTP 405"]
    assert doc["adapter"] == "ical"
    assert doc["records"] == []
