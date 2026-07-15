"""SOP response playbooks.

Turns a fired incident into an automated response: on events.candidate we load the
live incident, evaluate the playbook rules (risk score / severity / category /
signature / verdict), and run the first matching playbook's actions for real:
escalate the incident, open a linked Case, email the SOC (mailpit), and POST a
webhook to the response receiver. This is the automation layer above detection
(Ambient.ai-style SOP execution). Each incident runs at most one playbook.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import (
    AuditLogEntry,
    Case,
    Incident,
    IncidentStatusLog,
    Signature,
    case_incidents,
)
from sentigon_common.logging import get_logger
from sentigon_common.schemas.enums import CaseStatus, IncidentStatus
from sqlalchemy import select

from .notifier import send_email, send_webhook, send_webpush

log = get_logger("notify.playbooks")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLAYBOOKS_FILE = _REPO_ROOT / "configs" / "playbooks.yaml"


def _load_playbooks() -> list[dict]:
    try:
        data = yaml.safe_load(_PLAYBOOKS_FILE.read_text())
        return data.get("playbooks", []) if data else []
    except Exception:  # noqa: BLE001
        log.exception("playbooks.load_failed")
        return []


def _matches(match: dict, *, risk_score: int, severity: str, category: str | None,
             signature: str, verdict: str | None) -> bool:
    if "min_risk_score" in match and (risk_score or 0) < match["min_risk_score"]:
        return False
    if "severities" in match and severity.lower() not in [s.lower() for s in match["severities"]]:
        return False
    if "categories" in match and (category or "") not in match["categories"]:
        return False
    if "signatures" in match and signature not in match["signatures"]:
        return False
    return not (match.get("require_confirmed") and verdict != "confirmed")


class PlaybookEngine:
    def __init__(self) -> None:
        self.playbooks = _load_playbooks()
        self._ran: set[tuple[str, str]] = set()  # (incident_id, playbook_name)
        self.stats = {
            "evaluated": 0, "triggered": 0, "cases": 0, "emails": 0, "webhooks": 0, "webpush": 0,
        }
        log.info("playbooks.loaded", count=len(self.playbooks))

    async def handle(self, payload: dict, correlation_id: str | None) -> None:
        corr = payload.get("correlation_id") or correlation_id
        if not corr:
            return
        self.stats["evaluated"] += 1
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    select(Incident, Signature.name, Signature.category)
                    .join(Signature, Incident.signature_id == Signature.id, isouter=True)
                    .where(Incident.correlation_id == corr)
                    .limit(1)
                )
            ).first()
            if row is None:
                return
            inc, sig_name, sig_cat = row
            ctx = {
                "risk_score": inc.risk_score or 0,
                "severity": inc.severity.value,
                "category": sig_cat,
                "signature": sig_name or payload.get("signature_name", ""),
                "verdict": inc.verdict.value if inc.verdict else None,
            }
            pb = next((p for p in self.playbooks if _matches(p.get("match", {}), **ctx)), None)
            if pb is None:
                return
            key = (str(inc.id), pb["name"])
            if key in self._ran:
                return
            self._ran.add(key)
            actions = pb.get("actions", {})
            done = await self._run_actions(session, inc, sig_name, pb, actions)
            await session.commit()

        self.stats["triggered"] += 1
        log.info("playbooks.triggered", playbook=pb["name"], incident=str(inc.id), actions=done)

    async def _run_actions(
        self, session, inc: Incident, sig_name: str, pb: dict, actions: dict
    ) -> list[str]:
        done: list[str] = []
        title = inc.title or sig_name or "incident"

        if actions.get("escalate") and inc.status in (IncidentStatus.NEW, IncidentStatus.ACK):
            inc.status = IncidentStatus.ESCALATED
            session.add(
                IncidentStatusLog(
                    incident_id=inc.id, to_status="escalated",
                    note=f"SOP playbook: {pb['name']}",
                )
            )
            done.append("escalate")

        if actions.get("create_case"):
            case = Case(
                title=f"[{pb['name']}] {title}",
                description=f"Auto-opened by SOP playbook '{pb['name']}' for incident {inc.id}.",
                status=CaseStatus.OPEN,
                priority=inc.severity,
                tags={"playbook": pb["name"], "auto": True, "risk_score": inc.risk_score},
            )
            session.add(case)
            await session.flush()
            await session.execute(
                case_incidents.insert().values(case_id=case.id, incident_id=inc.id)
            )
            self.stats["cases"] += 1
            done.append(f"case:{str(case.id)[:8]}")

        subject = f"[SENTIGON SOP {inc.severity.value.upper()}] {pb['name']}: {title}"
        body = (
            f"SOP playbook '{pb['name']}' triggered.\n\n"
            f"Incident:   {inc.id}\n"
            f"Signature:  {sig_name}\n"
            f"Severity:   {inc.severity.value}\n"
            f"Risk score: {inc.risk_score}\n"
            f"Camera:     {inc.camera_id}\n"
        )
        if actions.get("email"):
            ok, _ = send_email(subject, body)
            if ok:
                self.stats["emails"] += 1
                done.append("email")
        if actions.get("webhook"):
            ok, _ = await send_webhook(
                {
                    "type": "sentigon.playbook.triggered",
                    "playbook": pb["name"],
                    "incident_id": str(inc.id),
                    "signature": sig_name,
                    "severity": inc.severity.value,
                    "risk_score": inc.risk_score,
                    "ts": datetime.now(UTC).isoformat(),
                }
            )
            if ok:
                self.stats["webhooks"] += 1
                done.append("webhook")
        if actions.get("investigate"):
            # autonomously assemble the multi-camera investigation timeline
            import httpx
            from sentigon_common.config import settings as common

            try:
                async with httpx.AsyncClient(timeout=45.0) as c:
                    r = await c.post(
                        f"http://localhost:8010/incidents/{inc.id}/investigate",
                        headers={"X-Service-Token": common.service_token},
                    )
                if r.status_code == 200:
                    self.stats["investigations"] = self.stats.get("investigations", 0) + 1
                    done.append("investigate")
            except Exception:  # noqa: BLE001
                log.exception("playbooks.investigate_failed")

        if actions.get("webpush"):
            # browser push straight to the on-duty operator for P1 threats
            ok, _ = send_webpush(
                f"P1 THREAT: {sig_name}",
                f"{title} (risk {inc.risk_score}) on camera {inc.camera_id}",
            )
            if ok:
                self.stats["webpush"] += 1
                done.append("webpush")

        session.add(
            AuditLogEntry(
                action="playbook.triggered",
                resource_type="incident",
                resource_id=str(inc.id),
                details={"playbook": pb["name"], "actions": done},
            )
        )
        return done
