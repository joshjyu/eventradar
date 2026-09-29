# 0001: Structured-layer retrieval, single scheduled job

- Status: accepted; search discovery superseded by 0010; feeds are
  served through the site's bucket binding, not a public bucket URL
  (docs/site.md)
- Date: 2026-09-26

## Context

We need daily discovery of new events with no manual intervention, low
compute cost, and resilience to website layout changes.

## Decision

- Read data only from layers with stronger stability contracts than HTML:
  platform APIs, iCalendar feeds, and schema.org/Event JSON-LD. A search
  API (SerpApi `google_events`) acts as a discovery net for the long tail.
- Run as one scheduled GitHub Actions job. State is a SQLite file persisted
  to Cloudflare R2; outputs are static JSON in a public R2 bucket.
- Adapters split `fetch` (IO) from `parse` (pure) and raw payloads are
  stored, so parsing fixes can be replayed without refetching.

## Consequences

- No headless browsers and no servers; each run makes a few hundred HTTP
  requests.
- Sources without structured data are out of scope until email push or
  social listening are added (deferred).
