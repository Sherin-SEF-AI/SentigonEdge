"""Media-source FastAPI app: health, source status, and USB camera scan/onboard."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentigon_common.auth import install_auth_middleware
from sentigon_common.config import settings as common_settings
from sentigon_common.health import make_health_router
from sentigon_common.logging import configure_logging

from .manager import MediaSourceManager
from .usb import scan_devices


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("mediasource")
    manager = MediaSourceManager()
    manager.start()
    app.state.manager = manager
    try:
        yield
    finally:
        manager.stop()


app = FastAPI(title="Sentigon Media Source", version="0.1.0", lifespan=lifespan)
# Browser-facing (the Cameras screen scans/adds USB devices), so it needs CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=common_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_auth_middleware(app, protect_reads=True)
app.include_router(make_health_router("mediasource"))


@app.get("/sources")
async def sources(request: Request) -> list[dict]:
    return request.app.state.manager.status()


@app.delete("/sources/by-camera/{camera_id}")
async def remove_source(camera_id: str, request: Request) -> dict:
    """Stop + de-register the relay bound to a camera (called by the API on camera
    delete, so a mediasource-managed camera is not re-created on the next start)."""
    removed = request.app.state.manager.remove_source(camera_id)
    return {"removed": removed, "camera_id": camera_id}


@app.get("/usb/scan")
async def usb_scan(request: Request) -> list[dict]:
    """Scan/refresh the plugged-in USB (v4l2) capture devices, flagging which are
    already onboarded so the console can offer only the new ones."""
    registered = request.app.state.manager.registered_device_urls()
    devices = scan_devices()
    for d in devices:
        d["registered"] = d["device"] in registered
    return devices


class UsbAddIn(BaseModel):
    device: str  # /dev/videoN
    name: str
    fps: int = 15
    resolution: str = "1280x720"
    input_format: str = ""  # v4l2 pixel format (e.g. mjpeg); "" = auto
    zone_name: str = "Camera FOV"


@app.post("/usb/add", status_code=201)
async def usb_add(payload: UsbAddIn, request: Request) -> dict:
    """Onboard a scanned USB device as a live camera (writer role via middleware)."""
    if not payload.name.strip():
        raise HTTPException(400, "name is required")
    try:
        return request.app.state.manager.add_usb_source(
            payload.device,
            payload.name.strip(),
            fps=payload.fps,
            resolution=payload.resolution,
            input_format=payload.input_format,
            zone_name=payload.zone_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class StreamAddIn(BaseModel):
    name: str
    url: str  # rtsp:// | rtmp:// | srt:// | http(s):// (hls / mjpeg)
    fps: int = 15
    resolution: str = "1280x720"
    zone_name: str = "Camera FOV"


@app.post("/stream/add", status_code=201)
async def stream_add(payload: StreamAddIn, request: Request) -> dict:
    """Onboard ANY live network stream as a camera (the generic catch-all video
    driver): relay to MediaMTX + register Camera/Zone. Writer role via middleware."""
    if not payload.name.strip():
        raise HTTPException(400, "name is required")
    try:
        return request.app.state.manager.add_stream_source(
            payload.name.strip(),
            payload.url.strip(),
            fps=payload.fps,
            resolution=payload.resolution,
            zone_name=payload.zone_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
