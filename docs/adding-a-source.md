# Adding a source

## Existing protocol (no code)

1. Add an entry under `config/sources/<family>.yaml`:

   ```yaml
   - id: luma-oc-tech        # {platform}-{scope}, kebab-case, unique
     adapter: ical
     params:
       url: https://api.lu.ma/ics/get?entity=calendar&id=cal-XXXX
       default_tz: America/Los_Angeles
   ```

   Any site whose pages carry schema.org `Event` JSON-LD works with the
   `jsonld` adapter, either from listing pages (`hub`) or fixed pages
   (`urls`):

   ```yaml
   - id: example-riverside-tech
     adapter: jsonld
     params:
       discovery:
         type: hub                 # or: type: urls, urls: [...]
         pages: 2                  # hard cap; `{page}` marks pagination
         urls:
           - "https://events.example.com/riverside/tech?page={page}"
       url_pattern: '^https://events\.example\.com/e/'
       default_tz: America/Los_Angeles
       max_events: 100             # pages fetched per run, at most
       refresh_days: 7             # unchanged pages refetched this often
       dates_only: false           # true: dates stamped as midnight UTC
       default_state: null         # e.g. CA: for addresses naming no state
   ```

   For a site that links to its events on another platform (for example
   a tech week's homepage linking to Luma pages), use `links` discovery;
   `url_pattern` is required and selects which links are event pages:

   ```yaml
   - id: site-oc-tech-week
     adapter: jsonld
     params:
       discovery:
         type: links
         urls: ["https://octechweek.com/"]
       url_pattern: '^https://(luma\.com|lu\.ma)/[A-Za-z0-9-]+$'
   ```

   Check the site's `robots.txt` first; the client enforces it and will
   report blocked URLs as fetch errors. Also run `source test` from a
   cloud machine if you can: some sites (Eventbrite, Devpost) answer
   datacenter IPs, including GitHub's runners, with an error or a bot
   challenge. The client reports challenges as fetch errors; do not work
   around them.

   Optional source settings:
   - `priority` (0-100, default 50): higher wins when sources describe
     the same event differently.
   - `default_kinds`: kinds for its events when title rules find none.
   - `keep_region`: drop events outside this region at ingest, for
     sources (e.g. global community calendars) that mostly cover places
     no profile needs.

2. `uv run eventradar config validate`
3. `uv run eventradar source test luma-oc-tech`
4. Profiles use every source by default. If the source is already scoped
   to the profile's region or topic, list it under the profile's
   `trust.region`, `trust.topic` (weaker keyword evidence), or
   `trust.topic_always` (no keywords needed).

## Platform adapters

| Adapter | Discovery | Credentials | In use |
|---|---|---|---|
| `meetup` | GraphQL `eventSearch` per area x keyword, filtered by topic category | none | yes |
| `mlh` | Season pages' embedded page data (current and next season) | none | yes |
| `devpost` | Listing API, then each hackathon page's JSON-LD | none | no: pages challenge datacenter IPs |
| `eventbrite` | Official API v3, live events per organizer | `EVENTBRITE_TOKEN` | no: needs a token |

Meetup's keyword search is a loose semantic match; set
`topic_category_id` (546 is Technology) to keep results on topic.

Addresses: write country names out ("Toronto, Ontario, Canada"). The
enrich stage reads a trailing two-letter code as a U.S. state, and many
country codes (CA, IN, GA) are also state abbreviations.

## New adapter

1. Implement the `Source` protocol (`src/eventradar/sources/base.py`):
   network IO in `fetch`, pure logic in `parse`. Validate params with a
   pydantic model in `__init__`.
2. Put generic protocols in `sources/protocols/`, vendor-specific code in
   `sources/platforms/`.
3. Register it in `pyproject.toml` under
   `[project.entry-points."eventradar.sources"]`.
4. Record fixtures (`eventradar source record <id>`), review them for
   personal data, and add a case to `tests/contract/test_sources.py`.
