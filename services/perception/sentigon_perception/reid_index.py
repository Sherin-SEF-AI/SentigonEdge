"""Qdrant ReID appearance index (cross-camera search foundation)."""

from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentigon_common.config import settings as common_settings
from sentigon_common.logging import get_logger

log = get_logger("reid")


class ReidIndex:
    def __init__(self, collection: str, dim: int) -> None:
        self.client = QdrantClient(url=common_settings.qdrant_url)
        self.collection = collection
        self.dim = dim
        self._ensure()

    def _ensure(self) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            self.client.create_collection(
                self.collection,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )
            log.info("reid.collection_created", name=self.collection, dim=self.dim)

    def upsert(self, vectors: list[list[float]], payloads: list[dict]) -> list[str]:
        points, ids = [], []
        for vec, payload in zip(vectors, payloads, strict=False):
            pid = str(uuid.uuid4())
            ids.append(pid)
            points.append(PointStruct(id=pid, vector=vec, payload=payload))
        if points:
            self.client.upsert(self.collection, points=points)
        return ids

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count
