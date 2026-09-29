# 0006: Platform adapters for Meetup, Devpost, and Eventbrite

- Status: accepted
- Date: 2026-09-29

## Context

Measured on 2026-09-29:

- Meetup's public GraphQL endpoint (`/gql-ext`) answers `eventSearch`
  without authentication, and the API host's robots.txt allows `/gql*`.
  Keyword search alone is loosely semantic (about 1 in 4 results were
  tech); adding the Technology topic category made results almost all
  relevant (176 events across five SoCal areas).
- Devpost's listing API needs no key but reports display-only dates and
  venue names; each hackathon page has JSON-LD with exact times and an
  address. Every hackathon lives on its own subdomain.
- Eventbrite's website answers HTTP 405 to datacenter IPs. Its official
  API needs a personal token and offers no public search, only
  organizer- and event-level reads.

## Decision

- `meetup`: area x keyword searches with a topic category, deduplicated by
  event id; RSVP counts become the size signal; online events are dropped
  by default (their coordinates are placeholders).
- `devpost`: listing API for discovery, the shared JSON-LD page harvester
  for detail, registration counts as refetch-free signals.
- `eventbrite`: official API per curated organizer, token from the
  environment; disabled until the secret exists.
- Rate limits are keyed by registrable domain so per-subdomain sites
  (Devpost) are still spaced politely.
- Superseded raw versions are pruned after 30 days, since daily counter
  changes would otherwise grow the state database without bound.

## Consequences

- Worldwide (Devpost) and nationwide (Eventbrite organizers) sources stay
  out of regional profiles until region filtering exists (M5).
- First Devpost run makes about 230 requests (listing, one robots.txt and
  one page per hackathon); later runs fetch only new or due pages.
