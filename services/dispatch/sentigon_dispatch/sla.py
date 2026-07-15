"""Background SLA sweeper.

Wakes every `settings.sweep_seconds` and enforces two SLAs on open dispatches:

* ACK SLA - a NOTIFIED dispatch not acknowledged within `sla_ack_seconds` of being
  notified escalates up the on-call tier (a real re-notification), moving to
  ESCALATED. Because the state changes, it will not re-escalate on the same tier.
* RESOLVE SLA - a dispatch (NOTIFIED/ESCALATED/ACKNOWLEDGED) not resolved within
  `sla_resolve_seconds` of creation expires (EXPIRED).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import AuditLogEntry, Camera, Dispatch
from sentigon_common.logging import get_logger
from sentigon_common.schemas.enums import DispatchState
from sqlalchemy import select

from .config import settings

log = get_logger("dispatch.sla")


def _aware(dt: datetime) -> datetime:
    """Normalise a possibly tz-naive timestamp to UTC-aware for safe arithmetic."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SlaSweeper:
    def __init__(self, engine) -> None:  # noqa: ANN001 - DispatchEngine (avoid import cycle)
        self.engine = engine

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001
                log.exception("dispatch.sla_sweep_error")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.sweep_seconds)

    async def _sweep(self) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session:
            # ── ACK-SLA breach → escalate up the tier ────────────
            notified = (
                await session.execute(
                    select(Dispatch).where(
                        Dispatch.state == DispatchState.NOTIFIED,
                        Dispatch.notified_at.is_not(None),
                    )
                )
            ).scalars().all()
            for d in notified:
                if now - _aware(d.notified_at) <= timedelta(seconds=d.sla_ack_seconds):
                    continue
                next_tier = d.tier + 1
                # prefer the next tier; fall back to any on-call responder
                next_resp = await self.engine._resolve_oncall(session, d.site_id, tier=next_tier)
                if next_resp is None:
                    next_resp = await self.engine._resolve_oncall(session, d.site_id, tier=1)

                d.tier = next_tier
                d.state = DispatchState.ESCALATED
                if next_resp is not None:
                    d.responder_id = next_resp.id

                camera_name = await self._camera_name(session, d.camera_id)
                results = await self.engine._notify(d, next_resp, camera_name)
                merged = dict(d.channels_used or {})
                merged.update(results)
                d.channels_used = merged

                session.add(
                    AuditLogEntry(
                        action="dispatch.escalated",
                        resource_type="dispatch",
                        resource_id=str(d.id),
                        details={
                            "incident_id": str(d.incident_id),
                            "tier": next_tier,
                            "responder": next_resp.name if next_resp else None,
                            "responder_id": str(next_resp.id) if next_resp else None,
                            "reason": "ack_sla_breach",
                            "channels": results,
                        },
                        correlation_id=d.correlation_id,
                    )
                )
                self.engine.stats["escalated"] += 1
                log.info(
                    "dispatch.escalated",
                    dispatch=str(d.id),
                    tier=next_tier,
                    responder=next_resp.name if next_resp else None,
                )

            # ── RESOLVE-SLA breach → expire ──────────────────────
            open_states = (
                DispatchState.NOTIFIED,
                DispatchState.ESCALATED,
                DispatchState.ACKNOWLEDGED,
            )
            unresolved = (
                await session.execute(
                    select(Dispatch).where(
                        Dispatch.state.in_(open_states),
                        Dispatch.resolved_at.is_(None),
                    )
                )
            ).scalars().all()
            for d in unresolved:
                if now - _aware(d.created_at) <= timedelta(seconds=d.sla_resolve_seconds):
                    continue
                d.state = DispatchState.EXPIRED
                session.add(
                    AuditLogEntry(
                        action="dispatch.expired",
                        resource_type="dispatch",
                        resource_id=str(d.id),
                        details={
                            "incident_id": str(d.incident_id),
                            "reason": "resolve_sla_breach",
                            "age_seconds": int((now - _aware(d.created_at)).total_seconds()),
                        },
                        correlation_id=d.correlation_id,
                    )
                )
                log.info("dispatch.expired", dispatch=str(d.id))

            await session.commit()

    async def _camera_name(self, session, camera_id) -> str:  # noqa: ANN001
        if camera_id is None:
            return "camera"
        camera = (
            await session.execute(select(Camera).where(Camera.id == camera_id))
        ).scalar_one_or_none()
        return camera.name if camera is not None else "camera"
