# 0011: Publish organizations, never individuals, as organizers

- Status: accepted
- Date: 2026-09-29

## Context

The published `organizer` field is meant to name who runs an event. The
JSON-LD path already kept only schema.org `Organization` names, but the
iCal adapter copied `ORGANIZER;CN=`, which calendar platforms such as Luma
fill with a host's personal name. Those names reached the public feed.

## Decision

- `organizer` holds an organization name or nothing. Adapters set it only
  from fields that name organizations (Meetup group, schema.org
  Organization, Devpost organization, Eventbrite organizer); the iCal
  adapter no longer sets it.
- Migration 0006 cleared every stored organizer and queued a one-time
  `reparse` maintenance task, which the next run performs from stored raw
  records, restoring organization names without refetching. Migrations can
  queue maintenance tasks the same way in future.
- Event descriptions are published as the organizers wrote them.

## Consequences

- Events from iCal-only sources have no organizer.
