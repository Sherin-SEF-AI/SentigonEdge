"""Fleet service settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class FleetSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="FLEET_",
    )

    # how often the engine collects telemetry + re-evaluates findings
    collect_seconds: float = 20.0
    # a camera whose last_seen is older than this is treated as offline
    camera_stale_seconds: float = 30.0
    # camera is "low fps" when its fps drops below this fraction of its target fps
    low_fps_ratio: float = 0.5
    # host disk usage (percent) at/above which a disk_pressure finding is raised
    disk_pct_warn: float = 85.0
    # host memory usage (percent) at/above which a mem_pressure finding is raised
    mem_pct_warn: float = 90.0
    # per-request timeout for service health/stats probes
    probe_timeout: float = 3.0
    # services probed each pass, encoded as "name|healthz_url|stats_url_or_empty"
    service_probes: list[str] = [
        "api|http://localhost:8010/healthz|",
        "ingest|http://localhost:8020/healthz|http://localhost:8020/health/summary",
        "perception|http://localhost:8030/healthz|http://localhost:8030/stats",
        "context|http://localhost:8040/healthz|",
        "reason|http://localhost:8050/healthz|http://localhost:8050/stats",
        "mediasource|http://localhost:8055/healthz|",
        "search|http://localhost:8060/healthz|http://localhost:8060/stats",
        "notify|http://localhost:8070/healthz|http://localhost:8070/stats",
        "dispatch|http://localhost:8081/healthz|http://localhost:8081/stats",
        "crosssite|http://localhost:8086/healthz|http://localhost:8086/stats",
    ]


@lru_cache
def get_fleet_settings() -> FleetSettings:
    return FleetSettings()


settings = get_fleet_settings()
