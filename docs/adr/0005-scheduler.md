# 0005: Cloudflare Cron Trigger starts the daily run

- Status: accepted
- Date: 2026-09-28

## Context

GitHub disables `schedule` workflow triggers in public repositories after
60 days without repository activity. The pipeline stores state in R2 and
never commits, so a scheduled workflow would silently stop.

## Decision

A Cloudflare Worker with a Cron Trigger calls GitHub's `workflow_dispatch`
API daily. It uses a fine-grained token limited to this repository with
only Actions read/write, stored as a Worker secret. The Worker exposes no
HTTP endpoint. The workflow's own `schedule` stays as a fallback until the
Worker is verified, then is removed.

## Consequences

- Scheduling no longer depends on repository activity.
- The token expires yearly and must be rotated (`docs/scheduler.md`).
