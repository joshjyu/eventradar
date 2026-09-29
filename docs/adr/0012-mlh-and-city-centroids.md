# 0012: MLH hackathons and city-centroid locations

- Status: accepted
- Date: 2026-09-29

## Context

With Devpost disabled (ADR 0006), the feed listed almost no hackathons.
Measured on 2026-09-29:

- MLH's season pages (`/seasons/{year}/events`) embed their data as
  Inertia page JSON: upcoming events with UTC start and end times, a
  format (physical, digital, hybrid), and the venue's city, state, and
  country code, but no street address. robots.txt allows the pages, and
  they load normally from datacenter IPs. A later season's page answers
  404 until it is published.
- The enrich stage located addresses only by street (Census geocoder) or
  ZIP; a city-only address stayed unlocated and was dropped by region
  filtering.
- MLH country codes collide with U.S. state abbreviations (CA is Canada,
  IN is India).

## Decision

- `mlh` adapter: read the current season (named for the year it ends,
  July to June) and the next one, skipping a 404 for the next; keep
  upcoming, non-online events; every event is a hackathon. No organizer:
  the data does not name one.
- Addresses spell country names out; only `US` stays a code.
- Enrich falls back to a packaged Census places table (city centroids)
  after the ZIP table, for addresses ending in a U.S. city and state.
- Events placed at a city centroid are stored with
  `geo_precision = 'place'`. Dedup compares them as if unlocated (strict
  title rule, any distance); a source's own coordinates replace them, and
  merges keep the more precise location.

## Consequences

- SoCal MLH events reach the profile; the rest are stored but filtered
  out (about 70 per season).
- City-only addresses from any source are now located, so a few more
  events pass region filtering.
- MLH lists only member events; many local university hackathons are not
  members, so hackathon coverage stays partial without Devpost.
