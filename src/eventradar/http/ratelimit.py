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
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        """
        Sleep until a request to `host` is allowed.

        Parameters:
          host: Hostname being requested.
        """
        async with self._locks[host]:
            last = self._last.get(host)
            if last is not None:
                delay = self._interval - (time.monotonic() - last)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last[host] = time.monotonic()
