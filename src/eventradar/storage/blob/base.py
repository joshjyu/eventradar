"""Blob store protocol and backend factory."""

from typing import Protocol

from eventradar.config.loader import expand_env
from eventradar.config.schema import BlobSettings
from eventradar.plugins import load_plugin

BLOB_GROUP = "eventradar.blob"


class BlobStore(Protocol):
    """Minimal key/value object storage."""

    def get(self, key: str) -> bytes | None:
        """
        Read an object.

        Parameters:
          key: Object key.
        Returns:
          Object bytes, or None if absent.
        """
        ...

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        cache_control: str | None = None,
    ) -> None:
        """
        Write an object, replacing any existing one atomically.

        Parameters:
          key: Object key.
          data: Object bytes.
          content_type: MIME type served to readers.
          cache_control: Optional Cache-Control header value.
        """
        ...

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """
        Write an object only if the key does not exist.

        Parameters:
          key: Object key.
          data: Object bytes.
        Returns:
          True if written, False if the key already existed.
        """
        ...

    def delete(self, key: str) -> None:
        """
        Remove an object; missing keys are ignored.

        Parameters:
          key: Object key.
        """
        ...

    def list(self, prefix: str) -> list[str]:
        """
        List keys under a prefix.

        Parameters:
          prefix: Key prefix.
        Returns:
          Sorted keys.
        """
        ...


def validate_key(key: str) -> str:
    """
    Reject keys that could escape a store's namespace.

    Parameters:
      key: Candidate key.
    Returns:
      The key, unchanged.
    """
    parts = key.split("/")
    if not key or key.startswith("/") or ".." in parts or "" in parts:
        raise ValueError(f"invalid blob key: {key!r}")
    return key


def build_blob(settings: BlobSettings) -> BlobStore:
    """
    Instantiate the configured backend with env-expanded options.

    Parameters:
      settings: Backend name and options from config.
    Returns:
      A ready blob store.
    """
    backend = load_plugin(BLOB_GROUP, settings.backend)
    return backend(**expand_env(settings.options))
