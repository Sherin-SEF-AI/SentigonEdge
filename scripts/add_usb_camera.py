#!/usr/bin/env python3
"""Detect and onboard a physical USB (v4l2) camera into Sentigon.

    # list what is plugged in
    uv run python scripts/add_usb_camera.py --list

    # onboard /dev/video0 as a named camera + zone (auto-detects a supported mode)
    uv run python scripts/add_usb_camera.py --device /dev/video0 --name "Front Desk"

It appends a first-class `type: usb` source to configs/media_sources.yaml and
restarts the media-source service, which publishes the real camera to MediaMTX
as RTSP and onboards it (Camera + Zone) through the same real API path the file
sources use. Everything downstream (YOLO26 inference, tracking, events, VLM) is
identical; only the pixels now come from real camera hardware.
"""

from __future__ import annotations

import argparse
import glob
import re
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "media_sources.yaml"
FFMPEG = str(REPO / "tools" / "ffmpeg")


def list_devices() -> list[str]:
    return sorted(glob.glob("/dev/video*"))


def probe_modes(device: str) -> list[tuple[str, str, str]]:
    """Return [(pixel_format, WxH, fps_csv)] the device advertises, via ffmpeg."""
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", device],
        capture_output=True,
        text=True,
    )
    modes: list[tuple[str, str, str]] = []
    for line in out.stderr.splitlines():
        # e.g. "[video4linux2] Raw: yuyv422: ... : 640x480 1280x720"
        m = re.search(r"\]\s+\w+\s*:\s*(\w+)\s*:.*?:\s*(.+)$", line)
        if m:
            fmt = m.group(1)
            for size in re.findall(r"\d+x\d+", m.group(2)):
                modes.append((fmt, size, ""))
    return modes


def best_mode(modes: list[tuple[str, str, str]]) -> tuple[str, str]:
    """Pick a sensible default: prefer mjpeg 1280x720, else the largest size."""
    if not modes:
        return ("", "1280x720")
    for fmt, size, _ in modes:
        if fmt.lower() in ("mjpeg", "mjpg") and size == "1280x720":
            return (fmt, size)

    def area(s: str) -> int:
        w, h = s.split("x")
        return int(w) * int(h)

    fmt, size, _ = max(modes, key=lambda m: area(m[1]))
    return (fmt, size)


def main() -> int:
    ap = argparse.ArgumentParser(description="Onboard a USB camera into Sentigon")
    ap.add_argument("--list", action="store_true", help="list plugged-in devices and exit")
    ap.add_argument("--device", help="v4l2 device, e.g. /dev/video0")
    ap.add_argument("--name", help="camera name, e.g. 'Front Desk'")
    ap.add_argument("--resolution", help="WxH override (else auto)")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--input-format", default="", help="v4l2 pixel format override (e.g. mjpeg)")
    ap.add_argument("--zone", default="Camera FOV", help="zone name to create for this camera")
    args = ap.parse_args()

    devices = list_devices()
    if args.list or not args.device:
        if not devices:
            print("No /dev/video* devices found. Plug in a USB camera and retry.")
            print("(If it is plugged in but not listed, the uvcvideo kernel module may")
            print(" need loading: sudo modprobe uvcvideo)")
            return 1
        print("Detected capture devices:")
        for d in devices:
            modes = probe_modes(d)
            fmt, size = best_mode(modes)
            summary = f"{len(modes)} modes, suggested {fmt or 'auto'} {size}" if modes else "no modes readable"
            print(f"  {d}: {summary}")
        if not args.device:
            print("\nRe-run with --device /dev/videoN --name '<camera name>' to onboard one.")
            return 0

    if args.device not in devices:
        print(f"{args.device} not found. Available: {devices or 'none'}")
        return 1
    if not args.name:
        print("--name is required to onboard.")
        return 1

    modes = probe_modes(args.device)
    auto_fmt, auto_size = best_mode(modes)
    resolution = args.resolution or auto_size
    input_format = args.input_format or auto_fmt

    # stable path/slug from the device number
    n = re.search(r"(\d+)$", args.device)
    path = f"cam_usb{n.group(1) if n else '0'}"

    data = yaml.safe_load(CONFIG.read_text()) or {"sources": []}
    data.setdefault("sources", [])
    data["sources"] = [s for s in data["sources"] if s.get("path") != path]  # replace if re-adding
    data["sources"].append(
        {
            "name": args.name,
            "type": "usb",
            "url": args.device,
            "path": path,
            "fps": args.fps,
            "resolution": resolution,
            "input_format": input_format,
            "zone": {"name": args.zone, "type": "general"},
        }
    )
    head = "".join(line for line in CONFIG.read_text().splitlines(keepends=True) if line.startswith("#"))
    with CONFIG.open("w") as f:
        f.write(head + "\n")
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

    print(f"Added USB camera '{args.name}' -> {args.device} ({input_format or 'auto'} {resolution}) as {path}.")
    print("Restart media-source to publish + onboard it:")
    print("  pkill -f sentigon_mediasource; MEDIASOURCE_HTTP_PORT=8055 uv run python -m sentigon_mediasource &")
    print(f"It will appear on the wall as '{args.name}' once frames flow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
