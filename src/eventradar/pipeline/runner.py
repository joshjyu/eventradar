"""Run orchestrator: lock, load state, run stages, publish, save state."""

import logging
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from ulid import ULID

from eventradar.classify.kinds import KindClassifier
from eventradar.config.schema import (
    ConfigBundle,
    EnvironmentSettings,
    ProfileConfig,
    SourceConfig,
)
from eventradar.domain.ids import IdFactory, new_event_id
from eventradar.geo.region import Region, load_region
from eventradar.health.monitor import (
    HealthChange,
    build_alerter,
    probe_due,
    update_health,
)
from eventradar.http import HttpClient
from eventradar.pipeline.classify import classify_kinds
from eventradar.pipeline.enrich import EnrichStats, build_geocoder, enrich
from eventradar.pipeline.fetch import fetch_all
from eventradar.pipeline.normalize import ingest, parse_and_upsert
from eventradar.pipeline.profile_filter import assign_profile
from eventradar.pipeline.publish import render_profile, upload
from eventradar.pipeline.resolve import resolve
from eventradar.sources.base import SourceContext
from eventradar.sources.registry import build_source
from eventradar.storage.blob import BlobStore, build_blob
from eventradar.storage.db import check_integrity, connect, migrate
from eventradar.storage.lock import BlobLock
from eventradar.storage.repositories import (
    EventRepository,
    MaintenanceRepository,
    RawRecordRepository,
    RunRepository,
    SourceRunRow,
    SourceState,
    SourceStateRepository,
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
    enrich: EnrichStats | None = None
    merged: int = 0
    health: list[HealthChange] = field(default_factory=list)


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


def _compact(conn: sqlite3.Connection, now: datetime, days: int) -> None:
    """
    Drop old superseded raw versions and shrink the database file.

    Parameters:
      conn: State database.
      now: Run time.
      days: History to keep for superseded versions.
    """
    deleted = RawRecordRepository(conn).prune(now - timedelta(days=days))
    if deleted:
        log.info("pruned %d superseded raw records", deleted)
        conn.execute("VACUUM")


def _status(rows: list[SourceRunRow]) -> str:
    """
    Summarize source outcomes into a run status.

    Sources skipped because health checks disabled them do not count.

    Parameters:
      rows: Per-source metrics.
    Returns:
      `ok`, `degraded`, or `failed`.
    """
    active = [r for r in rows if r.status != "disabled"]
    failed = sum(r.status != "ok" for r in active)
    if active and failed == len(active):
        return "failed"
    return "degraded" if failed else "ok"


def _reparse(
    bundle: ConfigBundle,
    conn: sqlite3.Connection,
    events: EventRepository,
    source_ids: tuple[str, ...],
    now: datetime,
) -> dict[str, int]:
    """
    Parse every source's latest stored records again, without fetching.

    Parameters:
      bundle: Loaded config.
      conn: State database.
      events: Event repository.
      source_ids: Sources to reparse.
      now: Observation time recorded on updated events.
    Returns:
      Drafts written per source.
    """
    raws = RawRecordRepository(conn)
    written: dict[str, int] = {}
    with transaction(conn):
        for source_id in source_ids:
            config = bundle.sources[source_id]
            counts = parse_and_upsert(
                build_source(config),
                raws.latest([source_id]),
                events,
                now,
                _keep_region(bundle, config),
            )
            written[source_id] = counts.parsed
    return written


def _run_maintenance(
    bundle: ConfigBundle,
    conn: sqlite3.Connection,
    events: EventRepository,
    now: datetime,
) -> None:
    """
    Perform one-off tasks that migrations requested.

    Parameters:
      bundle: Loaded config.
      conn: State database.
      events: Event repository.
      now: Run time.
    """
    tasks = MaintenanceRepository(conn)
    for task in tasks.pending():
        if task == "reparse":
            written = _reparse(
                bundle, conn, events, tuple(sorted(bundle.sources)), now
            )
            log.info("maintenance reparse: %s", written)
        else:
            log.warning("unknown maintenance task %r; leaving it", task)
            continue
        tasks.complete(task)


def _keep_region(bundle: ConfigBundle, source: SourceConfig) -> Region | None:
    """
    Load a source's ingest region, if it has one.

    Parameters:
      bundle: Loaded config.
      source: Source config.
    Returns:
      The region, or None.
    """
    if source.keep_region is None:
        return None
    return load_region(bundle.regions[source.keep_region])


def _select_sources(
    bundle: ConfigBundle, conn: sqlite3.Connection, now: datetime
) -> tuple[list[SourceConfig], list[str]]:
    """
    Split enabled sources into those to fetch and those health disabled.

    A disabled source is fetched again when its weekly probe is due.

    Parameters:
      bundle: Loaded config.
      conn: State database.
      now: Run time.
    Returns:
      (sources to fetch, ids of skipped sources).
    """
    states = SourceStateRepository(conn)
    active: list[SourceConfig] = []
    skipped: list[str] = []
    for source in bundle.enabled_sources():
        state = states.get(source.id)
        if state.status == "disabled" and not probe_due(state, now):
            skipped.append(source.id)
        else:
            active.append(source)
    return active, skipped


def _record_disabled(
    runs: RunRepository, run_id: str, source_id: str, now: datetime
) -> SourceRunRow:
    """
    Record that a disabled source was skipped this run.

    Parameters:
      runs: Run repository.
      run_id: Current run.
      source_id: Skipped source.
      now: Run time.
    Returns:
      The recorded row.
    """
    row = SourceRunRow(
        run_id=run_id,
        source_id=source_id,
        started_at=now,
        finished_at=now,
        status="disabled",
        fetched=0,
        changed=0,
        parsed=0,
        parse_errors=0,
    )
    runs.record_source(row)
    return row


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
        priorities = {sid: s.priority for sid, s in bundle.sources.items()}
        events = EventRepository(conn, id_factory, priorities)
        runs.start(run_id, now)
        _run_maintenance(bundle, conn, events, now)
        async with HttpClient(
            bundle.settings.http, transport=transport
        ) as http:
            ctx = SourceContext(
                http=http,
                now=now,
                previous=RawRecordRepository(conn).latest_one,
            )
            active, skipped = _select_sources(bundle, conn, now)
            results = await fetch_all(active, ctx)
            summary.sources = [
                ingest(
                    conn,
                    result,
                    run_id,
                    now,
                    events,
                    _keep_region(bundle, result.config),
                )
                for result in results
            ]
            summary.sources += [
                _record_disabled(runs, run_id, source_id, now)
                for source_id in skipped
            ]
            geo = bundle.settings.geo
            summary.enrich = await enrich(
                conn, events, build_geocoder(geo), http, now, geo
            )
            artifact_dir = os.environ.get("EVENTRADAR_ARTIFACT_DIR")
            summary.health = await update_health(
                conn,
                bundle,
                summary.sources,
                now,
                build_alerter(env.alerts),
                http,
                Path(artifact_dir) if artifact_dir else None,
            )
        summary.merged = resolve(conn, events, now)
        defaults = {i: s.default_kinds for i, s in bundle.sources.items()}
        classify_kinds(
            conn, events, KindClassifier(bundle.kinds), now, defaults
        )
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
        _compact(conn, now, bundle.settings.run.raw_history_days)
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
    owner = f"replay-{ULID()}"
    with open_state(state, owner, ttl) as (conn, path):
        priorities = {sid: s.priority for sid, s in bundle.sources.items()}
        events = EventRepository(conn, priorities=priorities)
        written = _reparse(bundle, conn, events, ids, now)
        save_state(
            state,
            conn,
            path,
            now,
            owner,
            bundle.settings.run.snapshot_retention,
        )
    return written


def source_health(bundle: ConfigBundle, env_name: str) -> list[SourceState]:
    """
    Read every configured source's health state.

    Parameters:
      bundle: Loaded config.
      env_name: Environment whose state is read.
    Returns:
      States in source id order.
    """
    env = environment(bundle, env_name)
    state = build_blob(env.state)
    ttl = timedelta(seconds=bundle.settings.run.lock_ttl_s)
    with open_state(state, f"health-{ULID()}", ttl) as (conn, _path):
        states = SourceStateRepository(conn)
        return [states.get(source_id) for source_id in sorted(bundle.sources)]


def enable_source(
    bundle: ConfigBundle,
    env_name: str,
    source_id: str,
    now: datetime | None = None,
) -> SourceState:
    """
    Mark a source healthy again, e.g. after fixing its adapter.

    Its open alert is closed by the next run that passes its checks.

    Parameters:
      bundle: Loaded config.
      env_name: Environment whose state is changed.
      source_id: Source to re-enable.
      now: Change time.
    Returns:
      The new state.
    """
    env = environment(bundle, env_name)
    now = (now or datetime.now(UTC)).astimezone(UTC)
    state = build_blob(env.state)
    ttl = timedelta(seconds=bundle.settings.run.lock_ttl_s)
    owner = f"enable-{ULID()}"
    with open_state(state, owner, ttl) as (conn, path):
        states = SourceStateRepository(conn)
        current = states.get(source_id)
        updated = SourceState(
            source_id=source_id,
            status="healthy",
            alerted=current.alerted,
            last_probe=current.last_probe,
        )
        with transaction(conn):
            states.put(updated, now)
        save_state(
            state,
            conn,
            path,
            now,
            owner,
            bundle.settings.run.snapshot_retention,
        )
    return updated
