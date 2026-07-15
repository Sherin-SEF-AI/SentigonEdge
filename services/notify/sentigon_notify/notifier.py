"""Real notification transports: SMTP email, HTTP webhook, and Web Push (VAPID)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import smtplib
from email.message import EmailMessage
from pathlib import Path

import httpx
from sentigon_common.logging import get_logger

from .config import settings

log = get_logger("notify.transport")


def make_ack_token(incident_id: str) -> str:
    return hmac.new(settings.ack_secret.encode(), incident_id.encode(), hashlib.sha256).hexdigest()[:24]


def ack_url(incident_id: str, notify_base: str = "http://localhost:8070") -> str:
    return f"{notify_base}/ack/{incident_id}?token={make_ack_token(incident_id)}"

_SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def severity_meets(sev: str, minimum: str) -> bool:
    return _SEV_ORDER.get(sev, 0) >= _SEV_ORDER.get(minimum, 0)


def send_email(subject: str, body: str) -> tuple[bool, str]:
    if not settings.smtp_host:
        return False, "smtp not configured"
    try:
        msg = EmailMessage()
        msg["From"] = settings.email_from
        msg["To"] = settings.email_to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
            s.send_message(msg)
        return True, "sent"
    except Exception as exc:  # noqa: BLE001
        log.exception("notify.email_failed")
        return False, str(exc)


async def send_webhook(payload: dict) -> tuple[bool, str]:
    if not settings.webhook_url:
        return False, "webhook not configured"
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.post(settings.webhook_url, json=payload)
            return (r.status_code < 400, f"http {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        log.exception("notify.webhook_failed")
        return False, str(exc)


# ── Web Push (VAPID) ──────────────────────────────────────────


def _subs_path() -> Path:
    p = Path(settings.webpush_subs_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_subscriptions() -> list[dict]:
    p = _subs_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return []


def save_subscription(sub: dict) -> int:
    subs = load_subscriptions()
    endpoint = sub.get("endpoint")
    subs = [s for s in subs if s.get("endpoint") != endpoint]  # dedupe by endpoint
    subs.append(sub)
    _subs_path().write_text(json.dumps(subs))
    return len(subs)


def _vapid():
    """A Vapid signing object from the stored key (base64 PKCS8 PEM). py_vapid's
    from_string does not parse PEM, so load it explicitly and hand pywebpush the
    object it accepts directly."""
    if not settings.webpush_vapid_key:
        return None
    from py_vapid import Vapid01

    try:
        pem = base64.b64decode(settings.webpush_vapid_key)
    except Exception:  # noqa: BLE001
        pem = settings.webpush_vapid_key.encode()
    return Vapid01.from_pem(pem)


def send_webpush(title: str, body: str) -> tuple[bool, str]:
    """Send a real Web Push to every stored browser subscription."""
    vapid = _vapid()
    if vapid is None:
        return False, "webpush not configured"
    subs = load_subscriptions()
    if not subs:
        return False, "no subscriptions"
    from pywebpush import WebPushException, webpush

    payload = json.dumps({"title": title, "body": body})
    ok, fail = 0, 0
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=vapid,
                vapid_claims={"sub": settings.webpush_subject},
                timeout=10,
            )
            ok += 1
        except WebPushException as exc:
            fail += 1
            # 404/410 = subscription gone; prune it
            if exc.response is not None and exc.response.status_code in (404, 410):
                remaining = [s for s in load_subscriptions() if s.get("endpoint") != sub.get("endpoint")]
                _subs_path().write_text(json.dumps(remaining))
            log.warning("notify.webpush_failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            fail += 1
            log.warning("notify.webpush_error", error=str(exc))
    return (ok > 0, f"sent {ok}, failed {fail}")


def channel_status() -> dict:
    return {
        "email": "configured" if settings.smtp_host else "unconfigured",
        "webhook": "configured" if settings.webhook_url else "unconfigured",
        "sms": (
            "configured" if settings.sms_provider else "unconfigured (needs provider credentials)"
        ),
        "webpush": (
            f"configured ({len(load_subscriptions())} subscriptions)"
            if settings.webpush_vapid_key
            else "unconfigured (needs VAPID key + browser subscription)"
        ),
    }
