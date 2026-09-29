# Security runbook

## One-time setup

### Cloudflare R2

1. Create two private buckets: one for state, one for published feeds.
   Keep their names out of the repository; workflows read them from
   secrets.
2. Leave public access off on both. The site serves feeds through a
   Pages binding to the feed bucket (see the website runbook), so no
   `r2.dev` URL or bucket custom domain is needed.
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
   `test`, `worker`, `site`, and `secrets-scan`, block force pushes and
   deletion.
5. **Code security**: enable Dependabot alerts and security updates.
6. Two-factor authentication on GitHub and Cloudflare.

### Local

- `uv run pre-commit install` enables gitleaks and lint hooks.
- Prefer not to keep production credentials locally. Only a few manual
  commands need them (e.g. `health enable --env production`); for those,
  keep them in `.env` (gitignored) and load it with
  `set -a; . ./.env; set +a`. For experiments, use a separate token
  scoped to separate dev buckets.

## Rotation

Every 90 days, or immediately on suspected exposure:

1. Create a new R2 token with the same scope.
2. Update `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` in `production`.
3. Trigger `daily` with *Run workflow* and confirm it succeeds.
4. Delete the old token in Cloudflare.

## Health alerts

The daily job files `source-health` issues with its own short-lived
`GITHUB_TOKEN`, granted `issues: write` for that job only. No personal
token is involved.

## Eventbrite API token (optional)

The `eventbrite` adapter is disabled until a token exists. It reads a
personal OAuth token from `EVENTBRITE_TOKEN` and only reads public event
data.

1. Sign in at eventbrite.com -> account menu -> Developer Links -> API
   Keys -> Create API key. Copy the **Private token**.
2. Add it as the `EVENTBRITE_TOKEN` secret in the `production`
   environment.
3. Set `enabled: true` for `eventbrite-tech-organizers` in
   `config/sources/eventbrite.yaml`.

## Later secrets

Add any future provider key to the same `production` environment, and set
a usage cap or spend limit on the provider account. Luma, Meetup, MLH, and
Devpost need no credentials.
