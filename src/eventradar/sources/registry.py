"""Resolve configured sources to adapter instances."""

from eventradar.config.schema import SourceConfig
from eventradar.plugins import SOURCE_GROUP, load_plugin
from eventradar.sources.base import Source


def build_source(config: SourceConfig) -> Source:
    """
    Instantiate the adapter named by a source's config.

    Parameters:
      config: Source configuration.
    Returns:
      Adapter bound to the source.
    """
    return load_plugin(SOURCE_GROUP, config.adapter)(config)
