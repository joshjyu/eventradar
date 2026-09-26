"""Polite async HTTP client with retries and a response size cap."""

import asyncio
import random
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Self

import httpx

from eventradar.config.schema import HttpSettings
from eventradar.http.ratelimit import HostRateLimiter

type ResponseHook = Callable[[httpx.Response], None]

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_BACKOFF_S = 60.0


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
          The final response (status < 400 or 304).
        """
        host = httpx.URL(url).host
        attempts = self._settings.max_retries + 1
        status: int | None = None
        for attempt in range(attempts):
            last = attempt + 1 == attempts
            async with self._semaphore:
                await self._limiter.wait(host)
                try:
                    response = await self._fetch(url, headers)
                except httpx.TransportError as exc:
                    if last:
                        raise HttpError(f"GET {url}: {exc!r}") from exc
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
        raise HttpError(f"GET {url}: HTTP {status}")

    async def _fetch(
        self, url: str, headers: dict[str, str] | None
    ) -> httpx.Response:
        """
        Stream a response, aborting if the body exceeds the cap.

        Parameters:
          url: Absolute URL.
          headers: Extra request headers.
        Returns:
          A response whose body has been read.
        """
        async with self._client.stream("GET", url, headers=headers) as resp:
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > self._max_bytes:
                    raise HttpError(
                        f"GET {url}: body exceeds {self._max_bytes} bytes"
                    )
                chunks.append(chunk)
            return httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
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
