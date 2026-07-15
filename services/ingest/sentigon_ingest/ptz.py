"""ONVIF Profile S PTZ control adapter.

Real pan/tilt/zoom control against an ONVIF camera's PTZ service. Behind a clean
interface so callers do not touch ONVIF details. A camera without an onvif_uri
(e.g. a file-over-RTSP dev source, or a fixed camera) reports ptz_supported=False,
which is the honest result: nothing is faked.

Hardware note: exercising a real move needs a physical ONVIF Profile S PTZ camera
on the network. The dev sources are file-over-RTSP with no ONVIF device, so this
adapter correctly returns "not supported" for them until such a camera is added.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from sentigon_common.logging import get_logger

log = get_logger("ingest.ptz")


def _parse_onvif(onvif_uri: str) -> tuple[str, int]:
    u = urlparse(onvif_uri if "://" in onvif_uri else f"http://{onvif_uri}")
    return u.hostname or onvif_uri, u.port or 80


class PtzController:
    """One controller per camera. Lazily connects on first use."""

    def __init__(self, onvif_uri: str | None, user: str = "admin", password: str = "") -> None:
        self.onvif_uri = onvif_uri
        self.user = user
        self.password = password
        self._cam = None
        self._ptz = None
        self._profile = None

    @property
    def supported(self) -> bool:
        return bool(self.onvif_uri)

    def _connect_blocking(self) -> None:
        from onvif import ONVIFCamera

        host, port = _parse_onvif(self.onvif_uri or "")
        cam = ONVIFCamera(host, port, self.user, self.password)
        media = cam.create_media_service()
        ptz = cam.create_ptz_service()
        profile = media.GetProfiles()[0]
        self._cam, self._ptz, self._profile = cam, ptz, profile

    async def _ensure(self) -> None:
        if self._ptz is None:
            await asyncio.to_thread(self._connect_blocking)

    async def move(self, pan: float, tilt: float, zoom: float = 0.0) -> dict:
        """Continuous move; velocities in [-1, 1]. Call stop() to halt."""
        if not self.supported:
            return {"ok": False, "detail": "camera has no ONVIF PTZ endpoint"}
        await self._ensure()

        def _do() -> None:
            req = self._ptz.create_type("ContinuousMove")
            req.ProfileToken = self._profile.token
            req.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
            self._ptz.ContinuousMove(req)

        await asyncio.to_thread(_do)
        log.info("ptz.move", pan=pan, tilt=tilt, zoom=zoom)
        return {"ok": True, "op": "move", "pan": pan, "tilt": tilt, "zoom": zoom}

    async def stop(self) -> dict:
        if not self.supported:
            return {"ok": False, "detail": "camera has no ONVIF PTZ endpoint"}
        await self._ensure()

        def _do() -> None:
            req = self._ptz.create_type("Stop")
            req.ProfileToken = self._profile.token
            req.PanTilt = True
            req.Zoom = True
            self._ptz.Stop(req)

        await asyncio.to_thread(_do)
        return {"ok": True, "op": "stop"}

    async def goto_preset(self, preset_token: str) -> dict:
        if not self.supported:
            return {"ok": False, "detail": "camera has no ONVIF PTZ endpoint"}
        await self._ensure()

        def _do() -> None:
            req = self._ptz.create_type("GotoPreset")
            req.ProfileToken = self._profile.token
            req.PresetToken = preset_token
            self._ptz.GotoPreset(req)

        await asyncio.to_thread(_do)
        return {"ok": True, "op": "preset", "preset": preset_token}

    async def list_presets(self) -> dict:
        if not self.supported:
            return {"ok": False, "detail": "camera has no ONVIF PTZ endpoint", "presets": []}
        await self._ensure()
        presets = await asyncio.to_thread(lambda: self._ptz.GetPresets(self._profile.token))
        return {"ok": True, "presets": [{"token": p.token, "name": getattr(p, "Name", "")} for p in presets]}
