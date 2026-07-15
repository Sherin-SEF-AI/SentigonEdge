"""Search settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class SearchSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="SEARCH_",
    )

    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    clip_device: str = "cpu"  # CPU keeps the GPU free for perception + VLM
    collection: str = "snapshots"
    index_interval_seconds: float = 8.0
    index_batch: int = 24


@lru_cache
def get_search_settings() -> SearchSettings:
    return SearchSettings()


settings = get_search_settings()
