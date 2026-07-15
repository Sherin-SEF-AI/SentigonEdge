"""Search FastAPI app: natural-language search over captured evidence."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera
from sentigon_common.health import check_postgres, check_qdrant, make_health_router
from sentigon_common.logging import configure_logging
from sqlalchemy import select

from .config import settings
from .reid import ReidTrajectory
from .service import SearchService


async def _camera_names() -> dict[str, str]:
    async with async_session_factory() as session:
        rows = (await session.execute(select(Camera.id, Camera.name))).all()
    return {str(cid): name for cid, name in rows}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("search")
    service = SearchService()
    await service.start()
    task = asyncio.create_task(service.index_loop())
    app.state.service = service
    app.state.reid = ReidTrajectory()
    app.state.task = task
    try:
        yield
    finally:
        service.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


app = FastAPI(title="Sentigon Search", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(
    make_health_router("search", {"postgres": check_postgres, "qdrant": check_qdrant})
)


@app.get("/search")
async def search(request: Request, q: str = Query(..., min_length=1), limit: int = 24) -> dict:
    service: SearchService = request.app.state.service
    results = await asyncio.to_thread(service.search, q, limit)
    return {"query": q, "count": len(results), "results": results}


@app.get("/stats")
async def stats(request: Request) -> dict:
    service: SearchService = request.app.state.service
    return {"model": settings.clip_model, "collection": settings.collection, **service.stats}


_COLORS = {"white", "black", "gray", "red", "orange", "yellow", "green", "cyan", "blue", "purple", "silver"}
_VTYPES = {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw", "pickup", "van"}
_VTYPE_ALIAS = {"pickup": "truck", "van": "truck"}


@app.get("/vehicles/search")
async def vehicle_search(request: Request, q: str = Query(..., min_length=1), limit: int = 30) -> dict:
    """Natural-language vehicle search, e.g. 'white truck'. Parses colour + type
    from the query and filters the indexed vehicle sightings."""
    reid: ReidTrajectory = request.app.state.reid
    words = q.lower().replace(",", " ").split()
    color = next((w for w in words if w in _COLORS), None)
    vtype = next((_VTYPE_ALIAS.get(w, w) for w in words if w in _VTYPES), None)
    names = await _camera_names()
    rows = await asyncio.to_thread(reid.search_vehicles, color, vtype, limit)
    for r in rows:
        r["camera"] = names.get(r["camera_id"], (r["camera_id"] or "")[:8])
    return {"query": q, "parsed": {"color": color, "type": vtype}, "count": len(rows), "results": rows}


@app.get("/reid/tracks")
async def reid_tracks(request: Request, limit: int = 40) -> dict:
    reid: ReidTrajectory = request.app.state.reid
    names = await _camera_names()
    rows = await asyncio.to_thread(reid.list_tracks, limit)
    for r in rows:
        r["camera"] = names.get(r["camera_id"], r["camera_id"][:8])
    return {"count": len(rows), "tracks": rows}


@app.get("/reid/trajectory")
async def reid_trajectory(
    request: Request,
    camera_id: str = Query(...),
    track_id: int = Query(...),
    min_score: float = 0.55,
) -> dict:
    reid: ReidTrajectory = request.app.state.reid
    names = await _camera_names()
    other_cams = [cid for cid in names if cid != camera_id]
    result = await asyncio.to_thread(
        reid.trajectory, camera_id, track_id, other_cams, 25, min_score
    )

    def _name(cid: str) -> str:
        return names.get(cid, cid[:8])

    if result.get("found"):
        result["query"]["camera"] = _name(result["query"]["camera_id"])
        for m in result["cross_camera_matches"]:
            m["camera"] = _name(m["camera_id"])
        for e in result["timeline"]:
            e["camera"] = _name(e["camera_id"])
    return result
