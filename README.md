# eventradar

Config-driven discovery of regional events. Sources are fetched once per
day, normalized into a versioned `Event` schema, filtered into profiles
(region x topic), and published as static JSON.

## Quickstart

```bash
uv sync
uv run eventradar config validate
uv run eventradar run --no-publish
```

## Layout

- `config/` — settings, source registry, profiles, regions, topics
- `src/eventradar/` — pipeline, adapters, storage, publishing
- `tests/` — unit, contract, integration, regression, golden
- `workers/scheduler/` — Cloudflare Worker that triggers the daily run
- `docs/adr/` — architecture decisions

See `CONTRIBUTING.md` for conventions.
