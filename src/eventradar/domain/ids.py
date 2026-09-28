"""Identifier and content-hash helpers."""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from ulid import ULID

type IdFactory = Callable[[], str]


def new_event_id() -> str:
    """
    Create a new, time-sortable event identifier.

    Returns:
      A 26-character ULID string.
    """
    return str(ULID())


def record_key(source_id: str, native_id: str) -> str:
    """
    Build the globally unique key for a source record.

    Parameters:
      source_id: Configured source id.
      native_id: Identifier assigned by the upstream platform.
    Returns:
      The key formatted as `{source_id}:{native_id}`.
    """
    return f"{source_id}:{native_id}"


def content_hash(payload: dict[str, Any]) -> str:
    """
    Hash a payload deterministically, independent of key order.

    Parameters:
      payload: JSON-serializable mapping.
    Returns:
      Hex-encoded SHA-256 digest.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
