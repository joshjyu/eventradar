# Published API (v1)

Static JSON served from the public bucket. Breaking changes ship under
`/v2/`; `/v1/` only changes additively (enforced by
`tests/golden/test_schema_compat.py`).

| Path | Content |
|---|---|
| `/v1/schema/event.json` | JSON Schema for one event |
| `/v1/{profile}/events.json` | Every event that has not ended |
| `/v1/{profile}/new/{YYYY-MM-DD}.json` | Events added that UTC day, not yet started, not cancelled |
| `/v1/{profile}/manifest.json` | Run id, counts, per-source status; written last |

Feed document: `schema_version`, `profile`, `generated_at`, `count`,
`events[]`. Times are ISO 8601 UTC; `tz` is the event's IANA zone.
`event_id` is stable across runs.
