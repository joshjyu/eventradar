# 0007: Keyless geocoding and fuzzy cross-source dedup

- Status: accepted
- Date: 2026-09-29

## Context

- Several sources give addresses without coordinates (Devpost, later
  search results), and region filtering needs coordinates.
- Nominatim's and Photon's robots.txt disallow their search endpoints; the
  client enforces robots.txt everywhere. The U.S. Census geocoder has no
  robots.txt, needs no key, and is public domain, but covers only U.S.
  street addresses.
- The same event is often posted several times: by several Meetup groups,
  or on both Luma and Meetup, with different titles, times rounded
  differently, and slightly different coordinates.

## Decision

- Enrich stage: geocode upcoming in-person events that have an address
  but no coordinates with the Census geocoder, falling back to a packaged
  Census ZIP-centroid table when the address includes a U.S. state and
  ZIP, then to a packaged Census place (city) centroid table when it ends
  in a U.S. city and state. Results and misses are cached; misses retry after 30 days; lookups
  per run are capped. Missing time zones come from coordinates (`tzfpy`,
  offline).
- Resolve stage: events starting within 90 minutes are compared. Within
  1 km, titles match on token-set similarity >= 85; within 1-5 km
  (geocoding noise) or without coordinates, on token-sort similarity
  >= 92. Titles equal after removing spaces always match. Duplicates merge
  into the oldest event; retired ids are kept as aliases.
- Events placed at a city centroid are compared as if unlocated (the
  strict title rule, at any distance). A source's own coordinates replace
  a centroid, and merges keep the more precise location.
- Field precedence: each source has a `priority`. The leading source's
  values win but never erase known values; others only fill gaps. A
  cancellation reported by any source sticks.
- A labeled corpus (`tests/golden/dedup_pairs.yaml`) enforces precision
  >= 0.98 and recall >= 0.90; every observed false or missed merge is
  added to it.

## Consequences

- Events outside the U.S. without source coordinates stay unlocated; they
  cannot match a U.S. region anyway.
- Each merge is logged with the titles involved, for audit.
