"""Vehicle appearance attributes computed from the crop (real pixels, no model
needed for colour). Coarse make/model would consume a Forge classifier bundle;
type comes from the detector class. Colour is a real HSV analysis of the vehicle
region, published as a searchable attribute on tracked vehicles."""

from __future__ import annotations

import cv2
import numpy as np

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw"}

# HSV hue ranges (OpenCV hue 0-179) -> colour name
_HUE_NAMES = [
    (0, 10, "red"), (10, 22, "orange"), (22, 33, "yellow"), (33, 85, "green"),
    (85, 100, "cyan"), (100, 130, "blue"), (130, 160, "purple"), (160, 180, "red"),
]


def dominant_color(crop: np.ndarray) -> str:
    """Return a coarse colour name for a vehicle crop. Uses the centre region to
    avoid background, splits chromatic vs achromatic by saturation."""
    if crop is None or crop.size == 0:
        return "unknown"
    h, w = crop.shape[:2]
    # centre 60% avoids road/background at the edges
    cy0, cy1 = int(h * 0.2), int(h * 0.8)
    cx0, cx1 = int(w * 0.2), int(w * 0.8)
    region = crop[cy0:cy1, cx0:cx1]
    if region.size == 0:
        region = crop
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    chromatic = ss > 60
    if chromatic.mean() < 0.15:
        # achromatic: decide by brightness
        mv = float(vv.mean())
        if mv < 60:
            return "black"
        if mv > 175:
            return "white"
        return "gray"
    hue = float(np.median(hh[chromatic]))
    for lo, hi, name in _HUE_NAMES:
        if lo <= hue < hi:
            return name
    return "unknown"
