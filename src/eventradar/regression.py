"""Turn captured raw records into regression cases and replay them."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from eventradar.config.schema import SourceConfig
from eventradar.domain.models import RawRecord
from eventradar.sources.registry import build_source


def parse_records(
    adapter: str, params: dict[str, Any], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Parse stored records the way the pipeline would.

    Parameters:
      adapter: Registered adapter name.
      params: Adapter params.
      records: Serialized raw records.
    Returns:
      Per record, in native id order: its drafts, or the error type.
    """
    config = SourceConfig(id="regression", adapter=adapter, params=params)
    source = build_source(config)
    out: list[dict[str, Any]] = []
    raws = sorted(
        (RawRecord.model_validate(r) for r in records),
        key=lambda r: r.native_id,
    )
    for raw in raws:
        try:
            drafts = [d.model_dump(mode="json") for d in source.parse(raw)]
            out.append({"native_id": raw.native_id, "drafts": drafts})
        except Exception as exc:
            out.append(
                {"native_id": raw.native_id, "error": type(exc).__name__}
            )
    return out


def capture_case(capture: Path, slug: str, out_dir: Path) -> Path:
    """
    Write a `parse` regression case from a health capture file.

    `expected.json` records today's output; edit it to the correct output,
    confirm the test fails, then fix the adapter.

    Parameters:
      capture: Capture JSON written by the health stage.
      slug: Short kebab-case name for the case.
      out_dir: Regression cases directory.
    Returns:
      The new case directory.
    """
    doc = json.loads(capture.read_text())
    stamp = datetime.now(UTC).date().isoformat()
    case = out_dir / f"{stamp}-{slug}"
    (case / "input").mkdir(parents=True, exist_ok=False)
    (case / "case.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "parse",
                "adapter": doc["adapter"],
                "params": doc["params"],
            },
            sort_keys=False,
        )
    )
    (case / "input" / "records.json").write_text(
        json.dumps(doc["records"], indent=2, sort_keys=True) + "\n"
    )
    current = parse_records(doc["adapter"], doc["params"], doc["records"])
    (case / "expected.json").write_text(json.dumps(current, indent=2) + "\n")
    reasons = "; ".join(doc.get("reasons", []))
    (case / "README.md").write_text(
        f"# {slug}\n\n"
        f"Captured from source `{doc['source_id']}`, run `{doc['run_id']}`: "
        f"{reasons}.\n\n"
        "TODO: describe the cause, correct `expected.json`, and confirm the "
        "case fails before the fix.\n"
    )
    return case
