"""Dispatch service settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class DispatchSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="DISPATCH_",
    )

    # Only dispatch responders for high/critical confirmed incidents.
    min_severity: str = "high"
    # Timezone for on-call shift resolution (shift hours are authored site-local).
    dispatch_timezone: str = "UTC"
    # SLA windows (seconds): ack breach escalates up the tier; resolve breach expires.
    sla_ack_seconds: int = 300
    sla_resolve_seconds: int = 1800
    # How often the SLA sweeper wakes.
    sweep_seconds: float = 10.0
    # API base used to build deep links back into the incident console.
    api_url: str = "http://localhost:8010"


@lru_cache
def get_dispatch_settings() -> DispatchSettings:
    return DispatchSettings()


settings = get_dispatch_settings()
