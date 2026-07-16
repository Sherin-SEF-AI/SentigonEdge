"""Per-camera perception worker (thread).

Reads the RTSP restream, runs YOLO26 detect+track, computes zone hits, embeds
person crops for ReID, and emits object metadata + embeddings to Kafka. Keeps the
latest detections in memory for the console overlay WebSocket. Reconnects with
backoff. Pixels never leave this process.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")

import cv2  # noqa: E402
import numpy as np
from sentigon_common.logging import get_logger  # noqa: E402
from sentigon_common.schemas.bus import (  # noqa: E402
    DetectedObject,
    EmbeddingMsg,
    ObjectDetectionMsg,
    Topics,
)

from .attributes import VEHICLE_CLASSES, dominant_color  # noqa: E402
from .config import settings  # noqa: E402
from .detector import Detector  # noqa: E402
from .embedder import Embedder  # noqa: E402
from .reid_index import ReidIndex  # noqa: E402
from .zones import ZoneIndex  # noqa: E402

log = get_logger("perception.worker")

PublishFn = Callable[[str, object, str | None], None]
LatestFn = Callable[[uuid.UUID, dict], None]


class PerceptionWorker(threading.Thread):
    def __init__(
        self,
        *,
        camera_id: uuid.UUID,
        name: str,
        rtsp_uri: str,
        device: str,
        embedder: Embedder,
        reid: ReidIndex,
        zones: ZoneIndex,
        publish: PublishFn,
        set_latest: LatestFn,
        plate_reader: object | None = None,
        fall_detector: object | None = None,
    ) -> None:
        super().__init__(name=f"perc-{name}", daemon=True)
        self.camera_id = camera_id
        self.name_ = name
        self.rtsp_uri = rtsp_uri
        self.device = device
        self.embedder = embedder
        self.reid = reid
        self.zones = zones
        self.publish = publish
        self.set_latest = set_latest
        self.plate_reader = plate_reader
        self._plate_votes: dict[int, Counter] = {}  # track_id -> plate-text vote counts
        self._plate_last: dict[int, int] = {}  # track_id -> last frame we OCR'd
        self.fall_detector = fall_detector
        self._fall_since: float | None = None
        self._fall_last_fired = 0.0

        self._stop = threading.Event()
        self.detector: Detector | None = None
        self._seq = 0
        self._frames = 0
        self._skipped = 0
        self._tamper_since: float | None = None
        self._tamper_last = 0.0
        self.model_name = settings.model
        self._last_embed: dict[int, int] = {}
        self.stats: dict = {"status": "loading", "fps": 0.0, "objects": 0, "inference_ms": 0.0}

    def stop(self) -> None:
        self._stop.set()

    def swap_detector(self, model_path: str) -> str:
        """Hot-swap the detection model in place. The new model is loaded while the
        capture loop keeps running on the old one, then the reference is swapped
        atomically, so the stream is never dropped."""
        new = Detector(model_path, "cuda" if self.device == 0 or self.device == "cuda" else "cpu")
        old_model = getattr(self.detector, "model_path", settings.model)
        self.detector = new
        self.model_name = model_path
        log.info("perception.model_swapped", camera=self.name_, from_model=old_model, to_model=model_path)
        return model_path

    def run(self) -> None:
        try:
            # pass None so the Detector selects the seg model when seg is enabled
            self.detector = Detector(None, self.device)
        except Exception:
            log.exception("perception.model_load_failed", camera=self.name_)
            self.stats["status"] = "error"
            return
        log.info("perception.worker_ready", camera=self.name_, model=settings.model)
        backoff = 1.0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.rtsp_uri, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                self.stats["status"] = "offline"
                cap.release()
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 15.0)
                continue
            self.stats["status"] = "online"
            connected_at = time.monotonic()
            try:
                self._loop(cap)
            except Exception:
                log.exception("perception.loop_error", camera=self.name_)
            finally:
                cap.release()
            # Reset backoff only if the stream stayed up a while; a source that connects
            # then immediately drops now backs off exponentially instead of hot-looping
            # a reconnect + model re-init every second.
            backoff = 1.0 if (time.monotonic() - connected_at) > 30.0 else min(backoff * 2, 15.0)
            if self._stop.is_set() or self._stop.wait(backoff):
                break
        self.stats["status"] = "offline"
        log.info("perception.worker_stopped", camera=self.name_)

    def _loop(self, cap: cv2.VideoCapture) -> None:
        assert self.detector is not None
        min_interval = 1.0 / settings.infer_fps
        last = 0.0
        fps_t = time.monotonic()
        fps_n = 0
        last_ok = time.monotonic()

        while not self._stop.is_set():
            ok, frame = cap.read()
            now = time.monotonic()
            if not ok or frame is None:
                if now - last_ok > 8.0:
                    break
                time.sleep(0.02)
                continue
            last_ok = now
            if now - last < min_interval:
                # backpressure: source delivers faster than infer_fps; drop this
                # frame (BUFFERSIZE=1 already keeps only the latest) and record it
                self._skipped += 1
                continue
            last = now

            h, w = frame.shape[:2]
            t0 = time.perf_counter()
            dets = self.detector.track(frame)
            infer_ms = (time.perf_counter() - t0) * 1000
            self._frames += 1
            self._seq += 1

            objs: list[DetectedObject] = []
            crops: list = []
            crop_tracks: list[int] = []
            crop_classes: list[str] = []
            crop_colors: list[str] = []
            for d in dets:
                cx, cy = d.centroid
                # mask-precise intrusion when a segmentation mask is available,
                # else fall back to the bbox-centroid test
                if d.mask:
                    zone_hits = self.zones.hits_mask(self.camera_id, d.mask)
                else:
                    zone_hits = self.zones.hits(self.camera_id, cx / w, cy / h)
                attrs = dict(d.attributes)
                if d.is_weapon:
                    attrs["weapon"] = True
                # vehicle appearance attributes: type (detector class) + real colour
                if d.object_class in VEHICLE_CLASSES:
                    x1, y1, x2, y2 = (int(v) for v in d.xyxy)
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w, x2), min(h, y2)
                    if x2 > x1 and y2 > y1:
                        attrs["vehicle_type"] = d.object_class
                        attrs["color"] = dominant_color(frame[y1:y2, x1:x2])
                        if self.plate_reader is not None and d.track_id >= 0:
                            self._read_plate(frame[y1:y2, x1:x2], d.track_id)
                            plate = self._best_plate(d.track_id)
                            if plate:
                                attrs["plate_hash"] = plate["hash"]
                                attrs["plate_conf"] = plate["conf"]
                                if settings.anpr_store_raw:
                                    attrs["plate"] = plate["text"]
                objs.append(
                    DetectedObject(
                        track_id=d.track_id,
                        object_class=d.object_class,
                        confidence=round(d.confidence, 3),
                        bbox=[round(v, 1) for v in d.bbox],
                        zone_hits=zone_hits,
                        mask=d.mask,
                        attributes=attrs,
                    )
                )
                if (
                    settings.embed_enabled
                    and d.object_class in settings.embed_classes
                    and d.track_id >= 0
                    and self._frames - self._last_embed.get(d.track_id, -(10**9))
                    >= settings.embed_every_n
                ):
                    x1, y1, x2, y2 = (int(v) for v in d.xyxy)
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w, x2), min(h, y2)
                    if x2 > x1 and y2 > y1:
                        crops.append(frame[y1:y2, x1:x2])
                        crop_tracks.append(d.track_id)
                        crop_classes.append(d.object_class)
                        crop_colors.append(attrs.get("color", ""))

            frame_ts = datetime.now(UTC)
            msg = ObjectDetectionMsg(
                producer="perception",
                camera_id=self.camera_id,
                seq=self._seq,
                frame_ts=frame_ts,
                frame_width=w,
                frame_height=h,
                objects=objs,
                inference_ms=round(infer_ms, 1),
            )
            self.set_latest(self.camera_id, msg.model_dump(mode="json"))
            self.publish(Topics.PERCEPTION_OBJECTS, msg, str(self.camera_id))

            if crops:
                self._embed(crops, crop_tracks, crop_classes, frame_ts, crop_colors)

            if self.fall_detector is not None and self._frames % settings.fall_every_n == 0:
                self._check_fall(frame, now)

            fps_n += 1
            if now - fps_t >= 1.0:
                self.stats.update(
                    status="online",
                    fps=round(fps_n / (now - fps_t), 2),
                    objects=len(objs),
                    inference_ms=round(infer_ms, 1),
                    frames_skipped=self._skipped,
                )
                self._check_tamper(frame, now)
                fps_t = now
                fps_n = 0

    def _read_plate(self, vehicle_crop: np.ndarray, track_id: int) -> None:
        """OCR the vehicle crop for a plate (throttled per track) and accumulate a
        temporal vote. Voting across frames beats any single noisy read."""
        if self._frames - self._plate_last.get(track_id, -(10**9)) < settings.anpr_every_n:
            return
        self._plate_last[track_id] = self._frames
        try:
            reads = self.plate_reader.read(vehicle_crop)  # type: ignore[union-attr]
        except Exception:
            log.exception("perception.anpr_read_failed", camera=self.name_)
            return
        votes = self._plate_votes.setdefault(track_id, Counter())
        for r in reads:
            # weight the vote by OCR confidence (rounded) so clean reads dominate
            votes[r.text] += max(1, int(r.confidence * 10))

    def _best_plate(self, track_id: int) -> dict | None:
        votes = self._plate_votes.get(track_id)
        if not votes:
            return None
        from .anpr import plate_hash

        text, weight = votes.most_common(1)[0]
        if weight < settings.anpr_min_votes:
            return None
        total = sum(votes.values())
        return {"text": text, "hash": plate_hash(text), "conf": round(weight / total, 3)}

    def _check_fall(self, frame: np.ndarray, now: float) -> None:
        """Pose-based fall detection: when a fallen posture persists past the sustain
        window, report a fall to the context engine (which fires Person Fall)."""
        try:
            boxes = self.fall_detector.fallen_boxes(frame)  # type: ignore[union-attr]
        except Exception:
            log.exception("perception.fall_detect_failed", camera=self.name_)
            return
        if boxes:
            if self._fall_since is None:
                self._fall_since = now
            elif (
                now - self._fall_since >= settings.fall_sustain_s
                and now - self._fall_last_fired >= settings.fall_cooldown_s
            ):
                self._report_fall(boxes[0])
                self._fall_last_fired = now
        else:
            self._fall_since = None

    def _report_fall(self, box: list) -> None:
        import json
        import urllib.request

        from sentigon_common.config import settings as common

        try:
            req = urllib.request.Request(
                f"{settings.context_url}/fall-event",
                data=json.dumps({"camera_id": str(self.camera_id), "bbox": box}).encode(),
                headers={"Content-Type": "application/json", "X-Service-Token": common.service_token},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=4)
            log.warning("perception.fall_detected", camera=self.name_)
        except Exception:  # noqa: BLE001
            log.exception("perception.fall_report_failed", camera=self.name_)

    def _check_tamper(self, frame: np.ndarray, now: float) -> None:
        """Detect a covered (dark), defocused/sprayed (low edge energy) camera and
        raise a tamper incident when the condition is sustained."""
        small = cv2.cvtColor(cv2.resize(frame, (160, 120)), cv2.COLOR_BGR2GRAY)
        brightness = float(small.mean())
        blur_var = float(cv2.Laplacian(small, cv2.CV_64F).var())
        dark = brightness < settings.tamper_dark_below
        defocus = blur_var < settings.tamper_blur_below
        if dark or defocus:
            if self._tamper_since is None:
                self._tamper_since = now
            elif (
                now - self._tamper_since >= settings.tamper_sustain_seconds
                and now - self._tamper_last >= settings.tamper_cooldown_seconds
            ):
                kind = "blackout" if dark else "defocus"
                metric = brightness if dark else blur_var
                self._report_tamper(kind, metric)
                self._tamper_last = now
        else:
            self._tamper_since = None

    def _report_tamper(self, kind: str, metric: float) -> None:
        import json
        import urllib.request

        from sentigon_common.config import settings as common

        try:
            req = urllib.request.Request(
                f"{settings.api_url}/system/camera-tamper",
                data=json.dumps({"camera_id": str(self.camera_id), "kind": kind, "metric": metric}).encode(),
                headers={"Content-Type": "application/json", "X-Service-Token": common.service_token},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=4)
            log.warning("perception.camera_tamper", camera=self.name_, kind=kind, metric=round(metric, 1))
        except Exception:  # noqa: BLE001
            log.exception("perception.tamper_report_failed", camera=self.name_)

    def _embed(
        self, crops: list, tracks: list[int], classes: list[str], ts: datetime,
        colors: list[str] | None = None,
    ) -> None:
        try:
            vecs = self.embedder.embed(crops)
            colors = colors or [""] * len(tracks)
            payloads = [
                {
                    "camera_id": str(self.camera_id),
                    "track_id": t,
                    "object_class": c,
                    "ts": ts.isoformat(),
                    **({"color": col} if col else {}),
                }
                for t, c, col in zip(tracks, classes, colors, strict=False)
            ]
            self.reid.upsert(vecs, payloads)
            for t, c, vec in zip(tracks, classes, vecs, strict=False):
                self._last_embed[t] = self._frames
                self.publish(
                    Topics.PERCEPTION_EMBEDDINGS,
                    EmbeddingMsg(
                        producer="perception",
                        camera_id=self.camera_id,
                        track_id=t,
                        object_class=c,
                        embedding=vec,
                        model=settings.embed_backbone,
                        frame_ts=ts,
                    ),
                    f"{self.camera_id}:{t}",
                )
        except Exception:
            log.exception("perception.embed_error", camera=self.name_)
