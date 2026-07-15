"""MediaMTX control-API client and browser playback URL helpers."""

from __future__ import annotations

import httpx
from sentigon_common.config import settings

from .config import ingest_settings


class MediaMTXClient:
    def __init__(self, api_base: str | None = None) -> None:
        self.api_base = (api_base or settings.mediamtx_api).rstrip("/")

    async def list_paths(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self.api_base}/v3/paths/list")
            r.raise_for_status()
            return r.json().get("items", [])

    async def path(self, name: str) -> dict | None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self.api_base}/v3/paths/get/{name}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    async def path_ready(self, name: str) -> bool:
        info = await self.path(name)
        return bool(info and info.get("ready"))

    @staticmethod
    def _mtx_path(rtsp_uri: str) -> str:
        # rtsp://host:8554/cam_lobby -> cam_lobby
        return rtsp_uri.rstrip("/").rsplit("/", 1)[-1]

    def whep_url(self, rtsp_uri: str) -> str:
        return f"{ingest_settings.webrtc_base.rstrip('/')}/{self._mtx_path(rtsp_uri)}/whep"

    def hls_url(self, rtsp_uri: str) -> str:
        return f"{ingest_settings.hls_base.rstrip('/')}/{self._mtx_path(rtsp_uri)}/index.m3u8"
