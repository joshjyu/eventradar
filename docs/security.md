# Security runbook

## One-time setup

### Cloudflare R2

1. Create two buckets: `eventradar-state` (private) and
   `eventradar-public`.
2. Expose only `eventradar-public`, preferably through a custom domain
   (the `r2.dev` URL is rate-limited and meant for development). Never
   enable public access on the state bucket.
3. Create an R2 API token:
   - Permission: **Object Read & Write**
   - Scope: **Apply to specific buckets only** -> both buckets
   - TTL: 90 days (calendar reminder to rotate)
4. Copy the Access Key ID and Secret Access Key once, straight into
   GitHub (below). Do not save them in files, notes, or chat.

### GitHub

1. **Environment**: Settings -> Environments -> New `production`.
   Deployment branches: selected branches -> `main` only. No required
   reviewers (the job must run unattended).
2. **Environment secrets** (not repository secrets) in `production`:
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_STATE_BUCKET`, `R2_PUBLIC_BUCKET`.
3. **Actions**: Settings -> Actions -> General
   - Workflow permissions: *Read repository contents*.
   - Allowed actions: owner plus selected: `actions/*`,
     `astral-sh/setup-uv@*`.
4. **Ruleset for `main`**: require a pull request, require status checks
   `test` and `secrets-scan`, block force pushes and deletion.
5. **Code security**: enable Dependabot alerts and security updates.
6. Two-factor authentication on GitHub and Cloudflare.

### Local

- `uv run pre-commit install` enables gitleaks and lint hooks.
- Do not keep production credentials locally. For local testing against
  R2, create a separate token scoped to separate dev buckets and put it in
  `.env` (gitignored), loaded with `set -a; . ./.env; set +a`.

## Rotation

Every 90 days, or immediately on suspected exposure:

1. Create a new R2 token with the same scope.
2. Update `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` in `production`.
3. Trigger `daily` with *Run workflow* and confirm it succeeds.
4. Delete the old token in Cloudflare.

## Later secrets

Add to the same `production` environment when their milestones land:
`MEETUP_TOKEN` (M3), `SERPAPI_KEY` (M7), `ANTHROPIC_API_KEY` (M8). Set a
usage cap or spend limit on each provider account.
