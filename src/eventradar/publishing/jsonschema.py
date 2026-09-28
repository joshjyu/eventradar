"""Published JSON Schema for the versioned `Event` contract."""

from typing import Any

from eventradar.domain.models import Event
from eventradar.publishing.artifacts import API_VERSION, Artifact, dump_json


def event_json_schema() -> dict[str, Any]:
    """
    Generate the JSON Schema for serialized events.

    Returns:
      JSON Schema document.
    """
    return Event.model_json_schema(mode="serialization")


def schema_artifact() -> Artifact:
    """
    Render `/v1/schema/event.json`.

    Returns:
      The schema artifact.
    """
    return Artifact(
        key=f"{API_VERSION}/schema/event.json",
        body=dump_json(event_json_schema()),
        cache_control="public, max-age=3600",
    )
