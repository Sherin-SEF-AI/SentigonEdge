"""CrossSite service settings.

Cross-site correlation tuning + the API/ingest base URLs used to build a real
edge/site onboarding bundle. Loaded from the environment and the repo-root .env.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class CrossSiteSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="CROSSSITE_",
    )

    # Base URLs baked into the site onboarding bundle (real config, not fabricated).
    api_url: str = "http://localhost:8010"
    ingest_url: str = "http://localhost:8020"

    # Qdrant appearance (ReID) cross-site scan.
    reid_collection: str = "reid"
    reid_scan_seconds: float = 30.0
    reid_score_threshold: float = 0.82
    reid_scan_limit: int = 200  # recent points to scan per cycle

    # Plate correlation lookback window: two sites within this window link a vehicle.
    plate_window_hours: int = 168


@lru_cache
def get_crosssite_settings() -> CrossSiteSettings:
    return CrossSiteSettings()


settings = get_crosssite_settings()
