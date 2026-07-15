"""Reason service settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class ReasonSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="REASON_SVC_",
    )

    ingest_url: str = "http://localhost:8020"
    vlm_timeout: float = 120.0
    postroll_enabled: bool = True
    postroll_delay_seconds: float = 1.5
    auto_dismiss_rejected: bool = True
    # signatures that must alert immediately and are verified in parallel (never blocked)
    fast_path_categories: list[str] = ["active_shooter", "violence", "terrorism"]
    # Backpressure: when the VLM falls behind, a candidate arrives stale (its bus
    # timestamp is older than this). Under that condition we shed low-severity
    # verifications (defer them, never drop the incident) so critical/high always
    # get the model's limited throughput.
    backpressure_stale_seconds: float = 25.0
    backpressure_shed_below: str = "medium"  # shed severities strictly below this
    # natural-language activity notifications (VLM-evaluated, open-set)
    nl_enabled: bool = True
    nl_eval_tick_s: float = 8.0  # how often the evaluator wakes to check due NL alerts


@lru_cache
def get_reason_settings() -> ReasonSettings:
    return ReasonSettings()


settings = get_reason_settings()
