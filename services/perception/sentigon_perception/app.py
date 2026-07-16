"""Perception FastAPI app: overlay WebSocket + latest-detections/zones/stats REST.

The console subscribes to /ws/objects/{camera_id} and draws boxes, track IDs, and
zone polygons over the WebRTC video, client-side, from the metadata bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sentigon_common.auth import install_auth_middleware
from sentigon_common.config import settings as common_settings
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera
from sentigon_common.health import check_kafka, check_postgres, check_qdrant, make_health_router
from sentigon_common.logging import configure_logging, get_logger

from .config import settings
from .manager import PerceptionManager

log = get_logger("perception.app")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("perception")
    manager = PerceptionManager()
    await manager.start()
    app.state.manager = manager
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="Sentigon Perception", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=common_settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
install_auth_middleware(app, protect_reads=False)
app.include_router(
    make_health_router(
        "perception",
        {"postgres": check_postgres, "kafka": check_kafka, "qdrant": check_qdrant},
    )
)


def _manager(request: Request) -> PerceptionManager:
    return request.app.state.manager


@app.get("/stats")
async def stats(request: Request) -> dict:
    m = _manager(request)
    return {
        "device": m.device,
        "model": settings.model,
        "embed_backbone": settings.embed_backbone,
        "reid_indexed": m.reid_count(),
        "cameras": m.stats(),
    }


@app.get("/model")
async def get_model(request: Request) -> dict:
    return {"current_model": _manager(request).current_model()}


@app.post("/model/swap")
async def swap_model(request: Request, body: dict = Body(...)) -> dict:
    """Hot-swap the detector model across all camera workers with zero stream drop."""
    model = body.get("model")
    if not model:
        raise HTTPException(400, "model required")
    swapped = await asyncio.to_thread(_manager(request).swap_model, model)
    return {"swapped": swapped, "model": model, "current_model": _manager(request).current_model()}


@app.get("/objects/{camera_id}")
async def objects(camera_id: uuid.UUID, request: Request) -> dict:
    return _manager(request).latest.get(camera_id) or {"camera_id": str(camera_id), "objects": []}


@app.get("/zones/{camera_id}")
async def zones(camera_id: uuid.UUID, request: Request) -> dict:
    return {"camera_id": str(camera_id), "zones": _manager(request).zones.overlay(camera_id)}


@app.post("/cameras/{camera_id}/stop")
async def stop_camera(camera_id: uuid.UUID, request: Request) -> dict:
    return {"stopped": _manager(request).stop_worker(camera_id)}


@app.post("/cameras/{camera_id}/start")
async def start_camera(camera_id: uuid.UUID, request: Request) -> dict:
    async with async_session_factory() as session:
        cam = await session.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(404, "camera not found")
    await _manager(request).add_camera(cam.id, cam.name, cam.rtsp_uri)
    return {"started": True}


@app.websocket("/ws/objects/{camera_id}")
async def ws_objects(websocket: WebSocket, camera_id: str) -> None:
    await websocket.accept()
    manager: PerceptionManager = websocket.app.state.manager
    try:
        cid = uuid.UUID(camera_id)
    except ValueError:
        await websocket.close(code=1008)
        return
    interval = 1.0 / settings.ws_hz
    last_seq = -1
    # also send zones once up front so the overlay can draw them
    with contextlib.suppress(Exception):
        await websocket.send_json({"type": "zones", "zones": manager.zones.overlay(cid)})
    try:
        while True:
            payload = manager.latest.get(cid)
            if payload and payload.get("seq") != last_seq:
                last_seq = payload.get("seq")
                await websocket.send_json({"type": "objects", **payload})
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("perception.ws_error", camera=camera_id)
