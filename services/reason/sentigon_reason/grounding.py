"""Open-vocabulary grounding — "find and box it".

Given a camera frame and a plain-English query, return zero or more bounding boxes
for the described thing(s). This is what turns a natural-language alert from a
yes/no verdict into an actual localization, and gives open-vocabulary detection for
the long tail the fixed-class YOLO detector structurally cannot cover (a ladder
against a wall, an abandoned bag, "a person on the roof").

Two interchangeable backends, selected by REASON_SVC_GROUND_BACKEND:

  - "vlm" (default): reuse the reason tier's OpenAI-compatible VLM (Qwen2.5-VL via
    Ollama in this deployment). One chat call returns a presence flag, a short
    reason, and normalized boxes. No extra model, a license you can ship, runs
    today on the same endpoint the verifier uses.

  - "locateanything": call a LocateAnything server (NVIDIA LocateAnything-3B, a
    Qwen2.5 + Moon-ViT grounding model) served over HTTP by
    scripts/locateanything_server.py. Purpose-built grounder — but the weights are
    under the NVIDIA **non-commercial** research license; see that script's header
    before using it for anything you ship.

Both backends normalise to the same shape: boxes as 0..1 [x1, y1, x2, y2] with a
top-left origin, so the same frame renders them identically regardless of which
model produced them.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass

import httpx
from sentigon_common.config import settings as common
from sentigon_common.logging import get_logger
from sentigon_common.storage import get_store

from .config import settings

log = get_logger("reason.grounding")
_store = get_store()
_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class GroundedBox:
    label: str
    score: float
    box: tuple[float, float, float, float]  # normalized x1, y1, x2, y2 (top-left origin)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "score": round(self.score, 3),
            "box": [round(v, 4) for v in self.box],
        }


@dataclass
class GroundingResult:
    match: bool
    reason: str
    boxes: list[GroundedBox]
    backend: str
    latency_ms: float

    def as_dict(self) -> dict:
        return {
            "match": self.match,
            "reason": self.reason,
            "backend": self.backend,
            "latency_ms": self.latency_ms,
            "boxes": [b.as_dict() for b in self.boxes],
        }


# ── frame helpers (shared by nl_alerts and the /ground endpoint) ──────────────


def fetch_bytes(ref: str | None) -> bytes | None:
    """Load an object (bucket/key) from the evidence store."""
    if not ref or "/" not in ref:
        return None
    bucket, key = ref.split("/", 1)
    try:
        return _store.get_bytes(bucket, key)
    except Exception:  # noqa: BLE001
        return None


async def fresh_frame(camera_id: str) -> tuple[str | None, bytes | None]:
    """Grab a fresh snapshot from a camera. Returns (ref, bytes) so callers can both
    ground the frame and pin that exact image on the incident for the overlay."""
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            # ingest snapshot is a protected write — authenticate as an internal caller
            r = await c.post(
                f"{settings.ingest_url}/cameras/{camera_id}/snapshot",
                headers={"X-Service-Token": common.service_token},
            )
            if r.status_code == 200:
                ref = r.json().get("ref")
                return ref, fetch_bytes(ref)
    except Exception:  # noqa: BLE001
        return None, None
    return None, None


# ── backend config resolution ────────────────────────────────────────────────


def _endpoint() -> str:
    return settings.ground_endpoint or common.reason_endpoint


def _model() -> str:
    return settings.ground_model or common.reason_model


# ── cleanup: clamp, filter, cap ──────────────────────────────────────────────


def _clean(boxes: list[GroundedBox]) -> list[GroundedBox]:
    out: list[GroundedBox] = []
    for b in boxes:
        x1, y1, x2, y2 = b.box
        # tolerate models that return [x, y, w, h] or unordered corners
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        x1, y1, x2, y2 = (max(0.0, min(1.0, v)) for v in (x1, y1, x2, y2))
        if x2 - x1 < 0.005 or y2 - y1 < 0.005:  # degenerate
            continue
        if b.score < settings.ground_min_score:
            continue
        out.append(GroundedBox(label=b.label[:64] or "object", score=b.score, box=(x1, y1, x2, y2)))
    out.sort(key=lambda b: b.score, reverse=True)
    return out[: settings.ground_max_boxes]


def _auto_reason(boxes: list[GroundedBox], query: str) -> str:
    if not boxes:
        return f'no match for "{query[:80]}"'
    labels = ", ".join(f"{b.label} {b.score:.2f}" for b in boxes[:4])
    return f"localized {len(boxes)} region(s): {labels}"


# ── VLM backend (Qwen2.5-VL via the reason endpoint) ─────────────────────────

_VLM_INSTRUCTION = (
    "You are a physical-security vision system performing open-vocabulary "
    "localization. Look at the image and find every instance of the described "
    "target. Be strict: only include something you can actually see, not something "
    "merely plausible.\n\n"
    'Target: "{query}"\n\n'
    "Respond with ONLY strict JSON, no prose, no markdown:\n"
    '{{"present": <true|false>, "reason": "<one short sentence>", '
    '"objects": [{{"label": "<short noun>", "confidence": <0..1>, '
    '"box": [x1, y1, x2, y2]}}]}}\n'
    "Coordinates MUST be normalized to the range 0..1 with the origin at the "
    "top-left of the image: x1,y1 is the top-left corner and x2,y2 the "
    "bottom-right. If nothing matches, set present=false and objects=[]."
)


async def _ground_vlm(image: bytes, query: str) -> tuple[list[GroundedBox], str]:
    b64 = base64.b64encode(image).decode()
    payload = {
        "model": _model(),
        "temperature": 0.1,
        "stream": False,
        "max_tokens": 700,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VLM_INSTRUCTION.format(query=query)},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
    }
    async with httpx.AsyncClient(timeout=settings.vlm_timeout) as c:
        r = await c.post(f"{_endpoint()}/chat/completions", json=payload)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    return _parse_json_boxes(text)


def _parse_json_boxes(text: str) -> tuple[list[GroundedBox], str]:
    m = _JSON.search(text or "")
    if not m:
        return [], (text or "")[:160]
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return [], (text or "")[:160]
    reason = str(d.get("reason", ""))[:200]
    if d.get("present") is False:
        return [], reason
    boxes: list[GroundedBox] = []
    for o in d.get("objects", []) or []:
        box = o.get("box") or o.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            coords = tuple(float(v) for v in box)
        except (TypeError, ValueError):
            continue
        # Qwen-VL-style grounding emits 0..1000 normalized coords. If any value is
        # clearly outside 0..1, treat it as /1000 (the same scalar on both axes, so
        # aspect is preserved) then clamp. Dividing by max(coords) distorted the box
        # on non-square frames and stretched the largest coord to exactly 1.0.
        if max(coords) > 1.5:
            coords = tuple(v / 1000.0 for v in coords)
        coords = tuple(min(1.0, max(0.0, v)) for v in coords)
        try:
            score = float(o.get("confidence", o.get("score", 0.6)))
        except (TypeError, ValueError):
            score = 0.6
        boxes.append(GroundedBox(label=str(o.get("label", "object")), score=score, box=coords))  # type: ignore[arg-type]
    return boxes, reason


# ── LocateAnything backend (dedicated grounding server) ──────────────────────


async def _ground_locateanything(image: bytes, query: str) -> tuple[list[GroundedBox], str]:
    b64 = base64.b64encode(image).decode()
    payload = {
        "image_b64": b64,
        "query": query,
        "max_boxes": settings.ground_max_boxes,
        "min_score": settings.ground_min_score,
    }
    async with httpx.AsyncClient(timeout=settings.vlm_timeout) as c:
        r = await c.post(f"{_endpoint()}/ground", json=payload)
        r.raise_for_status()
        d = r.json()
    boxes: list[GroundedBox] = []
    for o in d.get("boxes", []) or []:
        box = o.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            coords = tuple(float(v) for v in box)
            score = float(o.get("score", 0.6))
        except (TypeError, ValueError):
            continue
        boxes.append(GroundedBox(label=str(o.get("label", "object")), score=score, box=coords))  # type: ignore[arg-type]
    return boxes, str(d.get("reason", ""))[:200]


# ── entry point ──────────────────────────────────────────────────────────────


async def ground(image: bytes, query: str) -> GroundingResult:
    """Localize `query` in `image`. Never raises for a missed match — an empty box
    list with match=False is the normal "not present" result. Genuine transport or
    model errors propagate so the caller can log and skip."""
    backend = settings.ground_backend.lower()
    t = time.perf_counter()
    if backend == "locateanything":
        raw, reason = await _ground_locateanything(image, query)
    else:
        raw, reason = await _ground_vlm(image, query)
    boxes = _clean(raw)
    latency_ms = round((time.perf_counter() - t) * 1000, 1)
    return GroundingResult(
        match=len(boxes) > 0,
        reason=reason or _auto_reason(boxes, query),
        boxes=boxes,
        backend=backend,
        latency_ms=latency_ms,
    )
