"""MinIO (S3-compatible) object storage: recordings, clips, snapshots, evidence."""

from __future__ import annotations

import io
from datetime import timedelta

from minio import Minio

from .config import settings
from .logging import get_logger

log = get_logger("storage")


class ObjectStore:
    def __init__(self) -> None:
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        # Presigned URLs are handed to the browser, so they must be signed for a host
        # the browser can reach. Use a separate client bound to the public endpoint
        # when one is configured (split internal/external networks); otherwise reuse
        # the internal client.
        public = settings.minio_public_endpoint or settings.minio_endpoint
        self._presign_client = (
            self.client
            if public == settings.minio_endpoint
            else Minio(
                public,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
        )

    def ensure_buckets(self, buckets: list[str] | None = None) -> None:
        for bucket in buckets or settings.all_buckets:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                log.info("bucket.created", bucket=bucket)

    def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        self.client.put_object(
            bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        return f"{bucket}/{key}"

    def put_file(self, bucket: str, key: str, path: str, content_type: str | None = None) -> str:
        self.client.fput_object(bucket, key, path, content_type=content_type)
        return f"{bucket}/{key}"

    def get_bytes(self, bucket: str, key: str) -> bytes:
        resp = self.client.get_object(bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def stat(self, bucket: str, key: str):
        return self.client.stat_object(bucket, key)

    def presigned_get(self, bucket: str, key: str, expires_seconds: int = 3600) -> str:
        return self._presign_client.presigned_get_object(
            bucket, key, expires=timedelta(seconds=expires_seconds)
        )

    def list_keys(self, bucket: str, prefix: str = "", recursive: bool = True):
        return [
            obj.object_name for obj in self.client.list_objects(bucket, prefix, recursive=recursive)
        ]

    def remove(self, bucket: str, key: str) -> None:
        self.client.remove_object(bucket, key)


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = ObjectStore()
    return _store
