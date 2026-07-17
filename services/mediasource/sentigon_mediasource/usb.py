"""USB / v4l2 camera device discovery.

Enumerates `/dev/video*` capture devices, reads their card name and the pixel
formats/resolutions they advertise (via ffmpeg -list_formats), and suggests a
sensible default mode. Pure host introspection — no device is held open. Mirrors
scripts/add_usb_camera.py so the console scan and the CLI agree.
"""
from __future__ import annotations

import glob
import re
import subprocess
from pathlib import Path

from .config import settings


def _card_name(device: str) -> str:
    """Human-readable name from sysfs, e.g. 'Logitech BRIO'."""
    base = Path(device).name  # video0
    name_file = Path("/sys/class/video4linux") / base / "name"
    try:
        return name_file.read_text().strip() or base
    except OSError:
        return base


def _probe_modes(device: str) -> list[dict]:
    """[(pixel_format, WxH)] the device advertises, via ffmpeg -list_formats."""
    try:
        out = subprocess.run(
            [settings.ffmpeg_path, "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", device],
            capture_output=True,
            text=True,
            timeout=settings.probe_timeout,
        )
    except Exception:  # noqa: BLE001
        return []
    modes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in out.stderr.splitlines():
        # e.g. "[video4linux2 @ ..] Raw: yuyv422: ... : 640x480 1280x720"
        m = re.search(r"\]\s+\w+\s*:\s*(\w+)\s*:.*?:\s*(.+)$", line)
        if not m:
            continue
        fmt = m.group(1)
        for size in re.findall(r"\d+x\d+", m.group(2)):
            key = (fmt, size)
            if key not in seen:
                seen.add(key)
                modes.append({"format": fmt, "resolution": size})
    return modes


def _area(m: dict) -> int:
    w, h = m["resolution"].split("x")
    return int(w) * int(h)


def _best_mode(modes: list[dict]) -> dict:
    """Pick a mode that actually runs at full fps over USB. MJPEG is compressed and
    fits the bus at 30fps; RAW formats (yuyv/uyvy422) are bandwidth-limited to a few
    fps at high resolution — capturing raw 1920x1536 is why a 30fps camera crawled at
    2fps. So: strongly prefer MJPEG (favouring 720p, capped at 1080p), and only fall
    back to a *modest* raw resolution if the device offers no MJPEG."""
    if not modes:
        return {"format": "", "resolution": "1280x720"}
    mjpeg = [m for m in modes if m["format"].lower() in ("mjpeg", "mjpg")]
    if mjpeg:
        for m in mjpeg:
            if m["resolution"] == "1280x720":
                return m
        capped = [m for m in mjpeg if _area(m) <= 1920 * 1080] or mjpeg
        return max(capped, key=_area)
    raw_capped = [m for m in modes if _area(m) <= 1280 * 720] or modes
    return max(raw_capped, key=_area)


def scan_devices() -> list[dict]:
    """Enumerate plugged-in v4l2 capture devices with their modes + a suggestion.

    Non-capture nodes (e.g. a camera's metadata `/dev/videoN`) advertise no
    formats; they are returned with capture=False so the UI can grey them out."""
    devices: list[dict] = []
    for device in sorted(glob.glob("/dev/video*"), key=lambda d: int(re.search(r"(\d+)$", d).group(1)) if re.search(r"(\d+)$", d) else 0):
        modes = _probe_modes(device)
        n = re.search(r"(\d+)$", device)
        devices.append(
            {
                "device": device,
                "index": int(n.group(1)) if n else 0,
                "name": _card_name(device),
                "capture": bool(modes),
                "modes": modes,
                "suggested": _best_mode(modes),
            }
        )
    return devices
