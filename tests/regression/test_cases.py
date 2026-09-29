"""
Regression cases: one directory per fixed bug or source-drift event.

Layout of `tests/regression/<yyyy-mm-dd>-<slug>/`:
  case.yaml      `kind` (default `adapter`) plus kind-specific settings
  input/         inputs for the kind
  expected.json  reviewed output
  README.md      one-paragraph cause

Kinds:
  adapter  case.yaml: adapter, params (url is injected). input/*.body are
           upstream responses served in name order, one per fetch.
           Expected: per fetch, record count, changed ids, parsed drafts.
  robots   case.yaml: agent, paths. input/robots.txt is parsed for the
           agent. Expected: {path: allowed} for each path.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml

from eventradar.config.schema import HttpSettings, SourceConfig
from eventradar.http import HttpClient
from eventradar.http.robots import parse_robots
from eventradar.sources.base import SourceContext
from eventradar.sources.registry import build_source

CASES_DIR = Path(__file__).parent
FEED_URL = "https://regression.example.test/feed"
NOW = datetime(2026, 9, 26, tzinfo=UTC)
CASES = sorted(p.parent for p in CASES_DIR.glob("*/case.yaml"))


def _robots_case(case_dir: Path, case: dict[str, Any]) -> dict[str, bool]:
    """
    Decide each listed path against the case's robots.txt.

    Parameters:
      case_dir: Regression case directory.
      case: Parsed case.yaml.
    Returns:
      Path to allowed flag.
    """
    rules = parse_robots(
        (case_dir / "input" / "robots.txt").read_text(), case["agent"]
    )
    return {path: rules.allowed(path) for path in case["paths"]}


async def _adapter_case(
    case_dir: Path, case: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Fetch and parse each input body in order, tracking changes.

    Parameters:
      case_dir: Regression case directory.
      case: Parsed case.yaml.
    Returns:
      One summary per fetch.
    """
    params = {**case.get("params", {}), "url": FEED_URL}
    source = build_source(
        SourceConfig(id="regression", adapter=case["adapter"], params=params)
    )
    settings = HttpSettings(
        user_agent="test", per_host_min_interval_s=0, respect_robots=False
    )
    seen: dict[str, str] = {}
    fetches = []
    for body in sorted((case_dir / "input").glob("*.body")):
        with respx.mock:
            respx.get(FEED_URL).mock(
                return_value=httpx.Response(200, content=body.read_bytes())
            )
            async with HttpClient(settings) as http:
                records = await source.fetch(SourceContext(http=http, now=NOW))
        changed = sorted(
            r.native_id
            for r in records
            if seen.get(r.native_id) != r.content_hash
        )
        seen.update({r.native_id: r.content_hash for r in records})
        drafts = []
        for raw in sorted(records, key=lambda r: r.native_id):
            try:
                drafts.extend(
                    d.model_dump(mode="json") for d in source.parse(raw)
                )
            except Exception as exc:
                drafts.append(
                    {"native_id": raw.native_id, "error": type(exc).__name__}
                )
        fetches.append(
            {
                "input": body.name,
                "records": len(records),
                "changed": changed,
                "drafts": drafts,
            }
        )
    return fetches


@pytest.mark.parametrize("case_dir", CASES, ids=[c.name for c in CASES])
async def test_regression_case(case_dir: Path, update_golden: bool) -> None:
    """
    Current behavior matches the case's reviewed expectation.

    Parameters:
      case_dir: Regression case directory.
      update_golden: Rewrite the expectation instead of comparing.
    """
    case = yaml.safe_load((case_dir / "case.yaml").read_text())
    kind = case.get("kind", "adapter")
    if kind == "robots":
        actual: Any = _robots_case(case_dir, case)
    else:
        actual = await _adapter_case(case_dir, case)
    expected_path = case_dir / "expected.json"
    if update_golden:
        expected_path.write_text(json.dumps(actual, indent=2) + "\n")
    assert actual == json.loads(expected_path.read_text())
