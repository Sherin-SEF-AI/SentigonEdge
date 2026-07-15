#!/usr/bin/env python3
"""Run a batch of REAL pending incidents through the reason verifier, pointed at
whatever REASON_ENDPOINT is set (here: the RunPod Qwen3-VL-32B). Proves the 32B
tier adjudicating real Sentigon incidents at scale, then writes the verdicts back.

    REASON_ENDPOINT=https://<pod>-8000.proxy.runpod.net/v1 REASON_MODEL=reason \
      REASON_BACKEND=vllm uv run python scripts/runpod_batch_verify.py 12
"""

from __future__ import annotations

import asyncio
import sys

from sentigon_common.config import settings as common
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Event, Incident, Signature
from sentigon_common.schemas.enums import Verdict
from sentigon_reason.verifier import verify
from sqlalchemy import select

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10


async def main() -> None:
    print(f"reason -> backend={common.reason_backend} model={common.reason_model}")
    print(f"endpoint -> {common.reason_endpoint}\n")

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Incident, Signature.name, Event.snapshot_ref)
                .join(Signature, Incident.signature_id == Signature.id)
                .join(Event, Incident.event_id == Event.id)
                .where(Event.snapshot_ref.is_not(None), Incident.verdict.is_(None))
                .order_by(Incident.created_at.desc())
                .limit(N)
            )
        ).all()

    print(f"verifying {len(rows)} real incidents through the 32B...\n")
    confirmed = rejected = other = 0
    for inc, signame, snap in rows:
        candidate = {
            "incident_id": str(inc.id),
            "camera_id": str(inc.camera_id),
            "signature_name": signame,
            "severity": inc.severity.value,
            "title": inc.title,
            "snapshot_ref": snap,
            "context": inc.attributes or {},
        }
        r = await verify(candidate)
        v = r["verdict"]
        confirmed += v == "confirmed"
        rejected += v == "rejected"
        other += v not in ("confirmed", "rejected")
        # persist the 32B verdict back to the incident
        async with async_session_factory() as s2:
            row = await s2.get(Incident, inc.id)
            if row is not None and v in ("confirmed", "rejected"):
                row.verdict = Verdict.CONFIRMED if v == "confirmed" else Verdict.REJECTED
                row.sitrep = r["sitrep"]
                await s2.commit()
        mark = {"confirmed": "CONFIRM", "rejected": "reject "}.get(v, "unsure ")
        print(f"  [{mark}] {signame:20} {r['latency_ms']:>7.0f}ms  {r['sitrep'][:88]}")

    print(f"\n=== 32B batch result: {confirmed} confirmed, {rejected} rejected, {other} other ===")


if __name__ == "__main__":
    asyncio.run(main())
