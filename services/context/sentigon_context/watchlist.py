"""Watchlist (appearance-based BOLO) live matcher.

Consumes `perception.embeddings`. Each active watchlist entry is one reference
appearance embedding (enrolled from a real captured track) stored in the Qdrant
`watchlist` collection. When a live detection's appearance matches an entry above
that entry's cosine threshold, a "Watchlist Hit" incident is fired through the same
context incident pipeline (Event -> Incident -> events.candidate -> VLM verify).

Matching is gated by category: a person entry only matches person detections, a
vehicle entry only matches vehicle detections, so a car never matches a person.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from sentigon_common.config import settings as common_settings
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import WatchlistEntry
from sentigon_common.logging import get_logger
from sqlalchemy import select, update

from .config import settings

log = get_logger("context.watchlist")

COLLECTION = "watchlist"
_VEHICLE = {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw"}


def _category_of(object_class: str) -> str:
    return "vehicle" if object_class in _VEHICLE else "person"


class WatchlistMatcher:
    """Live appearance matcher. Shares the engine's `_fire` so hits become real
    incidents identical in shape to signature-driven ones."""

    def __init__(self, engine, dim: int = 512) -> None:
        self.engine = engine
        self.client = QdrantClient(url=common_settings.qdrant_url)
        self.dim = dim
        self._entries: dict[str, dict] = {}  # watchlist_id -> {threshold, category, label, ...}
        self._loaded = 0.0
        self._ensure()

    def _ensure(self) -> None:
        names = {c.name for c in self.client.get_collections().collections}
        if COLLECTION not in names:
            self.client.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )
            log.info("watchlist.collection_created", dim=self.dim)

    async def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._loaded < settings.meta_refresh_seconds:
            return
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(WatchlistEntry).where(WatchlistEntry.active.is_(True))
                )
            ).scalars().all()
        self._entries = {
            str(r.id): {
                "threshold": r.threshold,
                "category": r.category,
                "label": r.label,
                "object_class": r.object_class,
                "reason": r.reason,
            }
            for r in rows
        }
        self._loaded = time.monotonic()

    async def handle(self, payload: dict, correlation_id: str | None) -> None:
        await self.refresh()
        if not self._entries:
            return
        emb = payload.get("embedding")
        if not emb:
            return
        try:
            camera_id = uuid.UUID(payload["camera_id"])
        except (KeyError, ValueError):
            return
        object_class = payload.get("object_class", "")
        track_id = payload.get("track_id")
        live_category = _category_of(object_class)

        hits = self.client.query_points(
            COLLECTION, query=emb, limit=3, with_payload=True
        ).points
        for h in hits:
            wl_id = (h.payload or {}).get("watchlist_id")
            entry = self._entries.get(wl_id)
            if entry is None:
                continue
            # category gate: a person entry never matches a vehicle and vice versa
            if entry["category"] != live_category:
                continue
            score = float(h.score)
            if score < entry["threshold"]:
                continue
            await self._fire_hit(camera_id, track_id, object_class, wl_id, entry, score)
            break  # one hit per embedding (highest-scoring matching entry)

    async def _fire_hit(
        self, camera_id: uuid.UUID, track_id, object_class: str,
        wl_id: str, entry: dict, score: float,
    ) -> None:
        from .engine import Candidate  # local import avoids a cycle

        now = time.time()
        cand = Candidate(
            signature_name="Watchlist Hit",
            event_type="watchlist.hit",
            title=f"Watchlist candidate: {entry['label']}",
            zone_id=None,
            scope=f"wl:{wl_id}",  # per-entry cooldown
            confidence=round(min(score, 1.0), 3),
            object_refs={"camera_id": str(camera_id), "track_id": track_id},
            context={
                "watchlist_id": wl_id,
                "label": entry["label"],
                "category": entry["category"],
                "watch_reason": entry.get("reason"),
                "match_score": round(score, 4),
                "matched_class": object_class,
                # honest framing: appearance similarity on a generic backbone is a
                # candidate for human/VLM confirmation, not a positive identification
                "method": "appearance-reid",
                "requires_confirmation": True,
            },
        )
        before = self.engine.stats.get("incidents", 0)
        await self.engine._fire(camera_id, cand, now)
        # only count a hit if _fire actually created one (cooldown may suppress it)
        if self.engine.stats.get("incidents", 0) > before:
            async with async_session_factory() as session:
                await session.execute(
                    update(WatchlistEntry)
                    .where(WatchlistEntry.id == uuid.UUID(wl_id))
                    .values(
                        hit_count=WatchlistEntry.hit_count + 1,
                        last_hit_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            log.warning(
                "watchlist.hit", label=entry["label"], camera=str(camera_id),
                track=track_id, score=round(score, 4),
            )
