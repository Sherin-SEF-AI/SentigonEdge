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
    # Relay video encoder (configurable). "libx264" is the reliable CPU default.
    # "h264_v4l2m2m" is the Jetson hardware encoder — much lower CPU, but on some
    # JetPack/ffmpeg builds it needs extra params or conflicts with the v4l2 capture
    # device, so it is opt-in (test with a single source before enabling fleet-wide).
    encoder: str = "libx264"

    mediamtx_rtsp: str = "rtsp://localhost:8554"
    mediamtx_api: str = "http://localhost:9997"
    webrtc_base: str = "http://localhost:8889"
    ingest_url: str = "http://localhost:8020"
    api_url: str = "http://localhost:8010"

    probe_timeout: float = 30.0
    ready_timeout: float = 30.0
    reconnect_base: float = 2.0
    reconnect_max: float = 30.0

    # Device-push auto-onboard: poll MediaMTX for streams pushed IN by external
    # devices (body cams, phones via RTMP/SRT/WHIP/RTSP) and register each as a
    # camera automatically — point any streaming device at the box and it appears.
    push_watch: bool = True
    push_poll_interval: float = 5.0
    push_default_fps: int = 15


@lru_cache
def get_media_settings() -> MediaSourceSettings:
    return MediaSourceSettings()


settings = get_media_settings()
