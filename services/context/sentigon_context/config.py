"""Context engine settings. Thresholds are defaults; a signature's params.conditions
in the DB override them per-signature (hot-reloadable in the UI)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class ContextSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="CONTEXT_",
    )

    ingest_url: str = "http://localhost:8020"
    track_stale_seconds: float = 6.0
    min_track_age_seconds: float = 0.6
    default_cooldown_seconds: float = 45.0
    meta_refresh_seconds: float = 10.0

    loiter_seconds: float = 8.0
    crowd_threshold: int = 8
    tailgate_window_seconds: float = 5.0
    tailgate_min_count: int = 2
    vehicle_speed_threshold: float = 0.34  # normalized units / second (uncalibrated fallback)
    vehicle_speed_kmh: float = 20.0  # real-world speeding threshold on calibrated cameras
    run_speed_ms: float = 2.5  # person running threshold (walking ~1.4 m/s)
    person_speed_threshold: float = 0.28
    abandoned_seconds: float = 15.0
    abandoned_stationary_speed: float = 0.02

    vehicle_classes: list[str] = ["car", "truck", "bus", "motorcycle"]
    abandoned_classes: list[str] = ["backpack", "handbag", "suitcase"]

    # live cross-camera handoff (OSNet ReID). When a person leaves one camera and a
    # matching appearance was seen on another within the window, emit a handoff.
    handoff_enabled: bool = True
    handoff_threshold: float = 0.86  # OSNet cosine; same-person is ~0.9, cross ~0.57
    handoff_window_s: float = 45.0  # the other-camera sighting must be this recent
    handoff_min_appearances: int = 4  # track needs this many reid embeddings to match
    handoff_cooldown_s: float = 60.0  # per (from_track, to_camera)
    reid_collection: str = "reid"

    # segmentation-based zone analytics
    zone_analytics_enabled: bool = True
    zone_snapshot_interval_s: float = 10.0  # persist a per-zone metric sample this often

    # incident dedup / grouping: repeated same-signature/camera/zone detections roll
    # up into one open incident (occurrence_count) instead of flooding queue + VLM
    dedup_enabled: bool = True
    dedup_window_s: float = 150.0  # group a new detection into an open incident within this window

    # Timezone for evaluating schedule-suppression windows (start/end minute and
    # days_of_week are authored in site-local time). Single-site-per-box deployment,
    # so one zone; set to the site's IANA tz (e.g. "America/New_York").
    schedule_timezone: str = "UTC"

    # behavioral anomaly detection (learned per-zone/hour baselines -> deviation)
    anomaly_enabled: bool = True
    anomaly_sigma: float = 3.0  # fire when current occupancy is this many std above baseline
    anomaly_min_samples: int = 30  # need this many baseline samples for the (zone, hour) bucket
    anomaly_min_occ_delta: float = 3.0  # and at least this many more people than normal
    anomaly_cooldown_s: float = 120.0
    anomaly_baseline_refresh_s: float = 60.0
    # exclude the most recent snapshots from the baseline so a sustained anomaly is
    # not absorbed into "normal" (the baseline is established history, not right now)
    anomaly_baseline_exclude_recent_s: float = 300.0
    anomaly_baseline_window_s: float = 21600.0  # trailing history window for the baseline (6h)


@lru_cache
def get_context_settings() -> ContextSettings:
    return ContextSettings()


settings = get_context_settings()
