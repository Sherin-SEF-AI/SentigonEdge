"""VLM verifier: builds the structured prompt, calls the VLM (OpenAI-compatible),
and parses the verdict. Pulls the event-time frame and a follow-up frame.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid

import httpx
from sentigon_common.config import settings as common
from sentigon_common.logging import get_logger
from sentigon_common.storage import get_store

from .config import settings

log = get_logger("reason.verifier")
_store = get_store()
_VALID = {"confirmed", "rejected", "unverified"}
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _fetch(ref: str | None) -> bytes | None:
    if not ref or "/" not in ref:
        return None
    bucket, key = ref.split("/", 1)
    try:
        return _store.get_bytes(bucket, key)
    except Exception:  # noqa: BLE001
        return None


async def _postroll(camera_id: str) -> bytes | None:
    if not settings.postroll_enabled:
        return None
    await asyncio.sleep(settings.postroll_delay_seconds)
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.post(f"{settings.ingest_url}/cameras/{camera_id}/snapshot")
            if r.status_code == 200:
                return _fetch(r.json().get("ref"))
    except Exception:  # noqa: BLE001
        return None
    return None


def _build_prompt(c: dict) -> str:
    ctx = c.get("context", {})
    return (
        "You are a security operations analyst verifying an automated video-analytics alert. "
        "Do not assume the alert is correct. Confirm only if the imagery genuinely supports it; "
        "reject if it is a false alarm; use unverified if the imagery is inconclusive.\n\n"
        f"Signature: {c.get('signature_name')}\n"
        f"Event type: {c.get('event_type')}\n"
        f"Zone: {ctx.get('zone', 'n/a')} (type: {ctx.get('zone_type', 'n/a')})\n"
        f"Detector context: {json.dumps(ctx)}\n"
        f"Time (UTC): {c.get('ts')}\n\n"
        "You are shown the event-time frame and a follow-up frame from the same camera. "
        "Assess whether this is a genuine security event.\n"
        "Respond ONLY with strict JSON, no prose, no markdown:\n"
        '{"verdict":"confirmed|rejected|unverified",'
        '"sitrep":"<one concise sentence an operator reads>",'
        '"reasoning":"<2 to 3 sentences citing what you see>",'
        '"attributes":{"people":<int>,"vehicles":<int>,"weapon":<bool>}}'
    )


def _parse(text: str) -> dict:
    raw = text.strip()
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "verdict": "unverified",
            "sitrep": text[:200],
            "reasoning": "unparseable VLM output",
            "attributes": {},
        }
    verdict = str(data.get("verdict", "unverified")).lower()
    if verdict not in _VALID:
        verdict = "unverified"
    return {
        "verdict": verdict,
        "sitrep": str(data.get("sitrep", ""))[:500],
        "reasoning": str(data.get("reasoning", ""))[:1000],
        "attributes": (
            data.get("attributes", {}) if isinstance(data.get("attributes"), dict) else {}
        ),
    }


async def verify(candidate: dict) -> dict:
    """Return {verdict, sitrep, reasoning, attributes, frames, latency_ms, model}."""
    frames: list[bytes] = []
    primary = _fetch(candidate.get("snapshot_ref"))
    if primary:
        frames.append(primary)
    post = await _postroll(str(candidate["camera_id"]))
    if post:
        frames.append(post)
    if not frames:
        return {
            "verdict": "unverified",
            "sitrep": "no imagery available",
            "reasoning": "",
            "attributes": {},
            "frames": 0,
            "latency_ms": 0.0,
            "model": common.reason_model,
        }

    content: list[dict] = [{"type": "text", "text": _build_prompt(candidate)}]
    for f in frames:
        b64 = base64.b64encode(f).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    payload = {
        "model": common.reason_model,
        "temperature": 0.1,
        "stream": False,
        # The verdict JSON is short (~150 tokens); cap generation so a rambling model
        # can never stall a verification, and to bound VLM tail latency.
        "max_tokens": 512,
        "messages": [{"role": "user", "content": content}],
    }
    t = time.perf_counter()
    async with httpx.AsyncClient(timeout=settings.vlm_timeout) as c:
        r = await c.post(f"{common.reason_endpoint}/chat/completions", json=payload)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    latency_ms = (time.perf_counter() - t) * 1000
    result = _parse(text)
    result.update(frames=len(frames), latency_ms=round(latency_ms, 1), model=common.reason_model)
    return result


def new_correlation() -> str:
    return uuid.uuid4().hex
