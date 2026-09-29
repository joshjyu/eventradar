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
   - id: eventbrite-riverside-tech
     adapter: jsonld
     params:
       discovery:
         type: hub                 # or: type: urls, urls: [...]
         pages: 2                  # hard cap; `{page}` marks pagination
         urls:
           - "https://www.eventbrite.com/d/ca--riverside/science-and-tech--events/?page={page}"
       url_pattern: '^https://www\.eventbrite\.com/e/'
       default_tz: America/Los_Angeles
       max_events: 100             # pages fetched per run, at most
       refresh_days: 7             # unchanged pages refetched this often
   ```

   Check the site's `robots.txt` first; the client enforces it and will
   report blocked URLs as fetch errors.

2. `uv run eventradar config validate`
3. `uv run eventradar source test luma-oc-tech`
4. Include it in a profile's `sources:` list (or leave the profile at
   `all`).

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
