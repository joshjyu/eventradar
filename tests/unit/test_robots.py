"""Tests for robots.txt parsing, matching, and client enforcement."""

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings
from eventradar.http import HttpClient, HttpError
from eventradar.http.robots import parse_robots

ROBOTS = """
# comment
User-agent: Googlebot
Disallow: /

User-agent: *
Disallow: /private/
Allow: /private/open
Disallow: *?session*
Disallow: /*.pdf$
Crawl-delay: 2

User-agent: eventradar
User-agent: otherbot
Disallow: /blocked-for-us
Crawl-delay: 5
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/", True),
        ("/private/x", False),
        ("/private/open/page", True),
        ("/a?session=1", False),
        ("/doc.pdf", False),
        ("/doc.pdf?download=1", True),
    ],
)
def test_star_group_matching(path: str, allowed: bool) -> None:
    """
    Wildcards, end anchors, and longest-match precedence follow RFC 9309.

    Parameters:
      path: Path and query to check.
      allowed: Expected decision.
    """
    rules = parse_robots(ROBOTS, "somebot")
    assert rules.allowed(path) is allowed


def test_specific_group_replaces_star_group() -> None:
    """A group naming our agent is used instead of `*`."""
    rules = parse_robots(ROBOTS, "EventRadar")
    assert not rules.allowed("/blocked-for-us")
    assert rules.allowed("/private/x")
    assert rules.crawl_delay == 5


def test_allow_wins_equal_length_tie() -> None:
    """Equal-length Allow and Disallow resolve to Allow."""
    rules = parse_robots("User-agent: *\nDisallow: /a\nAllow: /a\n", "x")
    assert rules.allowed("/a")


def test_empty_disallow_allows_everything() -> None:
    """`Disallow:` with no value places no restriction."""
    assert parse_robots("User-agent: *\nDisallow:\n", "x").allowed("/any")


def _settings() -> HttpSettings:
    """
    Build settings with robots enforcement on and delays capped low.

    Returns:
      HTTP settings.
    """
    return HttpSettings(
        user_agent="eventradar/0.1 (+test)",
        per_host_min_interval_s=0,
        max_retries=0,
        max_crawl_delay_s=0.01,
    )


@respx.mock
async def test_client_blocks_disallowed_paths() -> None:
    """Disallowed URLs fail without being requested."""
    respx.get("https://site.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS)
    )
    page = respx.get("https://site.test/blocked-for-us").mock(
        return_value=httpx.Response(200)
    )
    async with HttpClient(_settings()) as http:
        with pytest.raises(HttpError, match="disallowed by robots"):
            await http.get("https://site.test/blocked-for-us")
    assert not page.called


@respx.mock
async def test_client_fetches_robots_once_and_applies_delay() -> None:
    """robots.txt is cached per origin; Crawl-delay is applied, capped."""
    robots = respx.get("https://site.test/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS)
    )
    respx.get(url__startswith="https://site.test/ok").mock(
        return_value=httpx.Response(200)
    )
    async with HttpClient(_settings()) as http:
        await http.get("https://site.test/ok1")
        await http.get("https://site.test/ok2")
        assert http._limiter._overrides["site.test"] == 0.01
    assert robots.call_count == 1


@pytest.mark.parametrize(
    ("status", "allowed"), [(404, True), (403, True), (503, False)]
)
@respx.mock
async def test_unavailable_robots_follows_rfc(
    status: int, allowed: bool
) -> None:
    """
    4xx means no restrictions; 5xx means disallow everything.

    Parameters:
      status: robots.txt response status.
      allowed: Whether the page fetch should proceed.
    """
    respx.get("https://site.test/robots.txt").mock(
        return_value=httpx.Response(status)
    )
    respx.get("https://site.test/page").mock(return_value=httpx.Response(200))
    async with HttpClient(_settings()) as http:
        if allowed:
            assert (await http.get("https://site.test/page")).status_code == 200
        else:
            with pytest.raises(HttpError, match="robots"):
                await http.get("https://site.test/page")


@respx.mock
async def test_errors_redact_credentials() -> None:
    """Error messages never contain API keys from query strings."""
    respx.get("https://api.test/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    respx.get(url__startswith="https://api.test/search").mock(
        return_value=httpx.Response(401)
    )
    async with HttpClient(_settings()) as http:
        with pytest.raises(HttpError) as exc:
            await http.get("https://api.test/search?q=x&api_key=supersecret")
    assert "supersecret" not in str(exc.value)
