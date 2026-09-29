# 0009: Source health checks, alerts, and weekly probes

- Status: accepted
- Date: 2026-09-29

## Context

Sources break without warning: an endpoint starts refusing datacenter IPs
(Eventbrite, HTTP 405), a page layout drops its JSON-LD, or a feed shrinks
to nothing. The pipeline already isolates a failing source, but nobody is
told, and a broken source keeps being requested every day.

The original plan had a weekly `canary` workflow. In a public repository
GitHub disables `schedule` triggers after 60 days without commits, and the
daily run already exercises every live source, so a separate canary would
duplicate it and eventually stop silently.

## Decision

- After each run, every fetched source is checked against its SLOs
  (`slo:` in its config): the fetch failed, fewer records than
  `min_yield_ratio` of the median over `window_days`, or more than
  `max_parse_error_ratio` of changed records failing to parse.
- Consecutive unhealthy runs escalate: `alert_after_runs` opens an alert,
  `disable_after_runs` disables the source. Disabled sources are skipped
  but retried once a week inside the daily run; a passing run re-enables
  them and resolves the alert. `eventradar health enable` re-enables one
  immediately.
- Alerts are pluggable. Production files one GitHub issue per source
  (label `source-health`) with the job's own token; repeats comment on
  it and recovery closes it. Local runs only log. Delivery failures never
  fail the run and are retried on the next one.
- An unhealthy source's metrics and sample raw records are uploaded as a
  14-day workflow artifact; `eventradar regression capture` turns one into
  a `parse` regression case.

## Consequences

- One bad day is noise; two open an issue; a week disables the source.
- No separate scheduled workflow to keep alive.
