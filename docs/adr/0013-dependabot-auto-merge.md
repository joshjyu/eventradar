# 0013: Dependabot auto-merge

- Status: accepted
- Date: 2026-10-15

## Context

Dependabot opens weekly grouped PRs for Actions, Python, and the
scheduler worker's npm packages, each merged by hand once CI passed.
Merging them automatically removes that chore but also removes the
pause in which a compromised release could be noticed. Python packages
run in the `daily` job with production secrets; Actions define that job.

## Decision

- Each ecosystem has a 7-day cooldown: Dependabot proposes no release
  younger than a week. Security updates ignore it.
- Groups hold only minor and patch updates; major updates arrive as
  separate PRs.
- `dependabot-automerge.yml` enables auto-merge (squash) on Dependabot
  PRs whose metadata, read by `dependabot/fetch-metadata`, shows a
  minor or patch update to `uv` or `npm` packages. The `main` ruleset's
  required checks gate the merge.
- Major updates and all Actions updates wait for review.

## Consequences

- Most weekly PRs merge without attention; a failing check leaves the
  PR open.
- Fixes in new releases arrive at least a week late.
- A malicious release that survives the cooldown and passes CI reaches
  the daily job unreviewed. The cooldown, the lockfiles, and the
  narrowly scoped R2 token (ADR 0003) limit that risk.
- Merges made with `GITHUB_TOKEN` do not start the `ci` workflow's
  `push` run on `main`; the PR's own checks cover the change.
