"""Reusable validated field types for adapter parameters."""

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator


def _check_zone(value: str) -> str:
    """
    Require an IANA time zone name.

    Parameters:
      value: Zone name.
    Returns:
      The zone name.
    """
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone: {value}") from exc
    return value


def _check_https(value: str) -> str:
    """
    Require an absolute https URL.

    Parameters:
      value: URL.
    Returns:
      The URL.
    """
    if not value.startswith("https://"):
        raise ValueError(f"url must use https: {value}")
    return value


type IanaZone = Annotated[str, AfterValidator(_check_zone)]
type HttpsUrl = Annotated[str, AfterValidator(_check_https)]
