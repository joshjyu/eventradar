"""Tests for blob backends, the backend factory, and the run lock."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from botocore.stub import Stubber

from eventradar.config import ConfigError
from eventradar.config.schema import BlobSettings
from eventradar.storage.blob import build_blob
from eventradar.storage.blob.local import LocalBlobStore
from eventradar.storage.blob.s3 import S3BlobStore
from eventradar.storage.lock import BlobLock, LockHeldError

NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> LocalBlobStore:
    """
    Create a local store in a scratch directory.

    Parameters:
      tmp_path: Pytest temporary directory.
    Returns:
      Local blob store.
    """
    return LocalBlobStore(str(tmp_path / "blobs"))


def _s3() -> S3BlobStore:
    """
    Create an S3 store with dummy credentials.

    Returns:
      S3 blob store (network calls must be stubbed).
    """
    return S3BlobStore(
        bucket="b",
        endpoint_url="https://example.invalid",
        access_key_id="AKIA-TEST",
        secret_access_key="not-a-real-secret",
    )


def test_local_round_trip(store: LocalBlobStore) -> None:
    """Objects can be written, listed, read, and deleted."""
    store.put("a/b.json", b"1")
    assert store.get("a/b.json") == b"1"
    assert store.list("a/") == ["a/b.json"]
    store.delete("a/b.json")
    assert store.get("a/b.json") is None


def test_local_put_if_absent(store: LocalBlobStore) -> None:
    """A second conditional write to the same key is refused."""
    assert store.put_if_absent("lock", b"x")
    assert not store.put_if_absent("lock", b"y")
    assert store.get("lock") == b"x"


@pytest.mark.parametrize("key", ["", "/abs", "a/../b", "a//b", ".."])
def test_keys_cannot_escape_namespace(store: LocalBlobStore, key: str) -> None:
    """
    Traversal and malformed keys are rejected.

    Parameters:
      store: Local store fixture.
      key: Malformed key.
    """
    with pytest.raises(ValueError, match="invalid blob key"):
        store.put(key, b"x")


def test_build_blob_expands_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Options are env-expanded before the backend is built.

    Parameters:
      tmp_path: Pytest temporary directory.
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("BLOB_ROOT", str(tmp_path))
    blob = build_blob(
        BlobSettings(backend="local", options={"root": "${BLOB_ROOT}/x"})
    )
    blob.put("k", b"v")
    assert (tmp_path / "x" / "k").read_bytes() == b"v"


def test_build_blob_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Missing variables fail before any backend is created.

    Parameters:
      monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("NOPE_VAR", raising=False)
    with pytest.raises(ConfigError, match="NOPE_VAR"):
        build_blob(
            BlobSettings(backend="local", options={"root": "${NOPE_VAR}"})
        )


def test_s3_options_hide_secrets() -> None:
    """Credentials never appear in reprs."""
    text = repr(_s3()._opts)
    assert "not-a-real-secret" not in text
    assert "AKIA-TEST" not in text


def test_s3_put_if_absent_maps_precondition_failure() -> None:
    """A 412 from the server means the key already exists."""
    blob = _s3()
    with Stubber(blob._client) as stub:
        stub.add_client_error(
            "put_object",
            service_error_code="PreconditionFailed",
            http_status_code=412,
        )
        assert not blob.put_if_absent("lock", b"x")


def test_s3_get_missing_returns_none() -> None:
    """A missing key reads as None instead of raising."""
    blob = _s3()
    with Stubber(blob._client) as stub:
        stub.add_client_error(
            "get_object", service_error_code="NoSuchKey", http_status_code=404
        )
        assert blob.get("absent") is None


def test_lock_blocks_second_holder(store: LocalBlobStore) -> None:
    """An unexpired lock cannot be taken by another owner."""
    ttl = timedelta(hours=1)
    BlobLock(store, "lock", "run-1", ttl).acquire(NOW)
    with pytest.raises(LockHeldError):
        BlobLock(store, "lock", "run-2", ttl).acquire(NOW)


def test_lock_expires_and_release_is_owner_only(store: LocalBlobStore) -> None:
    """Expired locks are broken; only the holder can release."""
    ttl = timedelta(hours=1)
    BlobLock(store, "lock", "run-1", ttl).acquire(NOW)
    second = BlobLock(store, "lock", "run-2", ttl)
    second.acquire(NOW + timedelta(hours=2))
    BlobLock(store, "lock", "run-1", ttl).release()
    assert store.get("lock") is not None
    second.release()
    assert store.get("lock") is None
