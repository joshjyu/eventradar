"""S3-compatible blob store (Cloudflare R2, AWS S3, MinIO)."""

from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, SecretStr

from eventradar.storage.blob.base import validate_key

_PRECONDITION_CODES = {"PreconditionFailed", "412"}
_MISSING_CODES = {"NoSuchKey", "404"}


class S3Options(BaseModel):
    """Connection options; credentials are never shown in reprs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_url: str | None = None
    bucket: str
    access_key_id: SecretStr
    secret_access_key: SecretStr
    region: str = "auto"
    prefix: str = ""


class S3BlobStore:
    """Objects stored in a single bucket under an optional prefix."""

    def __init__(self, **options: Any) -> None:
        """
        Create the store and its client.

        Parameters:
          options: Fields of `S3Options`.
        """
        self._opts = S3Options.model_validate(options)
        self._client = boto3.client(
            "s3",
            endpoint_url=self._opts.endpoint_url,
            region_name=self._opts.region,
            aws_access_key_id=self._opts.access_key_id.get_secret_value(),
            aws_secret_access_key=(
                self._opts.secret_access_key.get_secret_value()
            ),
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )

    def _key(self, key: str) -> str:
        """
        Apply the configured prefix to a validated key.

        Parameters:
          key: Object key.
        Returns:
          Full bucket key.
        """
        return self._opts.prefix + validate_key(key)

    def get(self, key: str) -> bytes | None:
        """
        Read an object.

        Parameters:
          key: Object key.
        Returns:
          Object bytes, or None if absent.
        """
        try:
            obj = self._client.get_object(
                Bucket=self._opts.bucket, Key=self._key(key)
            )
        except ClientError as exc:
            if _error_code(exc) in _MISSING_CODES:
                return None
            raise
        return obj["Body"].read()

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        cache_control: str | None = None,
    ) -> None:
        """
        Write an object; S3 PUTs replace objects atomically.

        Parameters:
          key: Object key.
          data: Object bytes.
          content_type: MIME type served to readers.
          cache_control: Optional Cache-Control header value.
        """
        extra = {"CacheControl": cache_control} if cache_control else {}
        self._client.put_object(
            Bucket=self._opts.bucket,
            Key=self._key(key),
            Body=data,
            ContentType=content_type,
            **extra,
        )

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """
        Create an object only if absent, using `If-None-Match: *`.

        Parameters:
          key: Object key.
          data: Object bytes.
        Returns:
          True if written, False if the key already existed.
        """
        try:
            self._client.put_object(
                Bucket=self._opts.bucket,
                Key=self._key(key),
                Body=data,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _error_code(exc) in _PRECONDITION_CODES:
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        """
        Remove an object; missing keys are ignored.

        Parameters:
          key: Object key.
        """
        self._client.delete_object(Bucket=self._opts.bucket, Key=self._key(key))

    def list(self, prefix: str) -> list[str]:
        """
        List keys under a prefix.

        Parameters:
          prefix: Key prefix.
        Returns:
          Sorted keys, without the store prefix.
        """
        paginator = self._client.get_paginator("list_objects_v2")
        full = self._opts.prefix + prefix
        keys = [
            obj["Key"][len(self._opts.prefix) :]
            for page in paginator.paginate(
                Bucket=self._opts.bucket, Prefix=full
            )
            for obj in page.get("Contents", [])
        ]
        return sorted(keys)


def _error_code(exc: ClientError) -> str:
    """
    Extract the error code from a botocore error.

    Parameters:
      exc: Error raised by the client.
    Returns:
      The service error code, or an empty string.
    """
    return str(exc.response.get("Error", {}).get("Code", ""))
