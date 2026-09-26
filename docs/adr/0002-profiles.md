# 0002: Sources are global; profiles are views

- Status: accepted
- Date: 2026-09-26

## Decision

Sources are fetched and resolved once. A profile (region x topic, plus an
optional source scope) selects events for publication. Region and topic
names appear only in `config/`, never in code.

## Consequences

Adding a region or topic is a config change. An event can belong to many
profiles (`event_profiles`).
