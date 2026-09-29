"""Health state transitions, alerts, and drift capture after each run."""

import json
import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from eventradar.config.loader import expand_env
from eventradar.config.schema import (
    ConfigBundle,
    ProviderSettings,
    SloSettings,
)
from eventradar.health.alerts.base import Alerter
from eventradar.health.slo import Verdict, evaluate
from eventradar.http import HttpClient
from eventradar.plugins import ALERTER_GROUP, load_plugin
from eventradar.storage.repositories import (
    RawRecordRepository,
    RunRepository,
    SourceRunRow,
    SourceState,
    SourceStateRepository,
    transaction,
)

log = logging.getLogger(__name__)

PROBE_INTERVAL = timedelta(days=7)
SAMPLE_RECORDS = 10


@dataclass(frozen=True)
class HealthChange:
    """A source whose health status changed this run."""

    source_id: str
    before: str
    after: str
    reasons: tuple[str, ...]


def build_alerter(settings: ProviderSettings) -> Alerter:
    """
    Instantiate the configured alerter plugin.

    Parameters:
      settings: Alerter provider and options.
    Returns:
      Alerter.
    """
    provider = load_plugin(ALERTER_GROUP, settings.provider)
    return provider(**expand_env(settings.options))


def probe_due(state: SourceState, now: datetime) -> bool:
    """
    Decide whether a disabled source should be tried again.

    Parameters:
      state: Source state.
      now: Run time.
    Returns:
      True when never probed or last probed a week or more ago.
    """
    last = state.last_probe or state.since
    return last is None or now - last >= PROBE_INTERVAL


def transition(
    state: SourceState,
    verdict: Verdict,
    slo: SloSettings,
    now: datetime,
) -> SourceState:
    """
    Compute the next state from this run's verdict.

    Parameters:
      state: Current state.
      verdict: This run's verdict.
      slo: Thresholds.
      now: Run time.
    Returns:
      The next state; `alerted` is left for the caller to settle.
    """
    probed = state.last_probe
    if state.status == "disabled":
        probed = now
    if verdict.healthy:
        return SourceState(
            source_id=state.source_id,
            status="healthy",
            alerted=state.alerted,
            last_probe=probed,
        )
    runs = state.unhealthy_runs + 1
    return SourceState(
        source_id=state.source_id,
        status="disabled" if runs >= slo.disable_after_runs else "unhealthy",
        reason="; ".join(verdict.reasons),
        unhealthy_runs=runs,
        alerted=state.alerted,
        since=state.since or now,
        last_probe=probed,
    )


def _body(row: SourceRunRow, state: SourceState, slo: SloSettings) -> str:
    """
    Describe a problem for an alert.

    Parameters:
      row: This run's metrics.
      state: New state.
      slo: Thresholds.
    Returns:
      Markdown body.
    """
    lines = [
        f"Source `{row.source_id}` has been unhealthy for "
        f"{state.unhealthy_runs} consecutive runs.",
        "",
        f"- Reasons: {state.reason}",
        f"- This run: fetched {row.fetched}, changed {row.changed}, "
        f"parsed {row.parsed}, parse errors {row.parse_errors}",
        f"- Status: **{state.status}** (disables after "
        f"{slo.disable_after_runs} runs; disabled sources are retried "
        "weekly)",
        "",
        "The run's artifacts include sample records for "
        "`eventradar regression capture`.",
    ]
    return "\n".join(lines)


async def _notify(
    alerter: Alerter,
    http: HttpClient,
    row: SourceRunRow,
    before: SourceState,
    after: SourceState,
    verdict: Verdict,
    slo: SloSettings,
) -> SourceState:
    """
    Raise or resolve alerts for one source; never raises.

    Parameters:
      alerter: Alert delivery.
      http: Shared HTTP client.
      row: This run's metrics.
      before: Previous state.
      after: Next state.
      verdict: This run's verdict.
      slo: Thresholds.
    Returns:
      `after` with `alerted` settled.
    """
    key = row.source_id
    try:
        if verdict.healthy and before.alerted:
            await alerter.resolve(
                key, f"Recovered: `{key}` passed its checks again.", http
            )
            return replace(after, alerted=False)
        newly_disabled = (
            after.status == "disabled" and before.status != "disabled"
        )
        due = after.unhealthy_runs >= slo.alert_after_runs
        if not verdict.healthy and (
            (due and not after.alerted) or newly_disabled
        ):
            title = "disabled" if after.status == "disabled" else "unhealthy"
            await alerter.raise_alert(
                key, f"source {title}", _body(row, after, slo), http
            )
            return replace(after, alerted=True)
    except Exception:
        log.exception("alert delivery failed for %s; will retry", key)
    return after


def _capture(
    directory: Path,
    bundle: ConfigBundle,
    row: SourceRunRow,
    verdict: Verdict,
    raws: RawRecordRepository,
) -> None:
    """
    Write an unhealthy run's details and sample records for later triage.

    Parameters:
      directory: Artifact directory.
      bundle: Loaded config.
      row: This run's metrics.
      verdict: This run's verdict.
      raws: Raw record repository.
    """
    config = bundle.sources[row.source_id]
    records = raws.for_run(row.source_id, row.run_id, SAMPLE_RECORDS)
    doc = {
        "source_id": row.source_id,
        "run_id": row.run_id,
        "adapter": config.adapter,
        "params": config.params,
        "reasons": list(verdict.reasons),
        "metrics": {
            "fetched": row.fetched,
            "changed": row.changed,
            "parsed": row.parsed,
            "parse_errors": row.parse_errors,
            "error": row.error,
        },
        "records": [r.model_dump(mode="json") for r in records],
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{row.source_id}.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


async def update_health(
    conn: sqlite3.Connection,
    bundle: ConfigBundle,
    rows: list[SourceRunRow],
    now: datetime,
    alerter: Alerter,
    http: HttpClient,
    artifact_dir: Path | None = None,
) -> list[HealthChange]:
    """
    Judge each fetched source, update its state, and send alerts.

    Parameters:
      conn: State database.
      bundle: Loaded config.
      rows: This run's per-source metrics.
      now: Run time.
      alerter: Alert delivery.
      http: Shared HTTP client.
      artifact_dir: Where to write drift captures; skipped when None.
    Returns:
      Sources whose status changed.
    """
    states = SourceStateRepository(conn)
    runs = RunRepository(conn)
    raws = RawRecordRepository(conn)
    changes: list[HealthChange] = []
    updates: list[SourceState] = []
    for row in rows:
        config = bundle.sources.get(row.source_id)
        if row.status == "disabled" or config is None:
            continue
        slo = config.slo
        since = now - timedelta(days=slo.window_days)
        verdict = evaluate(
            row, runs.history(row.source_id, since, row.run_id), slo
        )
        before = states.get(row.source_id)
        after = transition(before, verdict, slo, now)
        after = await _notify(alerter, http, row, before, after, verdict, slo)
        updates.append(after)
        if not verdict.healthy and artifact_dir is not None:
            _capture(artifact_dir, bundle, row, verdict, raws)
        if after.status != before.status:
            changes.append(
                HealthChange(
                    row.source_id, before.status, after.status, verdict.reasons
                )
            )
    with transaction(conn):
        for state in updates:
            states.put(state, now)
    return changes
