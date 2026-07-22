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
    gst_launch_path: str = "gst-launch-1.0"  # for the NVENC hardware-encode relay
    ffprobe_path: str = str(_REPO_ROOT / "tools" / "ffprobe")
    config_file: str = str(_REPO_ROOT / "configs" / "media_sources.yaml")
    # Relay video encoder (configurable):
    #   "libx264" — portable CPU default (works for every source type).
    #   "nvenc"   — Jetson hardware encode for MJPEG USB cams: a GStreamer pipeline
    #               (nvv4l2decoder + nvv4l2h264enc) does decode+encode on the video
    #               engines and ffmpeg only muxes/pushes RTSP (-c copy). Measured
    #               ~11% vs ~61% CPU for one 720p relay. Non-MJPEG/file/network
    #               sources auto-fall back to libx264. (ffmpeg's own h264_v4l2m2m is
    #               broken on this JetPack build, so GStreamer is the hardware path.)
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
