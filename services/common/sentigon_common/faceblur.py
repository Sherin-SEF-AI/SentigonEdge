"""Face detection + blur for privacy-preserving export.

A YOLO face model locates faces; each face region is Gaussian-blurred in place so
exported snapshots/clips retain the scene and behavior evidence while obscuring
identities (GDPR/DPDP minimization). The detector is loaded once, lazily, on first
use. Operates on BGR numpy frames (OpenCV) so it composes with the recording path.
"""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from .config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from ultralytics import YOLO

                _model = YOLO(str(_REPO_ROOT / settings.face_model))
    return _model


def blur_faces(frame: np.ndarray, conf: float | None = None, expand: float = 0.15) -> tuple[np.ndarray, int]:
    """Blur every detected face in a BGR frame. Returns (frame, faces_blurred).

    The frame is modified in place and also returned. `expand` grows each face box
    outward so hairline/jaw are covered, not just the tight face crop.
    """
    if frame is None or frame.size == 0:
        return frame, 0
    model = _get_model()
    conf = settings.face_conf if conf is None else conf
    res = model.predict(frame, conf=conf, verbose=False, device=settings.face_device)[0]
    if res.boxes is None:
        return frame, 0
    h, w = frame.shape[:2]
    n = 0
    for box in res.boxes:
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        bw, bh = x2 - x1, y2 - y1
        x1 = max(0, int(x1 - bw * expand))
        y1 = max(0, int(y1 - bh * expand))
        x2 = min(w, int(x2 + bw * expand))
        y2 = min(h, int(y2 + bh * expand))
        if x2 <= x1 or y2 <= y1:
            continue
        roi = frame[y1:y2, x1:x2]
        # kernel scaled to face size so small and large faces are equally obscured
        k = max(15, (min(x2 - x1, y2 - y1) // 2) | 1)
        frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)
        n += 1
    return frame, n
