"""Search service: continuously index real incident snapshots into Qdrant with CLIP,
and answer natural-language queries by CLIP text-image similarity.
"""

from __future__ import annotations

import asyncio

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentigon_common.config import settings as common
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera, Incident, Signature
from sentigon_common.logging import get_logger
from sentigon_common.storage import get_store
from sqlalchemy import select

from .config import settings
from .embedder import ClipEmbedder

log = get_logger("search.service")


class SearchService:
    def __init__(self) -> None:
        self.embedder: ClipEmbedder | None = None
        self.client = QdrantClient(url=common.qdrant_url)
        self.store = get_store()
        self._indexed: set[str] = set()
        self._stop = asyncio.Event()
        self.stats = {"indexed": 0, "searches": 0}

    async def start(self) -> None:
        self.embedder = await asyncio.to_thread(ClipEmbedder)
        cols = {c.name for c in self.client.get_collections().collections}
        if settings.collection not in cols:
            self.client.create_collection(
                settings.collection,
                vectors_config=VectorParams(size=self.embedder.dim, distance=Distance.COSINE),
            )
            log.info("search.collection_created", name=settings.collection, dim=self.embedder.dim)

    async def index_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._index_batch()
            except Exception:  # noqa: BLE001
                log.exception("search.index_error")
            if await self._wait(settings.index_interval_seconds):
                break

    async def _wait(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _index_batch(self) -> None:
        assert self.embedder is not None
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(Incident, Signature.name, Camera.name)
                    .join(Signature, Incident.signature_id == Signature.id, isouter=True)
                    .join(Camera, Incident.camera_id == Camera.id, isouter=True)
                    .where(Incident.snapshot_ref.is_not(None))
                    .order_by(Incident.created_at.desc())
                    .limit(300)
                )
            ).all()
        todo = [r for r in rows if str(r[0].id) not in self._indexed][: settings.index_batch]
        points = []
        for inc, sig_name, cam_name in todo:
            self._indexed.add(str(inc.id))
            data = self._fetch(inc.snapshot_ref)
            if data is None:
                continue
            vec = await asyncio.to_thread(self.embedder.embed_image, data)
            if vec is None:
                continue
            points.append(
                PointStruct(
                    id=str(inc.id),
                    vector=vec,
                    payload={
                        "incident_id": str(inc.id),
                        "title": inc.title,
                        "signature": sig_name,
                        "camera": cam_name,
                        "severity": inc.severity.value,
                        "snapshot_ref": inc.snapshot_ref,
                        "ts": inc.created_at.isoformat(),
                    },
                )
            )
        if points:
            self.client.upsert(settings.collection, points=points)
            self.stats["indexed"] += len(points)
            log.info("search.indexed", count=len(points), total=self.stats["indexed"])

    def _fetch(self, ref: str | None) -> bytes | None:
        if not ref or "/" not in ref:
            return None
        bucket, key = ref.split("/", 1)
        try:
            return self.store.get_bytes(bucket, key)
        except Exception:  # noqa: BLE001
            return None

    def _presigned(self, ref: str | None) -> str | None:
        if not ref or "/" not in ref:
            return None
        bucket, key = ref.split("/", 1)
        try:
            return self.store.presigned_get(bucket, key, 3600)
        except Exception:  # noqa: BLE001
            return None

    def search(self, query: str, limit: int = 24) -> list[dict]:
        assert self.embedder is not None
        vec = self.embedder.embed_text(query)
        hits = self.client.query_points(settings.collection, query=vec, limit=limit).points
        self.stats["searches"] += 1
        return [
            {
                **h.payload,
                "score": round(h.score, 4),
                "snapshot_url": self._presigned(h.payload.get("snapshot_ref")),
            }
            for h in hits
        ]

    def stop(self) -> None:
        self._stop.set()
