"""Dispatch engine: consume confirmed incidents, open a responder Dispatch,
resolve the on-call responder from the roster, and notify through the real notify
transports. The consumer handler is idempotent (at-least-once delivery): one
Dispatch per incident.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import (
    AuditLogEntry,
    Camera,
    Dispatch,
    Incident,
    OncallShift,
    Responder,
)
from sentigon_common.logging import get_logger, set_correlation_id
from sentigon_common.schemas.enums import DispatchState, Severity
from sentigon_notify.notifier import (
    send_email,
    send_sms,
    send_webhook,
    send_webpush,
    severity_meets,
)
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings

log = get_logger("dispatch.engine")


class DispatchEngine:
    def __init__(self) -> None:
        self.stats = {"dispatched": 0, "acked": 0, "resolved": 0, "escalated": 0, "skipped": 0}

    # ── consumer handler ──────────────────────────────────────
    async def handle(self, payload: dict, correlation_id: str | None) -> None:
        """Open a dispatch for a confirmed, high/critical incident. Idempotent:
        a second delivery of the same incident is a no-op."""
        if payload.get("verdict") != "confirmed":
            self.stats["skipped"] += 1
            return

        sev = str(payload.get("severity", "info"))
        if not severity_meets(sev, settings.min_severity):
            self.stats["skipped"] += 1
            return

        try:
            incident_id = uuid.UUID(str(payload["incident_id"]))
        except (KeyError, ValueError):
            log.warning("dispatch.bad_incident_id", incident_id=payload.get("incident_id"))
            self.stats["skipped"] += 1
            return

        set_correlation_id(payload.get("correlation_id") or correlation_id)

        async with async_session_factory() as session:
            # Idempotency: exactly one dispatch per incident.
            existing = (
                await session.execute(
                    select(Dispatch.id).where(Dispatch.incident_id == incident_id)
                )
            ).first()
            if existing is not None:
                log.info("dispatch.duplicate_skipped", incident=str(incident_id))
                return

            incident = (
                await session.execute(select(Incident).where(Incident.id == incident_id))
            ).scalar_one_or_none()

            camera_id: uuid.UUID | None = None
            if payload.get("camera_id"):
                try:
                    camera_id = uuid.UUID(str(payload["camera_id"]))
                except ValueError:
                    camera_id = None

            site_id: uuid.UUID | None = None
            camera_name = str(payload.get("camera_id") or "camera")
            if camera_id is not None:
                camera = (
                    await session.execute(select(Camera).where(Camera.id == camera_id))
                ).scalar_one_or_none()
                if camera is not None:
                    site_id = camera.site_id
                    camera_name = camera.name

            responder = await self._resolve_oncall(session, site_id, tier=1)

            dispatch = Dispatch(
                incident_id=incident_id,
                camera_id=camera_id,
                site_id=site_id,
                responder_id=responder.id if responder else None,
                severity=Severity(sev),
                risk_score=incident.risk_score if incident else payload.get("risk_score"),
                signature_name=payload.get("signature_name"),
                sitrep=payload.get("sitrep"),
                state=DispatchState.PENDING,
                tier=1,
                sla_ack_seconds=settings.sla_ack_seconds,
                sla_resolve_seconds=settings.sla_resolve_seconds,
                correlation_id=correlation_id,
            )
            session.add(dispatch)
            await session.flush()  # assign id/seq before notifying + auditing

            results = await self._notify(dispatch, responder, camera_name)
            dispatch.notified_at = datetime.now(UTC)
            dispatch.state = DispatchState.NOTIFIED
            dispatch.channels_used = results

            session.add(
                AuditLogEntry(
                    action="dispatch.created",
                    resource_type="dispatch",
                    resource_id=str(dispatch.id),
                    details={
                        "incident_id": str(incident_id),
                        "severity": sev,
                        "signature": dispatch.signature_name,
                        "responder": responder.name if responder else None,
                        "responder_id": str(responder.id) if responder else None,
                        "site_id": str(site_id) if site_id else None,
                        "tier": 1,
                        "channels": results,
                    },
                    correlation_id=correlation_id,
                )
            )
            await session.commit()

        self.stats["dispatched"] += 1
        log.info(
            "dispatch.created",
            incident=str(incident_id),
            severity=sev,
            responder=responder.name if responder else None,
            channels=results,
        )

    # ── on-call resolution ────────────────────────────────────
    async def _resolve_oncall(
        self, session: AsyncSession, site_id: uuid.UUID | None, tier: int
    ) -> Responder | None:
        """Resolve the on-call responder for a site at the current local time and
        tier. Site-specific shifts win over global (site_id NULL) shifts. Windows are
        [start_hour, end_hour); a NULL weekday matches every day."""
        # site-local time (naive server-local wall clock resolved the wrong window on
        # non-UTC boxes); windows are authored in this tz.
        try:
            tz = ZoneInfo(settings.dispatch_timezone)
        except Exception:  # noqa: BLE001
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        hour = now.hour
        wd = now.weekday()
        stmt = (
            select(Responder)
            .join(OncallShift, OncallShift.responder_id == Responder.id)
            .where(
                OncallShift.active.is_(True),
                Responder.active.is_(True),
                OncallShift.tier == tier,
                or_(OncallShift.site_id == site_id, OncallShift.site_id.is_(None)),
                # same-day window [start,end); OR an overnight window (start>end) that
                # wraps midnight (e.g. 22->06) — the old start<=hour<end never matched.
                or_(
                    and_(OncallShift.start_hour <= hour, OncallShift.end_hour > hour),
                    and_(
                        OncallShift.start_hour > OncallShift.end_hour,
                        or_(OncallShift.start_hour <= hour, OncallShift.end_hour > hour),
                    ),
                ),
                or_(OncallShift.weekday == wd, OncallShift.weekday.is_(None)),
            )
            # site-specific (site_id NOT NULL -> is_(None) is False -> sorts first)
            .order_by(OncallShift.site_id.is_(None), OncallShift.tier)
        )
        return (await session.execute(stmt)).scalars().first()

    # ── notification ──────────────────────────────────────────
    async def _notify(
        self, dispatch: Dispatch, responder: Responder | None, camera_name: str
    ) -> dict:
        """Notify the resolved responder through the real notify transports. The
        webhook always fires; email/web-push fire when the responder opted into that
        channel (or when no responder resolved, so a human still gets paged). Every
        channel is guarded so one failing transport never breaks the dispatch."""
        sev = dispatch.severity.value
        subject = f"[SENTIGON DISPATCH] {sev.upper()} {dispatch.signature_name} @ {camera_name}"
        body = (
            "Responder dispatch for a confirmed security incident (VLM-verified).\n\n"
            f"Severity:  {sev}\n"
            f"Signature: {dispatch.signature_name}\n"
            f"Camera:    {camera_name}\n"
            f"Responder: {responder.name if responder else '(unassigned - no on-call match)'}\n"
            f"Incident:  {dispatch.incident_id}\n"
            f"Risk:      {dispatch.risk_score}\n\n"
            f"SITREP: {dispatch.sitrep or ''}\n\n"
            f"Open: {settings.api_url}/incidents/{dispatch.incident_id}\n"
        )
        webhook_payload = {
            "type": "sentigon.dispatch",
            "dispatch_id": str(dispatch.id),
            "incident_id": str(dispatch.incident_id),
            "responder": responder.name if responder else None,
            "severity": sev,
            "signature": dispatch.signature_name,
            "sitrep": dispatch.sitrep,
        }
        channels = responder.channels if (responder and isinstance(responder.channels, dict)) else None
        results: dict = {}
        loop = asyncio.get_running_loop()

        # webhook: always attempted
        try:
            ok, _ = await send_webhook(webhook_payload)
            results["webhook"] = ok
        except Exception:  # noqa: BLE001
            log.exception("dispatch.notify_webhook_failed")
            results["webhook"] = False

        # email: to the RESOLVED responder's own address so the on-call person is
        # actually paged; fall back to the global inbox only when nobody resolved.
        if channels is None or "email" in channels:
            recipient = responder.email if responder else None
            try:
                ok, _ = await loop.run_in_executor(None, send_email, subject, body, recipient)
                results["email"] = ok
            except Exception:  # noqa: BLE001
                log.exception("dispatch.notify_email_failed")
                results["email"] = False

        # web push: when opted in, or when nobody resolved
        if channels is None or "webpush" in channels:
            try:
                ok, _ = await loop.run_in_executor(
                    None, send_webpush, subject, f"{dispatch.signature_name} ({sev})"
                )
                results["webpush"] = ok
            except Exception:  # noqa: BLE001
                log.exception("dispatch.notify_webpush_failed")
                results["webpush"] = False

        # sms: to the responder's phone via the configured gateway (was never wired)
        if responder and responder.phone and (channels is None or "sms" in channels):
            try:
                ok, _ = await send_sms(responder.phone, f"{subject} — {dispatch.sitrep or ''}"[:300])
                results["sms"] = ok
            except Exception:  # noqa: BLE001
                log.exception("dispatch.notify_sms_failed")
                results["sms"] = False

        return results
