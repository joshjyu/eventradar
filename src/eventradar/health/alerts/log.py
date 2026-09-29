"""Alerter that only logs; used for local runs."""

import logging
from typing import Any

from eventradar.http import HttpClient

log = logging.getLogger(__name__)


class LogAlerter:
    """Writes alerts to the log."""

    name = "log"

    def __init__(self, **options: Any) -> None:
        """
        Accept and ignore options.

        Parameters:
          options: Unused.
        """

    async def raise_alert(
        self, key: str, title: str, body: str, http: HttpClient
    ) -> None:
        """
        Log an alert.

        Parameters:
          key: Alert key.
          title: Summary.
          body: Details.
          http: Unused.
        """
        log.warning("ALERT %s: %s\n%s", key, title, body)

    async def resolve(self, key: str, body: str, http: HttpClient) -> None:
        """
        Log a resolution.

        Parameters:
          key: Alert key.
          body: Closing note.
          http: Unused.
        """
        log.info("RESOLVED %s: %s", key, body)
