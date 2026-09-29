-- Geocoding results by normalized address; lat/lon NULL records a miss.
CREATE TABLE geocode_cache (
    address_key  TEXT PRIMARY KEY,
    lat          REAL,
    lon          REAL,
    precision    TEXT,
    provider     TEXT,
    looked_up_at TEXT NOT NULL
);

-- The source whose values an event's fields come from.
ALTER TABLE events ADD COLUMN primary_source TEXT;

UPDATE events SET primary_source = (
    SELECT source_id FROM event_sources s
    WHERE s.event_id = events.event_id
    ORDER BY source_id LIMIT 1
);

-- Ids retired by merges, pointing at the surviving event.
CREATE TABLE event_aliases (
    alias_id  TEXT PRIMARY KEY,
    event_id  TEXT NOT NULL,
    merged_at TEXT NOT NULL
);
