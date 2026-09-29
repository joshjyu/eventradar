"""Duplicate detection meets quality floors on the labeled corpus."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from eventradar.resolve.match import Thresholds, is_duplicate
from eventradar.storage.repositories import ResolveCandidate

CORPUS = yaml.safe_load(
    (Path(__file__).parent / "dedup_pairs.yaml").read_text()
)


def _candidate(event_id: str, fields: dict[str, Any]) -> ResolveCandidate:
    """
    Build a candidate from a corpus entry.

    Parameters:
      event_id: Id to assign.
      fields: title, start, and optional lat, lon, url, approximate.
    Returns:
      Candidate.
    """
    return ResolveCandidate(
        event_id=event_id,
        title=fields["title"],
        start_utc=datetime.fromisoformat(fields["start"]),
        lat=fields.get("lat"),
        lon=fields.get("lon"),
        url=fields.get("url"),
        approximate=fields.get("approximate", False),
    )


def _decisions() -> list[tuple[str, bool, bool]]:
    """
    Run the matcher over every pair.

    Returns:
      (note, expected, predicted) per pair.
    """
    limits = Thresholds()
    return [
        (
            p["note"],
            p["same"],
            is_duplicate(
                _candidate("a", p["a"]), _candidate("b", p["b"]), limits
            ),
        )
        for p in CORPUS["pairs"]
    ]


def test_precision_and_recall_floors() -> None:
    """False merges are rarer than missed duplicates, and both are rare."""
    decisions = _decisions()
    tp = sum(e and p for _, e, p in decisions)
    fp = sum(p and not e for _, e, p in decisions)
    fn = sum(e and not p for _, e, p in decisions)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    wrong = [n for n, e, p in decisions if e != p]
    assert precision >= CORPUS["floors"]["precision"], wrong
    assert recall >= CORPUS["floors"]["recall"], wrong


@pytest.mark.parametrize(
    "pair",
    [p for p in CORPUS["pairs"] if not p["same"]],
    ids=lambda p: p["note"],
)
def test_no_false_merges(pair: dict[str, Any]) -> None:
    """
    Every labeled non-duplicate stays separate.

    Parameters:
      pair: Corpus entry.
    """
    a, b = _candidate("a", pair["a"]), _candidate("b", pair["b"])
    assert not is_duplicate(a, b, Thresholds())
