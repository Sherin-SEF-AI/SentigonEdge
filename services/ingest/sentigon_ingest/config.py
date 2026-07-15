"""Ingest-specific settings (on top of the shared sentigon_common settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="INGEST_",
    )

    # recording
    segment_seconds: int = 10  # length of each recorded chunk
    preroll_seconds: int = 12  # in-memory ring buffer depth
    record_fps: int = 15  # frames per second written to segments
    retention_segments: int = 240  # rolling retention per camera (~40 min at 10s)
    work_dir: str = "/tmp/sentigon-ingest"

    # capture
    read_timeout_seconds: float = 8.0  # no-frame window before treating the stream as down
    reconnect_base_seconds: float = 1.0
    reconnect_max_seconds: float = 20.0
    health_interval_seconds: float = 2.0

    # mediamtx host-facing base (browser WHEP/HLS)
    webrtc_base: str = "http://localhost:8889"
    hls_base: str = "http://localhost:8888"


@lru_cache
def get_ingest_settings() -> IngestSettings:
    return IngestSettings()


ingest_settings = get_ingest_settings()
