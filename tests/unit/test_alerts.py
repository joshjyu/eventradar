"""Tests for alert delivery."""

import json

import httpx
import pytest
import respx

from eventradar.config.schema import HttpSettings
from eventradar.health.alerts.github_issue import GitHubIssueAlerter
from eventradar.http import HttpClient

API = "https://api.github.example.test"
ISSUES = f"{API}/repos/owner/repo/issues"


def _alerter() -> GitHubIssueAlerter:
    """
    Build an alerter against the test API.

    Returns:
      Alerter.
    """
    return GitHubIssueAlerter(
        repository="owner/repo",
        token_env="TEST_GH_TOKEN",
        api=API,
        run_url="https://ci.example.test/runs/1",
    )


def _settings() -> HttpSettings:
    """
    Build fast test settings.

    Returns:
      HTTP settings.
    """
    return HttpSettings(
        user_agent="test", per_host_min_interval_s=0, respect_robots=False
    )


def _listing(*issues: dict[str, object]) -> respx.Route:
    """
    Serve the open-issue listing.

    Parameters:
      issues: Issues to return.
    Returns:
      The route.
    """
    return respx.get(url__startswith=ISSUES).mock(
        return_value=httpx.Response(200, json=list(issues))
    )


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Provide a fake token.

    Parameters:
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("TEST_GH_TOKEN", "fake-token")


@respx.mock
async def test_raise_creates_labeled_issue_once() -> None:
    """With no open issue, one is created with the label and run link."""
    _listing(
        {"number": 9, "title": "[source-health] s1: old", "pull_request": {}}
    )
    create = respx.post(ISSUES).mock(return_value=httpx.Response(201))
    async with HttpClient(_settings()) as http:
        await _alerter().raise_alert("s1", "fetch failing", "details", http)
    sent = json.loads(create.calls[0].request.content)
    assert sent["title"] == "[source-health] s1: fetch failing"
    assert sent["labels"] == ["source-health"]
    assert "https://ci.example.test/runs/1" in sent["body"]
    assert (
        create.calls[0].request.headers["Authorization"] == "Bearer fake-token"
    )


@respx.mock
async def test_raise_comments_on_existing_issue() -> None:
    """A repeat alert comments instead of opening a duplicate."""
    _listing({"number": 3, "title": "[source-health] s1: fetch failing"})
    comment = respx.post(f"{ISSUES}/3/comments").mock(
        return_value=httpx.Response(201)
    )
    create = respx.post(ISSUES).mock(return_value=httpx.Response(201))
    async with HttpClient(_settings()) as http:
        await _alerter().raise_alert("s1", "now disabled", "details", http)
    assert comment.called
    assert not create.called


@respx.mock
async def test_resolve_closes_only_matching_issue() -> None:
    """Recovery comments and closes; unrelated keys are untouched."""
    _listing({"number": 3, "title": "[source-health] s1: fetch failing"})
    comment = respx.post(f"{ISSUES}/3/comments").mock(
        return_value=httpx.Response(201)
    )
    close = respx.patch(f"{ISSUES}/3").mock(return_value=httpx.Response(200))
    async with HttpClient(_settings()) as http:
        await _alerter().resolve("s2", "recovered", http)
        assert not close.called
        await _alerter().resolve("s1", "recovered", http)
    assert comment.called
    assert json.loads(close.calls[0].request.content) == {"state": "closed"}


async def test_missing_token_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Without a token the alerter fails loudly and names the variable.

    Parameters:
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("TEST_GH_TOKEN")
    async with HttpClient(_settings()) as http:
        with pytest.raises(RuntimeError, match="TEST_GH_TOKEN"):
            await _alerter().raise_alert("s1", "t", "b", http)
