# 0004: JSON-LD via listing hubs, refetch on change

- Status: accepted; the Eventbrite hub sources are disabled (Eventbrite
  answers datacenter IPs with HTTP 405). The approach still serves
  `links` discovery (OC Tech Week).
- Date: 2026-09-28

## Context

Measured on 2026-09-28:

- Luma and Eventbrite event pages embed schema.org `Event` JSON-LD
  (Eventbrite uses subtypes such as `BusinessEvent`).
- Eventbrite city listing pages embed an `ItemList` linking to event pages;
  the listed items carry only date-level start times. Later pages keep
  returning loosely related events, so pagination needs a hard cap.
- Neither site's sitemap can be scoped by region (Luma: one 3.7 MB file
  with random slugs; Eventbrite: global event sitemaps).
- Luma pages are `no-store` with no validators; Eventbrite pages send only
  an `ETag`. Conditional GETs would still cost one request per page.

## Decision

- The `jsonld` adapter discovers event URLs from listing hubs (or a fixed
  URL list) and stores only whitelisted, stable Event fields.
- An event page is refetched only when its hub listing changes or on a
  staggered `refresh_days` cycle; otherwise the stored record is reused.
- Sitemap discovery and HTTP conditional-GET caching are deferred until a
  source benefits from them.
- JSON-LD is extracted with the standard library instead of `extruct`,
  avoiding its RDF dependencies.
- robots.txt is enforced for every request (RFC 9309, including wildcards
  and Crawl-delay).

## Consequences

- A daily run fetches a few dozen hub pages plus mostly new event pages.
- Organizers that are people, not organizations, are not stored.
