# Contributing

## Getting started

```bash
uv sync
uv run pre-commit install
uv run eventradar config validate
uv run eventradar source test meetup-socal-tech
uv run eventradar run
uv run pytest
```

`run` uses the `local` environment: state and feeds go to
`.eventradar/`. To view the page against them, see
[docs/site.md](docs/site.md#local-preview). `uv run eventradar source
list` prints every configured source.

## Layout

- `config/`: settings, sources, profiles, regions, topics, kind rules
- `src/eventradar/`: pipeline, adapters, geo, dedup, storage, publishing
- `tests/`: unit, contract, integration, regression, golden corpora
- `site/`: events page and the Pages Function that serves feeds
- `workers/scheduler/`: Cloudflare Worker that triggers the daily run
- `scripts/`: builders for the packaged geo tables, local site preview
- `docs/`: runbooks, API, and architecture decisions (`docs/adr/`)

## Naming

- No region or topic literals (e.g. `socal`, `tech`) in code identifiers.
  Those belong in `config/` only.
- Modules are named by role or protocol. Vendor-specific code lives only in
  `*/providers/` or `sources/platforms/` leaf modules.
- Each IO package pairs a `base.py` `Protocol` with one module per
  implementation. Implementations register via entry points in
  `pyproject.toml` (`eventradar.<group>`).
- Config ids are kebab-case and globally unique: sources
  `{platform}-{scope}`, profiles `{region}-{topic}`.
- Versioning: config files carry `version`, published paths use `/v1/`,
  `Event.schema_version`, migrations are `NNNN_description.sql`.
- Datetimes are timezone-aware and stored in UTC plus an IANA tz name.

## Python style

- 80-column lines, PEP 8, type hints on every signature.
- Every function has a docstring with description, `Parameters:`, and
  `Returns:` sections (omit a section only when it would be empty).
- Comments go above code blocks, not inline.
- `_` prefix means strictly private.
- Do not explicitly `return None`; no unnecessary `pass`.
- US English spelling.

## Security

- Secrets are read only from environment variables and held as
  `pydantic.SecretStr`. Never log, print, or write them to disk.
- `.env` is gitignored; `gitleaks` runs in pre-commit and CI.
- Workflows default to `permissions: contents: read`, pin actions to commit
  SHAs, and never expose secrets to `pull_request` jobs.

## Testing

- `uv run pytest` runs everything except `live` tests.
- Every bug or source-drift fix adds a case under
  `tests/regression/<yyyy-mm-dd>-<slug>/` that fails before the fix.
- Intentional output changes: `uv run pytest --update-golden`, then review
  the diff.

## Git

- One branch per milestone or feature; atomic, conventional commits
  (`feat(scope): ...`, `fix`, `test`, `ci`, `docs`, `chore`).
- Keep commit messages and PR descriptions short.
