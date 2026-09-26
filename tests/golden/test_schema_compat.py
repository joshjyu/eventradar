"""The published `/v1` Event schema may only change additively."""

import json
from pathlib import Path
from typing import Any

from eventradar.publishing.jsonschema import event_json_schema

FROZEN = Path(__file__).parent / "schema" / "v1" / "event.json"


def _without_docs(node: Any) -> Any:
    """
    Drop documentation-only keys so wording edits are not breaking.

    Parameters:
      node: Schema fragment.
    Returns:
      The fragment without `title`, `description`, or `examples`.
    """
    if isinstance(node, dict):
        return {
            k: _without_docs(v)
            for k, v in node.items()
            if k not in {"title", "description", "examples"}
        }
    if isinstance(node, list):
        return [_without_docs(v) for v in node]
    return node


def breaking_changes(
    frozen: dict[str, Any], current: dict[str, Any]
) -> list[str]:
    """
    List changes that would break `/v1` consumers.

    Parameters:
      frozen: Schema consumers were promised.
      current: Schema generated from code.
    Returns:
      Human-readable problems; empty when compatible.
    """
    problems = []
    cur_props = current.get("properties", {})
    for name, spec in frozen.get("properties", {}).items():
        if name not in cur_props:
            problems.append(f"property removed: {name}")
        elif _without_docs(spec) != _without_docs(cur_props[name]):
            problems.append(f"property changed: {name}")
    for name in set(frozen.get("required", [])) - set(
        current.get("required", [])
    ):
        problems.append(f"no longer required: {name}")
    cur_defs = current.get("$defs", {})
    for name, spec in frozen.get("$defs", {}).items():
        cur = cur_defs.get(name)
        if cur is None:
            problems.append(f"definition removed: {name}")
        elif "enum" in spec:
            missing = set(spec["enum"]) - set(cur.get("enum", []))
            problems.extend(f"enum value removed: {name}.{v}" for v in missing)
        elif _without_docs(spec) != _without_docs(cur):
            problems.append(f"definition changed: {name}")
    return problems


def test_v1_schema_is_backward_compatible() -> None:
    """Removing, retyping, or un-requiring a field requires `/v2`."""
    frozen = json.loads(FROZEN.read_text())
    assert breaking_changes(frozen, event_json_schema()) == []


def test_checker_flags_breaking_edits() -> None:
    """The checker itself catches removals, retypes, and enum shrinkage."""
    frozen = json.loads(FROZEN.read_text())
    current = json.loads(FROZEN.read_text())
    del current["properties"]["title"]
    current["properties"]["lat"] = {"type": "string"}
    current["$defs"]["AttendanceMode"]["enum"].remove("online")
    current["properties"]["extra"] = {"type": "string"}
    problems = breaking_changes(frozen, current)
    assert "property removed: title" in problems
    assert "property changed: lat" in problems
    assert "enum value removed: AttendanceMode.online" in problems
    assert not any("extra" in p for p in problems)
