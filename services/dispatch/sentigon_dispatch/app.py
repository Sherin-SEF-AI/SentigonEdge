"""Dispatch service: consume incidents.verified, open responder dispatches for
high/critical confirmed incidents, and drive their SLA lifecycle. Exposes the
dispatch queue plus responder/on-call roster and SOC-shift management to the
console.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sentigon_common.auth import is_writer, user_from_token
from sentigon_common.config import settings as common_settings
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import (
    AuditLogEntry,
    Dispatch,
    OncallShift,
    Responder,
    SocShift,
)
from sentigon_common.health import check_kafka, check_postgres, make_health_router
from sentigon_common.kafka import run_consumer
from sentigon_common.logging import configure_logging, get_logger
from sentigon_common.metrics import mount_metrics
from sentigon_common.schemas.bus import Topics
from sentigon_common.schemas.enums import DispatchState
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .engine import DispatchEngine
from .sla import SlaSweeper

log = get_logger("dispatch.app")


# ── lifespan ──────────────────────────────────────────────────
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("dispatch")
    engine = DispatchEngine()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(
            [Topics.INCIDENTS_VERIFIED],
            "dispatch",
            engine.handle,
            stop_event=stop,
            auto_offset_reset="latest",
        )
    )
    sweeper = asyncio.create_task(SlaSweeper(engine).run(stop))
    app.state.engine = engine
    app.state.stop = stop
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        sweeper.cancel()
        for t in (task, sweeper):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


app = FastAPI(title="Sentigon Dispatch", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(make_health_router("dispatch", {"postgres": check_postgres, "kafka": check_kafka}))
mount_metrics(app)


# ── auth: writes require operator+ (JWT) or the internal service token ─────
async def require_writer(
    authorization: str | None = Header(None),
    x_service_token: str | None = Header(None),
) -> None:
    if x_service_token and x_service_token == common_settings.service_token:
        return
    token = (
        authorization[7:].strip()
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    user = await user_from_token(token)
    if user is None or not is_writer(user):
        raise HTTPException(status_code=401, detail="operator+ auth required")


# ── serialization helpers ─────────────────────────────────────
def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _opt_uuid(value: object) -> uuid.UUID | None:
    """Parse an optional UUID; a falsy value means 'unset'. Raises on a bad value."""
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid uuid: {value!r}") from exc


def _dispatch_dict(d: Dispatch, responder_name: str | None = None) -> dict:
    return {
        "id": str(d.id),
        "seq": d.seq,
        "incident_id": str(d.incident_id),
        "camera_id": str(d.camera_id) if d.camera_id else None,
        "site_id": str(d.site_id) if d.site_id else None,
        "responder_id": str(d.responder_id) if d.responder_id else None,
        "responder_name": responder_name,
        "severity": d.severity.value,
        "risk_score": d.risk_score,
        "signature_name": d.signature_name,
        "sitrep": d.sitrep,
        "state": d.state.value,
        "tier": d.tier,
        "sla_ack_seconds": d.sla_ack_seconds,
        "sla_resolve_seconds": d.sla_resolve_seconds,
        "notified_at": _iso(d.notified_at),
        "acknowledged_at": _iso(d.acknowledged_at),
        "resolved_at": _iso(d.resolved_at),
        "ack_by": d.ack_by,
        "channels_used": d.channels_used or {},
        "notes": d.notes,
        "correlation_id": d.correlation_id,
        "created_at": _iso(d.created_at),
    }


def _responder_dict(r: Responder) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "email": r.email,
        "phone": r.phone,
        "role": r.role,
        "channels": r.channels or {},
        "site_id": str(r.site_id) if r.site_id else None,
        "active": r.active,
        "created_at": _iso(r.created_at),
    }


def _oncall_dict(o: OncallShift, responder_name: str | None) -> dict:
    return {
        "id": str(o.id),
        "responder_id": str(o.responder_id),
        "responder_name": responder_name,
        "site_id": str(o.site_id) if o.site_id else None,
        "weekday": o.weekday,
        "start_hour": o.start_hour,
        "end_hour": o.end_hour,
        "tier": o.tier,
        "active": o.active,
        "created_at": _iso(o.created_at),
    }


def _shift_dict(s: SocShift) -> dict:
    return {
        "id": str(s.id),
        "operator": s.operator,
        "user_id": str(s.user_id) if s.user_id else None,
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "active": s.active,
        "note": s.note,
    }


async def _responder_name(session: AsyncSession, responder_id: uuid.UUID | None) -> str | None:
    if responder_id is None:
        return None
    return (
        await session.execute(select(Responder.name).where(Responder.id == responder_id))
    ).scalar_one_or_none()


async def _load_dispatch(session: AsyncSession, dispatch_id: str) -> Dispatch:
    try:
        did = uuid.UUID(dispatch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="dispatch not found") from exc
    d = (
        await session.execute(select(Dispatch).where(Dispatch.id == did))
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail="dispatch not found")
    return d


# ── dispatch queue + lifecycle ────────────────────────────────
@app.get("/stats")
async def stats(request: Request) -> dict:
    engine: DispatchEngine = request.app.state.engine
    async with async_session_factory() as session:
        open_count = (
            await session.execute(
                select(func.count())
                .select_from(Dispatch)
                .where(Dispatch.state.not_in([DispatchState.RESOLVED, DispatchState.EXPIRED]))
            )
        ).scalar_one()
    return {**engine.stats, "open": int(open_count)}


@app.get("/dispatches")
async def list_dispatches(
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    stmt = select(Dispatch, Responder.name).outerjoin(
        Responder, Dispatch.responder_id == Responder.id
    )
    if state:
        try:
            stmt = stmt.where(Dispatch.state == DispatchState(state))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid state: {state!r}") from exc
    stmt = stmt.order_by(Dispatch.created_at.desc()).limit(limit)
    async with async_session_factory() as session:
        rows = (await session.execute(stmt)).all()
    return [_dispatch_dict(d, name) for d, name in rows]


@app.post("/dispatches/{dispatch_id}/ack", dependencies=[Depends(require_writer)])
async def ack_dispatch(
    dispatch_id: str, request: Request, body: dict | None = Body(default=None)
) -> dict:
    by = (body or {}).get("by")
    async with async_session_factory() as session:
        d = await _load_dispatch(session, dispatch_id)
        d.acknowledged_at = datetime.now(UTC)
        d.state = DispatchState.ACKNOWLEDGED
        d.ack_by = by
        session.add(
            AuditLogEntry(
                action="dispatch.acked",
                resource_type="dispatch",
                resource_id=str(d.id),
                details={"by": by},
                correlation_id=d.correlation_id,
            )
        )
        await session.commit()
        result = _dispatch_dict(d, await _responder_name(session, d.responder_id))
    request.app.state.engine.stats["acked"] += 1
    return result


@app.post("/dispatches/{dispatch_id}/resolve", dependencies=[Depends(require_writer)])
async def resolve_dispatch(
    dispatch_id: str, request: Request, body: dict | None = Body(default=None)
) -> dict:
    body = body or {}
    by = body.get("by")
    notes = body.get("notes")
    async with async_session_factory() as session:
        d = await _load_dispatch(session, dispatch_id)
        d.resolved_at = datetime.now(UTC)
        d.state = DispatchState.RESOLVED
        if notes is not None:
            d.notes = notes
        if by and not d.ack_by:
            d.ack_by = by
        session.add(
            AuditLogEntry(
                action="dispatch.resolved",
                resource_type="dispatch",
                resource_id=str(d.id),
                details={"by": by, "notes": notes},
                correlation_id=d.correlation_id,
            )
        )
        await session.commit()
        result = _dispatch_dict(d, await _responder_name(session, d.responder_id))
    request.app.state.engine.stats["resolved"] += 1
    return result


@app.post("/dispatches/{dispatch_id}/assign", dependencies=[Depends(require_writer)])
async def assign_dispatch(dispatch_id: str, body: dict = Body(...)) -> dict:
    responder_id = _opt_uuid(body.get("responder_id"))
    async with async_session_factory() as session:
        d = await _load_dispatch(session, dispatch_id)
        if responder_id is not None:
            exists = (
                await session.execute(select(Responder.id).where(Responder.id == responder_id))
            ).first()
            if exists is None:
                raise HTTPException(status_code=404, detail="responder not found")
        d.responder_id = responder_id
        session.add(
            AuditLogEntry(
                action="dispatch.assigned",
                resource_type="dispatch",
                resource_id=str(d.id),
                details={"responder_id": str(responder_id) if responder_id else None},
                correlation_id=d.correlation_id,
            )
        )
        await session.commit()
        result = _dispatch_dict(d, await _responder_name(session, d.responder_id))
    return result


# ── responder roster ──────────────────────────────────────────
@app.get("/responders")
async def list_responders() -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(select(Responder).order_by(Responder.created_at.desc()))
        ).scalars().all()
    return [_responder_dict(r) for r in rows]


@app.post("/responders", dependencies=[Depends(require_writer)])
async def create_responder(body: dict = Body(...)) -> dict:
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    responder = Responder(
        name=name,
        email=body.get("email"),
        phone=body.get("phone"),
        role=body.get("role") or "responder",
        channels=body.get("channels") or {},
        site_id=_opt_uuid(body.get("site_id")),
        active=bool(body.get("active", True)),
    )
    async with async_session_factory() as session:
        session.add(responder)
        await session.commit()
        result = _responder_dict(responder)
    return result


@app.patch("/responders/{responder_id}", dependencies=[Depends(require_writer)])
async def update_responder(responder_id: str, body: dict = Body(...)) -> dict:
    rid = _opt_uuid(responder_id)
    async with async_session_factory() as session:
        r = (
            await session.execute(select(Responder).where(Responder.id == rid))
        ).scalar_one_or_none()
        if r is None:
            raise HTTPException(status_code=404, detail="responder not found")
        for field in ("name", "email", "phone", "role", "channels", "active"):
            if field in body:
                setattr(r, field, body[field])
        if "site_id" in body:
            r.site_id = _opt_uuid(body["site_id"])
        await session.commit()
        result = _responder_dict(r)
    return result


# ── on-call roster ────────────────────────────────────────────
@app.get("/oncall")
async def list_oncall() -> list[dict]:
    stmt = (
        select(OncallShift, Responder.name)
        .outerjoin(Responder, OncallShift.responder_id == Responder.id)
        .order_by(OncallShift.tier, OncallShift.start_hour)
    )
    async with async_session_factory() as session:
        rows = (await session.execute(stmt)).all()
    return [_oncall_dict(o, name) for o, name in rows]


@app.post("/oncall", dependencies=[Depends(require_writer)])
async def create_oncall(body: dict = Body(...)) -> dict:
    responder_id = _opt_uuid(body.get("responder_id"))
    if responder_id is None:
        raise HTTPException(status_code=400, detail="responder_id is required")
    shift = OncallShift(
        responder_id=responder_id,
        site_id=_opt_uuid(body.get("site_id")),
        weekday=body.get("weekday"),
        start_hour=int(body.get("start_hour", 0)),
        end_hour=int(body.get("end_hour", 24)),
        tier=int(body.get("tier", 1)),
        active=bool(body.get("active", True)),
    )
    async with async_session_factory() as session:
        exists = (
            await session.execute(select(Responder.id).where(Responder.id == responder_id))
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="responder not found")
        session.add(shift)
        await session.commit()
        name = await _responder_name(session, responder_id)
        result = _oncall_dict(shift, name)
    return result


# ── SOC monitoring shifts ─────────────────────────────────────
@app.get("/shifts/active")
async def active_shifts() -> list[dict]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(SocShift)
                .where(SocShift.active.is_(True))
                .order_by(SocShift.started_at.desc())
            )
        ).scalars().all()
    return [_shift_dict(s) for s in rows]


@app.post("/shifts/checkin", dependencies=[Depends(require_writer)])
async def checkin_shift(body: dict = Body(...)) -> dict:
    operator = body.get("operator")
    if not operator:
        raise HTTPException(status_code=400, detail="operator is required")
    shift = SocShift(operator=operator, active=True, note=body.get("note"))
    async with async_session_factory() as session:
        session.add(shift)
        await session.commit()
        result = _shift_dict(shift)
    return result


@app.post("/shifts/checkout", dependencies=[Depends(require_writer)])
async def checkout_shift(body: dict = Body(...)) -> dict:
    operator = body.get("operator")
    if not operator:
        raise HTTPException(status_code=400, detail="operator is required")
    async with async_session_factory() as session:
        shifts = (
            await session.execute(
                select(SocShift)
                .where(SocShift.operator == operator, SocShift.active.is_(True))
                .order_by(SocShift.started_at.desc())
            )
        ).scalars().all()
        if not shifts:
            raise HTTPException(status_code=404, detail="no active shift for operator")
        now = datetime.now(UTC)
        for s in shifts:
            s.active = False
            s.ended_at = now
        await session.commit()
        result = [_shift_dict(s) for s in shifts]
    return {"checked_out": result}
