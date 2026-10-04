# eventradar

Daily, hands-off discovery of regional events. The live instance lists
tech hackathons, conferences, workshops, and meetups in Southern
California: **https://eventsradar.pages.dev**

Subscribe from the site (iCalendar or RSS), or read the JSON feeds
described in [docs/api.md](docs/api.md).

## Sources

| Platform | Adapter | What it reads |
|---|---|---|
| Luma calendars | `ical` | Tech-week, city, and community calendars |
| Meetup | `meetup` | Public GraphQL search, Technology category, five SoCal areas |
| MLH | `mlh` | Member hackathons from the season listing |
| Event websites | `jsonld` | schema.org `Event` data on linked pages (OC Tech Week) |
| dev.events | `jsonld` | Conference and meetup listings for Los Angeles and San Diego |
| Devpost, Eventbrite | `devpost`, `jsonld`, `eventbrite` | Disabled: their pages block datacenter IPs; the Eventbrite API adapter needs a token |

## How it runs

A Cloudflare Worker starts a GitHub Actions workflow every day at 12:00
UTC. It fetches each source, deduplicates and filters the events, and
publishes the feeds that the Cloudflare Pages site serves. Sources that
keep failing open a GitHub issue and are paused.

## Docs

- [Contributing](CONTRIBUTING.md): setup, layout, and conventions
- [Adding a source](docs/adding-a-source.md)
- [Published API](docs/api.md)
- [Operations](docs/operations.md) and [security](docs/security.md)
  runbooks
- [Scheduler](docs/scheduler.md) and [website](docs/site.md) setup
