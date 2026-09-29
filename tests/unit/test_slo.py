"""Tests for SLO evaluation."""

from datetime import UTC, datetime

from eventradar.config.schema import SloSettings
from eventradar.health.slo import evaluate
from eventradar.storage.repositories import SourceRunRow

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SLO = SloSettings()


def _row(**overrides: object) -> SourceRunRow:
    """
    Build a run row with healthy defaults.

    Parameters:
      overrides: Field overrides.
    Returns:
      Row.
    """
    fields: dict[str, object] = {
        "run_id": "r",
        "source_id": "s",
        "started_at": NOW,
        "finished_at": NOW,
        "status": "ok",
        "fetched": 100,
        "changed": 10,
        "parsed": 10,
        "parse_errors": 0,
    }
    fields.update(overrides)
    return SourceRunRow(**fields)  # type: ignore[arg-type]


def test_healthy_run() -> None:
    """A normal run passes."""
    assert evaluate(_row(), [_row()] * 5, SLO).healthy


def test_fetch_failure() -> None:
    """A failed fetch is unhealthy and says why."""
    verdict = evaluate(_row(status="failed", error="HTTP 405"), [], SLO)
    assert not verdict.healthy
    assert verdict.reasons == ("fetch failed: HTTP 405",)


def test_parse_error_ratio() -> None:
    """Most changed records failing to parse is unhealthy."""
    verdict = evaluate(_row(parsed=2, parse_errors=8), [], SLO)
    assert not verdict.healthy
    assert "8 of 10" in verdict.reasons[0]
    assert evaluate(_row(parsed=6, parse_errors=4), [], SLO).healthy


def test_yield_collapse_needs_history() -> None:
    """A collapse counts only against enough successful history."""
    collapse = _row(fetched=5)
    assert evaluate(collapse, [_row()] * 2, SLO).healthy
    failed_runs = [_row(status="failed", fetched=0)] * 5
    assert evaluate(collapse, failed_runs, SLO).healthy
    verdict = evaluate(collapse, [_row()] * 3, SLO)
    assert not verdict.healthy
    assert "typical is 100" in verdict.reasons[0]
    assert evaluate(_row(fetched=40), [_row()] * 3, SLO).healthy
