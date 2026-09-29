"""Per-host request spacing."""

import asyncio
import time
from collections import defaultdict


class HostRateLimiter:
    """Enforces a minimum interval between requests to the same host."""

    def __init__(self, min_interval_s: float) -> None:
        """
        Create the limiter.

        Parameters:
          min_interval_s: Minimum seconds between requests per host.
        """
        self._interval = min_interval_s
        self._overrides: dict[str, float] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last: dict[str, float] = {}

    def slow_down(self, host: str, min_interval_s: float) -> None:
        """
        Raise one host's interval, e.g. to honor robots.txt Crawl-delay.

        Parameters:
          host: Hostname.
          min_interval_s: Requested minimum interval; lower values are
            ignored.
        """
        current = self._overrides.get(host, self._interval)
        self._overrides[host] = max(current, min_interval_s)

    async def wait(self, host: str) -> None:
        """
        Sleep until a request to `host` is allowed.

        Parameters:
          host: Hostname being requested.
        """
        async with self._locks[host]:
            last = self._last.get(host)
            if last is not None:
                interval = self._overrides.get(host, self._interval)
                delay = interval - (time.monotonic() - last)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


_SECOND_LEVEL = frozenset({"ac", "co", "com", "edu", "gov", "net", "org"})


def site_key(host: str) -> str:
    """
    Group subdomains of one site so they share a rate limit.

    e.g. `a.devpost.com` and `b.devpost.com` both map to `devpost.com`;
    `x.example.co.uk` maps to `example.co.uk`.

    Parameters:
      host: Hostname.
    Returns:
      Registrable-domain approximation used as the limiter key.
    """
    labels = host.lower().rstrip(".").split(".")
    if (
        len(labels) >= 3
        and len(labels[-1]) == 2
        and labels[-2] in _SECOND_LEVEL
    ):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
