#!/usr/bin/env python3
"""Prove the reason VLM-path severity backpressure.

Creates two real incidents (one LOW, one CRITICAL), then feeds the reason engine
STALE candidate events (older than backpressure_stale_seconds) for each. Under
that backpressure condition the engine must:
  - DEFER the low-severity one (never call the VLM; mark it, do not drop it), and
  - VERIFY the critical one (call the VLM regardless of load).

    uv run python scripts/prove_backpressure.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera, Incident, Signature
from sentigon_common.schemas.enums import IncidentStatus, Severity
from sentigon_reason.engine import ReasonEngine
from sqlalchemy import select


async def _seed_incident(sev: Severity, snap: str, cam_id, sig_id) -> tuple[uuid.UUID, str]:
    corr = uuid.uuid4().hex
    async with async_session_factory() as s:
        inc = Incident(
            signature_id=sig_id,
            camera_id=cam_id,
            title=f"backpressure test ({sev.value})",
            severity=sev,
            status=IncidentStatus.NEW,
            confidence=0.85,
            snapshot_ref=snap,
            correlation_id=corr,
        )
        s.add(inc)
        await s.flush()
        iid = inc.id
        await s.commit()
    return iid, corr


def _stale_candidate(corr: str, sev: Severity, cam_id, snap: str) -> dict:
    return {
        "correlation_id": corr,
        "camera_id": str(cam_id),
        "signature_name": "Loitering",
        "severity": sev.value,
        "ts": (datetime.now(UTC) - timedelta(seconds=60)).isoformat(),  # stale => backpressure
        "snapshot_ref": snap,
        "context": {},
    }


async def main() -> None:
    async with async_session_factory() as s:
        cam = (await s.execute(select(Camera).limit(1))).scalars().first()
        sig = (await s.execute(select(Signature).where(Signature.name == "Loitering"))).scalars().first()
        snap = (
            await s.execute(
                select(Incident.snapshot_ref).where(Incident.snapshot_ref.is_not(None)).limit(1)
            )
        ).scalar_one()

    low_id, low_corr = await _seed_incident(Severity.LOW, snap, cam.id, sig.id)
    crit_id, crit_corr = await _seed_incident(Severity.CRITICAL, snap, cam.id, sig.id)
    print(f"seeded LOW incident {str(low_id)[:8]} and CRITICAL incident {str(crit_id)[:8]}")
    print("feeding both as STALE candidates (age 60s > 25s threshold)...\n")

    engine = ReasonEngine()
    await engine.start()
    try:
        await engine.handle(_stale_candidate(low_corr, Severity.LOW, cam.id, snap), low_corr)
        await engine.handle(_stale_candidate(crit_corr, Severity.CRITICAL, cam.id, snap), crit_corr)
    finally:
        await engine.stop()

    async with async_session_factory() as s:
        low = await s.get(Incident, low_id)
        crit = await s.get(Incident, crit_id)

    def deferred(i: Incident) -> bool:
        return bool((i.attributes or {}).get("vlm_deferred_backpressure"))

    print("=== RESULT ===")
    print(f"  LOW      -> verdict={low.verdict}  deferred={deferred(low)}  (expect: deferred, no verdict)")
    print(f"  CRITICAL -> verdict={crit.verdict}  verified={crit.reasoning_trace is not None}  (expect: VLM ran)")
    print(f"  engine.deferred_backpressure counter: {engine.stats['deferred_backpressure']}")
    ok = deferred(low) and low.verdict is None and (crit.verdict is not None or crit.reasoning_trace is not None)
    print("\n  ==> BACKPRESSURE WORKS: low-severity shed, critical verified" if ok else "\n  ==> FAILED")


if __name__ == "__main__":
    asyncio.run(main())
