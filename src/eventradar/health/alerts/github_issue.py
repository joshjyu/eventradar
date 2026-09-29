"""Alerter that tracks each problem as a GitHub issue."""

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from eventradar.config.types import HttpsUrl
from eventradar.http import HttpClient


class GitHubIssueOptions(BaseModel):
    """Options for the GitHub issue alerter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str = Field(pattern=r"^[A-Za-z0-9-]+/[\w.-]+$")
    token_env: str = Field(default="GITHUB_TOKEN", pattern=r"^[A-Z][A-Z0-9_]*$")
    label: str = "source-health"
    api: HttpsUrl = "https://api.github.com"
    run_url: str | None = None


class GitHubIssueAlerter:
    """One open issue per key, found again by its title prefix."""

    name = "github-issue"

    def __init__(self, **options: Any) -> None:
        """
        Configure the alerter.

        Parameters:
          options: Fields of `GitHubIssueOptions`.
        """
        self._opts = GitHubIssueOptions.model_validate(options)

    def _headers(self) -> dict[str, str]:
        """
        Build API headers with the token from the environment.

        Returns:
          Request headers.
        """
        token = SecretStr(os.environ.get(self._opts.token_env, "").strip())
        if not token.get_secret_value():
            raise RuntimeError(f"{self._opts.token_env} is not set")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token.get_secret_value()}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _prefix(self, key: str) -> str:
        """
        Title prefix that identifies a key's issue.

        Parameters:
          key: Alert key.
        Returns:
          e.g. `[source-health] meetup-socal-tech:`.
        """
        return f"[{self._opts.label}] {key}:"

    def _with_run(self, body: str) -> str:
        """
        Append a link to the workflow run, when known.

        Parameters:
          body: Markdown body.
        Returns:
          Body with the run link.
        """
        if not self._opts.run_url:
            return body
        return f"{body}\n\nRun: {self._opts.run_url}"

    async def _open_issue(
        self, key: str, http: HttpClient
    ) -> dict[str, Any] | None:
        """
        Find the open issue for a key.

        Parameters:
          key: Alert key.
          http: Shared HTTP client.
        Returns:
          The issue, or None.
        """
        url = (
            f"{self._opts.api}/repos/{self._opts.repository}/issues"
            f"?state=open&labels={self._opts.label}&per_page=100"
        )
        response = await http.get(url, headers=self._headers())
        prefix = self._prefix(key)
        for issue in response.json():
            is_issue = "pull_request" not in issue
            if is_issue and str(issue.get("title", "")).startswith(prefix):
                return issue
        return None

    async def raise_alert(
        self, key: str, title: str, body: str, http: HttpClient
    ) -> None:
        """
        Comment on the key's open issue, or open one.

        Parameters:
          key: Alert key.
          title: Summary.
          body: Details in Markdown.
          http: Shared HTTP client.
        """
        base = f"{self._opts.api}/repos/{self._opts.repository}/issues"
        existing = await self._open_issue(key, http)
        if existing:
            await http.send_json(
                "POST",
                f"{base}/{existing['number']}/comments",
                {"body": self._with_run(f"**{title}**\n\n{body}")},
                self._headers(),
                retry=False,
            )
            return
        await http.send_json(
            "POST",
            base,
            {
                "title": f"{self._prefix(key)} {title}",
                "body": self._with_run(body),
                "labels": [self._opts.label],
            },
            self._headers(),
            retry=False,
        )

    async def resolve(self, key: str, body: str, http: HttpClient) -> None:
        """
        Comment on and close the key's open issue, if any.

        Parameters:
          key: Alert key.
          body: Closing note in Markdown.
          http: Shared HTTP client.
        """
        existing = await self._open_issue(key, http)
        if not existing:
            return
        url = (
            f"{self._opts.api}/repos/{self._opts.repository}/issues/"
            f"{existing['number']}"
        )
        await http.send_json(
            "POST",
            f"{url}/comments",
            {"body": self._with_run(body)},
            self._headers(),
            retry=False,
        )
        await http.send_json("PATCH", url, {"state": "closed"}, self._headers())
