"""YOLO26 detector + tracker wrapper.

Each camera worker owns its own Detector instance so the tracker state (persistent
track IDs) stays isolated per stream. NMS-free end-to-end model, FP16 on GPU.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

import numpy as np
from ultralytics import YOLO

from .config import settings

_WEAPON_NAMES = {n.lower() for n in settings.weapon_classes}


@dataclass
class Detection:
    track_id: int
    object_class: str
    confidence: float
    bbox: list[float]  # top-left [x, y, w, h] in pixels
    xyxy: list[float]  # [x1, y1, x2, y2] for cropping
    is_weapon: bool = False
    mask: list[list[float]] | None = None  # seg polygon [[x,y],...] normalized 0..1
    attributes: dict = field(default_factory=dict)

    @property
    def centroid(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2] / 2, self.bbox[1] + self.bbox[3] / 2)


class Detector:
    def __init__(self, model_path: str | None = None, device: str = "cuda") -> None:
        self.seg = settings.seg_enabled
        chosen = model_path or (settings.seg_model if self.seg else settings.model)
        self.model = YOLO(chosen)
        self.model_path = chosen
        self.device = 0 if device == "cuda" else "cpu"
        self.names: dict[int, str] = self.model.names
        # Exported engines (TensorRT .engine / ONNX) are already device-bound and reject
        # .to(); only a .pt PyTorch model needs an explicit device move. Device is passed
        # per-call to track() regardless, so the engine still runs on the GPU.
        if str(chosen).endswith(".pt"):
            self.model.to("cuda" if device == "cuda" else "cpu")

        # Warm up: the first inference allocates the TensorRT execution context + CUDA
        # buffers (a one-off multi-hundred-ms spike). Do it at load so the first real
        # frames — and every model swap / reconnect — run at full speed instead of
        # stalling or tripping frame-timeout logic.
        with contextlib.suppress(Exception):
            self.model.predict(
                np.zeros((settings.imgsz, settings.imgsz, 3), dtype=np.uint8),
                device=self.device,
                half=settings.half,
                imgsz=settings.imgsz,
                verbose=False,
            )

    def track(self, frame: np.ndarray) -> list[Detection]:
        result = self.model.track(
            frame,
            persist=True,
            tracker=settings.tracker,
            device=self.device,
            half=settings.half,  # FP16 on GPU (was configured but never passed → ran FP32)
            conf=settings.conf,
            iou=settings.iou,
            imgsz=settings.imgsz,
            classes=settings.classes,
            verbose=False,
        )[0]
        boxes = result.boxes
        out: list[Detection] = []
        if boxes is None or len(boxes) == 0:
            return out
        h, w = frame.shape[:2]
        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        conf = boxes.conf.cpu().numpy()
        ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None
        masks = result.masks.xy if getattr(result, "masks", None) is not None else None
        for i in range(len(boxes)):
            x1, y1, x2, y2 = (float(v) for v in xyxy[i])
            name = self.names.get(int(cls[i]), str(cls[i]))
            poly = None
            if masks is not None and i < len(masks) and len(masks[i]) >= 3:
                pts = masks[i]
                step = max(1, len(pts) // settings.seg_poly_points)
                poly = [[round(float(p[0]) / w, 4), round(float(p[1]) / h, 4)] for p in pts[::step]]
            out.append(
                Detection(
                    track_id=int(ids[i]) if ids is not None else -1,
                    object_class=name,
                    confidence=float(conf[i]),
                    bbox=[x1, y1, x2 - x1, y2 - y1],
                    xyxy=[x1, y1, x2, y2],
                    is_weapon=name.lower() in _WEAPON_NAMES,
                    mask=poly,
                )
            )
        return out
