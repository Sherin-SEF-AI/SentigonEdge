"""Fleet engine: the periodic collect -> evaluate -> reconcile -> persist loop.

Each tick gathers telemetry, runs the rule engine, upserts the resulting findings
into fleet_findings (dedup on kind+target_id, auto-resolving anything that has
cleared), and writes a fleet_snapshots rollup. `self.latest` caches the most
recent snapshot dict so the read endpoints can serve instantly without a DB hit.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

from sentigon_common.db import async_session_factory
from sentigon_common.db.models import FleetFinding, FleetSnapshot
from sentigon_common.logging import get_logger
from sentigon_common.schemas.enums import Severity
from sqlalchemy import select

from .collector import collect_cameras, collect_host, collect_services
from .config import settings
from .diagnostics import evaluate

log = get_logger("fleet.engine")


def _severity(value: object) -> Severity:
    try:
        return Severity(str(value))
    except ValueError:
        return Severity.INFO


def _to_uuid(value: object) -> uuid.UUID | None:
    if not value:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


class FleetEngine:
    def __init__(self) -> None:
        self.latest: dict = {}
        self.stats: dict = {"collections": 0, "findings_active": 0}

    async def run(self, stop: asyncio.Event) -> None:
        """Tick immediately, then every collect_seconds until stopped."""
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("fleet.tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.collect_seconds)

    async def tick(self) -> None:
        cameras = await collect_cameras()
        services = await collect_services()
        host = collect_host()  # sync, cheap

        findings = evaluate(cameras, services, host)
        now = datetime.now(UTC)
        cameras_online = sum(1 for c in cameras if c.get("status") == "online")
        services_up = sum(1 for s in services if s.get("up"))

        async with async_session_factory() as session:
            await self._reconcile_findings(session, findings, now)
            session.add(
                FleetSnapshot(
                    cameras_total=len(cameras),
                    cameras_online=cameras_online,
                    services_total=len(services),
                    services_up=services_up,
                    findings_active=len(findings),
                    disk_pct=host.get("disk_pct"),
                    mem_pct=host.get("mem_pct"),
                    gpu_pct=host.get("gpu_pct"),
                    load1=host.get("load1"),
                    payload={
                        "cameras": cameras,
                        "services": services,
                        "host": host,
                        "findings": findings,
                    },
                )
            )
            await session.commit()

        self.latest = {
            "ts": now.isoformat(),
            "cameras_total": len(cameras),
            "cameras_online": cameras_online,
            "services_total": len(services),
            "services_up": services_up,
            "findings_active": len(findings),
            "disk_pct": host.get("disk_pct"),
            "mem_pct": host.get("mem_pct"),
            "gpu_pct": host.get("gpu_pct"),
            "load1": host.get("load1"),
            "cameras": cameras,
            "services": services,
            "host": host,
            "findings": findings,
        }
        self.stats["collections"] += 1
        self.stats["findings_active"] = len(findings)
        log.info(
            "fleet.tick",
            cameras=f"{cameras_online}/{len(cameras)}",
            services=f"{services_up}/{len(services)}",
            findings=len(findings),
        )

    async def _reconcile_findings(
        self, session, produced: list[dict], now: datetime
    ) -> None:
        """Upsert produced findings by (kind, target_id); resolve any active
        finding that is no longer produced."""
        existing = (
            await session.execute(select(FleetFinding).where(FleetFinding.active.is_(True)))
        ).scalars().all()
        by_key: dict[tuple[str, str | None], FleetFinding] = {
            (f.kind, f.target_id): f for f in existing
        }

        produced_keys: set[tuple[str, str | None]] = set()
        for finding in produced:
            key = (finding["kind"], finding.get("target_id"))
            produced_keys.add(key)
            current = by_key.get(key)
            if current is not None:
                current.severity = _severity(finding.get("severity"))
                current.detail = finding.get("detail", current.detail)
                current.metric = finding.get("metric") or {}
                current.recommended_action = finding.get("recommended_action")
                current.target_name = finding.get("target_name")
                current.site_id = _to_uuid(finding.get("site_id"))
                current.last_seen_at = now
                current.active = True
                current.resolved_at = None
            else:
                session.add(
                    FleetFinding(
                        kind=finding["kind"],
                        severity=_severity(finding.get("severity")),
                        target_type=finding.get("target_type", "host"),
                        target_id=finding.get("target_id"),
                        target_name=finding.get("target_name"),
                        detail=finding.get("detail", ""),
                        metric=finding.get("metric") or {},
                        recommended_action=finding.get("recommended_action"),
                        site_id=_to_uuid(finding.get("site_id")),
                        active=True,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )

        for key, finding in by_key.items():
            if key not in produced_keys:
                finding.active = False
                finding.resolved_at = now
