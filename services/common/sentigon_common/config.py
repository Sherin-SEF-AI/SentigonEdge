"""Typed settings, loaded from environment and the repo-root .env.

Host-facing defaults match docker-compose published ports. In-container services
override the *_HOST / *_URL values with in-network service names via compose env.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Known placeholder/default secrets that must never be used outside dev. The JWT
# startup guard in the API only checked length (<16), so "change-me-in-production"
# (23 chars) slipped through; this catches those by value across every service.
_PLACEHOLDER_SECRETS = {
    "",
    "changeme",
    "change-me",
    "change-me-in-production",
    "dev_service_token_change_me",
    "change-me-ack-secret",
    "changeme123",
    "sentigon_secret",
    "sentigon123",
    "minioadmin",
    "secret",
    "password",
    "admin",
}
_DEV_ENVS = {"dev", "development", "local", "test", "ci"}


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

    # MQTT sensor bridge (optional): when enabled, the API subscribes to an MQTT
    # broker and ingests messages as sensor events. Topic convention:
    # "<mqtt_topic_prefix>/<external_id>" with a JSON body ({event_type,state,...}).
    mqtt_enabled: bool = False
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_topic_prefix: str = "sentigon/sensors"
    mqtt_username: str = ""
    mqtt_password: str = ""

    # Qdrant
    qdrant_url: str = "http://localhost:6335"
    qdrant_grpc_port: int = 6336

    # MinIO / S3
    minio_endpoint: str = "localhost:9002"
    # Public/browser-facing MinIO host used ONLY to sign presigned URLs. Empty = use
    # minio_endpoint. Presigned signatures are host-bound, so a URL signed for an
    # internal host (e.g. "minio:9000") is unreachable from the operator's browser.
    minio_public_endpoint: str = ""
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

    # peer service URLs (co-located on the box; override per deployment topology)
    ingest_url: str = "http://localhost:8020"
    mediasource_url: str = "http://localhost:8055"

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
    # CORS allowlist for the browser console (comma-separated origins). Never "*"
    # outside dev — reads are authenticated but an allowlist is defence in depth.
    cors_allow_origins: str = "http://localhost:3000,http://localhost:3002"
    # bind address for service HTTP servers. Default localhost: internal services
    # must not be reachable from the LAN. Front them with an authenticated proxy.
    service_bind_host: str = "127.0.0.1"
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
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def all_buckets(self) -> list[str]:
        return [
            self.minio_bucket_recordings,
            self.minio_bucket_clips,
            self.minio_bucket_snapshots,
            self.minio_bucket_evidence,
        ]

    @model_validator(mode="after")
    def _reject_default_secrets_in_production(self) -> Settings:
        """Fail fast (in every service, not just the API) when deployed outside dev
        with placeholder/weak secrets. No-op for the dev defaults."""
        if self.app_env.strip().lower() in _DEV_ENVS:
            return self
        weak: list[str] = []
        if self.jwt_secret_key.strip().lower() in _PLACEHOLDER_SECRETS or len(self.jwt_secret_key) < 32:
            weak.append("JWT_SECRET_KEY (needs >=32 non-default chars)")
        if self.service_token.strip().lower() in _PLACEHOLDER_SECRETS or len(self.service_token) < 16:
            weak.append("SERVICE_TOKEN (needs >=16 non-default chars)")
        if self.minio_secret_key.strip().lower() in _PLACEHOLDER_SECRETS:
            weak.append("MINIO_SECRET_KEY")
        if self.anpr_salt.strip().lower() in _PLACEHOLDER_SECRETS | {"sentigon-anpr"}:
            weak.append("ANPR_SALT (plate-hash HMAC key must be secret)")
        if weak:
            raise ValueError(
                f"Refusing to start with app_env={self.app_env!r} and weak/default secrets: "
                f"{', '.join(weak)}. Set strong values in the environment."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
