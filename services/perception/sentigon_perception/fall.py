"""Pose-based fall / slip detection.

A YOLO pose model estimates body keypoints; a person is judged FALLEN when their
posture is horizontal, from two robust signals: the keypoint bounding box is wider
than it is tall (lying down), and/or the torso vector (shoulders->hips) is more
horizontal than upright. Validated on real fall footage: upright/walking reads
aspect ~0.4 / torso <25deg, a fall reads aspect >1.1.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np

from .config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


class PoseFallDetector:
    def __init__(self, device: str = "cuda") -> None:
        from ultralytics import YOLO

        self.model = YOLO(str(_REPO_ROOT / settings.fall_pose_model))
        self.device = device
        self._lock = threading.Lock()

    def fallen_boxes(self, frame: np.ndarray) -> list[list[float]]:
        """Return bounding boxes [x1,y1,x2,y2] of people judged to be fallen."""
        if frame is None or frame.size == 0:
            return []
        with self._lock:
            res = self.model.predict(
                frame, conf=settings.fall_pose_conf, verbose=False, device=self.device
            )[0]
        if res.keypoints is None or res.boxes is None:
            return []
        out: list[list[float]] = []
        kdata = res.keypoints.data.tolist()
        boxes = res.boxes.xyxy.tolist()
        for kp, box in zip(kdata, boxes, strict=False):
            if self._is_fallen(kp):
                out.append([float(v) for v in box])
        return out

    @staticmethod
    def _is_fallen(kp: list) -> bool:
        pts = [(x, y) for x, y, c in kp if c > 0.3]
        if len(pts) < 5:
            return False
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        aspect = w / (h + 1e-6)  # >1 = wider than tall = lying
        sh = ((kp[5][0] + kp[6][0]) / 2, (kp[5][1] + kp[6][1]) / 2)
        hip = ((kp[11][0] + kp[12][0]) / 2, (kp[11][1] + kp[12][1]) / 2)
        torso = math.degrees(math.atan2(abs(hip[0] - sh[0]), abs(hip[1] - sh[1]) + 1e-6))
        return aspect > settings.fall_aspect_min or torso > settings.fall_torso_angle
