"""Context service: runs the Kafka consumer of perception.objects under a FastAPI
app (for health + stats + hot-reload trigger)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from sentigon_common.health import check_kafka, check_postgres, make_health_router
from sentigon_common.kafka import run_consumer
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.schemas.bus import Topics

from .engine import ContextEngine
from .watchlist import WatchlistMatcher

log = get_logger("context.app")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("context")
    engine = ContextEngine()
    await engine.start()
    matcher = WatchlistMatcher(engine)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(
            [Topics.PERCEPTION_OBJECTS],
            "context-engine",
            engine.handle,
            stop_event=stop,
            auto_offset_reset="latest",
        )
    )
    wl_task = asyncio.create_task(
        run_consumer(
            [Topics.PERCEPTION_EMBEDDINGS],
            "context-watchlist",
            matcher.handle,
            stop_event=stop,
            auto_offset_reset="latest",
        )
    )
    app.state.engine = engine
    app.state.matcher = matcher
    app.state.stop = stop
    app.state.task = task
    try:
        yield
    finally:
        stop.set()
        for t in (task, wl_task):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await engine.stop()


app = FastAPI(title="Sentigon Context", version="0.1.0", lifespan=lifespan)
app.include_router(
    make_health_router("context", {"postgres": check_postgres, "kafka": check_kafka})
)


@app.get("/stats")
async def stats(request: Request) -> dict:
    engine: ContextEngine = request.app.state.engine
    return {
        "cameras_tracked": len(engine.store),
        "signatures_active": len(engine.registry._by_name),
        "zones_loaded": len(engine.zone_meta),
        **engine.stats,
    }


@app.post("/access-event")
async def access_event(request: Request) -> dict:
    """Correlate an access-control event with live video to fire composite signals."""
    engine: ContextEngine = request.app.state.engine
    payload = await request.json()
    return await engine.handle_access(payload)


@app.post("/fall-event")
async def fall_event(request: Request) -> dict:
    """A pose-based fall detected by perception; fire a Person Fall incident."""
    engine: ContextEngine = request.app.state.engine
    payload = await request.json()
    return await engine.handle_fall(payload)


@app.post("/reload")
async def reload_signatures(request: Request) -> dict:
    engine: ContextEngine = request.app.state.engine
    await engine.registry.refresh(force=True)
    await engine._load_zones(force=True)
    return {"signatures": len(engine.registry._by_name), "zones": len(engine.zone_meta)}
