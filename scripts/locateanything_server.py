#!/usr/bin/env python3
"""LocateAnything grounding server (OPTIONAL, opt-in) — the "locateanything" backend
for the reason tier's open-vocabulary grounder.

It loads NVIDIA's LocateAnything-3B (a Qwen2.5 + Moon-ViT grounding model) with
HF Transformers and exposes one endpoint:

    POST /ground   {"image_b64": "...", "query": "a ladder against the wall",
                    "max_boxes": 12, "min_score": 0.3}
      -> {"boxes": [{"label": "...", "score": 0.71, "box": [x1,y1,x2,y2]}],
          "reason": "...", "backend": "locateanything", "latency_ms": 1234.5}

Boxes are normalized 0..1 with a top-left origin, matching the "vlm" backend, so the
console overlays them identically.

    uv run python scripts/locateanything_server.py        # listens on :8055

──────────────────────────────────────────────────────────────────────────────
LICENSE — READ FIRST. nvidia/LocateAnything-3B is released under the NVIDIA License
for **non-commercial use** (academic / non-profit research only; commercial use not
permitted). Running this server pulls those weights. Do NOT use it for anything you
ship or deploy commercially — use the default REASON_SVC_GROUND_BACKEND=vlm path,
which reuses the reason VLM you already run under a license you can ship.

CAVEATS on a Jetson AGX Orin. The model card benchmarks on an H100; the Orin is
Ampere-class and not on NVIDIA's listed support matrix, so treat this as on-demand /
interval grounding, not per-frame. It runs via Transformers (BF16), shares the GPU
and 64 GB unified memory with the TensorRT perception engine and the Ollama VLM, and
will be much slower than the published throughput. Benchmark before you rely on it.

NOTE. This is a best-effort adapter built to the standard HF vision-language pattern
(AutoProcessor + chat template + JSON-box instruction). If the released model expects
a different processor call or emits boxes in its own coordinate format, reconcile the
two clearly-marked sections below with the model's own example code on Hugging Face.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = os.environ.get("LOCATEANYTHING_MODEL", "nvidia/LocateAnything-3B")
PORT = int(os.environ.get("LOCATEANYTHING_PORT", "8055"))
MAX_NEW_TOKENS = int(os.environ.get("LOCATEANYTHING_MAX_NEW_TOKENS", "768"))

_JSON = re.compile(r"\{.*\}", re.DOTALL)
_model = None
_processor = None

_INSTRUCTION = (
    "You are an open-vocabulary localization system. Find every instance of the "
    "target in the image. Respond with ONLY strict JSON, no prose:\n"
    '{{"present": <true|false>, "reason": "<one short sentence>", '
    '"objects": [{{"label": "<short noun>", "confidence": <0..1>, '
    '"box": [x1, y1, x2, y2]}}]}}\n'
    "Coordinates MUST be normalized to 0..1 with the origin at the top-left "
    "(x1,y1 top-left corner; x2,y2 bottom-right). Target: \"{query}\""
)


def _load():
    """Lazily load the model+processor (kept warm across requests)."""
    global _model, _processor
    if _model is not None:
        return
    import torch  # noqa: PLC0415
    from transformers import AutoModelForImageTextToText, AutoProcessor  # noqa: PLC0415

    print(f"loading {MODEL_ID} (bf16, trust_remote_code) ...", flush=True)
    _processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    _model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    _model.eval()
    print("model ready.", flush=True)


def _infer(image_bytes: bytes, query: str) -> str:
    """Run one grounding pass, returning the raw model text. The two lines marked
    ADAPTER are the ones to reconcile with the released model's example if needed."""
    import torch  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": img},
                {"type": "text", "text": _INSTRUCTION.format(query=query)},
            ],
        }
    ]
    # ADAPTER (in): build model inputs from the chat template + image.
    inputs = _processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    ).to(_model.device)
    with torch.no_grad():
        generated = _model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
    # ADAPTER (out): strip the prompt tokens and decode only the completion.
    trimmed = generated[:, inputs["input_ids"].shape[1]:]
    return _processor.batch_decode(trimmed, skip_special_tokens=True)[0]


def _parse(text: str, min_score: float, max_boxes: int) -> dict:
    m = _JSON.search(text or "")
    if not m:
        return {"boxes": [], "reason": (text or "")[:160]}
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {"boxes": [], "reason": (text or "")[:160]}
    boxes = []
    for o in d.get("objects", []) or []:
        box = o.get("box") or o.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            coords = [float(v) for v in box]
            score = float(o.get("confidence", o.get("score", 0.6)))
        except (TypeError, ValueError):
            continue
        if max(coords) > 1.5:  # model returned scaled/pixel coords
            s = max(coords)
            coords = [v / s for v in coords]
        x1, x2 = sorted((coords[0], coords[2]))
        y1, y2 = sorted((coords[1], coords[3]))
        if x2 - x1 < 0.005 or y2 - y1 < 0.005 or score < min_score:
            continue
        boxes.append({"label": str(o.get("label", "object"))[:64], "score": round(score, 3),
                      "box": [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]})
    boxes.sort(key=lambda b: b["score"], reverse=True)
    return {"boxes": boxes[:max_boxes], "reason": str(d.get("reason", ""))[:200]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002
        pass

    def do_GET(self) -> None:
        self._json({"ok": _model is not None, "model": MODEL_ID})

    def do_POST(self) -> None:
        if not self.path.startswith("/ground"):
            self._json({"error": "not found"}, code=404)
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            self._json({"error": "bad json"}, code=400)
            return
        query = str(req.get("query", "")).strip()
        image_b64 = req.get("image_b64")
        if not query or not image_b64:
            self._json({"error": "query and image_b64 required"}, code=400)
            return
        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:  # noqa: BLE001
            self._json({"error": "image_b64 not decodable"}, code=400)
            return
        t = time.perf_counter()
        try:
            _load()
            text = _infer(image_bytes, query)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"inference failed: {exc}"}, code=500)
            return
        out = _parse(text, float(req.get("min_score", 0.3)), int(req.get("max_boxes", 12)))
        out.update(backend="locateanything", latency_ms=round((time.perf_counter() - t) * 1000, 1))
        self._json(out)

    def _json(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"LocateAnything grounding server on :{PORT} (model={MODEL_ID})", flush=True)
    print("NON-COMMERCIAL research license — see file header.", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
