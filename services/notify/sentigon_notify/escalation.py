"""Escalation engine + on-call routing.

A confirmed incident that stays unacknowledged (status NEW) escalates up the
chain: at each level's delay it is re-notified to the on-call contact for the
current window, with the escalation recorded on the incident and in the audit
log. Idempotent: an incident is escalated to each level at most once.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import AuditLogEntry, Incident, Signature
from sentigon_common.logging import get_logger
from sentigon_common.schemas.enums import IncidentStatus, Verdict
from sqlalchemy import select

from .config import settings
from .notifier import send_email, send_webhook, send_webpush

log = get_logger("notify.escalation")


def oncall_contact(now: datetime | None = None) -> str:
    """Resolve the on-call contact for the current local hour from the roster."""
    hour = (now or datetime.now()).hour
    for window in settings.oncall_schedule.split(","):
        try:
            span, contact = window.split(":", 1)
            start, end = (int(x) for x in span.split("-"))
            if start <= hour < end:
                return contact.strip()
        except ValueError:
            continue
    return settings.email_to


def _levels() -> list[dict]:
    return [
        {"level": 1, "after": settings.escalation_l1_after, "channels": ("webhook", "webpush")},
        {"level": 2, "after": settings.escalation_l2_after, "channels": ("email", "webhook")},
    ]


class Escalator:
    def __init__(self) -> None:
        self.stats = {"escalated_l1": 0, "escalated_l2": 0, "checks": 0}

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001
                log.exception("escalation.sweep_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.escalation_check_seconds)

    async def _sweep(self) -> None:
        self.stats["checks"] += 1
        now = datetime.now(UTC)
        floor = now - timedelta(seconds=settings.escalation_max_age_seconds)
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(Incident, Signature.name)
                    .join(Signature, Incident.signature_id == Signature.id, isouter=True)
                    .where(
                        Incident.status == IncidentStatus.NEW,
                        # only page on-call for VLM-CONFIRMED threats — `is_not(None)`
                        # also matched UNVERIFIED/REJECTED, causing false pages.
                        Incident.verdict == Verdict.CONFIRMED,
                        Incident.created_at >= floor,
                    )
                )
            ).all()

            for inc, signame in rows:
                created = inc.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                age = (now - created).total_seconds()
                cur_level = int((inc.attributes or {}).get("escalation_level", 0))
                for lvl in _levels():
                    if age >= lvl["after"] and cur_level < lvl["level"]:
                        await self._escalate(session, inc, signame, lvl, age)
                        cur_level = lvl["level"]
            await session.commit()

    async def _escalate(self, session, inc: Incident, signame: str | None, lvl: dict, age: float) -> None:
        contact = oncall_contact()
        subject = f"[SENTIGON ESCALATION L{lvl['level']}] {signame or 'incident'} unacknowledged {int(age)}s"
        body = (
            f"Confirmed incident still UNACKNOWLEDGED after {int(age)}s.\n\n"
            f"Signature: {signame}\nSeverity: {inc.severity.value}\nIncident: {inc.id}\n"
            f"On-call: {contact}\nAck: {settings.api_url}/incidents/{inc.id}\n"
        )
        loop = asyncio.get_running_loop()
        results: dict[str, bool] = {}
        if "email" in lvl["channels"]:
            ok, _ = await loop.run_in_executor(None, send_email, subject, body)
            results["email"] = ok
        if "webhook" in lvl["channels"]:
            ok, _ = await send_webhook(
                {
                    "type": "sentigon.incident.escalated",
                    "level": lvl["level"],
                    "incident_id": str(inc.id),
                    "signature": signame,
                    "severity": inc.severity.value,
                    "age_seconds": int(age),
                    "oncall": contact,
                }
            )
            results["webhook"] = ok
        if "webpush" in lvl["channels"]:
            ok, _ = await loop.run_in_executor(None, send_webpush, subject, body[:120])
            results["webpush"] = ok

        inc.attributes = {**(inc.attributes or {}), "escalation_level": lvl["level"]}
        session.add(
            AuditLogEntry(
                action="incident.escalated",
                resource_type="incident",
                resource_id=str(inc.id),
                details={"level": lvl["level"], "oncall": contact, "age_s": int(age), "channels": results},
                correlation_id=inc.correlation_id,
            )
        )
        self.stats[f"escalated_l{lvl['level']}"] += 1
        log.info("incident.escalated", incident=str(inc.id), level=lvl["level"], oncall=contact, age_s=int(age))
