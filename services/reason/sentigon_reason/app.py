"""Reason service: runs the events.candidate consumer under a FastAPI app."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from sentigon_common.config import settings as common
from sentigon_common.health import check_kafka, check_postgres, make_health_router
from sentigon_common.kafka import run_consumer
from sentigon_common.logging import configure_logging
from sentigon_common.schemas.bus import Topics

from .config import settings
from .engine import ReasonEngine


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
    try:
        yield
    finally:
        stop.set()
        for t in (task, nl_task):
            if t is not None:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        await engine.stop()


app = FastAPI(title="Sentigon Reason", version="0.1.0", lifespan=lifespan)
app.include_router(make_health_router("reason", {"postgres": check_postgres, "kafka": check_kafka}))


@app.get("/stats")
async def stats(request: Request) -> dict:
    return {
        "backend": common.reason_backend,
        "model": common.reason_model,
        "endpoint": common.reason_endpoint,
        "nl_alerts": getattr(request.app.state, "nl_eval", None).stats if hasattr(request.app.state, "nl_eval") else {},
        **request.app.state.engine.stats,
    }
