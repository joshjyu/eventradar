# 0008: Region, topic, and kind rules decide profile membership

- Status: accepted
- Date: 2026-09-29

## Context

After M4, profiles were a hand-picked list of sources. Measured on
2026-09-29:

- Luma's city feeds carry every topic: 4 of 64 events were tech.
- Meetup's Technology category is mostly, not entirely, tech (it also
  returned a lecture on racism and a referral-networking breakfast).
- Devpost lists hackathons worldwide; 4 of 107 were in Southern
  California.
- Keyword rules calibrated on this data misfired on words common outside
  tech ("rust", "java", "react", "design", "summit", "launch").

## Decision

- Region: county polygons (U.S. Census TIGERweb, simplified to ~50 m so
  coastal venues stay inside) and a dependency-free point-in-polygon test.
  Unlocated events count only from region-trusted sources; online events
  only with `include_online`.
- Topic: a title keyword qualifies; otherwise the description needs 3
  distinct keywords, or 1 for topic-trusted sources. `topic_always`
  sources (curated calendars, hackathon platforms) skip the check.
- Kinds: title phrases from `config/kinds.yaml`, stored apart from
  source-reported kinds and recomputed each run; a source's
  `default_kinds` apply when no phrase matches.
- Membership is recomputed every run for upcoming events.
- Feeds: each profile also publishes `events.ics` (calendar apps) and
  `feed.xml` (RSS, newest additions first).
- A labeled topic corpus (`tests/golden/classify_labels.yaml`) holds an
  accuracy baseline that may only rise.

## Consequences

- All sources now feed the profile; curation lives in trust lists, not
  source lists.
- Events outside the U.S. or with only a city name and no street or ZIP
  stay unlocated and are excluded unless a trusted source vouches for
  them. A city-centroid fallback can be added if that proves costly.
  (Added with the MLH source, whose venues give only a city: U.S. events
  with a city and state are now placed at the city's centroid.)
