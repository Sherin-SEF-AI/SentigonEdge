"""Groq adjudication tier (text-only).

The local VLM sees the imagery and produces a first-pass verdict + scene
description (its `reasoning`). For high-stakes incidents this module escalates to
a fast, strong text reasoner (Groq `gpt-oss-120b`) that re-judges over that
description + the full detector/zone/time context and returns a sharper verdict,
SITREP, and recommended action.

Design notes:
- Vision stays local — Groq has no vision model, so we never send images here.
- Off the streaming hot path: only fires per incident, which is rare.
- Best-effort: any error/timeout returns None and the caller keeps the VLM verdict.
"""

from __future__ import annotations

import json
import re
import time

import httpx
from sentigon_common.logging import get_logger

from .config import settings

log = get_logger("reason.escalate")

_VALID = {"confirmed", "rejected", "unverified"}
_SEV_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def enabled() -> bool:
    return bool(settings.escalate_enabled and settings.escalate_api_key)


def should_escalate(severity: str, verdict: str) -> bool:
    """A high-stakes incident (severity at/above the threshold) or an inconclusive
    VLM verdict warrants a second opinion from the stronger reasoner."""
    if not enabled():
        return False
    if settings.escalate_on_unverified and verdict == "unverified":
        return True
    floor = _SEV_RANK.get(settings.escalate_min_severity.lower(), 3)
    return _SEV_RANK.get(str(severity).lower(), 2) >= floor


def _build_messages(candidate: dict, vlm: dict) -> list[dict]:
    ctx = candidate.get("context", {})
    system = (
        "You are a senior physical-security operations analyst adjudicating an "
        "automated video-analytics alert. A first-pass vision model has already "
        "described the scene; your job is to make the final call using that "
        "description plus the structured context. Be skeptical: confirm only when "
        "the evidence genuinely supports the alert, reject clear false alarms, and "
        "use 'unverified' when the description is inconclusive. Do not invent detail "
        "the vision model did not report."
    )
    user = (
        f"Signature: {candidate.get('signature_name')}\n"
        f"Event type: {candidate.get('event_type')}\n"
        f"Severity: {candidate.get('severity')}\n"
        f"Zone: {ctx.get('zone', 'n/a')} (type: {ctx.get('zone_type', 'n/a')})\n"
        f"Time (UTC): {candidate.get('ts')}\n"
        f"Detector context: {json.dumps(ctx)[:1500]}\n\n"
        "Vision model's first pass:\n"
        f"- verdict: {vlm.get('verdict')}\n"
        f"- what it saw: {vlm.get('reasoning', '')[:800]}\n"
        f"- attributes: {json.dumps(vlm.get('attributes', {}))}\n\n"
        "Adjudicate. Respond ONLY with strict JSON, no prose, no markdown:\n"
        '{"verdict":"confirmed|rejected|unverified",'
        '"sitrep":"<one concise sentence an operator reads>",'
        '"reasoning":"<2-3 sentences justifying the call from the evidence>",'
        '"recommended_action":"<one short next step for the operator>",'
        '"confidence":<0.0-1.0>}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse(text: str) -> dict | None:
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
        return None
    verdict = str(data.get("verdict", "")).lower()
    if verdict not in _VALID:
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "verdict": verdict,
        "sitrep": str(data.get("sitrep", ""))[:500],
        "reasoning": str(data.get("reasoning", ""))[:1000],
        "recommended_action": str(data.get("recommended_action", ""))[:300],
        "confidence": max(0.0, min(1.0, confidence)),
    }


async def adjudicate(candidate: dict, vlm: dict) -> dict | None:
    """Second-opinion adjudication via Groq. Returns a refined verdict dict, or None
    on any failure (caller then keeps the VLM verdict)."""
    payload = {
        "model": settings.escalate_model,
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False,
        "messages": _build_messages(candidate, vlm),
    }
    t = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.escalate_timeout) as c:
            r = await c.post(
                f"{settings.escalate_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.escalate_api_key}"},
                json=payload,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        log.warning("reason.escalate_failed", error=str(exc)[:200])
        return None
    parsed = _parse(text)
    if parsed is None:
        log.warning("reason.escalate_unparseable")
        return None
    parsed["model"] = settings.escalate_model
    parsed["latency_ms"] = round((time.perf_counter() - t) * 1000, 1)
    return parsed
