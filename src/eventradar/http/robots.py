"""robots.txt parsing and matching per RFC 9309, including `*` and `$`."""

import re
from dataclasses import dataclass, field
from functools import cached_property


@dataclass(frozen=True)
class _Rule:
    """One Allow or Disallow line."""

    allow: bool
    pattern: str

    @cached_property
    def regex(self) -> re.Pattern[str]:
        """
        Compile the path pattern.

        Returns:
          Regex anchored at the path start; `*` matches anything and a
          trailing `$` anchors the end.
        """
        anchored = self.pattern.endswith("$")
        body = self.pattern[:-1] if anchored else self.pattern
        escaped = ".*".join(re.escape(part) for part in body.split("*"))
        return re.compile(escaped + ("$" if anchored else ""))


@dataclass(frozen=True)
class RobotsRules:
    """Rules that apply to one user agent on one host."""

    rules: tuple[_Rule, ...] = ()
    crawl_delay: float | None = None
    disallow_all: bool = False

    def allowed(self, path: str) -> bool:
        """
        Decide whether a path may be fetched.

        The longest matching pattern wins; Allow wins ties.

        Parameters:
          path: URL path including any query string.
        Returns:
          True if fetching is permitted.
        """
        if self.disallow_all:
            return False
        best: _Rule | None = None
        for rule in self.rules:
            if not rule.regex.match(path):
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow)
            ):
                best = rule
        return best is None or best.allow


@dataclass
class _Group:
    """A user-agent group while parsing."""

    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: float | None = None
    closed: bool = False


def _groups(text: str) -> list[_Group]:
    """
    Split robots.txt into user-agent groups.

    Parameters:
      text: robots.txt body.
    Returns:
      Groups in file order.
    """
    groups: list[_Group] = []
    current: _Group | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (p.strip() for p in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            # Consecutive user-agent lines share one group; any rule line,
            # even an empty `Disallow:`, ends the group's agent list.
            if current is None or current.closed:
                current = _Group()
                groups.append(current)
            current.agents.append(value.lower())
            continue
        if current is None or key not in {"allow", "disallow", "crawl-delay"}:
            continue
        current.closed = True
        if key in {"allow", "disallow"} and value:
            current.rules.append(_Rule(allow=key == "allow", pattern=value))
        elif key == "crawl-delay":
            try:
                current.crawl_delay = float(value)
            except ValueError:
                continue
    return groups


def parse_robots(text: str, agent: str) -> RobotsRules:
    """
    Select and merge the groups that apply to an agent.

    Parameters:
      text: robots.txt body.
      agent: Product token, e.g. `eventradar`.
    Returns:
      Rules for the agent, falling back to the `*` group.
    """
    groups = _groups(text)
    token = agent.lower()
    matched = [g for g in groups if token in g.agents]
    if not matched:
        matched = [g for g in groups if "*" in g.agents]
    delays = [g.crawl_delay for g in matched if g.crawl_delay is not None]
    return RobotsRules(
        rules=tuple(r for g in matched for r in g.rules),
        crawl_delay=max(delays) if delays else None,
    )


ALLOW_ALL = RobotsRules()
DISALLOW_ALL = RobotsRules(disallow_all=True)
