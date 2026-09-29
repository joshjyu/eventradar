"""Entry-point based plugin discovery."""

from functools import cache
from importlib.metadata import entry_points
from typing import Any

SOURCE_GROUP = "eventradar.sources"
BLOB_GROUP = "eventradar.blob"
GEOCODER_GROUP = "eventradar.geocoders"
ALERTER_GROUP = "eventradar.alerters"


class PluginNotFoundError(LookupError):
    """Raised when no implementation is registered under a name."""


@cache
def available(group: str) -> tuple[str, ...]:
    """
    List plugin names registered in an entry-point group.

    Parameters:
      group: Entry-point group, e.g. `eventradar.sources`.
    Returns:
      Sorted plugin names.
    """
    return tuple(sorted(ep.name for ep in entry_points(group=group)))


@cache
def load_plugin(group: str, name: str) -> Any:
    """
    Load the object registered under a name in an entry-point group.

    Parameters:
      group: Entry-point group, e.g. `eventradar.sources`.
      name: Registered plugin name, e.g. `ical`.
    Returns:
      The loaded object, typically a class.
    """
    matches = entry_points(group=group, name=name)
    if not matches:
        known = ", ".join(available(group)) or "none"
        raise PluginNotFoundError(
            f"no plugin '{name}' in {group} (available: {known})"
        )
    return next(iter(matches)).load()
