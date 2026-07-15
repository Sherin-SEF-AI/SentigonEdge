"""Notify engine: consume verified incidents, deliver confirmed ones via real
transports, and audit each delivery.
"""

from __future__ import annotations

import asyncio

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import AuditLogEntry
from sentigon_common.logging import get_logger, set_correlation_id

from .config import settings
from .notifier import ack_url, send_email, send_webhook, send_webpush, severity_meets

log = get_logger("notify.engine")


class NotifyEngine:
    def __init__(self) -> None:
        self.stats = {"received": 0, "notified": 0, "email_ok": 0, "webhook_ok": 0, "skipped": 0}
        self._seen: set[str] = set()

    async def handle(self, payload: dict, correlation_id: str | None) -> None:
        self.stats["received"] += 1
        verdict = payload.get("verdict")
        sev = str(payload.get("severity", "info"))
        inc = str(payload.get("incident_id"))
        if settings.only_confirmed and verdict != "confirmed":
            self.stats["skipped"] += 1
            return
        if not severity_meets(sev, settings.min_severity):
            self.stats["skipped"] += 1
            return
        if inc in self._seen:
            return
        self._seen.add(inc)
        set_correlation_id(payload.get("correlation_id") or correlation_id)
        await self._dispatch(payload)

    async def _dispatch(self, p: dict) -> None:
        sig = p.get("signature_name", "incident")
        sev = str(p.get("severity", "info"))
        inc = str(p.get("incident_id"))
        sitrep = p.get("sitrep") or ""
        ack = ack_url(inc)
        subject = f"[SENTIGON {sev.upper()}] {sig}"
        body = (
            "Confirmed security incident (VLM-verified).\n\n"
            f"Signature: {sig}\n"
            f"Severity:  {sev}\n"
            f"Incident:  {inc}\n"
            f"Camera:    {p.get('camera_id')}\n\n"
            f"SITREP: {sitrep}\n\n"
            f"Acknowledge: {ack}\n"
        )
        webhook = {
            "type": "sentigon.incident.confirmed",
            "incident_id": inc,
            "signature": sig,
            "severity": sev,
            "verdict": p.get("verdict"),
            "sitrep": sitrep,
            "camera_id": p.get("camera_id"),
            "snapshot_ref": p.get("snapshot_ref"),
            "ack_url": ack,
            "ts": p.get("ts"),
        }
        loop = asyncio.get_running_loop()
        email_ok, email_detail = await loop.run_in_executor(None, send_email, subject, body)
        webhook_ok, webhook_detail = await send_webhook(webhook)
        push_ok, push_detail = await loop.run_in_executor(
            None, send_webpush, subject, f"{sig} ({sev}) - {sitrep[:80]}"
        )

        async with async_session_factory() as session:
            session.add(
                AuditLogEntry(
                    action="notify.sent",
                    resource_type="incident",
                    resource_id=inc,
                    details={
                        "email": {"ok": email_ok, "detail": email_detail, "to": settings.email_to},
                        "webhook": {
                            "ok": webhook_ok,
                            "detail": webhook_detail,
                            "url": settings.webhook_url,
                        },
                        "webpush": {"ok": push_ok, "detail": push_detail},
                    },
                    correlation_id=p.get("correlation_id"),
                )
            )
            await session.commit()

        self.stats["notified"] += 1
        if email_ok:
            self.stats["email_ok"] += 1
        if webhook_ok:
            self.stats["webhook_ok"] += 1
        log.info("notify.sent", incident=inc, signature=sig, email=email_ok, webhook=webhook_ok)

    async def test(self) -> dict:
        payload = {
            "verdict": "confirmed",
            "severity": "high",
            "incident_id": "test-" + str(len(self._seen)),
            "signature_name": "Sentigon Test Alert",
            "sitrep": "This is a real Sentigon test notification through the live transports.",
            "camera_id": "test",
            "snapshot_ref": None,
            "ts": "test",
            "correlation_id": None,
        }
        await self._dispatch(payload)
        return {"stats": self.stats}
