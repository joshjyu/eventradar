"""Filesystem-backed blob store for local runs and tests."""

import os
from pathlib import Path

from eventradar.storage.blob.base import validate_key


class LocalBlobStore:
    """Stores objects as files beneath a root directory."""

    def __init__(self, root: str) -> None:
        """
        Create the store.

        Parameters:
          root: Directory that holds all objects.
        """
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        """
        Map a key to a file path inside the root.

        Parameters:
          key: Object key.
        Returns:
          Absolute path.
        """
        return self._root / validate_key(key)

    def get(self, key: str) -> bytes | None:
        """
        Read an object.

        Parameters:
          key: Object key.
        Returns:
          Object bytes, or None if absent.
        """
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        cache_control: str | None = None,
    ) -> None:
        """
        Write an object atomically via a temp file and rename.

        Parameters:
          key: Object key.
          data: Object bytes.
          content_type: Ignored locally.
          cache_control: Ignored locally.
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """
        Create an object only if it does not exist.

        Parameters:
          key: Object key.
          data: Object bytes.
        Returns:
          True if written, False if the key already existed.
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return True

    def delete(self, key: str) -> None:
        """
        Remove an object; missing keys are ignored.

        Parameters:
          key: Object key.
        """
        self._path(key).unlink(missing_ok=True)

    def list(self, prefix: str) -> list[str]:
        """
        List keys under a prefix.

        Parameters:
          prefix: Key prefix.
        Returns:
          Sorted keys.
        """
        if not self._root.is_dir():
            return []
        keys = (
            p.relative_to(self._root).as_posix()
            for p in self._root.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )
        return sorted(k for k in keys if k.startswith(prefix))
