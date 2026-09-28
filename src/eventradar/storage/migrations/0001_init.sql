-- Timestamps are ISO 8601 strings in UTC; they sort lexically.

CREATE TABLE runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL
);

CREATE TABLE raw_records (
    source_id    TEXT NOT NULL,
    native_id    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    url          TEXT,
    payload      TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    run_id       TEXT NOT NULL REFERENCES runs (run_id),
    PRIMARY KEY (source_id, native_id, content_hash)
);

CREATE INDEX raw_records_latest
    ON raw_records (source_id, native_id, fetched_at);

CREATE TABLE events (
    event_id        TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    start_utc       TEXT NOT NULL,
    end_utc         TEXT,
    tz              TEXT,
    venue           TEXT,
    address         TEXT,
    lat             REAL,
    lon             REAL,
    attendance_mode TEXT NOT NULL,
    status          TEXT NOT NULL,
    organizer       TEXT,
    url             TEXT,
    kinds           TEXT NOT NULL,
    size_signal     INTEGER,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);

CREATE INDEX events_start ON events (start_utc);

CREATE TABLE event_sources (
    source_id    TEXT NOT NULL,
    native_id    TEXT NOT NULL,
    event_id     TEXT NOT NULL REFERENCES events (event_id),
    url          TEXT,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (source_id, native_id)
);

CREATE INDEX event_sources_event ON event_sources (event_id);

CREATE TABLE event_profiles (
    event_id   TEXT NOT NULL REFERENCES events (event_id),
    profile_id TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (event_id, profile_id)
);

CREATE INDEX event_profiles_new ON event_profiles (profile_id, first_seen);

CREATE TABLE source_runs (
    run_id        TEXT NOT NULL REFERENCES runs (run_id),
    source_id     TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    status        TEXT NOT NULL,
    fetched       INTEGER NOT NULL,
    changed       INTEGER NOT NULL,
    parsed        INTEGER NOT NULL,
    parse_errors  INTEGER NOT NULL,
    error         TEXT,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE source_state (
    source_id  TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    reason     TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE http_cache (
    url           TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    fetched_at    TEXT NOT NULL
);
