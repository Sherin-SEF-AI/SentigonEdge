"""Notify service: consume incidents.verified, deliver confirmed incidents."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
from collections.abc import AsyncIterator

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sentigon_common.auth import install_auth_middleware
from sentigon_common.config import settings as common_settings
from sentigon_common.health import check_kafka, check_postgres, make_health_router
from sentigon_common.kafka import run_consumer
from sentigon_common.logging import configure_logging
from sentigon_common.schemas.bus import Topics

from .config import settings
from .engine import NotifyEngine
from .escalation import Escalator, oncall_contact
from .notifier import channel_status, make_ack_token, save_subscription, send_webpush
from .playbooks import PlaybookEngine


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("notify")
    engine = NotifyEngine()
    escalator = Escalator()
    playbooks = PlaybookEngine()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(
            [Topics.INCIDENTS_VERIFIED],
            "notify",
            engine.handle,
            stop_event=stop,
            auto_offset_reset="latest",
        )
    )
    # SOP playbooks run on candidate events (immediate, no VLM dependency)
    pb_task = asyncio.create_task(
        run_consumer(
            [Topics.EVENTS_CANDIDATE],
            "notify-playbooks",
            playbooks.handle,
            stop_event=stop,
            auto_offset_reset="latest",
        )
    )
    esc_task = asyncio.create_task(escalator.run(stop))
    app.state.engine = engine
    app.state.playbooks = playbooks
    app.state.escalator = escalator
    app.state.stop = stop
    app.state.task = task
    try:
        yield
    finally:
        stop.set()
        for t in (task, pb_task, esc_task):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


app = FastAPI(title="Sentigon Notify", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=common_settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
# /ack uses its own signed token; /push/vapid returns only the public VAPID key
install_auth_middleware(app, protect_reads=True, public_paths={"/ack", "/push/vapid"})
app.include_router(make_health_router("notify", {"postgres": check_postgres, "kafka": check_kafka}))


@app.get("/push/vapid")
async def push_vapid() -> dict:
    """Public application-server key the browser subscribes with."""
    return {"publicKey": settings.webpush_public_key, "configured": bool(settings.webpush_vapid_key)}


@app.post("/push/subscribe")
async def push_subscribe(sub: dict = Body(...)) -> dict:
    count = save_subscription(sub)
    return {"ok": True, "subscriptions": count}


@app.post("/push/test")
async def push_test() -> dict:
    ok, detail = send_webpush("Sentigon", "Web push is live. This is a real test notification.")
    return {"ok": ok, "detail": detail}


@app.post("/ack/{incident_id}")
async def ack_from_channel(incident_id: str, token: str) -> dict:
    """Acknowledge an incident from a notification channel (email/webhook/push
    action) using a signed token, so an operator can ack without a full login.
    Routed through the API's real ack path (service token) so the lifecycle,
    timestamps, and audit are identical to a console ack."""
    if not hmac.compare_digest(token, make_ack_token(incident_id)):
        raise HTTPException(403, "invalid ack token")
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.post(
            f"{settings.api_url}/incidents/{incident_id}/ack",
            json={"note": "acknowledged from notification channel"},
            headers={"X-Service-Token": common_settings.service_token},
        )
    return {"ok": r.status_code < 400, "status": r.status_code, "incident_id": incident_id}


@app.get("/stats")
async def stats(request: Request) -> dict:
    return {
        "channels": channel_status(),
        "email_to": settings.email_to,
        # never return the webhook URL — it embeds a Slack/Teams/PagerDuty secret
        "webhook_configured": bool(settings.webhook_url),
        "min_severity": settings.min_severity,
        "oncall_now": oncall_contact(),
        "escalation": request.app.state.escalator.stats,
        "playbooks": request.app.state.playbooks.stats,
        **request.app.state.engine.stats,
    }


@app.post("/test")
async def test(request: Request) -> dict:
    """Send a real test notification through the live transports."""
    return await request.app.state.engine.test()
