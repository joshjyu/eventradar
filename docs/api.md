# Published API (v1)

Static JSON served from the public bucket. Breaking changes ship under
`/v2/`; `/v1/` only changes additively (enforced by
`tests/golden/test_schema_compat.py`).

| Path | Content |
|---|---|
| `/v1/schema/event.json` | JSON Schema for one event |
| `/v1/{profile}/events.json` | Every event that has not ended |
| `/v1/{profile}/new/{YYYY-MM-DD}.json` | Events added that UTC day, not yet started, not cancelled |
| `/v1/{profile}/events.ics` | The same events as an iCalendar feed for calendar apps |
| `/v1/{profile}/feed.xml` | RSS 2.0, the 100 most recently added upcoming events |
| `/v1/{profile}/manifest.json` | Run id, counts, per-source status; written last |

Feed document: `schema_version`, `profile`, `generated_at`, `count`,
`events[]`. Times are ISO 8601 UTC; `tz` is the event's IANA zone.
`event_id` is stable across runs. When two postings are found to be the
same event, they merge into the older `event_id`, and `sources` lists
every posting.

`kinds` combines what sources report (e.g. Devpost: hackathon) with
title rules from `config/kinds.yaml`; it may be empty.

Profile membership: an event is in a profile when it is in the region
(by coordinates, or from a region-trusted source), not online unless the
profile includes online events, and on the topic (see ADR 0008).

`organizer` is the organizing organization when a source names one; it is
never a person (ADR 0011).
