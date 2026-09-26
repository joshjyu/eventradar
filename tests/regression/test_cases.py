"""
Regression cases: one directory per fixed bug or source-drift event.

Layout of `tests/regression/<yyyy-mm-dd>-<slug>/`:
  case.yaml      adapter, params (url is injected), optional issue link
  input/*.body   upstream responses, served in name order, one per fetch
  expected.json  per fetch: record count, changed ids, parsed drafts
  README.md      one-paragraph cause
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
from eventradar.sources.base import SourceContext
from eventradar.sources.registry import build_source

CASES_DIR = Path(__file__).parent
FEED_URL = "https://regression.example.test/feed"
NOW = datetime(2026, 9, 26, tzinfo=UTC)
CASES = sorted(p.parent for p in CASES_DIR.glob("*/case.yaml"))


async def _replay_case(case_dir: Path) -> list[dict[str, Any]]:
    """
    Fetch and parse each input body in order, tracking changes.

    Parameters:
      case_dir: Regression case directory.
    Returns:
      One summary per fetch.
    """
    case = yaml.safe_load((case_dir / "case.yaml").read_text())
    params = {**case.get("params", {}), "url": FEED_URL}
    source = build_source(
        SourceConfig(id="regression", adapter=case["adapter"], params=params)
    )
    settings = HttpSettings(user_agent="test", per_host_min_interval_s=0)
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
    actual = await _replay_case(case_dir)
    expected_path = case_dir / "expected.json"
    if update_golden:
        expected_path.write_text(json.dumps(actual, indent=2) + "\n")
    assert actual == json.loads(expected_path.read_text())
