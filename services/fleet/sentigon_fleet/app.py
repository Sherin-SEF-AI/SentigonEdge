"""Fleet service: periodic health engine + read-only diagnostics API."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import FleetFinding, FleetSnapshot
from sentigon_common.health import check_postgres, make_health_router
from sentigon_common.logging import configure_logging
from sentigon_common.metrics import mount_metrics
from sentigon_common.schemas.enums import Severity
from sqlalchemy import case, select

from .collector import collect_cameras, collect_host, collect_services
from .engine import FleetEngine

# Logical severity ordering for finding lists (critical first, info last).
_SEVERITY_ORDER = case(
    (FleetFinding.severity == Severity.CRITICAL, 0),
    (FleetFinding.severity == Severity.HIGH, 1),
    (FleetFinding.severity == Severity.MEDIUM, 2),
    (FleetFinding.severity == Severity.LOW, 3),
    (FleetFinding.severity == Severity.INFO, 4),
    else_=5,
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("fleet")
    engine = FleetEngine()
    stop = asyncio.Event()
    task = asyncio.create_task(engine.run(stop))
    app.state.engine = engine
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


app = FastAPI(title="Sentigon Fleet", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(make_health_router("fleet", {"postgres": check_postgres}))


def _overview(snap: dict) -> dict:
    findings = snap.get("findings") or []
    breakdown: dict[str, int] = {}
    for f in findings:
        sev = str(f.get("severity", "info"))
        breakdown[sev] = breakdown.get(sev, 0) + 1
    return {
        "ts": snap.get("ts"),
        "cameras_total": snap.get("cameras_total", 0),
        "cameras_online": snap.get("cameras_online", 0),
        "services_total": snap.get("services_total", 0),
        "services_up": snap.get("services_up", 0),
        "host": snap.get("host"),
        "findings_active": len(findings),
        "severity_breakdown": breakdown,
    }


def _finding_dict(f: FleetFinding) -> dict:
    return {
        "id": str(f.id),
        "kind": f.kind,
        "severity": f.severity.value if f.severity else None,
        "target_type": f.target_type,
        "target_id": f.target_id,
        "target_name": f.target_name,
        "detail": f.detail,
        "metric": f.metric,
        "recommended_action": f.recommended_action,
        "active": f.active,
        "first_seen_at": f.first_seen_at.isoformat() if f.first_seen_at else None,
        "last_seen_at": f.last_seen_at.isoformat() if f.last_seen_at else None,
        "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
    }


@app.get("/stats")
async def stats(request: Request) -> dict:
    engine: FleetEngine = request.app.state.engine
    latest = engine.latest
    return {
        **engine.stats,
        "cameras_online": latest.get("cameras_online", 0),
        "services_up": latest.get("services_up", 0),
    }


@app.get("/fleet/overview")
async def overview(request: Request) -> dict:
    engine: FleetEngine = request.app.state.engine
    if engine.latest:
        return _overview(engine.latest)

    # No tick yet this process: fall back to the newest persisted snapshot.
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(FleetSnapshot).order_by(FleetSnapshot.ts.desc()).limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        return {
            "ts": None,
            "cameras_total": 0,
            "cameras_online": 0,
            "services_total": 0,
            "services_up": 0,
            "host": None,
            "findings_active": 0,
            "severity_breakdown": {},
        }
    payload = row.payload or {}
    return _overview(
        {
            "ts": row.ts.isoformat() if row.ts else None,
            "cameras_total": row.cameras_total,
            "cameras_online": row.cameras_online,
            "services_total": row.services_total,
            "services_up": row.services_up,
            "host": payload.get("host"),
            "findings": payload.get("findings", []),
        }
    )


@app.get("/fleet/cameras")
async def cameras(request: Request) -> list[dict]:
    latest = request.app.state.engine.latest
    if latest.get("cameras") is not None:
        return latest["cameras"]
    return await collect_cameras()


@app.get("/fleet/services")
async def services(request: Request) -> list[dict]:
    latest = request.app.state.engine.latest
    if latest.get("services") is not None:
        return latest["services"]
    return await collect_services()


@app.get("/fleet/host")
async def host(request: Request) -> dict:
    latest = request.app.state.engine.latest
    if latest.get("host") is not None:
        return latest["host"]
    return collect_host()


@app.get("/fleet/findings")
async def findings(active: bool = True) -> list[dict]:
    stmt = select(FleetFinding)
    if active:
        stmt = stmt.where(FleetFinding.active.is_(True))
    stmt = stmt.order_by(_SEVERITY_ORDER, FleetFinding.last_seen_at.desc())
    async with async_session_factory() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_finding_dict(r) for r in rows]


mount_metrics(app)
