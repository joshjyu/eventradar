"""Advisory run lock held in blob storage."""

import json
from datetime import UTC, datetime, timedelta

from eventradar.storage.blob.base import BlobStore


class LockHeldError(RuntimeError):
    """Raised when another unexpired run holds the lock."""


class BlobLock:
    """A TTL lock created with a conditional write."""

    def __init__(
        self, store: BlobStore, key: str, owner: str, ttl: timedelta
    ) -> None:
        """
        Describe a lock; nothing is written until `acquire`.

        Parameters:
          store: Blob store that holds the lock object.
          key: Lock object key.
          owner: Identifier of the holder, e.g. a run id.
          ttl: Age after which a lock is considered abandoned.
        """
        self._store = store
        self._key = key
        self._owner = owner
        self._ttl = ttl

    def acquire(self, now: datetime | None = None) -> None:
        """
        Take the lock, breaking it if the previous holder expired.

        Parameters:
          now: Current time; defaults to the system clock.
        """
        now = now or datetime.now(UTC)
        body = json.dumps(
            {"owner": self._owner, "expires_at": (now + self._ttl).isoformat()}
        ).encode()
        if self._store.put_if_absent(self._key, body):
            return
        if not self._is_expired(now):
            raise LockHeldError(f"lock {self._key} is held")
        self._store.delete(self._key)
        if not self._store.put_if_absent(self._key, body):
            raise LockHeldError(f"lock {self._key} was taken concurrently")

    def release(self) -> None:
        """Drop the lock if this owner still holds it."""
        current = self._read()
        if current and current.get("owner") == self._owner:
            self._store.delete(self._key)

    def _read(self) -> dict[str, str] | None:
        """
        Read the current lock document.

        Returns:
          Parsed document, or None if absent or unreadable.
        """
        raw = self._store.get(self._key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _is_expired(self, now: datetime) -> bool:
        """
        Decide whether the existing lock can be broken.

        Parameters:
          now: Current time.
        Returns:
          True if the lock is missing, unreadable, or past its expiry.
        """
        current = self._read()
        if not current or "expires_at" not in current:
            return True
        return datetime.fromisoformat(current["expires_at"]) <= now
