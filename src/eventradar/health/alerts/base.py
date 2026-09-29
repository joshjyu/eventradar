"""Alerter protocol."""

from typing import Protocol

from eventradar.http import HttpClient


class Alerter(Protocol):
    """Opens, updates, and resolves one alert per key."""

    name: str

    async def raise_alert(
        self, key: str, title: str, body: str, http: HttpClient
    ) -> None:
        """
        Open an alert, or add to the one already open for the key.

        Parameters:
          key: Stable identifier, e.g. a source id.
          title: One-line summary.
          body: Details in Markdown.
          http: Shared HTTP client.
        """
        ...

    async def resolve(self, key: str, body: str, http: HttpClient) -> None:
        """
        Close the open alert for a key, if any.

        Parameters:
          key: Stable identifier.
          body: Closing note in Markdown.
          http: Shared HTTP client.
        """
        ...
