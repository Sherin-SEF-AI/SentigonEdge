"""CrossSiteEngine: plate-sighting cross-site correlation over the bus.

Consumes `perception.objects`. Any detected object whose attributes carry an ANPR
`plate_hash` (a salted, site-agnostic vehicle identity) is persisted as a
PlateSighting tied to its camera's site. When the same plate hash has been seen at
two or more DISTINCT sites within the lookback window, a CrossSiteLink is upserted
(idempotent by entity_key), lighting up a "same vehicle, multiple sites" link.

ANPR is currently disabled, so plate_hash rarely appears; that is expected. The
engine is correct and idempotent, and lights up the moment ANPR + multi-site data
exist. No plate data is fabricated.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera, CrossSiteLink, PlateSighting
from sentigon_common.logging import get_logger
from sqlalchemy import select

from .config import settings

log = get_logger("crosssite.engine")

_CACHE_TTL_S = 60.0


def _parse_ts(value) -> datetime:
    """Parse an ISO frame_ts (or accept a datetime) into a tz-aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            dt = datetime.now(UTC)
    else:
        dt = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class CrossSiteEngine:
    def __init__(self) -> None:
        self.stats = {"plate_sightings": 0, "cross_site_links": 0, "objects_seen": 0}
        # camera_id (str) -> site_id (str) | None, refreshed periodically.
        self._site_cache: dict[str, str | None] = {}
        self._cache_loaded = 0.0

    def _maybe_expire_cache(self) -> None:
        now = time.monotonic()
        if now - self._cache_loaded > _CACHE_TTL_S:
            self._site_cache.clear()
            self._cache_loaded = now

    async def _camera_site(self, session, camera_id_str: str) -> str | None:
        """Resolve a camera's site_id (as str) with a small in-process cache."""
        if camera_id_str in self._site_cache:
            return self._site_cache[camera_id_str]
        cam_uuid = _as_uuid(camera_id_str)
        if cam_uuid is None:
            self._site_cache[camera_id_str] = None
            return None
        row = (
            await session.execute(select(Camera.site_id).where(Camera.id == cam_uuid))
        ).first()
        site_id = str(row[0]) if row and row[0] is not None else None
        self._site_cache[camera_id_str] = site_id
        return site_id

    async def handle(self, payload: dict, correlation_id: str | None) -> None:
        objects = payload.get("objects") or []
        self.stats["objects_seen"] += len(objects)
        # Guard: no plate_hash anywhere -> do not open a DB session.
        plate_objs = [o for o in objects if (o.get("attributes") or {}).get("plate_hash")]
        if not plate_objs:
            return

        camera_id_str = payload.get("camera_id")
        cam_uuid = _as_uuid(camera_id_str)
        if cam_uuid is None:
            return
        frame_ts = _parse_ts(payload.get("frame_ts"))
        self._maybe_expire_cache()

        async with async_session_factory() as session:
            site_id_str = await self._camera_site(session, str(camera_id_str))
            site_uuid = _as_uuid(site_id_str) if site_id_str else None
            for o in plate_objs:
                attrs = o.get("attributes") or {}
                plate_hash = attrs.get("plate_hash")
                plate_text = attrs.get("plate")
                sighting = PlateSighting(
                    plate_hash=plate_hash,
                    plate_text=plate_text,
                    site_id=site_uuid,
                    camera_id=cam_uuid,
                    track_id=o.get("track_id"),
                    ts=frame_ts,
                )
                session.add(sighting)
                await session.flush()  # make it visible to the correlation query below
                self.stats["plate_sightings"] += 1
                await self._correlate_plate(session, plate_hash, plate_text)
            await session.commit()

    async def _correlate_plate(self, session, plate_hash: str, plate_text: str | None) -> None:
        """If this plate hash has been seen at >= 2 distinct sites within the window,
        upsert its cross-site link (idempotent by entity_key)."""
        window_start = datetime.now(UTC) - timedelta(hours=settings.plate_window_hours)
        site_rows = (
            await session.execute(
                select(PlateSighting.site_id)
                .where(
                    PlateSighting.plate_hash == plate_hash,
                    PlateSighting.site_id.is_not(None),
                    PlateSighting.ts >= window_start,
                )
                .distinct()
            )
        ).all()
        site_ids = sorted({str(r[0]) for r in site_rows if r[0] is not None})
        if len(site_ids) < 2:
            return

        cam_rows = (
            await session.execute(
                select(PlateSighting.camera_id)
                .where(
                    PlateSighting.plate_hash == plate_hash,
                    PlateSighting.camera_id.is_not(None),
                    PlateSighting.ts >= window_start,
                )
                .distinct()
            )
        ).all()
        cameras = sorted({str(r[0]) for r in cam_rows if r[0] is not None})
        label = plate_text or f"vehicle {plate_hash[:8]}"
        now = datetime.now(UTC)

        link = (
            await session.execute(
                select(CrossSiteLink).where(
                    CrossSiteLink.entity_type == "plate",
                    CrossSiteLink.entity_key == plate_hash,
                )
            )
        ).scalar_one_or_none()

        if link is None:
            session.add(
                CrossSiteLink(
                    entity_type="plate",
                    entity_key=plate_hash,
                    label=label,
                    sites=site_ids,
                    site_count=len(site_ids),
                    sighting_count=1,
                    cameras=cameras,
                    detail={"plate_text": plate_text, "method": "anpr-hash"},
                    active=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            self.stats["cross_site_links"] += 1
            log.warning(
                "crosssite.plate_link_created",
                plate=plate_hash[:8],
                sites=len(site_ids),
            )
        else:
            link.sites = site_ids
            link.site_count = len(site_ids)
            link.sighting_count = (link.sighting_count or 0) + 1
            link.cameras = cameras
            link.last_seen_at = now
            link.active = True
            if plate_text and not link.label:
                link.label = label


def _as_uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
