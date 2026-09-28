# 0003: Secrets and supply-chain posture

- Status: accepted
- Date: 2026-09-26

## Decision

- Secrets exist only as GitHub `production` environment secrets, which
  are limited to `main`. `pull_request` workflows never receive them.
- Code reads secrets from the environment as `SecretStr`; HTTP client
  loggers stay at WARNING; recorded fixtures redact credential query
  parameters.
- One R2 token, scoped to exactly the state and public buckets.
- Actions are pinned to commit SHAs; gitleaks runs in pre-commit and CI
  from a checksum-verified binary; Dependabot tracks actions and Python.
- A run where every source fails never publishes, and a failed run never
  uploads state.
