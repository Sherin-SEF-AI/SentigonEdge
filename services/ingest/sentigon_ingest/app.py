"""Ingest FastAPI app: camera onboarding, ONVIF discovery, stream URLs for the
video wall, and per-stream health. The capture/record/health workers run under the
IngestManager for the app lifetime.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sentigon_common.auth import install_auth_middleware
from sentigon_common.config import settings
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera
from sentigon_common.health import (
    check_kafka,
    check_minio,
    check_postgres,
    check_redis,
    make_health_router,
)
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.schemas.entities import CameraCreate, CameraOut
from sqlalchemy import select

from . import onvif
from .manager import IngestManager
from .mediamtx import MediaMTXClient

log = get_logger("ingest.app")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("ingest")
    manager = IngestManager()
    await manager.start()
    app.state.manager = manager
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="Sentigon Ingest", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_auth_middleware(app, protect_reads=False)
app.include_router(
    make_health_router(
        "ingest",
        {
            "postgres": check_postgres,
            "redis": check_redis,
            "kafka": check_kafka,
            "minio": check_minio,
        },
    )
)

_mtx = MediaMTXClient()


def _manager(request: Request) -> IngestManager:
    return request.app.state.manager


@app.get("/cameras")
async def list_cameras(request: Request) -> list[dict]:
    manager = _manager(request)
    async with async_session_factory() as session:
        cameras = (await session.execute(select(Camera))).scalars().all()
    live = {s["camera_id"]: s for s in manager.list_streams()}
    out = []
    for cam in cameras:
        stream = live.get(str(cam.id))
        out.append(
            {
                **CameraOut.model_validate(cam).model_dump(mode="json"),
                "whep_url": _mtx.whep_url(cam.rtsp_uri),
                "hls_url": _mtx.hls_url(cam.rtsp_uri),
                "live_health": stream["health"] if stream else None,
            }
        )
    return out


@app.post("/cameras", status_code=201)
async def create_camera(payload: CameraCreate, request: Request) -> CameraOut:
    async with async_session_factory() as session:
        cam = Camera(
            name=payload.name,
            rtsp_uri=payload.rtsp_uri,
            onvif_uri=payload.onvif_uri,
            site_id=payload.site_id,
            fps=payload.fps,
            resolution=payload.resolution,
            ptz_capable=payload.ptz_capable,
            meta=payload.meta,
        )
        session.add(cam)
        await session.commit()
        await session.refresh(cam)
        result = CameraOut.model_validate(cam)
    await _manager(request).add_camera(cam.id, cam.name, cam.rtsp_uri)
    return result


@app.get("/cameras/{camera_id}")
async def get_camera(camera_id: uuid.UUID) -> dict:
    async with async_session_factory() as session:
        cam = await session.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404, "camera not found")
    return {
        **CameraOut.model_validate(cam).model_dump(mode="json"),
        "whep_url": _mtx.whep_url(cam.rtsp_uri),
        "hls_url": _mtx.hls_url(cam.rtsp_uri),
    }


@app.post("/cameras/{camera_id}/ptz")
async def ptz_control(camera_id: uuid.UUID, body: dict = Body(...)) -> dict:
    """ONVIF Profile S PTZ control. op = move|stop|preset|list. A camera without
    an ONVIF endpoint honestly reports not-supported."""
    from .ptz import PtzController

    async with async_session_factory() as session:
        cam = await session.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404, "camera not found")
    creds = (cam.meta or {}).get("ptz_credentials", {})
    ctl = PtzController(cam.onvif_uri, creds.get("user", "admin"), creds.get("password", ""))
    op = body.get("op", "list")
    if not ctl.supported:
        return {"ok": False, "ptz_supported": False, "detail": "camera has no ONVIF PTZ endpoint"}
    if op == "move":
        return await ctl.move(float(body.get("pan", 0)), float(body.get("tilt", 0)), float(body.get("zoom", 0)))
    if op == "stop":
        return await ctl.stop()
    if op == "preset":
        return await ctl.goto_preset(str(body.get("preset", "")))
    return await ctl.list_presets()


@app.post("/cameras/{camera_id}/stop")
async def stop_camera(camera_id: uuid.UUID, request: Request) -> dict:
    ok = _manager(request).stop_worker(camera_id)
    return {"stopped": ok}


@app.post("/cameras/{camera_id}/start")
async def start_camera(camera_id: uuid.UUID, request: Request) -> dict:
    async with async_session_factory() as session:
        cam = await session.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404, "camera not found")
    await _manager(request).add_camera(cam.id, cam.name, cam.rtsp_uri)
    return {"started": True}


@app.get("/streams")
async def streams(request: Request) -> list[dict]:
    return _manager(request).list_streams()


@app.post("/cameras/{camera_id}/snapshot")
async def snapshot(camera_id: uuid.UUID, request: Request) -> dict:
    import asyncio

    result = await asyncio.get_running_loop().run_in_executor(
        None, _manager(request).snapshot, camera_id
    )
    if result is None:
        raise HTTPException(404, "no frame available for camera")
    return result


@app.post("/cameras/{camera_id}/clip")
async def clip(camera_id: uuid.UUID, request: Request) -> dict:
    import asyncio

    result = await asyncio.get_running_loop().run_in_executor(
        None, _manager(request).clip, camera_id
    )
    if result is None:
        raise HTTPException(404, "no pre-roll available for camera")
    return result


@app.get("/discover")
async def discover(timeout: float = 4.0) -> dict:
    devices = await onvif.discover(timeout=timeout)
    return {"count": len(devices), "devices": devices}


@app.get("/health/summary")
async def health_summary(request: Request) -> dict:
    streams = _manager(request).list_streams()
    online = sum(1 for s in streams if s["health"]["status"] == "online")
    total_fps = round(sum(s["health"]["fps"] for s in streams), 1)
    return {
        "cameras": len(streams),
        "online": online,
        "aggregate_fps": total_fps,
        "mediamtx": settings.mediamtx_webrtc,
    }
