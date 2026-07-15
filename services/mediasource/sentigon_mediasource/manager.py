"""Media-source manager: one ffmpeg relay per source, real liveness verification,
API onboarding, and auto-reconnect.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml
from sentigon_common.config import settings as common_settings
from sentigon_common.logging import get_logger

from .config import settings

log = get_logger("mediasource")


@dataclass
class Source:
    name: str
    type: str
    url: str
    path: str
    fps: int
    resolution: str
    zone: dict = field(default_factory=dict)
    input_format: str = ""  # v4l2 pixel format for USB cams (e.g. mjpeg); "" = auto
    status: str = "init"
    codec: str = ""
    source_fps: str = ""
    restarts: int = 0
    camera_id: str | None = None


class MediaSourceManager:
    def __init__(self) -> None:
        self.sources: list[Source] = []
        self.procs: dict[str, subprocess.Popen] = {}
        self.threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()

    def load(self) -> None:
        data = yaml.safe_load(Path(settings.config_file).read_text())
        self.sources = [
            Source(
                name=s["name"],
                type=s["type"],
                url=s["url"],
                path=s["path"],
                fps=int(s.get("fps", 15)),
                resolution=s.get("resolution", "1280x720"),
                zone=s.get("zone", {}),
                input_format=s.get("input_format", ""),
            )
            for s in data.get("sources", [])
        ]

    # ── real liveness (ffprobe the actual source) ────────────
    def probe(self, s: Source) -> bool:
        # A USB (v4l2) device is "live" if the device node exists and is readable;
        # ffprobe on a live capture device would hold the device open.
        if s.type == "usb":
            ok = os.path.exists(s.url) and os.access(s.url, os.R_OK)
            if ok:
                s.codec = "v4l2"
            return ok
        try:
            out = subprocess.run(
                [
                    settings.ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,r_frame_rate",
                    "-of",
                    "csv=p=0",
                    s.url,
                ],
                capture_output=True,
                text=True,
                timeout=settings.probe_timeout,
            )
            if out.returncode == 0 and out.stdout.strip():
                parts = out.stdout.strip().split(",")
                s.codec = parts[0]
                s.source_fps = parts[1] if len(parts) > 1 else ""
                return True
        except Exception:  # noqa: BLE001
            log.exception("mediasource.probe_failed", name=s.name)
        return False

    def _ffmpeg_cmd(self, s: Source) -> list[str]:
        w, h = s.resolution.split("x")
        cmd = [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
        ]
        # HTTP reconnect options apply only to network inputs; ffmpeg rejects them
        # (immediate exit) for a local file path.
        is_network = s.url.startswith(("http://", "https://", "rtsp://", "rtmp://"))
        if is_network:
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if s.type == "file":
            cmd += ["-stream_loop", "-1", "-re", "-i", s.url]
        elif s.type == "usb":  # local USB / v4l2 capture device (real camera hardware)
            cmd += ["-f", "v4l2", "-framerate", str(s.fps), "-video_size", f"{w}x{h}"]
            if s.input_format:
                cmd += ["-input_format", s.input_format]
            cmd += ["-i", s.url]
        else:  # hls / rtsp live relay
            if s.type == "rtsp":
                cmd += ["-rtsp_transport", "tcp"]
            cmd += ["-re", "-i", s.url]
        cmd += [
            "-an",
            "-vf",
            f"scale={w}:{h},fps={s.fps}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-g",
            str(s.fps * 2),
            "-pix_fmt",
            "yuv420p",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            f"{settings.mediamtx_rtsp}/{s.path}",
        ]
        return cmd

    def _run_source(self, s: Source) -> None:
        backoff = settings.reconnect_base
        while not self._stop.is_set():
            proc = subprocess.Popen(
                self._ffmpeg_cmd(s), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self.procs[s.path] = proc
            s.status = "publishing"
            log.info("mediasource.publishing", name=s.name, path=s.path, codec=s.codec)
            proc.wait()
            if self._stop.is_set():
                break
            s.status = "reconnecting"
            s.restarts += 1
            log.warning("mediasource.exited", name=s.name, rc=proc.returncode, restarts=s.restarts)
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, settings.reconnect_max)
        s.status = "stopped"

    def start(self) -> None:
        self.load()
        for s in self.sources:
            if not self.probe(s):
                s.status = "unreachable"
                log.error("mediasource.unreachable", name=s.name, url=s.url)
                continue
            t = threading.Thread(
                target=self._run_source, args=(s,), name=f"media-{s.path}", daemon=True
            )
            self.threads[s.path] = t
            t.start()
        threading.Thread(target=self._register_all, daemon=True).start()

    # ── verify path is really live in MediaMTX, then onboard ──
    def _path_ready(self, path: str) -> bool:
        try:
            r = httpx.get(f"{settings.mediamtx_api}/v3/paths/get/{path}", timeout=5.0)
            return r.status_code == 200 and bool(r.json().get("ready"))
        except Exception:  # noqa: BLE001
            return False

    def _register_all(self) -> None:
        for s in self.sources:
            if s.status == "unreachable":
                continue
            deadline = time.monotonic() + settings.ready_timeout
            while time.monotonic() < deadline and not self._stop.is_set():
                if self._path_ready(s.path):
                    break
                if self._stop.wait(1.0):
                    return
            if self._path_ready(s.path):
                self._register(s)
            else:
                log.warning("mediasource.not_ready", path=s.path)

    def _register(self, s: Source) -> None:
        rtsp = f"{settings.mediamtx_rtsp}/{s.path}"
        try:
            with httpx.Client(
                timeout=20.0, headers={"X-Service-Token": common_settings.service_token}
            ) as c:
                sites = c.get(f"{settings.api_url}/sites").json()
                if not sites:
                    log.warning("mediasource.no_site")
                    return
                site_id = sites[0]["id"]
                cams = {x["name"]: x for x in c.get(f"{settings.api_url}/cameras").json()}
                if s.name in cams:
                    s.camera_id = cams[s.name]["id"]
                else:
                    r = c.post(
                        f"{settings.ingest_url}/cameras",
                        json={
                            "name": s.name,
                            "rtsp_uri": rtsp,
                            "site_id": site_id,
                            "fps": s.fps,
                            "resolution": s.resolution,
                        },
                    )
                    r.raise_for_status()
                    s.camera_id = r.json()["id"]
                    log.info("mediasource.camera_registered", name=s.name, id=s.camera_id)
                if s.zone and s.camera_id:
                    zoned = {
                        z["camera_id"]
                        for z in c.get(f"{settings.api_url}/zones").json()
                        if z.get("camera_id")
                    }
                    if s.camera_id not in zoned:
                        c.post(
                            f"{settings.api_url}/zones",
                            json={
                                "name": s.zone["name"],
                                "zone_type": s.zone["type"],
                                "camera_id": s.camera_id,
                                "site_id": site_id,
                                "polygon": s.zone.get("polygon", []),
                                "max_occupancy": s.zone.get("max_occupancy"),
                            },
                        ).raise_for_status()
                        log.info("mediasource.zone_created", zone=s.zone["name"], camera=s.name)
        except Exception:  # noqa: BLE001
            log.exception("mediasource.register_failed", name=s.name)

    def stop(self) -> None:
        self._stop.set()
        for p in self.procs.values():
            with contextlib.suppress(Exception):
                p.terminate()
        for t in self.threads.values():
            t.join(timeout=5.0)

    def status(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "path": s.path,
                "type": s.type,
                "status": s.status,
                "codec": s.codec,
                "source_fps": s.source_fps,
                "target_fps": s.fps,
                "restarts": s.restarts,
                "camera_id": s.camera_id,
                "whep_url": f"{settings.webrtc_base}/{s.path}/whep",
            }
            for s in self.sources
        ]
