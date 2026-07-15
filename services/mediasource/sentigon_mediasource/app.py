"""Media-source FastAPI app: health + real source status."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from sentigon_common.health import make_health_router
from sentigon_common.logging import configure_logging

from .manager import MediaSourceManager


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("mediasource")
    manager = MediaSourceManager()
    manager.start()
    app.state.manager = manager
    try:
        yield
    finally:
        manager.stop()


app = FastAPI(title="Sentigon Media Source", version="0.1.0", lifespan=lifespan)
app.include_router(make_health_router("mediasource"))


@app.get("/sources")
async def sources(request: Request) -> list[dict]:
    return request.app.state.manager.status()
