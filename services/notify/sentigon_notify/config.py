"""Notify settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class NotifySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="NOTIFY_",
    )

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    email_from: str = "alerts@sentigon.local"
    email_to: str = "soc@sentigon.local"
    webhook_url: str = "http://localhost:8028"
    min_severity: str = "medium"
    only_confirmed: bool = True

    # ── escalation + on-call ──────────────────────────────────
    # Unacknowledged confirmed incidents escalate up these levels after each
    # delay (seconds since creation), with a real re-notification at each level.
    escalation_check_seconds: float = 10.0
    escalation_l1_after: float = 60.0   # level 1: primary on-call
    escalation_l2_after: float = 180.0  # level 2: secondary / manager
    escalation_max_age_seconds: float = 3600.0  # do not escalate incidents older than this
    # On-call roster: comma-separated "start_hour-end_hour:contact" windows; the
    # contact whose window covers the current local hour is paged. Real routing.
    oncall_schedule: str = "0-8:night-soc@sentigon.local,8-20:day-soc@sentigon.local,20-24:night-soc@sentigon.local"
    ack_secret: str = "change-me-ack-secret"
    api_url: str = "http://localhost:8010"

    # real adapters, unconfigured until credentials supplied (never fake-send)
    sms_provider: str = ""
    # Web push (VAPID). The private key is a base64-encoded PKCS8 PEM; the public
    # key is the base64url application-server key the browser subscribes with.
    webpush_vapid_key: str = ""  # private (b64 PEM)
    webpush_public_key: str = ""  # public (base64url point)
    webpush_subject: str = "mailto:soc@sentigon.local"
    webpush_subs_file: str = str(_REPO_ROOT / "media" / "push_subs.json")


@lru_cache
def get_notify_settings() -> NotifySettings:
    return NotifySettings()


settings = get_notify_settings()
