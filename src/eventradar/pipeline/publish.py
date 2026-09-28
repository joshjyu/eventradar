"""Publish stage: render and upload each profile's outputs."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from eventradar.config.schema import ConfigBundle, ProfileConfig
from eventradar.publishing.artifacts import Artifact
from eventradar.publishing.json_feed import feed_artifacts
from eventradar.publishing.jsonschema import schema_artifact
from eventradar.publishing.manifest import manifest_artifact
from eventradar.storage.blob import BlobStore
from eventradar.storage.repositories import EventRepository, SourceRunRow


def render_profile(
    bundle: ConfigBundle,
    profile: ProfileConfig,
    events: EventRepository,
    run_id: str,
    now: datetime,
    source_rows: Sequence[SourceRunRow],
) -> tuple[list[Artifact], dict[str, int]]:
    """
    Render every artifact for one profile.

    Parameters:
      bundle: Loaded config.
      profile: Profile to render.
      events: Event repository.
      run_id: Current run.
      now: Publication time; its UTC date selects the daily delta.
      source_rows: Metrics from this run.
    Returns:
      Artifacts to upload and the published counts.
    """
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    upcoming = events.upcoming(profile.id, now)
    added = events.added_between(
        profile.id, day_start, day_start + timedelta(days=1), now
    )
    wanted = bundle.profile_sources(profile)
    rows = [r for r in source_rows if r.source_id in wanted]
    counts = {"upcoming": len(upcoming), "new": len(added)}
    artifacts = [
        *feed_artifacts(profile, upcoming, added, day_start.date(), now),
        manifest_artifact(profile, run_id, now, counts, rows),
    ]
    return artifacts, counts


def upload(store: BlobStore, artifacts: Sequence[Artifact]) -> None:
    """
    Write artifacts, schema first and manifests last.

    Readers treat the manifest as the commit marker for a run.

    Parameters:
      store: Public blob store.
      artifacts: Artifacts to write.
    """
    ordered = sorted(
        [schema_artifact(), *artifacts],
        key=lambda a: (a.key.endswith("manifest.json"), a.key),
    )
    for art in ordered:
        store.put(art.key, art.body, art.content_type, art.cache_control)
