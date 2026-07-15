"""Media-source settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class MediaSourceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="MEDIASOURCE_",
    )

    ffmpeg_path: str = str(_REPO_ROOT / "tools" / "ffmpeg")
    ffprobe_path: str = str(_REPO_ROOT / "tools" / "ffprobe")
    config_file: str = str(_REPO_ROOT / "configs" / "media_sources.yaml")

    mediamtx_rtsp: str = "rtsp://localhost:8554"
    mediamtx_api: str = "http://localhost:9997"
    webrtc_base: str = "http://localhost:8889"
    ingest_url: str = "http://localhost:8020"
    api_url: str = "http://localhost:8010"

    probe_timeout: float = 30.0
    ready_timeout: float = 30.0
    reconnect_base: float = 2.0
    reconnect_max: float = 30.0


@lru_cache
def get_media_settings() -> MediaSourceSettings:
    return MediaSourceSettings()


settings = get_media_settings()
