"""Tests for the shared HTTP client."""

import gzip

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings
from eventradar.http import HttpClient, HttpError
from eventradar.http import client as client_module

URL = "https://example.test/feed.ics"
_RETRY_DELAY = client_module._retry_delay


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Remove retry delays so tests run instantly.

    Parameters:
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(client_module, "_backoff", lambda attempt: 0.0)
    monkeypatch.setattr(client_module, "_retry_delay", lambda r, a: 0.0)


def _settings(**overrides: object) -> HttpSettings:
    """
    Build fast test settings.

    Parameters:
      overrides: Field overrides.
    Returns:
      HTTP settings.
    """
    fields: dict[str, object] = {
        "user_agent": "eventradar-test",
        "per_host_min_interval_s": 0,
        "max_retries": 2,
        "respect_robots": False,
    }
    fields.update(overrides)
    return HttpSettings.model_validate(fields)


@respx.mock
async def test_retries_transient_errors_then_succeeds() -> None:
    """429 and 503 responses are retried."""
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, text="ok"),
        ]
    )
    async with HttpClient(_settings()) as http:
        response = await http.get(URL)
    assert response.text == "ok"
    assert route.call_count == 3
    assert route.calls[0].request.headers["User-Agent"] == "eventradar-test"


@respx.mock
async def test_gives_up_after_max_retries() -> None:
    """Persistent server errors raise after the retry budget."""
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    async with HttpClient(_settings()) as http:
        with pytest.raises(HttpError, match="HTTP 500"):
            await http.get(URL)
    assert route.call_count == 3


@respx.mock
async def test_client_errors_are_not_retried() -> None:
    """A 404 fails immediately and reports its status."""
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    async with HttpClient(_settings()) as http:
        with pytest.raises(HttpError, match="HTTP 404") as caught:
            await http.get(URL)
    assert route.call_count == 1
    assert caught.value.status == 404


@respx.mock
async def test_transport_errors_are_retried() -> None:
    """Connection failures are retried, then surfaced as HttpError."""
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    async with HttpClient(_settings()) as http:
        with pytest.raises(HttpError, match="ConnectError"):
            await http.get(URL)


@respx.mock
async def test_oversized_body_is_rejected() -> None:
    """Bodies above the cap abort the request."""
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 11))
    async with HttpClient(_settings(), max_bytes=10) as http:
        with pytest.raises(HttpError, match="exceeds 10 bytes"):
            await http.get(URL)


@respx.mock
async def test_on_response_sees_final_response() -> None:
    """The recording hook receives only the successful response."""
    respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text="ok")]
    )
    seen: list[int] = []
    async with HttpClient(
        _settings(), on_response=lambda r: seen.append(r.status_code)
    ) as http:
        await http.get(URL)
    assert seen == [200]


def test_retry_after_seconds_is_honored_and_capped() -> None:
    """Numeric Retry-After values are used up to the cap."""
    ok = httpx.Response(429, headers={"Retry-After": "5"})
    huge = httpx.Response(429, headers={"Retry-After": "9999"})
    assert _RETRY_DELAY(ok, 0) == 5.0
    assert _RETRY_DELAY(huge, 0) == 60.0


def test_redact_url_masks_credentials() -> None:
    """API keys and userinfo never survive redaction."""
    from eventradar.http.redact import redact_url

    url = "https://user:pw@api.example.test/s?q=ai&api_key=abc123&Token=x"
    out = redact_url(url)
    assert "abc123" not in out
    assert "pw" not in out
    assert "q=ai" in out
    assert out.count("REDACTED") == 3


@respx.mock
async def test_gzip_body_is_decoded_once() -> None:
    """Compressed responses come back decoded, without a second decode."""
    body = gzip.compress(b"BEGIN:VCALENDAR")
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Encoding": "gzip"}
        )
    )
    async with HttpClient(_settings()) as http:
        response = await http.get(URL)
    assert response.content == b"BEGIN:VCALENDAR"


@respx.mock
async def test_post_json_sends_body_and_retries() -> None:
    """JSON bodies are encoded once and resent on retryable errors."""
    route = respx.post("https://api.example.test/gql").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    async with HttpClient(_settings()) as http:
        response = await http.post_json(
            "https://api.example.test/gql", {"query": "{ x }"}
        )
    assert response.json() == {"ok": 1}
    assert route.call_count == 2
    sent = route.calls[1].request
    assert sent.headers["Content-Type"] == "application/json"
    assert sent.content == b'{"query": "{ x }"}'


@pytest.mark.parametrize(
    ("host", "key"),
    [
        ("a.devpost.com", "devpost.com"),
        ("devpost.com", "devpost.com"),
        ("x.example.co.uk", "example.co.uk"),
        ("api.lu.ma", "lu.ma"),
    ],
)
def test_site_key_groups_subdomains(host: str, key: str) -> None:
    """
    Subdomains of one site share a rate-limit key.

    Parameters:
      host: Hostname.
      key: Expected limiter key.
    """
    from eventradar.http.ratelimit import site_key

    assert site_key(host) == key


@respx.mock
async def test_send_json_without_retry_makes_one_attempt() -> None:
    """Non-idempotent requests are sent once even on server errors."""
    route = respx.patch("https://api.example.test/issues/1").mock(
        return_value=httpx.Response(503)
    )
    async with HttpClient(_settings()) as http:
        with pytest.raises(HttpError, match=r"PATCH .* HTTP 503"):
            await http.send_json(
                "PATCH",
                "https://api.example.test/issues/1",
                {"state": "closed"},
                retry=False,
            )
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "headers", "reason"),
    [
        (202, {}, "HTTP 202"),
        (202, {"x-amzn-waf-action": "challenge"}, "AWS WAF challenge"),
        (200, {"cf-mitigated": "challenge"}, "Cloudflare challenge"),
    ],
)
@respx.mock
async def test_bot_challenges_are_fetch_errors(
    status: int, headers: dict[str, str], reason: str
) -> None:
    """
    Challenge pages served with a success status raise, without retries.

    Parameters:
      status: Response status.
      headers: Response headers.
      reason: Expected description in the error.
    """
    route = respx.get(URL).mock(
        return_value=httpx.Response(status, headers=headers, text="<html>")
    )
    async with HttpClient(_settings()) as http:
        with pytest.raises(HttpError, match=f"bot challenge \\({reason}\\)"):
            await http.get(URL)
    assert route.call_count == 1
