-- Supports matching the same event page reported by several sources.
CREATE INDEX events_url ON events (url);
