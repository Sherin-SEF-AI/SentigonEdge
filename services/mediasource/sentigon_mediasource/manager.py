"""Media-source manager: one ffmpeg relay per source, real liveness verification,
API onboarding, and auto-reconnect.
"""

from __future__ import annotations

import contextlib
import os
import re
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

# MediaMTX source types that mean "a device is pushing a stream INTO us" (as opposed
# to us pulling/relaying out). These are the inbound publishers we auto-onboard.
_PUSH_SOURCE_TYPES = {
    "rtmpConn",
    "rtmpsConn",
    "srtConn",
    "webRTCSession",
    "rtspSession",
    "rtspsSession",
}


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
    stop_event: threading.Event = field(default_factory=threading.Event)


class MediaSourceManager:
    def __init__(self) -> None:
        self.sources: list[Source] = []
        self.procs: dict[str, subprocess.Popen] = {}
        self.threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        # push paths the user has explicitly deleted: keep ignoring them while they
        # are still being pushed, but re-onboard on a fresh (re)connect (see watcher).
        self._suppressed: set[str] = set()

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
        # http(s) reconnect options apply only to those inputs; harmless elsewhere but
        # ffmpeg rejects them for a local file path.
        if s.url.startswith(("http://", "https://")):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if s.type == "file":
            cmd += ["-stream_loop", "-1", "-re", "-i", s.url]
        elif s.type == "usb":  # local USB / v4l2 capture device (real camera hardware)
            cmd += ["-f", "v4l2", "-framerate", str(s.fps), "-video_size", f"{w}x{h}"]
            if s.input_format:
                cmd += ["-input_format", s.input_format]
            cmd += ["-i", s.url]
        else:  # generic live network relay: rtsp / rtmp / srt / hls / http-mjpeg
            if s.url.startswith("rtsp://"):
                cmd += ["-rtsp_transport", "tcp"]
            # live sources pace themselves; -re (file pacing) would only add latency
            cmd += ["-i", s.url]
        cmd += ["-an", "-vf", f"scale={w}:{h},fps={s.fps}", "-c:v", settings.encoder]
        # preset/tune are libx264-only knobs; the hardware encoder rejects them.
        # ultrafast (not veryfast): on the Orin's CPU cores veryfast was pegging ~1.6
        # cores at only ~5fps for one 720p relay; ultrafast roughly halves that so the
        # relay keeps up at the target fps (local RTSP, so the higher bitrate is fine).
        if settings.encoder == "libx264":
            cmd += ["-preset", "ultrafast", "-tune", "zerolatency"]
        cmd += [
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
        while not self._stop.is_set() and not s.stop_event.is_set():
            proc = subprocess.Popen(
                self._ffmpeg_cmd(s), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self.procs[s.path] = proc
            s.status = "publishing"
            log.info("mediasource.publishing", name=s.name, path=s.path, codec=s.codec)
            proc.wait()
            if self._stop.is_set() or s.stop_event.is_set():
                break
            s.status = "reconnecting"
            s.restarts += 1
            log.warning("mediasource.exited", name=s.name, rc=proc.returncode, restarts=s.restarts)
            if s.stop_event.wait(backoff):  # per-source stop wakes this immediately
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
        if settings.push_watch:
            threading.Thread(target=self._watch_pushes, name="media-pushwatch", daemon=True).start()

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
            self._register_one(s)

    def _register_one(self, s: Source) -> None:
        """Wait for the MediaMTX path to go live, then onboard the Camera + Zone."""
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

    # ── device-push auto-onboard (body cams / phones streaming IN) ──
    def _watch_pushes(self) -> None:
        """Poll MediaMTX for streams pushed IN by external devices (a body cam via
        RTMP, a phone via SRT/WHIP/RTSP) and auto-onboard each as a camera. This is
        the generic device-push catch-all: point any streaming device at the box and
        it appears on the wall — no per-device config."""
        while not self._stop.wait(settings.push_poll_interval):
            try:
                r = httpx.get(f"{settings.mediamtx_api}/v3/paths/list", timeout=5.0)
                if r.status_code != 200:
                    continue
                items = r.json().get("items", [])
            except Exception:  # noqa: BLE001
                continue
            ready_paths = {i.get("name") for i in items if i.get("ready")}
            # a suppressed (user-deleted) path that stopped being pushed is forgotten,
            # so a genuine reconnect later re-onboards it.
            self._suppressed &= ready_paths
            # reflect liveness of already-onboarded push cameras.
            for s in self.sources:
                if s.type == "push":
                    s.status = "publishing" if s.path in ready_paths else "offline"
            known = {s.path for s in self.sources}
            for item in items:
                path = item.get("name")
                if not path or not item.get("ready"):
                    continue
                if path in known or path in self._suppressed:
                    continue
                src_type = (item.get("source") or {}).get("type", "")
                if src_type not in _PUSH_SOURCE_TYPES:
                    continue  # our own relays / pull sources, not an inbound device
                self._onboard_push(path, src_type)

    def _onboard_push(self, path: str, src_type: str) -> None:
        """Register an externally-pushed MediaMTX path as a camera. The device already
        publishes to MediaMTX, so there is no ffmpeg relay to run — perception pulls
        the path directly. Not persisted: MediaMTX + the API camera row are the source
        of truth and _register dedups by rtsp_uri, so it self-heals across restarts."""
        name = path.replace("_", " ").strip() or path
        s = Source(
            name=name,
            type="push",
            url=f"{settings.mediamtx_rtsp}/{path}",
            path=path,
            fps=settings.push_default_fps,
            resolution="1280x720",
            zone={"name": "Camera FOV", "type": "general"},
        )
        s.status = "publishing"
        s.codec = src_type
        self.sources.append(s)
        log.info("mediasource.push_discovered", path=path, source=src_type)
        threading.Thread(target=self._register_one, args=(s,), daemon=True).start()

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
                # match on the stable rtsp path, not the name — so a camera renamed
                # in the console is reused (not duplicated) on the next register.
                cams = {x.get("rtsp_uri"): x for x in c.get(f"{settings.api_url}/cameras").json()}
                if rtsp in cams:
                    s.camera_id = cams[rtsp]["id"]
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
        for s in self.sources:
            s.stop_event.set()
        for p in self.procs.values():
            with contextlib.suppress(Exception):
                p.terminate()
        for t in self.threads.values():
            t.join(timeout=5.0)

    def remove_source(self, camera_id: str) -> bool:
        """Stop the relay bound to `camera_id` and drop it from the config so it is
        not re-created on the next start. Used by the API's DELETE /cameras/{id}."""
        s = next((x for x in self.sources if x.camera_id == camera_id), None)
        if s is None:
            return False
        s.stop_event.set()
        proc = self.procs.pop(s.path, None)
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.terminate()
        t = self.threads.pop(s.path, None)
        if t is not None:
            t.join(timeout=5.0)
        self.sources = [x for x in self.sources if x.path != s.path]
        if s.type == "push":
            # keep ignoring it while the device is still pushing; it re-onboards only
            # on a fresh reconnect (the watcher clears suppression when it goes idle).
            self._suppressed.add(s.path)
        else:
            self._remove_source_from_config(s.path)
        log.info("mediasource.source_removed", name=s.name, path=s.path, camera_id=camera_id)
        return True

    def _remove_source_from_config(self, path: str) -> None:
        cfg = Path(settings.config_file)
        text = cfg.read_text()
        data = yaml.safe_load(text) or {"sources": []}
        data["sources"] = [x for x in data.get("sources", []) if x.get("path") != path]
        head = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
        with cfg.open("w") as f:
            if head:
                f.write(head + "\n")
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

    def _status_of(self, s: Source) -> dict:
        return {
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

    def status(self) -> list[dict]:
        return [self._status_of(s) for s in self.sources]

    # ── dynamic USB onboarding (console "scan + add") ─────────
    def registered_device_urls(self) -> set[str]:
        """Device paths already added as sources (so a scan can flag duplicates)."""
        return {s.url for s in self.sources}

    def _persist_source(self, entry: dict) -> None:
        """Append a source to media_sources.yaml (replacing any with the same path),
        preserving the leading comment header. Mirrors scripts/add_usb_camera.py so
        the source survives a service restart."""
        cfg = Path(settings.config_file)
        text = cfg.read_text()
        data = yaml.safe_load(text) or {"sources": []}
        data.setdefault("sources", [])
        data["sources"] = [x for x in data["sources"] if x.get("path") != entry["path"]]
        data["sources"].append(entry)
        head = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
        with cfg.open("w") as f:
            if head:
                f.write(head + "\n")
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

    def add_usb_source(
        self,
        device: str,
        name: str,
        *,
        fps: int = 15,
        resolution: str = "1280x720",
        input_format: str = "",
        zone_name: str = "Camera FOV",
    ) -> dict:
        """Onboard a plugged-in v4l2 device at runtime: persist it, start its ffmpeg
        relay to MediaMTX, and register the Camera + Zone through the API — the same
        path a file/config source takes, no service restart. Returns the source
        status. Raises ValueError on a missing/duplicate/unreadable device."""
        if not (os.path.exists(device) and os.access(device, os.R_OK)):
            raise ValueError(f"device {device} not found or not readable")
        if any(s.url == device for s in self.sources):
            raise ValueError(f"device {device} is already added")
        n = re.search(r"(\d+)$", device)
        path = f"cam_usb{n.group(1) if n else '0'}"
        if any(s.path == path for s in self.sources):
            raise ValueError(f"stream path {path} is already in use")

        entry = {
            "name": name,
            "type": "usb",
            "url": device,
            "path": path,
            "fps": int(fps),
            "resolution": resolution,
            "input_format": input_format,
            "zone": {"name": zone_name, "type": "general"},
        }
        self._persist_source(entry)

        s = Source(
            name=name,
            type="usb",
            url=device,
            path=path,
            fps=int(fps),
            resolution=resolution,
            zone=entry["zone"],
            input_format=input_format,
        )
        self.sources.append(s)
        if not self.probe(s):
            s.status = "unreachable"
            raise ValueError(f"device {device} could not be opened as a capture source")
        t = threading.Thread(target=self._run_source, args=(s,), name=f"media-{s.path}", daemon=True)
        self.threads[s.path] = t
        t.start()
        threading.Thread(target=self._register_one, args=(s,), daemon=True).start()
        log.info("mediasource.usb_added", name=name, device=device, path=path)
        return self._status_of(s)

    def add_stream_source(
        self,
        name: str,
        url: str,
        *,
        fps: int = 15,
        resolution: str = "1280x720",
        zone_name: str = "Camera FOV",
    ) -> dict:
        """Onboard ANY live network stream (rtsp/rtmp/srt/hls/http-mjpeg) as a camera:
        relay it to MediaMTX and register the Camera + Zone — the generic catch-all
        video driver, so a user can add any stream by URL with no device-specific code."""
        if not url.startswith(("rtsp://", "rtmp://", "rtmps://", "srt://", "http://", "https://")):
            raise ValueError("url must start with rtsp/rtmp/srt/http(s)")
        if any(s.url == url for s in self.sources):
            raise ValueError("that stream is already added")
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "stream"
        path, base, i = f"cam_{slug}", f"cam_{slug}", 2
        while any(s.path == path for s in self.sources):
            path, i = f"{base}_{i}", i + 1
        entry = {
            "name": name,
            "type": "stream",
            "url": url,
            "path": path,
            "fps": int(fps),
            "resolution": resolution,
            "zone": {"name": zone_name, "type": "general"},
        }
        self._persist_source(entry)
        s = Source(
            name=name,
            type="stream",
            url=url,
            path=path,
            fps=int(fps),
            resolution=resolution,
            zone=entry["zone"],
        )
        self.sources.append(s)
        if not self.probe(s):
            s.status = "unreachable"
            raise ValueError(f"stream not reachable or has no video: {url}")
        t = threading.Thread(target=self._run_source, args=(s,), name=f"media-{s.path}", daemon=True)
        self.threads[s.path] = t
        t.start()
        threading.Thread(target=self._register_one, args=(s,), daemon=True).start()
        log.info("mediasource.stream_added", name=name, url=url, path=path)
        return self._status_of(s)
