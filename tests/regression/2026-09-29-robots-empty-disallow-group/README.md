# Empty `Disallow:` ignored when grouping user agents

An empty `Disallow:` line (meaning "allow everything") was dropped while
parsing, so the `*` group looked empty and the next `User-agent:` line was
merged into it. That group's `Disallow: /` then applied to every crawler,
blocking the whole site. Observed on devpost.com, whose robots.txt opens
with `User-agent: *` / `Disallow:` followed by per-bot blocks.
