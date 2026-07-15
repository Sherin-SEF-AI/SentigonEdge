"""Per-camera capture worker.

Runs in its own thread (OpenCV capture is blocking). Reads the RTSP restream,
measures health (fps, jitter, decode errors, reconnects), maintains an in-memory
pre-roll ring buffer, and writes fixed-length MP4 segments. Completed segments are
handed to a callback for upload to MinIO. Reconnects with exponential backoff.
"""

from __future__ import annotations

import os
import statistics
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Force TCP RTSP transport before OpenCV loads ffmpeg.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")

import cv2  # noqa: E402
from sentigon_common.logging import get_logger  # noqa: E402

from .config import IngestSettings  # noqa: E402

log = get_logger("capture")


@dataclass
class SegmentInfo:
    camera_id: uuid.UUID
    path: str
    start_ts: float
    end_ts: float
    frames: int
    width: int
    height: int


@dataclass
class _Stats:
    status: str = "connecting"
    fps: float = 0.0
    jitter_ms: float = 0.0
    decode_errors: int = 0
    reconnects: int = 0
    frames_total: int = 0
    width: int = 0
    height: int = 0
    last_frame_monotonic: float = 0.0


SegmentCallback = Callable[[SegmentInfo], None]


class CameraWorker(threading.Thread):
    def __init__(
        self,
        *,
        camera_id: uuid.UUID,
        name: str,
        rtsp_uri: str,
        settings: IngestSettings,
        on_segment: SegmentCallback,
    ) -> None:
        super().__init__(name=f"capture-{name}", daemon=True)
        self.camera_id = camera_id
        self.name_ = name
        self.rtsp_uri = rtsp_uri
        self.settings = settings
        self.on_segment = on_segment

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stats = _Stats()
        self._frame_intervals: deque[float] = deque(maxlen=60)
        # ring buffer of recently written frames for pre-roll clip extraction
        self._ring: deque[tuple[float, cv2.typing.MatLike]] = deque(
            maxlen=max(1, settings.preroll_seconds * settings.record_fps)
        )
        self._work = Path(settings.work_dir) / str(camera_id)
        self._work.mkdir(parents=True, exist_ok=True)

    # ── public API ────────────────────────────────────────────
    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        with self._lock:
            s = self._stats
            return {
                "status": s.status,
                "fps": round(s.fps, 2),
                "jitter_ms": round(s.jitter_ms, 2),
                "decode_errors": s.decode_errors,
                "reconnects": s.reconnects,
                "frames_total": s.frames_total,
                "resolution": f"{s.width}x{s.height}" if s.width else None,
            }

    def latest_frame(self):
        """Return a copy of the most recent frame (for on-demand snapshots)."""
        with self._lock:
            if self._ring:
                return self._ring[-1][1].copy()
        return None

    def dump_preroll(self, path: str) -> SegmentInfo | None:
        """Write the current ring buffer to an MP4 (pre-event footage)."""
        with self._lock:
            frames = list(self._ring)
            w, h = self._stats.width, self._stats.height
        if not frames or not w:
            return None
        writer = cv2.VideoWriter(
            path, cv2.VideoWriter_fourcc(*"mp4v"), self.settings.record_fps, (w, h)
        )
        for _, frame in frames:
            writer.write(frame)
        writer.release()
        return SegmentInfo(self.camera_id, path, frames[0][0], frames[-1][0], len(frames), w, h)

    # ── thread body ───────────────────────────────────────────
    def run(self) -> None:
        backoff = self.settings.reconnect_base_seconds
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.rtsp_uri, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                self._set_status("offline")
                cap.release()
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self.settings.reconnect_max_seconds)
                self._bump_reconnect()
                continue

            self._set_status("online")
            backoff = self.settings.reconnect_base_seconds
            try:
                self._capture_loop(cap)
            except Exception:  # noqa: BLE001
                log.exception("capture.loop_error", camera=self.name_)
            finally:
                cap.release()

            if not self._stop.is_set():
                self._bump_reconnect()
                self._set_status("connecting")
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self.settings.reconnect_max_seconds)

        self._set_status("offline")
        log.info("capture.stopped", camera=self.name_)

    # ── internals ─────────────────────────────────────────────
    def _capture_loop(self, cap: cv2.VideoCapture) -> None:
        min_interval = 1.0 / self.settings.record_fps
        last_written = 0.0
        writer: cv2.VideoWriter | None = None
        seg_start = 0.0
        seg_frames = 0
        seg_path = ""
        w = h = 0
        last_ok = time.monotonic()

        while not self._stop.is_set():
            ok, frame = cap.read()
            now = time.monotonic()
            wall = time.time()
            if not ok or frame is None:
                with self._lock:
                    self._stats.decode_errors += 1
                if now - last_ok > self.settings.read_timeout_seconds:
                    break  # stream went dead: reconnect
                time.sleep(0.02)
                continue
            last_ok = now

            if w == 0:
                h, w = frame.shape[:2]

            # subsample to record_fps to control size and give segments a stable rate
            if now - last_written < min_interval:
                continue
            interval = now - last_written if last_written else min_interval
            last_written = now

            # open a new segment if needed
            if writer is None or (wall - seg_start) >= self.settings.segment_seconds:
                if writer is not None:
                    writer.release()
                    self._emit_segment(seg_path, seg_start, wall, seg_frames, w, h)
                seg_start = wall
                seg_frames = 0
                seg_path = str(self._work / f"seg_{int(wall)}_{uuid.uuid4().hex[:8]}.mp4")
                writer = cv2.VideoWriter(
                    seg_path, cv2.VideoWriter_fourcc(*"mp4v"), self.settings.record_fps, (w, h)
                )

            writer.write(frame)
            seg_frames += 1
            with self._lock:
                self._ring.append((wall, frame))
            self._update_stats(interval, w, h)

        if writer is not None:
            writer.release()
            if seg_frames > 0:
                self._emit_segment(seg_path, seg_start, time.time(), seg_frames, w, h)

    def _emit_segment(
        self, path: str, start: float, end: float, frames: int, w: int, h: int
    ) -> None:
        if frames <= 0 or not path:
            return
        try:
            self.on_segment(SegmentInfo(self.camera_id, path, start, end, frames, w, h))
        except Exception:  # noqa: BLE001
            log.exception("capture.segment_callback_error", camera=self.name_)

    def _update_stats(self, interval: float, w: int, h: int) -> None:
        with self._lock:
            self._frame_intervals.append(interval)
            self._stats.frames_total += 1
            self._stats.width, self._stats.height = w, h
            self._stats.last_frame_monotonic = time.monotonic()
            if len(self._frame_intervals) >= 3:
                mean = statistics.fmean(self._frame_intervals)
                self._stats.fps = 1.0 / mean if mean > 0 else 0.0
                self._stats.jitter_ms = statistics.pstdev(self._frame_intervals) * 1000.0

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._stats.status = status

    def _bump_reconnect(self) -> None:
        with self._lock:
            self._stats.reconnects += 1
