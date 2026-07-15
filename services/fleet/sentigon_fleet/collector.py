"""Raw telemetry collection for the fleet engine.

Three independent sources, all best-effort:
  * cameras  — read from the DB (ingest keeps Camera.health/last_seen current).
  * services — HTTP /healthz (+ optional /stats) probes of the sibling services.
  * host     — kernel-level disk/mem/load/gpu metrics with no extra dependencies.
"""

from __future__ import annotations

import os
import shutil
import time

import httpx
from sentigon_common.db import async_session_factory
from sentigon_common.db.models import Camera
from sentigon_common.logging import get_logger
from sqlalchemy import select

from .config import settings

log = get_logger("fleet.collector")


async def collect_cameras() -> list[dict]:
    """Every active camera and the health ingest last persisted for it."""
    async with async_session_factory() as session:
        rows = (
            await session.execute(select(Camera).where(Camera.is_active.is_(True)))
        ).scalars().all()

    cameras: list[dict] = []
    for cam in rows:
        cameras.append(
            {
                "id": str(cam.id),
                "name": cam.name,
                "site_id": str(cam.site_id) if cam.site_id else None,
                "status": cam.status.value if cam.status else "offline",
                "health": cam.health or {},
                "last_seen": cam.last_seen.isoformat() if cam.last_seen else None,
                "target_fps": cam.fps,
            }
        )
    return cameras


def _parse_probe(entry: str) -> tuple[str, str, str]:
    parts = entry.split("|")
    name = parts[0].strip() if parts else entry.strip()
    healthz = parts[1].strip() if len(parts) > 1 else ""
    stats_url = parts[2].strip() if len(parts) > 2 else ""
    return name, healthz, stats_url


async def collect_services() -> list[dict]:
    """Probe each configured service. `up` is a 200 from its /healthz; if a stats
    URL is configured its JSON is attached best-effort. Never raises."""
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=settings.probe_timeout) as client:
        for entry in settings.service_probes:
            name, healthz, stats_url = _parse_probe(entry)
            up = False
            detail = "no healthz url"
            stats: dict | None = None
            latency_ms: float | None = None

            if healthz:
                start = time.perf_counter()
                try:
                    resp = await client.get(healthz)
                    latency_ms = round((time.perf_counter() - start) * 1000, 1)
                    up = resp.status_code == 200
                    detail = f"http {resp.status_code}"
                except Exception as exc:  # noqa: BLE001
                    latency_ms = round((time.perf_counter() - start) * 1000, 1)
                    up = False
                    detail = str(exc)[:200] or exc.__class__.__name__

            if up and stats_url:
                try:
                    resp = await client.get(stats_url)
                    if resp.status_code == 200:
                        parsed = resp.json()
                        stats = parsed if isinstance(parsed, dict) else {"data": parsed}
                except Exception:  # noqa: BLE001
                    stats = None

            results.append(
                {
                    "name": name,
                    "up": up,
                    "detail": detail,
                    "stats": stats,
                    "latency_ms": latency_ms,
                }
            )
    return results


def _mem_pct() -> float | None:
    """Memory pressure from /proc/meminfo: (1 - MemAvailable/MemTotal) * 100."""
    try:
        info: dict[str, str] = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key.strip()] = rest.strip()
        total = float(info["MemTotal"].split()[0])
        avail = float(info["MemAvailable"].split()[0])
        if total <= 0:
            return None
        return round((1.0 - avail / total) * 100.0, 1)
    except Exception:  # noqa: BLE001
        return None


# Jetson exposes instantaneous GPU load (0-1000) at one of these sysfs paths.
_GPU_LOAD_PATHS = (
    "/sys/devices/platform/gpu.0/load",
    "/sys/devices/gpu.0/load",
    "/sys/devices/platform/17000000.gpu/load",
)


def _gpu_pct() -> float | None:
    """Best-effort Jetson GPU utilisation percentage; None when unavailable."""
    for path in _GPU_LOAD_PATHS:
        try:
            with open(path, encoding="ascii") as fh:
                raw = fh.read().strip()
            if raw:
                return round(int(raw) / 10.0, 1)
        except Exception:  # noqa: BLE001
            continue
    return None


def collect_host() -> dict:
    """Host metrics without third-party deps. Synchronous and cheap."""
    disk_pct: float | None = None
    try:
        total, used, _free = shutil.disk_usage("/")
        if total:
            disk_pct = round(used / total * 100.0, 1)
    except Exception:  # noqa: BLE001
        disk_pct = None

    load1: float | None = None
    try:
        load1 = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        load1 = None

    return {
        "disk_pct": disk_pct,
        "mem_pct": _mem_pct(),
        "gpu_pct": _gpu_pct(),
        "load1": load1,
        "cpu_count": os.cpu_count(),
    }
