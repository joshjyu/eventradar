"""Judge one source run against its SLOs."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import median

from eventradar.config.schema import SloSettings
from eventradar.storage.repositories import SourceRunRow


@dataclass(frozen=True)
class Verdict:
    """Whether a run met its SLOs, and why not."""

    healthy: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def evaluate(
    row: SourceRunRow, history: Sequence[SourceRunRow], slo: SloSettings
) -> Verdict:
    """
    Check a run for fetch failure, parse errors, and a yield collapse.

    Parameters:
      row: This run's metrics.
      history: Earlier runs of the same source within the window.
      slo: Thresholds.
    Returns:
      The verdict.
    """
    reasons: list[str] = []
    if row.status != "ok":
        reasons.append(f"fetch failed: {row.error or row.status}")
    attempted = row.parsed + row.parse_errors
    if attempted and row.parse_errors / attempted > slo.max_parse_error_ratio:
        reasons.append(
            f"{row.parse_errors} of {attempted} changed records failed to parse"
        )
    baseline = [r.fetched for r in history if r.status == "ok"]
    if row.status == "ok" and len(baseline) >= slo.min_history_runs:
        typical = median(baseline)
        if typical and row.fetched < slo.min_yield_ratio * typical:
            reasons.append(
                f"fetched {row.fetched} records, typical is {typical:g}"
            )
    return Verdict(healthy=not reasons, reasons=tuple(reasons))
