"""ReidScanner: periodic cross-site appearance correlation via Qdrant.

The `reid` collection stores appearance vectors with payload {camera_id (str),
track_id, object_class, ts, color?} but NO site_id. To correlate across sites we
map camera_id -> site_id via the DB Camera table. For each recent point at site A,
we run a filtered nearest-neighbour search restricted to cameras of OTHER sites; a
match above threshold whose camera belongs to a different site is a cross-site
appearance link (the same person/vehicle seen at two sites).

Current single-site reality: fewer than two sites have cameras, so the scan returns
early each cycle. That is correct, not an error. It lights up when multi-site data
exists. Qdrant calls are wrapped defensively; a missing/empty reid collection or a
down Qdrant returns quietly.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from qdrant_client import QdrantClient
from qdrant_client import models as qm
from sentigon_common.config import settings as common_settings
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera, CrossSiteLink
from sentigon_common.logging import get_logger
from sqlalchemy import select

from .config import settings

log = get_logger("crosssite.reidscan")

_MAX_LINKS_PER_SCAN = 50  # cap new links per cycle to avoid storms


class ReidScanner:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.client = QdrantClient(url=common_settings.qdrant_url)
        # entity_keys already linked this process run (in-memory dedup; DB is source of truth)
        self._seen_keys: set[str] = set()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._scan()
            except Exception:  # noqa: BLE001
                log.exception("crosssite.reidscan_error")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.reid_scan_seconds)

    async def _scan(self) -> None:
        # 1. camera_id (str) -> site_id (str) map for cameras that have a site.
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(Camera.id, Camera.site_id).where(Camera.site_id.is_not(None))
                )
            ).all()
        cam_site = {str(cid): str(sid) for cid, sid in rows if sid is not None}

        # 2. Need >= 2 distinct sites with cameras to correlate cross-site.
        site_cams: dict[str, list[str]] = {}
        for cam, site in cam_site.items():
            site_cams.setdefault(site, []).append(cam)
        if len(site_cams) < 2:
            return

        # 3. Scroll recent points from the reid collection (sync client -> to_thread).
        try:
            points, _ = await asyncio.to_thread(
                self.client.scroll,
                collection_name=settings.reid_collection,
                limit=settings.reid_scan_limit,
                with_vectors=True,
                with_payload=True,
            )
        except Exception:  # noqa: BLE001 -- collection missing / qdrant down: nothing to do
            return
        if not points:
            return

        new_links = 0
        run_seen: set[str] = set()
        for p in points:
            if new_links >= _MAX_LINKS_PER_SCAN:
                break
            payload = p.payload or {}
            cam_a = str(payload["camera_id"]) if payload.get("camera_id") is not None else None
            site_a = cam_site.get(cam_a) if cam_a else None
            vector = p.vector
            if site_a is None or vector is None:
                continue

            # cameras belonging to OTHER sites
            other_cams = [c for s, cams in site_cams.items() if s != site_a for c in cams]
            if not other_cams:
                continue
            try:
                hits = await asyncio.to_thread(self._search, vector, other_cams)
            except Exception:  # noqa: BLE001
                continue

            for h in hits:
                score = float(h.score)
                if score < settings.reid_score_threshold:
                    break  # hits are sorted descending
                hp = h.payload or {}
                cam_b = str(hp["camera_id"]) if hp.get("camera_id") is not None else None
                site_b = cam_site.get(cam_b) if cam_b else None
                if site_b is None or site_b == site_a:
                    continue
                lo, hi = sorted([str(p.id), str(h.id)])
                entity_key = f"appear:{lo}:{hi}"
                if entity_key in self._seen_keys or entity_key in run_seen:
                    continue
                run_seen.add(entity_key)
                object_class = payload.get("object_class") or hp.get("object_class") or "object"
                created = await self._upsert_appearance(
                    entity_key, [site_a, site_b], score, [cam_a, cam_b], object_class, lo, hi
                )
                self._seen_keys.add(entity_key)
                if created:
                    new_links += 1
                break  # one cross-site link per source point

    def _search(self, vector, other_cams: list[str]):
        """Blocking Qdrant nearest-neighbour search restricted to other-site cameras."""
        return self.client.query_points(
            collection_name=settings.reid_collection,
            query=vector,
            limit=3,
            with_payload=True,
            query_filter=qm.Filter(
                must=[qm.FieldCondition(key="camera_id", match=qm.MatchAny(any=other_cams))]
            ),
        ).points

    async def _upsert_appearance(
        self,
        entity_key: str,
        sites: list[str],
        score: float,
        cameras: list[str],
        object_class: str,
        id_a: str,
        id_b: str,
    ) -> bool:
        """Idempotent by entity_key. Returns True if a new link was created."""
        site_list = sorted({s for s in sites if s})
        camera_list = [c for c in cameras if c]
        now = datetime.now(UTC)
        async with async_session_factory() as session:
            existing = (
                await session.execute(
                    select(CrossSiteLink).where(
                        CrossSiteLink.entity_type == "appearance",
                        CrossSiteLink.entity_key == entity_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.last_seen_at = now
                existing.score = max(existing.score or 0.0, round(score, 4))
                existing.active = True
                await session.commit()
                return False
            session.add(
                CrossSiteLink(
                    entity_type="appearance",
                    entity_key=entity_key,
                    label=f"{object_class} appearance",
                    sites=site_list,
                    site_count=len(site_list),
                    sighting_count=1,
                    cameras=camera_list,
                    score=round(score, 4),
                    detail={"points": [id_a, id_b], "method": "osnet-reid"},
                    active=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            await session.commit()
        self.engine.stats["cross_site_links"] += 1
        log.warning("crosssite.appearance_link_created", key=entity_key, score=round(score, 4))
        return True
