"""Reason service: runs the events.candidate consumer under a FastAPI app."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from sentigon_common.auth import install_auth_middleware
from sentigon_common.config import settings as common
from sentigon_common.health import check_kafka, check_postgres, make_health_router
from sentigon_common.kafka import run_consumer
from sentigon_common.logging import configure_logging
from sentigon_common.schemas.bus import Topics

from .config import settings
from .engine import ReasonEngine
from .grounding import fetch_bytes, fresh_frame, ground


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("reason")
    engine = ReasonEngine()
    await engine.start()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(
            [Topics.EVENTS_CANDIDATE],
            "reason-verifier",
            engine.handle,
            stop_event=stop,
            auto_offset_reset="latest",
        )
    )
    app.state.engine = engine
    app.state.stop = stop
    app.state.task = task
    # natural-language activity-notification evaluator (VLM open-set detection)
    nl_task = None
    if settings.nl_enabled:
        from .nl_alerts import NLAlertEvaluator

        nl_eval = NLAlertEvaluator()
        app.state.nl_eval = nl_eval
        nl_task = asyncio.create_task(nl_eval.run(stop))
    # proactive VLM sweep: the detector for the open-vocab ("gemini") catalog
    sweep_task = None
    if settings.vlm_sweep_enabled:
        from .signature_sweep import VLMSignatureSweeper

        sweeper = VLMSignatureSweeper(engine.producer)
        app.state.sweeper = sweeper
        sweep_task = asyncio.create_task(sweeper.run(stop))
    try:
        yield
    finally:
        stop.set()
        for t in (task, nl_task, sweep_task):
            if t is not None:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        await engine.stop()


app = FastAPI(title="Sentigon Reason", version="0.1.0", lifespan=lifespan)
install_auth_middleware(app, protect_reads=True)
app.include_router(make_health_router("reason", {"postgres": check_postgres, "kafka": check_kafka}))


@app.get("/stats")
async def stats(request: Request) -> dict:
    return {
        "backend": common.reason_backend,
        "model": common.reason_model,
        "endpoint": common.reason_endpoint,
        "grounding": {
            "enabled": settings.ground_enabled,
            "backend": settings.ground_backend,
            "model": settings.ground_model or common.reason_model,
        },
        "nl_alerts": getattr(request.app.state, "nl_eval", None).stats if hasattr(request.app.state, "nl_eval") else {},
        "vlm_sweep": getattr(request.app.state, "sweeper", None).stats if hasattr(request.app.state, "sweeper") else {},
        **request.app.state.engine.stats,
    }


class GroundIn(BaseModel):
    query: str
    camera_id: uuid.UUID | None = None
    snapshot_ref: str | None = None


@app.post("/ground")
async def ground_query(payload: GroundIn) -> dict:
    """Open-vocabulary localization on demand: give a plain-English target and either
    a live camera (fresh snapshot) or a stored snapshot ref, get back normalized
    boxes. This is the long-tail detector the fixed-class YOLO cannot be — "a ladder
    against the wall", "an abandoned bag" — driven entirely by text."""
    query = payload.query.strip()
    if not query:
        raise HTTPException(400, "query is required")
    if payload.snapshot_ref:
        ref: str | None = payload.snapshot_ref
        frame = fetch_bytes(payload.snapshot_ref)
    elif payload.camera_id:
        ref, frame = await fresh_frame(str(payload.camera_id))
    else:
        raise HTTPException(400, "provide camera_id or snapshot_ref")
    if frame is None:
        raise HTTPException(404, "no frame available for grounding")
    try:
        result = await ground(frame, query)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"grounding backend error: {exc}") from exc
    return {**result.as_dict(), "frame_ref": ref}
