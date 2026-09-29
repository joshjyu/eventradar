# Operations runbook

## A `source-health` issue appeared

The daily run found a source failing its checks for several runs in a
row. The issue says why (fetch failed, yield collapse, or parse errors)
and links the run.

1. Open the linked run. Under **Artifacts**, download `health-captures`.
   It holds `<source-id>.json` with the run's metrics, the error, and up
   to 10 raw records.
2. Reproduce locally:

   ```bash
   uv run eventradar source test <source-id>
   ```

3. Decide what changed:
   - **Blocked or gone** (HTTP 403/404/405, robots.txt): set
     `enabled: false` in the source's config and note why, or find a
     sanctioned alternative (an official API).
   - **Layout or format drift** (parse errors, missing fields): capture a
     regression case, fix the adapter, and confirm the case fails before
     and passes after the fix:

     ```bash
     uv run eventradar regression capture health-captures/<source-id>.json --slug <what-broke>
     ```

   - **Temporary outage:** nothing to do. The source is retried and the
     issue closes itself when it passes again.
4. After a fix is merged, re-enable immediately instead of waiting for the
   weekly probe (production state needs the R2 credentials in `.env`):

   ```bash
   uv run eventradar health enable <source-id> --env production
   ```

## Checking health

```bash
uv run eventradar health status
```

The published `manifest.json` also lists each source's status per run.

## Tuning thresholds

Per source, under `slo:` in `config/sources/*.yaml`: `min_yield_ratio`,
`window_days`, `min_history_runs`, `max_parse_error_ratio`,
`alert_after_runs`, `disable_after_runs`. See ADR 0009.
