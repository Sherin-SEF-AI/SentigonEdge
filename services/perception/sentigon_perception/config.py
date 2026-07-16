"""Perception settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class PerceptionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="PERCEPTION_",
    )

    device: str = "auto"  # auto | cuda | cpu
    model: str = "yolo26m.pt"  # YOLO26 (NMS-free, end-to-end); yolo26x for max accuracy
    # Instance segmentation: when enabled, the detector runs a seg model so tracked
    # objects carry pixel masks (polygons) on the bus, enabling mask-precise zone
    # intrusion and mask-driven privacy blur. Heavier than detection; opt-in.
    seg_enabled: bool = False
    seg_model: str = "yolo11m-seg.pt"
    seg_poly_points: int = 24  # downsample mask polygons to keep the bus message small
    weapon_model: str = ""  # optional secondary GIE (fine-tuned firearm head)
    conf: float = 0.35
    iou: float = 0.5
    infer_fps: float = 8.0  # frames per second sent to the detector
    # Camera tamper / blindness detection thresholds.
    api_url: str = "http://localhost:8010"
    context_url: str = "http://localhost:8040"
    tamper_dark_below: float = 16.0     # mean brightness (0-255): covered / blacked out
    tamper_blur_below: float = 12.0     # Laplacian variance: defocused / sprayed
    tamper_sustain_seconds: float = 5.0
    tamper_cooldown_seconds: float = 120.0
    imgsz: int = 640
    tracker: str = "bytetrack.yaml"
    half: bool = True  # FP16 on GPU

    # security-relevant COCO classes (person, vehicles, bags, knife)
    classes: list[int] = [0, 1, 2, 3, 5, 7, 24, 26, 28, 43]
    weapon_classes: list[str] = ["knife", "gun", "rifle", "pistol", "weapon"]

    # ReID appearance embeddings
    embed_enabled: bool = True
    embed_backbone: str = "osnet_ain_x1_0"  # ReID-trained (MSMT17); "resnet50" = generic fallback
    embed_weights: str = "models/reid/osnet_ain_x1_0_msmt17.pth"
    embed_arch: str = "models/reid/osnet_ain.py"
    embed_every_n: int = 15  # embed a given track once per N processed frames

    # ANPR (number-plate reading)
    anpr_enabled: bool = False  # off by default; needs readable-plate footage
    anpr_model: str = "models/anpr/plate_yolo11m.pt"
    anpr_plate_conf: float = 0.35  # plate-detection confidence
    anpr_ocr_conf: float = 0.30  # min mean OCR confidence to accept a read
    anpr_min_len: int = 4
    anpr_max_len: int = 10
    anpr_every_n: int = 8  # read a vehicle track's plate once per N frames
    anpr_height_ratio: float = 0.55  # keep OCR fragments >= this * tallest (drops state/slogan noise)
    # pose-based fall / slip detection (per-camera via meta.fall_detection)
    fall_pose_model: str = "models/pose/yolo11n-pose.pt"
    fall_pose_conf: float = 0.25
    fall_aspect_min: float = 1.1  # keypoint bbox wider-than-tall ratio = lying
    fall_torso_angle: float = 55.0  # torso degrees from vertical = horizontal
    fall_every_n: int = 3  # run the pose model every N processed frames on a fall camera
    fall_sustain_s: float = 1.0  # fallen must persist this long before firing
    fall_cooldown_s: float = 30.0

    anpr_min_votes: int = 3  # min accumulated vote weight before a plate is emitted
    anpr_store_raw: bool = False  # DPDP: default off — publish only the HMAC hash, never raw text
    # (plate hashing salt lives in sentigon_common.config so api + perception share it)
    # person + vehicle appearance ReID (vehicle re-identification is first-class)
    embed_classes: list[str] = ["person", "car", "truck", "bus", "motorcycle", "bicycle"]
    reid_collection: str = "reid"

    # websocket overlay feed
    ws_hz: float = 12.0


@lru_cache
def get_perception_settings() -> PerceptionSettings:
    return PerceptionSettings()


settings = get_perception_settings()
