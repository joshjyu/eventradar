"""Run manifest: what was published and how each source fared."""

from collections.abc import Sequence
from datetime import datetime

from eventradar.config.schema import ProfileConfig
from eventradar.domain.models import SCHEMA_VERSION
from eventradar.publishing.artifacts import Artifact, dump_json, profile_key
from eventradar.storage.repositories import SourceRunRow


def manifest_artifact(
    profile: ProfileConfig,
    run_id: str,
    generated_at: datetime,
    counts: dict[str, int],
    source_rows: Sequence[SourceRunRow],
) -> Artifact:
    """
    Render `manifest.json` for a profile.

    Error details are omitted; they stay in the private state database.

    Parameters:
      profile: Profile being published.
      run_id: Run that produced the outputs.
      generated_at: Publication time.
      counts: Output counts, e.g. `upcoming` and `new`.
      source_rows: Metrics for the profile's sources.
    Returns:
      The manifest artifact.
    """
    doc = {
        "schema_version": SCHEMA_VERSION,
        "profile": {"id": profile.id, "name": profile.name},
        "run_id": run_id,
        "generated_at": generated_at.isoformat(),
        "counts": counts,
        "sources": [
            {
                "source_id": r.source_id,
                "status": r.status,
                "fetched": r.fetched,
                "changed": r.changed,
                "parsed": r.parsed,
                "parse_errors": r.parse_errors,
            }
            for r in source_rows
        ],
    }
    return Artifact(
        key=profile_key(profile.id, "manifest.json"),
        body=dump_json(doc),
        cache_control="no-cache",
    )
