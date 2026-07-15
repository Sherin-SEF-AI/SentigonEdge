"""CrossSite service: Sites CRUD/provisioning + cross-site entity correlation API.

Under one FastAPI app runs (1) a Kafka consumer that correlates plate sightings
across sites and (2) a periodic Qdrant scan that correlates appearance (ReID)
vectors across sites. The HTTP surface exposes multi-site management, the overview
rollup, and the cross-site links + plate timeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sentigon_common.auth import user_from_token
from sentigon_common.config import settings as common_settings
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera, CrossSiteLink, PlateSighting, Site
from sentigon_common.health import check_postgres, make_health_router
from sentigon_common.kafka import run_consumer
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.metrics import mount_metrics
from sentigon_common.schemas.bus import Topics
from sentigon_common.schemas.enums import UserRole
from sqlalchemy import func, select

from . import sites as sites_mod
from .engine import CrossSiteEngine
from .reidscan import ReidScanner

log = get_logger("crosssite.app")


# ── auth ──────────────────────────────────────────────────────


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def require_admin(
    authorization: str | None = Header(None),
    x_service_token: str | None = Header(None),
) -> None:
    """Admin-only guard for site create/update/delete/provision. Internal service
    calls pass via the shared service token."""
    if x_service_token and x_service_token == common_settings.service_token:
        return
    user = await user_from_token(_bearer_token(authorization))
    if user is None or user.role != UserRole.ADMIN:
        raise HTTPException(status_code=401, detail="admin auth required")


# ── lifespan: bus plate consumer + periodic reid scan ─────────


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("crosssite")
    engine = CrossSiteEngine()
    scanner = ReidScanner(engine)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(
            [Topics.PERCEPTION_OBJECTS],
            "crosssite-plates",
            engine.handle,
            stop_event=stop,
            auto_offset_reset="latest",
        )
    )
    scan = asyncio.create_task(scanner.run(stop))
    app.state.engine = engine
    app.state.scanner = scanner
    app.state.stop = stop
    log.info("crosssite.started")
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        scan.cancel()
        for t in (task, scan):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


app = FastAPI(title="Sentigon CrossSite", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
app.include_router(make_health_router("crosssite", {"postgres": check_postgres}))
mount_metrics(app)


# ── helpers ───────────────────────────────────────────────────


def _as_uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _link_dict(link: CrossSiteLink, site_names: dict[str, str]) -> dict:
    site_list = link.sites or []
    return {
        "id": str(link.id),
        "entity_type": link.entity_type,
        "entity_key": link.entity_key,
        "label": link.label,
        "sites": [{"id": str(s), "name": site_names.get(str(s))} for s in site_list],
        "site_count": link.site_count,
        "sighting_count": link.sighting_count,
        "cameras": link.cameras or [],
        "score": link.score,
        "active": link.active,
        "first_seen_at": link.first_seen_at.isoformat() if link.first_seen_at else None,
        "last_seen_at": link.last_seen_at.isoformat() if link.last_seen_at else None,
        "detail": link.detail or {},
    }


# ── stats ─────────────────────────────────────────────────────


@app.get("/stats")
async def stats(request: Request) -> dict:
    engine: CrossSiteEngine = request.app.state.engine
    async with async_session_factory() as session:
        site_count = (
            await session.execute(select(func.count()).select_from(Site))
        ).scalar_one()
        link_count = (
            await session.execute(
                select(func.count())
                .select_from(CrossSiteLink)
                .where(CrossSiteLink.active.is_(True))
            )
        ).scalar_one()
    return {
        **engine.stats,
        "sites": int(site_count or 0),
        "cross_site_links": int(link_count or 0),
    }


# ── sites CRUD + provisioning ─────────────────────────────────


@app.get("/sites")
async def list_sites() -> list[dict]:
    return await sites_mod.list_sites()


@app.get("/sites/{site_id}")
async def get_site(site_id: str) -> dict:
    site = await sites_mod.get_site(site_id)
    if site is None:
        raise HTTPException(404, "site not found")
    return site


@app.post("/sites", status_code=201, dependencies=[Depends(require_admin)])
async def create_site(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict) or not body.get("name"):
        raise HTTPException(422, "name is required")
    return await sites_mod.create_site(body)


@app.patch("/sites/{site_id}", dependencies=[Depends(require_admin)])
async def update_site(site_id: str, request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "invalid body")
    site = await sites_mod.update_site(site_id, body)
    if site is None:
        raise HTTPException(404, "site not found")
    return site


@app.delete("/sites/{site_id}", dependencies=[Depends(require_admin)])
async def delete_site(site_id: str) -> dict:
    ok = await sites_mod.delete_site(site_id)
    if not ok:
        raise HTTPException(404, "site not found")
    return {"deleted": True, "site_id": site_id}


@app.post("/sites/{site_id}/provision", dependencies=[Depends(require_admin)])
async def provision_site(site_id: str) -> dict:
    bundle = await sites_mod.provision(site_id)
    if bundle is None:
        raise HTTPException(404, "site not found")
    return bundle


# ── multi-site overview ───────────────────────────────────────


@app.get("/overview")
async def overview() -> list[dict]:
    return await sites_mod.site_rollup()


# ── cross-site intelligence ───────────────────────────────────


@app.get("/crosssite/links")
async def crosssite_links(
    entity_type: str | None = Query(None),
    active: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    async with async_session_factory() as session:
        stmt = select(CrossSiteLink)
        if entity_type:
            stmt = stmt.where(CrossSiteLink.entity_type == entity_type)
        if active is not None:
            stmt = stmt.where(CrossSiteLink.active.is_(active))
        stmt = stmt.order_by(CrossSiteLink.last_seen_at.desc()).limit(limit)
        links = (await session.execute(stmt)).scalars().all()

        site_ids: set[str] = set()
        for link in links:
            for s in link.sites or []:
                site_ids.add(str(s))
        names: dict[str, str] = {}
        uuids = [u for u in (_as_uuid(s) for s in site_ids) if u is not None]
        if uuids:
            rows = (
                await session.execute(select(Site.id, Site.name).where(Site.id.in_(uuids)))
            ).all()
            names = {str(i): n for i, n in rows}
    return [_link_dict(link, names) for link in links]


@app.get("/crosssite/plate/{plate_hash}")
async def plate_timeline(plate_hash: str) -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(PlateSighting)
                .where(PlateSighting.plate_hash == plate_hash)
                .order_by(PlateSighting.ts)
            )
        ).scalars().all()

        site_ids = {s.site_id for s in rows if s.site_id is not None}
        cam_ids = {s.camera_id for s in rows if s.camera_id is not None}
        site_names: dict[str, str] = {}
        cam_names: dict[str, str] = {}
        if site_ids:
            sr = (
                await session.execute(
                    select(Site.id, Site.name).where(Site.id.in_(site_ids))
                )
            ).all()
            site_names = {str(i): n for i, n in sr}
        if cam_ids:
            cr = (
                await session.execute(
                    select(Camera.id, Camera.name).where(Camera.id.in_(cam_ids))
                )
            ).all()
            cam_names = {str(i): n for i, n in cr}

    return [
        {
            "ts": s.ts.isoformat() if s.ts else None,
            "site_id": str(s.site_id) if s.site_id else None,
            "site_name": site_names.get(str(s.site_id)) if s.site_id else None,
            "camera_id": str(s.camera_id) if s.camera_id else None,
            "camera_name": cam_names.get(str(s.camera_id)) if s.camera_id else None,
            "plate_text": s.plate_text,
            "track_id": s.track_id,
        }
        for s in rows
    ]
