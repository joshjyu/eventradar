"""Run orchestrator: lock, load state, run stages, publish, save state."""

import logging
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from ulid import ULID

from eventradar.config.schema import (
    ConfigBundle,
    EnvironmentSettings,
    ProfileConfig,
)
from eventradar.domain.ids import IdFactory, new_event_id
from eventradar.http import HttpClient
from eventradar.pipeline.fetch import fetch_all
from eventradar.pipeline.normalize import ingest, parse_and_upsert
from eventradar.pipeline.profile_filter import assign_profile
from eventradar.pipeline.publish import render_profile, upload
from eventradar.sources.base import SourceContext
from eventradar.sources.registry import build_source
from eventradar.storage.blob import BlobStore, build_blob
from eventradar.storage.db import check_integrity, connect, migrate
from eventradar.storage.lock import BlobLock
from eventradar.storage.repositories import (
    EventRepository,
    RawRecordRepository,
    RunRepository,
    SourceRunRow,
    transaction,
)

log = logging.getLogger(__name__)

STATE_KEY = "state/eventradar.db"
LOCK_KEY = "state/lock"
SNAPSHOT_PREFIX = "state/snapshots/"


@dataclass(frozen=True)
class RunOptions:
    """Knobs for a single run."""

    env: str = "local"
    publish: bool = True
    profile_ids: tuple[str, ...] | None = None
    now: datetime | None = None
    run_id: str | None = None


@dataclass
class RunSummary:
    """What a run did, for logs and exit codes."""

    run_id: str
    status: str
    sources: list[SourceRunRow] = field(default_factory=list)
    profiles: dict[str, dict[str, int]] = field(default_factory=dict)


def environment(bundle: ConfigBundle, name: str) -> EnvironmentSettings:
    """
    Look up a deployment environment by name.

    Parameters:
      bundle: Loaded config.
      name: Environment name, e.g. `local` or `production`.
    Returns:
      The environment's backend settings.
    """
    try:
        return bundle.settings.environments[name]
    except KeyError as exc:
        known = ", ".join(sorted(bundle.settings.environments))
        raise ValueError(f"unknown env '{name}' (known: {known})") from exc


def _profiles(
    bundle: ConfigBundle, ids: tuple[str, ...] | None
) -> list[ProfileConfig]:
    """
    Select the profiles a run should publish.

    Parameters:
      bundle: Loaded config.
      ids: Requested profile ids; all when None.
    Returns:
      Profiles in id order.
    """
    if ids is None:
        return [bundle.profiles[k] for k in sorted(bundle.profiles)]
    unknown = [i for i in ids if i not in bundle.profiles]
    if unknown:
        raise ValueError(f"unknown profile(s): {', '.join(unknown)}")
    return [bundle.profiles[i] for i in sorted(ids)]


@contextmanager
def open_state(
    store: BlobStore, owner: str, lock_ttl: timedelta
) -> Iterator[tuple[sqlite3.Connection, Path]]:
    """
    Lock, download, and migrate the state database.

    The caller saves changes with `save_state`; anything unsaved is
    discarded, so a failed run leaves stored state untouched.

    Parameters:
      store: State blob store.
      owner: Lock owner, usually the run id.
      lock_ttl: Lock expiry.
    Returns:
      A context manager yielding the connection and local file path.
    """
    lock = BlobLock(store, LOCK_KEY, owner, lock_ttl)
    lock.acquire()
    try:
        with tempfile.TemporaryDirectory(prefix="eventradar-") as tmp:
            path = Path(tmp) / "state.db"
            if (data := store.get(STATE_KEY)) is not None:
                path.write_bytes(data)
            conn = connect(path)
            try:
                applied = migrate(conn)
                if applied:
                    log.info("applied migrations: %s", ", ".join(applied))
                yield conn, path
            finally:
                conn.close()
    finally:
        lock.release()


def save_state(
    store: BlobStore,
    conn: sqlite3.Connection,
    path: Path,
    now: datetime,
    owner: str,
    retention: int,
) -> None:
    """
    Verify and upload the database, keeping timestamped snapshots.

    Parameters:
      store: State blob store.
      conn: Open connection to `path`.
      path: Local database file.
      now: Run time; names the snapshot.
      owner: Run or replay id; disambiguates same-second snapshots.
      retention: Number of snapshots to keep.
    """
    check_integrity(conn)
    data = path.read_bytes()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    store.put(f"{SNAPSHOT_PREFIX}{stamp}-{owner}.db", data)
    store.put(STATE_KEY, data)
    for old in store.list(SNAPSHOT_PREFIX)[:-retention]:
        store.delete(old)


def _status(rows: list[SourceRunRow]) -> str:
    """
    Summarize source outcomes into a run status.

    Parameters:
      rows: Per-source metrics.
    Returns:
      `ok`, `degraded`, or `failed`.
    """
    failed = sum(r.status != "ok" for r in rows)
    if rows and failed == len(rows):
        return "failed"
    return "degraded" if failed else "ok"


async def run(
    bundle: ConfigBundle,
    options: RunOptions,
    transport: httpx.AsyncBaseTransport | None = None,
    id_factory: IdFactory = new_event_id,
) -> RunSummary:
    """
    Execute one full pipeline run.

    Parameters:
      bundle: Loaded config.
      options: Run options.
      transport: Optional HTTP transport override, e.g. for tests.
      id_factory: Event id generator.
    Returns:
      The run summary.
    """
    env = environment(bundle, options.env)
    profiles = _profiles(bundle, options.profile_ids)
    now = (options.now or datetime.now(UTC)).astimezone(UTC)
    run_id = options.run_id or str(ULID())
    state = build_blob(env.state)
    public = (
        build_blob(env.publish) if options.publish and env.publish else None
    )
    ttl = timedelta(seconds=bundle.settings.run.lock_ttl_s)
    summary = RunSummary(run_id=run_id, status="running")
    with open_state(state, run_id, ttl) as (conn, path):
        runs = RunRepository(conn)
        events = EventRepository(conn, id_factory)
        runs.start(run_id, now)
        async with HttpClient(
            bundle.settings.http, transport=transport
        ) as http:
            ctx = SourceContext(
                http=http,
                now=now,
                previous=RawRecordRepository(conn).latest_one,
            )
            results = await fetch_all(bundle.enabled_sources(), ctx)
        summary.sources = [
            ingest(conn, result, run_id, now, events) for result in results
        ]
        artifacts = []
        with transaction(conn):
            for profile in profiles:
                assign_profile(bundle, profile, events, now)
        for profile in profiles:
            rendered, counts = render_profile(
                bundle, profile, events, run_id, now, summary.sources
            )
            artifacts.extend(rendered)
            summary.profiles[profile.id] = counts
        summary.status = _status(summary.sources)
        # Never replace good published data with the output of a run in
        # which every source failed.
        if public is not None and summary.status != "failed":
            upload(public, artifacts)
        runs.finish(run_id, datetime.now(UTC), summary.status)
        save_state(
            state,
            conn,
            path,
            now,
            run_id,
            bundle.settings.run.snapshot_retention,
        )
    return summary


def replay(
    bundle: ConfigBundle,
    env_name: str,
    source_ids: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """
    Reparse stored raw records without any network access.

    Parameters:
      bundle: Loaded config.
      env_name: Environment whose state is replayed.
      source_ids: Restrict to these sources; all configured when None.
      now: Observation time recorded on updated events.
    Returns:
      Drafts written per source.
    """
    env = environment(bundle, env_name)
    now = (now or datetime.now(UTC)).astimezone(UTC)
    ids = source_ids or tuple(sorted(bundle.sources))
    state = build_blob(env.state)
    ttl = timedelta(seconds=bundle.settings.run.lock_ttl_s)
    written: dict[str, int] = {}
    owner = f"replay-{ULID()}"
    with open_state(state, owner, ttl) as (conn, path):
        events = EventRepository(conn)
        raws = RawRecordRepository(conn)
        with transaction(conn):
            for source_id in ids:
                source = build_source(bundle.sources[source_id])
                counts = parse_and_upsert(
                    source, raws.latest([source_id]), events, now
                )
                written[source_id] = counts.parsed
        save_state(
            state,
            conn,
            path,
            now,
            owner,
            bundle.settings.run.snapshot_retention,
        )
    return written
