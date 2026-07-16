"""Reason engine: consume candidate events, verify with the VLM, update the Incident
with the verdict + SITREP + reasoning trace, and publish incidents.verified.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Incident, IncidentStatusLog, Signature, Zone
from sentigon_common.kafka import BusProducer
from sentigon_common.logging import get_logger, set_correlation_id
from sentigon_common.risk import compute_risk_score
from sentigon_common.schemas.bus import Topics, VerifiedIncidentMsg
from sentigon_common.schemas.enums import IncidentStatus, Severity, Verdict
from sqlalchemy import select

from .config import settings
from .verifier import verify

log = get_logger("reason.engine")

# Higher = more urgent. Used by the backpressure shed policy.
_SEV_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def _candidate_age_seconds(payload: dict) -> float:
    ts = payload.get("ts")
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).total_seconds()
    except ValueError:
        return 0.0


class ReasonEngine:
    def __init__(self) -> None:
        self.producer = BusProducer("reason")
        self.stats = {
            "verified": 0,
            "confirmed": 0,
            "rejected": 0,
            "unverified": 0,
            "deferred_backpressure": 0,
            "avg_latency_ms": 0.0,
        }
        self._shed_rank = _SEV_RANK.get(Severity(settings.backpressure_shed_below), 2)
        self._lat_sum = 0.0
        self._lat_n = 0

    async def start(self) -> None:
        await self.producer.start()

    async def stop(self) -> None:
        await self.producer.stop()

    async def _defer(self, corr: str | None, age: float) -> None:
        """Mark a low-severity incident as VLM-deferred under backpressure. The
        incident stays visible and unverified (never dropped); an operator can
        still see and action it, and it can be re-verified later."""
        if not corr:
            return
        async with async_session_factory() as session:
            inc = (
                (await session.execute(select(Incident).where(Incident.correlation_id == corr)))
                .scalars()
                .first()
            )
            if inc is None:
                return
            inc.attributes = {
                **(inc.attributes or {}),
                "vlm_deferred_backpressure": True,
                "deferred_age_s": round(age, 1),
            }
            session.add(
                IncidentStatusLog(
                    incident_id=inc.id,
                    to_status=inc.status.value,
                    note=f"VLM verification deferred (backpressure, candidate age {age:.0f}s)",
                )
            )
            await session.commit()

    async def handle(self, payload: dict, correlation_id: str | None) -> None:
        corr = payload.get("correlation_id") or correlation_id
        set_correlation_id(corr)

        # Backpressure: if this candidate arrived stale (the VLM is behind) and its
        # severity is below the shed line, defer verification so the model's
        # throughput goes to critical/high. The incident is NOT dropped: it stays
        # visible as unverified with an explicit deferred marker.
        age = _candidate_age_seconds(payload)
        sev_name = str(payload.get("severity", "medium"))
        try:
            sev_rank = _SEV_RANK.get(Severity(sev_name), 2)
        except ValueError:
            sev_rank = 2
        if age > settings.backpressure_stale_seconds and sev_rank < self._shed_rank:
            await self._defer(corr, age)
            self.stats["deferred_backpressure"] += 1
            log.info("reason.deferred_backpressure", corr=corr, age_s=round(age, 1), severity=sev_name)
            return

        # Idempotency: the bus is at-least-once, so a candidate can be redelivered.
        # If its incident is already verified, do not re-run the VLM, re-publish, or
        # double-count — just acknowledge.
        if corr:
            async with async_session_factory() as session:
                prior = await session.scalar(
                    select(Incident.verdict).where(Incident.correlation_id == corr)
                )
            if prior is not None:
                log.info("reason.already_verified", corr=corr)
                return

        result = await verify(payload)
        verdict = Verdict(result["verdict"])

        async with async_session_factory() as session:
            inc = None
            if corr:
                inc = (
                    (await session.execute(select(Incident).where(Incident.correlation_id == corr)))
                    .scalars()
                    .first()
                )
            if inc is None:
                log.warning(
                    "reason.no_incident", corr=corr, signature=payload.get("signature_name")
                )
                return
            inc.verdict = verdict
            # re-score the threat now that the VLM has adjudicated (CONFIRMED lifts,
            # REJECTED sinks it) so the priority queue reflects verified danger
            sig_cat = (
                await session.scalar(
                    select(Signature.category).where(Signature.id == inc.signature_id)
                )
                if inc.signature_id
                else None
            )
            zone_type = None
            if inc.zone_id:
                zt = await session.scalar(select(Zone.zone_type).where(Zone.id == inc.zone_id))
                zone_type = zt.value if zt else None
            inc.risk_score, _ = compute_risk_score(
                severity=inc.severity.value,
                category=sig_cat,
                confidence=inc.confidence,
                verdict=verdict.value,
                zone_type=zone_type,
            )
            inc.sitrep = result["sitrep"]
            inc.reasoning_trace = {
                "reasoning": result["reasoning"],
                "model": result["model"],
                "latency_ms": result["latency_ms"],
                "frames": result["frames"],
            }
            if result.get("attributes"):
                inc.attributes = {**(inc.attributes or {}), **result["attributes"]}
            if verdict == Verdict.REJECTED and settings.auto_dismiss_rejected:
                inc.status = IncidentStatus.FALSE_POSITIVE
                inc.resolved_at = datetime.now(UTC)
                session.add(
                    IncidentStatusLog(
                        incident_id=inc.id,
                        from_status="new",
                        to_status="false_positive",
                        note="VLM rejected as false alarm",
                    )
                )
            else:
                session.add(
                    IncidentStatusLog(
                        incident_id=inc.id,
                        to_status=inc.status.value,
                        note=f"VLM verdict: {verdict.value}",
                    )
                )
            await session.commit()
            inc_id = inc.id
            cam_id = inc.camera_id
            sev = inc.severity
            snap = inc.snapshot_ref
            clip = inc.clip_ref

        await self.producer.publish(
            Topics.INCIDENTS_VERIFIED,
            VerifiedIncidentMsg(
                producer="reason",
                correlation_id=corr,
                incident_id=inc_id,
                camera_id=cam_id,
                signature_name=payload.get("signature_name", "unknown"),
                severity=sev,
                verdict=verdict,
                sitrep=result["sitrep"],
                reasoning_trace={"reasoning": result["reasoning"]},
                attributes=result.get("attributes", {}),
                snapshot_ref=snap,
                clip_ref=clip,
            ),
            key=str(cam_id),
        )

        self.stats["verified"] += 1
        self.stats[verdict.value] = self.stats.get(verdict.value, 0) + 1
        self._lat_sum += result["latency_ms"]
        self._lat_n += 1
        self.stats["avg_latency_ms"] = round(self._lat_sum / self._lat_n, 1)
        log.info(
            "reason.verified",
            signature=payload.get("signature_name"),
            verdict=verdict.value,
            latency_ms=result["latency_ms"],
            incident=str(inc_id),
        )
