"""Natural-language activity notifications.

Operators define an alert in plain English ('a person on a ladder near the racks').
This evaluator periodically grabs a fresh frame from the alert's camera and asks the
VLM whether the condition is present; on a match it fires a real, VLM-confirmed
incident. Open-set detection with no authored signature (Ambient 'Activity
Notifications' equivalent).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import uuid
from datetime import UTC, datetime

import httpx
from sentigon_common.config import settings as common
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Event, Incident, IncidentStatusLog, NLAlert, Signature
from sentigon_common.logging import get_logger
from sentigon_common.risk import compute_risk_score
from sentigon_common.schemas.enums import IncidentStatus, Verdict
from sentigon_common.storage import get_store
from sqlalchemy import select, update

from .config import settings

log = get_logger("reason.nl")
_store = get_store()
_JSON = re.compile(r"\{.*\}", re.DOTALL)
_SIGNATURE = "Custom Activity Alert"


def _fetch(ref: str | None) -> bytes | None:
    if not ref or "/" not in ref:
        return None
    bucket, key = ref.split("/", 1)
    try:
        return _store.get_bytes(bucket, key)
    except Exception:  # noqa: BLE001
        return None


async def _fresh_frame(camera_id: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.post(f"{settings.ingest_url}/cameras/{camera_id}/snapshot")
            if r.status_code == 200:
                return _fetch(r.json().get("ref"))
    except Exception:  # noqa: BLE001
        return None
    return None


async def evaluate_nl(image: bytes, prompt: str) -> tuple[bool, str]:
    """Ask the VLM whether the NL condition is present in the frame."""
    b64 = base64.b64encode(image).decode()
    instruction = (
        "You are a physical-security monitoring assistant. Examine the image and decide "
        "whether the following condition is clearly TRUE in the scene right now. Be strict: "
        "only answer true if it is genuinely visible, not merely plausible.\n\n"
        f"Condition: \"{prompt}\"\n\n"
        'Respond with ONLY JSON: {"match": true or false, "reason": "one short sentence"}'
    )
    payload = {
        "model": common.reason_model,
        "temperature": 0.1,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=settings.vlm_timeout) as c:
        r = await c.post(f"{common.reason_endpoint}/chat/completions", json=payload)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    m = _JSON.search(text)
    if not m:
        return False, text[:120]
    try:
        d = json.loads(m.group(0))
        return bool(d.get("match")), str(d.get("reason", ""))[:200]
    except Exception:  # noqa: BLE001
        return False, text[:120]


class NLAlertEvaluator:
    def __init__(self) -> None:
        self.stats = {"evaluations": 0, "matches": 0, "fired": 0}
        self._sig_id: uuid.UUID | None = None

    async def _signature_id(self) -> uuid.UUID | None:
        if self._sig_id is None:
            async with async_session_factory() as s:
                self._sig_id = await s.scalar(
                    select(Signature.id).where(Signature.name == _SIGNATURE)
                )
        return self._sig_id

    async def run(self, stop: asyncio.Event) -> None:
        log.info("nl.evaluator_started")
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("nl.tick_error")
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.nl_eval_tick_s)

    async def tick(self) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session:
            alerts = (
                await session.execute(select(NLAlert).where(NLAlert.active.is_(True)))
            ).scalars().all()
        for a in alerts:
            if a.camera_id is None:
                continue
            if a.last_eval_at and (now - a.last_eval_at).total_seconds() < a.eval_interval_s:
                continue
            frame = await _fresh_frame(str(a.camera_id))
            await self._mark_eval(a.id, now)
            if frame is None:
                continue
            self.stats["evaluations"] += 1
            try:
                match, reason = await evaluate_nl(frame, a.prompt)
            except Exception:  # noqa: BLE001
                log.exception("nl.vlm_error", alert=a.name)
                continue
            if not match:
                continue
            self.stats["matches"] += 1
            if a.last_fired_at and (now - a.last_fired_at).total_seconds() < a.cooldown_s:
                continue
            await self._fire(a, reason)

    async def _mark_eval(self, alert_id: uuid.UUID, now: datetime) -> None:
        async with async_session_factory() as session:
            await session.execute(
                update(NLAlert).where(NLAlert.id == alert_id).values(last_eval_at=now)
            )
            await session.commit()

    async def _fire(self, a: NLAlert, reason: str) -> None:
        sig_id = await self._signature_id()
        now = datetime.now(UTC)
        corr = uuid.uuid4().hex
        risk, _ = compute_risk_score(
            severity=a.severity.value, category="behavioral", confidence=0.85, verdict="confirmed"
        )
        async with async_session_factory() as session:
            ev = Event(
                signature_id=sig_id, camera_id=a.camera_id, event_type="nl.activity",
                ts=now, severity=a.severity, confidence=0.85,
                object_refs={"nl_alert_id": str(a.id)},
                context={"nl_alert": a.name, "prompt": a.prompt, "reason": reason, "method": "nl-vlm"},
                correlation_id=corr,
            )
            session.add(ev)
            await session.flush()
            inc = Incident(
                event_id=ev.id, signature_id=sig_id, camera_id=a.camera_id,
                title=f"Activity notification: {a.name}", severity=a.severity,
                status=IncidentStatus.NEW, confidence=0.85, risk_score=risk,
                verdict=Verdict.CONFIRMED, sitrep=reason,
                attributes={"nl_alert": a.name, "prompt": a.prompt, "reason": reason, "method": "nl-vlm"},
                correlation_id=corr, last_seen_at=now,
            )
            session.add(inc)
            await session.flush()
            session.add(
                IncidentStatusLog(incident_id=inc.id, to_status="new", note="NL activity alert (VLM)")
            )
            await session.execute(
                update(NLAlert).where(NLAlert.id == a.id).values(
                    last_fired_at=now, fire_count=NLAlert.fire_count + 1
                )
            )
            await session.commit()
        self.stats["fired"] += 1
        log.info("nl.fired", alert=a.name, camera=str(a.camera_id), reason=reason[:80])
