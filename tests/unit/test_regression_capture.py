"""Tests for scaffolding regression cases from health captures."""

import json
from pathlib import Path

import yaml

from eventradar.regression import capture_case, parse_records

VEVENT = (
    "BEGIN:VEVENT\r\nUID:u1\r\nSUMMARY:Hack Night\r\n"
    "DTSTART:20261015T010000Z\r\nEND:VEVENT\r\n"
)
BROKEN = "BEGIN:VEVENT\r\nUID:u2\r\nDTSTART:20261015T010000Z\r\nEND:VEVENT\r\n"


def _record(native_id: str, vevent: str) -> dict[str, object]:
    """
    Build a serialized iCal raw record.

    Parameters:
      native_id: Record id.
      vevent: VEVENT text.
    Returns:
      Record dict.
    """
    return {
        "source_id": "s",
        "native_id": native_id,
        "url": "https://x.test/a.ics",
        "payload": {"vevent": vevent, "vtimezones": [], "calendar_tz": None},
        "fetched_at": "2026-10-01T00:00:00Z",
    }


def test_capture_writes_runnable_parse_case(tmp_path: Path) -> None:
    """
    The scaffold holds config, inputs, and today's parse output.

    Parameters:
      tmp_path: Pytest temporary directory.
    """
    capture = tmp_path / "s.json"
    params = {"url": "https://x.test/a.ics", "default_tz": "UTC"}
    capture.write_text(
        json.dumps(
            {
                "source_id": "s",
                "run_id": "r1",
                "adapter": "ical",
                "params": params,
                "reasons": ["1 of 2 changed records failed to parse"],
                "records": [_record("u2", BROKEN), _record("u1", VEVENT)],
            }
        )
    )
    case = capture_case(capture, "missing-summary", tmp_path / "cases")
    assert case.name.endswith("-missing-summary")
    config = yaml.safe_load((case / "case.yaml").read_text())
    assert config == {"kind": "parse", "adapter": "ical", "params": params}
    expected = json.loads((case / "expected.json").read_text())
    assert [e["native_id"] for e in expected] == ["u1", "u2"]
    assert expected[0]["drafts"][0]["title"] == "Hack Night"
    assert expected[1] == {"native_id": "u2", "error": "ParseError"}
    records = json.loads((case / "input/records.json").read_text())
    assert parse_records("ical", params, records) == expected
    assert "missing-summary" in (case / "README.md").read_text()
