# eventradar

Daily, hands-off discovery of regional events. The live instance lists
tech hackathons, conferences, workshops, and meetups in Southern
California: **https://eventsradar.pages.dev**

Sources are read once a day from structured, public data (iCalendar
feeds, public APIs, and data embedded in pages, never page layouts),
normalized into a versioned `Event` schema, geocoded, deduplicated across
platforms, filtered into profiles (region x topic), and published as
static JSON, iCalendar, and RSS. Sources, regions, topics, and profiles
are configuration; adding a calendar needs no code.

## Sources

| Platform | Adapter | What it reads |
|---|---|---|
| Luma calendars | `ical` | Tech-week, city, and community calendars |
| Meetup | `meetup` | Public GraphQL search, Technology category, five SoCal areas |
| MLH | `mlh` | Member hackathons from the season listing |
| Event websites | `jsonld` | schema.org `Event` data on linked pages (OC Tech Week) |
| Devpost, Eventbrite | `devpost`, `jsonld`, `eventbrite` | Disabled: their pages block datacenter IPs; the Eventbrite API adapter needs a token |

`config/sources/` holds the full list; `uv run eventradar source list`
prints it.

## Feeds

Under `https://eventsradar.pages.dev/v1/socal-tech/`: `events.json`,
`events.ics` (subscribe from a calendar app), `feed.xml` (RSS of new
events), `new/{date}.json`, and `manifest.json`. Formats and stability
rules: [docs/api.md](docs/api.md).

## How it runs

1. A Cloudflare Worker cron starts the `daily` GitHub Actions workflow at
   13:17 UTC ([docs/scheduler.md](docs/scheduler.md)).
2. The workflow fetches every enabled source, updates the SQLite state
   kept in a private R2 bucket, and publishes feeds to a second bucket.
3. Cloudflare Pages serves the page in `site/` and the feeds from that
   bucket ([docs/site.md](docs/site.md)).
4. Sources that fail repeatedly open a `source-health` issue and are
   paused automatically ([docs/operations.md](docs/operations.md)).

## Quickstart

```bash
uv sync
uv run eventradar config validate
uv run eventradar source test meetup-socal-tech
uv run eventradar run
uv run pytest
```

`run` uses the `local` environment: state and feeds go to
`.eventradar/`. To view the page against them, see
[docs/site.md](docs/site.md#local-preview).

## Layout

- `config/`: settings, sources, profiles, regions, topics, kind rules
- `src/eventradar/`: pipeline, adapters, geo, dedup, storage, publishing
- `tests/`: unit, contract, integration, regression, golden corpora
- `site/`: events page and the Pages Function that serves feeds
- `workers/scheduler/`: Cloudflare Worker that triggers the daily run
- `scripts/`: builders for the packaged geo tables, local site preview
- `docs/`: runbooks, API, and architecture decisions (`docs/adr/`)

## Docs

- [Adding a source](docs/adding-a-source.md)
- [Published API](docs/api.md)
- [Operations runbook](docs/operations.md)
- [Security runbook](docs/security.md)
- [Scheduler](docs/scheduler.md) and [website](docs/site.md) setup
- [Contributor conventions](CONTRIBUTING.md)
