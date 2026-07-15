"""Liveness/readiness probes shared by every service.

/healthz  liveness: the process is up (always 200).
/readyz   readiness: dependency probes must all pass, else 503.

Reusable async probes for postgres, redis, kafka, qdrant, and minio are provided
so a service composes exactly the checks it depends on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter, Response
from sqlalchemy import text

from .config import settings
from .db.session import get_async_engine
from .logging import get_logger

log = get_logger("health")

Check = Callable[[], Awaitable[tuple[bool, str]]]


async def check_postgres() -> tuple[bool, str]:
    try:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def check_redis() -> tuple[bool, str]:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        try:
            await client.ping()
            return True, "ok"
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def check_kafka() -> tuple[bool, str]:
    try:
        from aiokafka.admin import AIOKafkaAdminClient

        admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap)
        await admin.start()
        try:
            await admin.list_topics()
            return True, "ok"
        finally:
            await admin.close()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def check_qdrant() -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.qdrant_url}/readyz")
            return (r.status_code == 200, f"http {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def check_minio() -> tuple[bool, str]:
    scheme = "https" if settings.minio_secure else "http"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{scheme}://{settings.minio_endpoint}/minio/health/live")
            return (r.status_code == 200, f"http {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def make_health_router(service: str, checks: dict[str, Check] | None = None) -> APIRouter:
    router = APIRouter(tags=["health"])
    checks = checks or {}

    @router.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": service}

    @router.get("/readyz")
    async def readyz(response: Response) -> dict:
        results: dict[str, dict] = {}
        all_ok = True
        for name, check in checks.items():
            ok, detail = await check()
            results[name] = {"ok": ok, "detail": detail}
            all_ok = all_ok and ok
        if not all_ok:
            response.status_code = 503
        return {"status": "ready" if all_ok else "degraded", "service": service, "checks": results}

    return router
