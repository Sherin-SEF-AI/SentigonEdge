"""Sites CRUD + provisioning helpers (multi-site management).

Pure async DB functions used by the crosssite FastAPI app. A site is a facility
(a store, a plant, a campus). Provisioning returns a real onboarding bundle an edge
node uses to join the estate; the multi-site rollup powers the overview dashboard.
"""

from __future__ import annotations

import uuid

from sentigon_common.config import settings as common_settings
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera, CrossSiteLink, Incident, Site
from sentigon_common.schemas.enums import CameraStatus, IncidentStatus
from sqlalchemy import delete, func, select

from .config import settings

# Incident states that are NOT counted as "open" for the site rollup.
_CLOSED_STATES = (IncidentStatus.RESOLVED, IncidentStatus.FALSE_POSITIVE)
_PATCHABLE = ("name", "address", "timezone", "center", "meta")


def _as_uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _site_dict(s: Site, camera_count: int = 0, online_cameras: int = 0) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "address": s.address,
        "timezone": s.timezone,
        "center": s.center,
        "meta": s.meta or {},
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "camera_count": camera_count,
        "online_cameras": online_cameras,
    }


async def _camera_counts(session, site_id: uuid.UUID) -> tuple[int, int]:
    """Return (total_cameras, online_cameras) for a single site."""
    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Camera.status == CameraStatus.ONLINE),
            ).where(Camera.site_id == site_id)
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def list_sites() -> list[dict]:
    """All sites with per-site camera counts (total + online)."""
    async with async_session_factory() as session:
        sites = (await session.execute(select(Site).order_by(Site.name))).scalars().all()
        cam_rows = (
            await session.execute(
                select(
                    Camera.site_id,
                    func.count(),
                    func.count().filter(Camera.status == CameraStatus.ONLINE),
                )
                .where(Camera.site_id.is_not(None))
                .group_by(Camera.site_id)
            )
        ).all()
    counts = {str(sid): (int(total or 0), int(online or 0)) for sid, total, online in cam_rows}
    out: list[dict] = []
    for s in sites:
        total, online = counts.get(str(s.id), (0, 0))
        out.append(_site_dict(s, total, online))
    return out


async def get_site(site_id) -> dict | None:
    sid = _as_uuid(site_id)
    if sid is None:
        return None
    async with async_session_factory() as session:
        s = await session.get(Site, sid)
        if s is None:
            return None
        total, online = await _camera_counts(session, sid)
    return _site_dict(s, total, online)


async def create_site(data: dict) -> dict:
    async with async_session_factory() as session:
        s = Site(
            name=data["name"],
            address=data.get("address"),
            timezone=data.get("timezone") or "UTC",
            center=data.get("center"),
            meta=data.get("meta") or {},
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
    return _site_dict(s, 0, 0)


async def update_site(site_id, data: dict) -> dict | None:
    sid = _as_uuid(site_id)
    if sid is None:
        return None
    async with async_session_factory() as session:
        s = await session.get(Site, sid)
        if s is None:
            return None
        for field in _PATCHABLE:
            if field not in data:
                continue
            value = data[field]
            # name/timezone are NOT NULL: ignore an explicit null patch.
            if field in ("name", "timezone") and not value:
                continue
            setattr(s, field, value)
        await session.commit()
        await session.refresh(s)
        total, online = await _camera_counts(session, sid)
    return _site_dict(s, total, online)


async def delete_site(site_id) -> bool:
    """Delete a site. Cameras' site_id is SET NULL and buildings/zones cascade via
    the DB FK rules (a Core DELETE lets Postgres apply them directly)."""
    sid = _as_uuid(site_id)
    if sid is None:
        return False
    async with async_session_factory() as session:
        result = await session.execute(delete(Site).where(Site.id == sid))
        await session.commit()
    return (result.rowcount or 0) > 0


async def provision(site_id) -> dict | None:
    """Onboarding bundle for a new edge/site node: real service token + API/ingest
    URLs + a dotenv snippet + step-by-step provisioning instructions."""
    site = await get_site(site_id)
    if site is None:
        return None
    sid = site["id"]
    name = site["name"]
    token = common_settings.service_token
    env_snippet = "\n".join(
        [
            f"SITE_ID={sid}",
            f"SITE_NAME={name}",
            f"SENTIGON_API_URL={settings.api_url}",
            f"SENTIGON_INGEST_URL={settings.ingest_url}",
            f"SERVICE_TOKEN={token}",
        ]
    )
    return {
        "site_id": sid,
        "site_name": name,
        "service_token": token,
        "api_url": settings.api_url,
        "ingest_url": settings.ingest_url,
        "provisioning": {
            "onvif_discovery": "GET /discover on ingest",
            "register_camera": "POST /cameras with site_id",
            "access_events": "POST /access-events",
        },
        "env_snippet": env_snippet,
        "instructions": [
            f"1. Write the env_snippet below to /etc/sentigon/{sid}.env on the edge node.",
            "2. Authenticate every edge->cloud call with header X-Service-Token: <service_token>.",
            f"3. Discover ONVIF cameras on the local network: GET {settings.ingest_url}/discover.",
            f"4. Register each discovered camera to this site: POST {settings.api_url}/cameras "
            f"with site_id={sid}.",
            f"5. Bridge access-control / alarm panels by POSTing to {settings.api_url}/access-events.",
            "6. Confirm the site turns online in the multi-site overview once cameras report health.",
        ],
    }


async def site_rollup() -> list[dict]:
    """Multi-site overview: per-site camera counts, open incidents, and the number
    of active cross-site links that touch the site."""
    async with async_session_factory() as session:
        sites = (await session.execute(select(Site).order_by(Site.name))).scalars().all()
        cam_rows = (
            await session.execute(
                select(
                    Camera.site_id,
                    func.count(),
                    func.count().filter(Camera.status == CameraStatus.ONLINE),
                )
                .where(Camera.site_id.is_not(None))
                .group_by(Camera.site_id)
            )
        ).all()
        # open incidents per site: Incident -> Camera (site_id), excluding closed states
        inc_rows = (
            await session.execute(
                select(Camera.site_id, func.count())
                .select_from(Incident)
                .join(Camera, Incident.camera_id == Camera.id)
                .where(
                    Camera.site_id.is_not(None),
                    Incident.status.not_in(_CLOSED_STATES),
                )
                .group_by(Camera.site_id)
            )
        ).all()
        links = (
            await session.execute(
                select(CrossSiteLink.sites).where(CrossSiteLink.active.is_(True))
            )
        ).all()

    cam_counts = {str(sid): (int(t or 0), int(o or 0)) for sid, t, o in cam_rows}
    inc_counts = {str(sid): int(c or 0) for sid, c in inc_rows}
    link_counts: dict[str, int] = {}
    for (site_ids,) in links:
        for s in site_ids or []:
            key = str(s)
            link_counts[key] = link_counts.get(key, 0) + 1

    out: list[dict] = []
    for s in sites:
        sid = str(s.id)
        total, online = cam_counts.get(sid, (0, 0))
        out.append(
            {
                "site_id": sid,
                "name": s.name,
                "timezone": s.timezone,
                "center": s.center,
                "cameras_total": total,
                "cameras_online": online,
                "open_incidents": inc_counts.get(sid, 0),
                "cross_site_links": link_counts.get(sid, 0),
            }
        )
    return out
