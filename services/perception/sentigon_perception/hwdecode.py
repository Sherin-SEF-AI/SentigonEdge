"""Hardware (NVDEC) RTSP decode via a GStreamer subprocess.

The Jetson has dedicated video decoders (NVDEC); decoding H.264 on the CPU with
OpenCV/ffmpeg wastes cores and caps how many cameras one box can run. This spawns a
`gst-launch-1.0` pipeline that decodes on `nvv4l2decoder` and pipes raw BGRx frames
we read as numpy — a drop-in for the `cv2.VideoCapture` read loop, with no GStreamer
Python bindings required (the worker venv is Python 3.12 without system `gi`).

Frames are forced to a fixed W×H by `nvvidconv` (hardware scale), so the byte-exact
frame size is deterministic for the pipe reader. Alpha is dropped in numpy (cheaper
than a CPU `videoconvert` to BGR). If the pipeline can't start, the caller falls back
to CPU decode.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess

import numpy as np
from sentigon_common.logging import get_logger

log = get_logger("perception.hwdecode")


def available() -> bool:
    """True if gst-launch and the NVDEC element are present on this box."""
    if shutil.which("gst-launch-1.0") is None:
        return False
    try:
        out = subprocess.run(
            ["gst-inspect-1.0", "nvv4l2decoder"],
            capture_output=True,
            timeout=5,
        )
        return out.returncode == 0
    except Exception:  # noqa: BLE001
        return False


class GstNvDecCapture:
    """cv2.VideoCapture-compatible NVDEC RTSP reader (read / isOpened / release)."""

    def __init__(
        self,
        rtsp_uri: str,
        width: int = 1280,
        height: int = 720,
        latency_ms: int = 100,
        drop_interval: int = 0,
    ) -> None:
        self.w = int(width)
        self.h = int(height)
        self._frame_bytes = self.w * self.h * 4  # BGRx
        # rtph264depay -> h264parse -> NVDEC -> nvvidconv(scale+format) -> raw BGRx.
        # drop-frame-interval makes the decoder emit only every Nth frame IN HARDWARE,
        # so we convert + pipe only the frames the detector will actually use (the raw
        # BGRx transfer, not the decode, is the real cost for one low-res stream). A
        # leaky queue keeps only the freshest frame if the reader lags, matching cv2
        # CAP_PROP_BUFFERSIZE=1 "latest frame" semantics.
        drop = f"drop-frame-interval={int(drop_interval)} " if drop_interval and drop_interval > 1 else ""
        pipeline = (
            f"rtspsrc location={rtsp_uri} protocols=tcp latency={int(latency_ms)} "
            f"! rtph264depay ! h264parse ! nvv4l2decoder {drop}"
            f"! nvvidconv ! video/x-raw,format=BGRx,width={self.w},height={self.h} "
            "! queue max-size-buffers=1 leaky=downstream ! fdsink fd=1 sync=false"
        )
        self._proc: subprocess.Popen | None = subprocess.Popen(  # noqa: S603
            ["gst-launch-1.0", "-q", *pipeline.split()],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def isOpened(self) -> bool:  # noqa: N802 (cv2 API compatibility)
        return self._proc is not None and self._proc.poll() is None

    def set(self, *_args) -> bool:  # noqa: D401 (cv2 API compatibility no-op)
        return True

    def _read_exact(self, n: int) -> bytes | None:
        assert self._proc is not None and self._proc.stdout is not None
        chunks: list[bytes] = []
        got = 0
        while got < n:
            b = self._proc.stdout.read(n - got)
            if not b:  # EOF: decoder/stream ended
                return None
            chunks.append(b)
            got += len(b)
        return b"".join(chunks)

    def read(self):  # -> tuple[bool, np.ndarray | None]
        if not self.isOpened():
            return False, None
        buf = self._read_exact(self._frame_bytes)
        if buf is None:
            return False, None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 4)
        return True, arr[:, :, :3]  # BGRx -> BGR (drop alpha)

    def release(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                p.kill()
