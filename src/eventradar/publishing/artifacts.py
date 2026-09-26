"""Artifact type and deterministic JSON encoding."""

import json
from dataclasses import dataclass
from typing import Any

API_VERSION = "v1"
JSON_TYPE = "application/json; charset=utf-8"


@dataclass(frozen=True)
class Artifact:
    """One published object."""

    key: str
    body: bytes
    content_type: str = JSON_TYPE
    cache_control: str = "public, max-age=300"


def dump_json(value: Any) -> bytes:
    """
    Encode JSON deterministically so identical data yields identical bytes.

    Parameters:
      value: JSON-serializable value.
    Returns:
      UTF-8 bytes with sorted keys and a trailing newline.
    """
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)
    return (text + "\n").encode()


def profile_key(profile_id: str, name: str) -> str:
    """
    Build a versioned key under a profile.

    Parameters:
      profile_id: Profile id.
      name: Path below the profile, e.g. `events.json`.
    Returns:
      Blob key.
    """
    return f"{API_VERSION}/{profile_id}/{name}"
