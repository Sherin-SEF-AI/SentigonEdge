"""Typed settings, loaded from environment and the repo-root .env.

Host-facing defaults match docker-compose published ports. In-container services
override the *_HOST / *_URL values with in-network service names via compose env.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    log_level: str = "info"
    log_json: bool = True
    service_name: str = "sentigon"

    # Database
    database_url: str = "postgresql+asyncpg://sentigon:sentigon_secret@localhost:5433/sentigon"
    database_url_sync: str = (
        "postgresql+psycopg2://sentigon:sentigon_secret@localhost:5433/sentigon"
    )

    # Redis
    redis_url: str = "redis://localhost:6380/0"

    # Kafka / Redpanda
    kafka_bootstrap: str = "localhost:19093"
    kafka_client_id: str = "sentigon"

    # Qdrant
    qdrant_url: str = "http://localhost:6335"
    qdrant_grpc_port: int = 6336

    # MinIO / S3
    minio_endpoint: str = "localhost:9002"
    minio_access_key: str = "sentigon"
    minio_secret_key: str = "sentigon_secret"
    minio_secure: bool = False
    minio_bucket_recordings: str = "recordings"
    minio_bucket_clips: str = "clips"
    minio_bucket_snapshots: str = "snapshots"
    minio_bucket_evidence: str = "evidence"

    # MediaMTX relay
    mediamtx_api: str = "http://localhost:9997"
    mediamtx_rtsp: str = "rtsp://localhost:8554"
    mediamtx_webrtc: str = "http://localhost:8889"
    mediamtx_hls: str = "http://localhost:8888"

    # Auth
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480
    # SSO: OIDC issuer (a real IdP, e.g. Keycloak). When set, bearer tokens signed
    # by this issuer are accepted and JIT-provision a Sentigon user by email.
    oidc_issuer: str = "http://localhost:8083/realms/sentigon"
    default_admin_email: str = "admin@sentigon.local"
    default_admin_password: str = ""
    # shared secret for internal service-to-service writes (X-Service-Token)
    service_token: str = "dev_service_token_change_me"
    # salt for hashing number plates (personal data): the same salt must be used by
    # the reader (perception) and the enroller (api) so hashes match. Override in prod.
    anpr_salt: str = "sentigon-anpr"
    # face blur for privacy-preserving export
    face_model: str = "models/face/yolov11n_face.pt"
    face_conf: float = 0.35
    face_device: str = "cuda"
    # live audio talk-down (TTS + delivery to the site speaker / dev stand-in)
    talkdown_voice: str = "models/tts/en_US-amy-low.onnx"
    talkdown_sink_url: str = "http://localhost:8099/play"

    # Model serving (config-pluggable: 8B local / 32B RunPod)
    reason_backend: str = "vllm"
    reason_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    reason_endpoint: str = "http://localhost:8050/v1"
    triton_url: str = "localhost:8001"

    @property
    def all_buckets(self) -> list[str]:
        return [
            self.minio_bucket_recordings,
            self.minio_bucket_clips,
            self.minio_bucket_snapshots,
            self.minio_bucket_evidence,
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
