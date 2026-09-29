"""Polite async HTTP client with retries and a response size cap."""

import asyncio
import json
import random
from collections import defaultdict
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Self

import httpx

from eventradar.config.schema import HttpSettings
from eventradar.http.ratelimit import HostRateLimiter, site_key
from eventradar.http.redact import redact_url
from eventradar.http.robots import (
    ALLOW_ALL,
    DISALLOW_ALL,
    RobotsRules,
    parse_robots,
)

type ResponseHook = Callable[[httpx.Response], None]

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_BACKOFF_S = 60.0
_ENCODING_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}


class HttpError(RuntimeError):
    """A request failed after all retries."""


class HttpClient:
    """Wraps `httpx.AsyncClient` with shared politeness policies."""

    def __init__(
        self,
        settings: HttpSettings,
        transport: httpx.AsyncBaseTransport | None = None,
        on_response: ResponseHook | None = None,
        max_bytes: int = 20_000_000,
    ) -> None:
        """
        Create the client.

        Parameters:
          settings: Timeouts, concurrency, spacing, and retries.
          transport: Optional transport override, e.g. for tests.
          on_response: Callback for every final response (recording).
          max_bytes: Largest response body accepted.
        """
        self._settings = settings
        self._limiter = HostRateLimiter(settings.per_host_min_interval_s)
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._on_response = on_response
        self._max_bytes = max_bytes
        self._agent = settings.user_agent.split("/")[0].split()[0].lower()
        self._robots: dict[str, RobotsRules] = {}
        self._robots_locks: defaultdict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )
        self._client = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        """
        Enter the async context.

        Returns:
          This client.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """
        Close the underlying connection pool.

        Parameters:
          exc_type: Exception type, if any.
          exc: Exception, if any.
          tb: Traceback, if any.
        """
        await self._client.aclose()

    async def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        """
        GET a URL with spacing, retries, and a body size cap.

        Parameters:
          url: Absolute URL.
          headers: Extra request headers.
        Returns:
          The final response (status < 400).
        """
        return await self._request("GET", url, headers)

    async def post_json(
        self,
        url: str,
        body: Any,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """
        POST a JSON body, e.g. a read-only GraphQL query.

        Retries apply, so use this only for idempotent requests.

        Parameters:
          url: Absolute URL.
          body: JSON-serializable request body.
          headers: Extra request headers.
        Returns:
          The final response (status < 400).
        """
        merged = {"Content-Type": "application/json", **(headers or {})}
        content = json.dumps(body).encode()
        return await self._request("POST", url, merged, content)

    async def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """
        Send a request with robots checks, spacing, and retries.

        Parameters:
          method: HTTP method.
          url: Absolute URL.
          headers: Request headers.
          content: Request body, if any.
        Returns:
          The final response (status < 400).
        """
        label = f"{method} {redact_url(url)}"
        if self._settings.respect_robots and not await self._allowed(url):
            raise HttpError(f"{label}: disallowed by robots.txt")
        key = site_key(httpx.URL(url).host)
        attempts = self._settings.max_retries + 1
        status: int | None = None
        for attempt in range(attempts):
            last = attempt + 1 == attempts
            async with self._semaphore:
                await self._limiter.wait(key)
                try:
                    response = await self._fetch(method, url, headers, content)
                except httpx.TransportError as exc:
                    if last:
                        raise HttpError(f"{label}: {exc!r}") from exc
                    await asyncio.sleep(_backoff(attempt))
                    continue
            status = response.status_code
            if status in _RETRY_STATUSES and not last:
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            if response.is_error:
                break
            if self._on_response:
                self._on_response(response)
            return response
        raise HttpError(f"{label}: HTTP {status}")

    async def _allowed(self, url: str) -> bool:
        """
        Check robots.txt for a URL, fetching it once per origin.

        Parameters:
          url: Absolute URL about to be requested.
        Returns:
          True if our user agent may fetch the URL.
        """
        parsed = httpx.URL(url)
        origin = f"{parsed.scheme}://{parsed.netloc.decode()}"
        async with self._robots_locks[origin]:
            if origin not in self._robots:
                self._robots[origin] = await self._load_robots(
                    origin, parsed.host
                )
        return self._robots[origin].allowed(parsed.raw_path.decode())

    async def _load_robots(self, origin: str, host: str) -> RobotsRules:
        """
        Fetch and parse robots.txt; apply any Crawl-delay.

        Per RFC 9309, a 4xx means no restrictions and an unreachable file
        or 5xx means everything is disallowed.

        Parameters:
          origin: Scheme, host, and port.
          host: Hostname, for rate limiting.
        Returns:
          Rules for our user agent.
        """
        async with self._semaphore:
            await self._limiter.wait(site_key(host))
            try:
                response = await self._fetch(
                    "GET", f"{origin}/robots.txt", None
                )
            except (httpx.TransportError, HttpError):
                return DISALLOW_ALL
        if response.status_code >= 500:
            return DISALLOW_ALL
        if response.status_code >= 400:
            return ALLOW_ALL
        rules = parse_robots(response.text, self._agent)
        if rules.crawl_delay:
            self._limiter.slow_down(
                site_key(host),
                min(rules.crawl_delay, self._settings.max_crawl_delay_s),
            )
        return rules

    async def _fetch(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """
        Stream a response, aborting if the body exceeds the cap.

        Parameters:
          method: HTTP method.
          url: Absolute URL.
          headers: Request headers.
          content: Request body, if any.
        Returns:
          A response whose body has been read.
        """
        async with self._client.stream(
            method, url, headers=headers, content=content
        ) as resp:
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > self._max_bytes:
                    raise HttpError(
                        f"{method} {redact_url(url)}: body exceeds "
                        f"{self._max_bytes} bytes"
                    )
                chunks.append(chunk)
            # The body is already decoded; keeping these headers would make
            # httpx decode it a second time.
            kept = [
                (k, v)
                for k, v in resp.headers.multi_items()
                if k.lower() not in _ENCODING_HEADERS
            ]
            return httpx.Response(
                status_code=resp.status_code,
                headers=kept,
                content=b"".join(chunks),
                request=resp.request,
            )


def _backoff(attempt: int) -> float:
    """
    Exponential backoff with full jitter.

    Parameters:
      attempt: Zero-based attempt number.
    Returns:
      Seconds to wait.
    """
    return random.uniform(0, min(_MAX_BACKOFF_S, 2.0**attempt))  # noqa: S311


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """
    Honor `Retry-After` when present, else back off.

    Parameters:
      response: Retryable response.
      attempt: Zero-based attempt number.
    Returns:
      Seconds to wait, capped.
    """
    value = response.headers.get("Retry-After")
    if value is None:
        return _backoff(attempt)
    if value.isdigit():
        return min(_MAX_BACKOFF_S, float(value))
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return _backoff(attempt)
    delta = (when - when.now(when.tzinfo)).total_seconds()
    return min(_MAX_BACKOFF_S, max(0.0, delta))
