"""ANPR: automatic number-plate reading.

A YOLO11 plate-detection model (fine-tuned for license plates) locates plate
regions; EasyOCR reads the characters; the read is normalized and quality-gated.
Runs only on vehicle regions, throttled per track, so the extra models do not slow
the main detect/track loop. Plate text is treated as personal data: callers hash it
for watchlist matching and can blur the plate region for privacy-preserving export
(DPDP-style handling).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sentigon_common.plates import normalize_plate, plate_hash

from .config import settings

__all__ = ["PlateRead", "PlateReader", "normalize_plate", "plate_hash"]

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class PlateRead:
    text: str  # normalized plate string
    confidence: float  # OCR confidence 0..1
    bbox: list[float]  # plate box in frame coords [x1,y1,x2,y2]


class PlateReader:
    def __init__(self, device: str = "cuda") -> None:
        from ultralytics import YOLO

        self.model = YOLO(str(_REPO_ROOT / settings.anpr_model))
        self.device = device
        self._lock = threading.Lock()
        # EasyOCR loads its own detector+recognizer; we only use the recognizer on
        # the already-cropped plate, so detail=1 + no paragraph grouping.
        import easyocr

        self.ocr = easyocr.Reader(["en"], gpu=(device != "cpu"))

    def read(self, crop: np.ndarray) -> list[PlateRead]:
        """crop: a BGR vehicle (or full-frame) region. Returns plate reads that pass
        the quality gate."""
        if crop is None or crop.size == 0:
            return []
        with self._lock:
            res = self.model.predict(
                crop, conf=settings.anpr_plate_conf, verbose=False, device=self.device
            )[0]
        reads: list[PlateRead] = []
        if res.boxes is None:
            return reads
        h, w = crop.shape[:2]
        for box in res.boxes:
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            px1, py1 = max(0, int(x1)), max(0, int(y1))
            px2, py2 = min(w, int(x2)), min(h, int(y2))
            if px2 - px1 < 12 or py2 - py1 < 6:
                continue
            plate_img = crop[py1:py2, px1:px2]
            text, conf = self._ocr_plate(plate_img)
            if text and self._plausible(text, conf):
                reads.append(PlateRead(text=text, confidence=round(conf, 3), bbox=[x1, y1, x2, y2]))
        return reads

    def _ocr_plate(self, plate_img: np.ndarray) -> tuple[str, float]:
        with self._lock:
            out = self.ocr.readtext(plate_img, detail=1, paragraph=False)
        if not out:
            return "", 0.0

        def cy(o: object) -> float:
            return sum(pt[1] for pt in o[0]) / 4

        def cx(o: object) -> float:
            return sum(pt[0] for pt in o[0]) / 4

        def height(o: object) -> float:
            ys = [pt[1] for pt in o[0]]
            return max(ys) - min(ys)

        # The plate NUMBER is the largest text on the plate; state names, slogans,
        # dealer frames, months and years are smaller. Keep only fragments whose
        # character height is close to the tallest, dropping the surrounding noise
        # so "MYFLORIDACOM 06 10 137YST sunshine STATES" -> "137YST".
        max_h = max(height(o) for o in out)
        big = [o for o in out if height(o) >= settings.anpr_height_ratio * max_h]
        if not big:
            big = out

        # order the kept fragments: cluster into rows by centre-y (for genuinely
        # stacked plates like "ABF" over "606"), rows top-to-bottom, each row
        # left-to-right.
        row_tol = 0.6 * (sum(height(o) for o in big) / len(big))
        rows: list[list] = []
        for o in sorted(big, key=cy):
            placed = False
            for row in rows:
                if abs(cy(o) - cy(row[0])) <= row_tol:
                    row.append(o)
                    placed = True
                    break
            if not placed:
                rows.append([o])
        ordered = [o for row in rows for o in sorted(row, key=cx)]

        text = normalize_plate("".join(o[1] for o in ordered))
        conf = float(np.mean([o[2] for o in ordered])) if ordered else 0.0
        # If the merged big-text is implausibly long (multiple big words merged),
        # fall back to the single most-confident big fragment that looks like a plate.
        if len(text) > settings.anpr_max_len:
            cands = [(normalize_plate(o[1]), o[2]) for o in big]
            cands = [(t, c) for t, c in cands if settings.anpr_min_len <= len(t) <= settings.anpr_max_len]
            if cands:
                best = max(cands, key=lambda tc: (any(ch.isdigit() for ch in tc[0]), tc[1]))
                return best[0], float(best[1])
        return text, conf

    @staticmethod
    def _plausible(text: str, conf: float) -> bool:
        # real plates: 4..10 alnum chars, at least one digit, decent OCR confidence
        return (
            settings.anpr_min_len <= len(text) <= settings.anpr_max_len
            and any(c.isdigit() for c in text)
            and conf >= settings.anpr_ocr_conf
        )
