# 0010: Curated calendars instead of search-API discovery

- Status: accepted
- Date: 2026-09-29
- Supersedes the search "discovery net" planned in ADR 0001

## Context

Spikes on 2026-09-29:

- SerpApi's `google_events` engine now answers "Unsupported"; Google's
  dedicated Events search is gone. Regular results for "tech events in
  Irvine" linked to aggregator pages (already covered, or blocking our
  runners) and to conference sites without schema.org data.
- Site-restricted searches for Luma pages mostly returned past events (1
  of 9 upcoming).
- Exa's search with schema-based extraction found real long-tail events
  (e.g. OC Tech Week, a San Diego tech summit), about 2 per search, but
  most extractions lacked dates and many came from LinkedIn posts.

## Decision

- No search API. Coverage grows through curated, machine-readable
  calendars:
  - OC Tech Week, via its website's links to Luma event pages (new
    `links` discovery mode for the `jsonld` adapter) and TCVN's Luma
    calendar.
  - Global tech-community Luma calendars with SoCal chapters (AI
    Collective, Claude Community, Codex Community, Latent.Space,
    OpenClaw, n8n, Lenny's Newsletter, Nucleate).
- Sources that cover far more than any profile set `keep_region`; their
  events outside it are dropped at ingest so the database holds only what
  a profile could use.

## Consequences

- No new accounts or secrets; every source is keyless and structured.
- New conferences are picked up only when someone adds their calendar or
  site to `config/sources/`. That is a deliberate trade for accuracy.
