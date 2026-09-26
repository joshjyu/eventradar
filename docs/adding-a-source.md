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
