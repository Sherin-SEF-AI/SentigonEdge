"""Reason service settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    # Proactive VLM sweep: evaluate the enabled VLM-detection ("gemini") catalog
    # signatures against fresh camera frames so the open-vocab catalog can actually
    # fire (context only produces geometric candidates; nothing else generates these).
    # One batched VLM call per camera per interval.
    vlm_sweep_enabled: bool = True
    vlm_sweep_tick_s: float = 10.0  # evaluator wake interval
    vlm_sweep_interval_s: float = 60.0  # minimum gap between sweeps of the same camera
    vlm_sweep_cooldown_s: float = 300.0  # per (camera, signature) refire cooldown
    vlm_sweep_max_signatures: int = 20  # cap signatures per prompt (token/latency budget)
    vlm_sweep_max_cameras: int = 12  # cap cameras evaluated per tick
    # open-vocabulary grounding ("find and box it"): localize the NL condition rather
    # than returning a bare yes/no, and serve on-demand open-vocab detection.
    ground_enabled: bool = True
    ground_backend: str = "vlm"  # "vlm" (reuse the reason VLM) | "locateanything"
    ground_endpoint: str = ""  # "" -> reason_endpoint (vlm) or the LocateAnything server base
    ground_model: str = ""  # "" -> reason_model
    ground_max_boxes: int = 12
    ground_min_score: float = 0.3

    # ── Escalation / adjudication tier (Groq, text-only) ──────────
    # The local VLM sees the imagery and produces a first-pass verdict + scene
    # description. For high-stakes incidents we escalate to a fast, strong TEXT
    # reasoner (Groq gpt-oss-120b) that re-adjudicates over that description + full
    # context and writes a sharper SITREP + recommended action. Off the streaming
    # hot path (only fires on incidents); best-effort (falls back to the VLM verdict
    # on any error). Vision stays local — Groq has no vision model.
    escalate_enabled: bool = False
    escalate_base_url: str = "https://api.groq.com/openai/v1"
    escalate_model: str = "openai/gpt-oss-120b"
    # read the unprefixed GROQ_API_KEY from .env (not REASON_SVC_-prefixed)
    escalate_api_key: str = Field(default="", validation_alias="GROQ_API_KEY")
    escalate_min_severity: str = "high"  # escalate at/above this severity
    escalate_on_unverified: bool = True  # also escalate when the VLM was inconclusive
    escalate_timeout: float = 15.0


@lru_cache
def get_reason_settings() -> ReasonSettings:
    return ReasonSettings()


settings = get_reason_settings()
