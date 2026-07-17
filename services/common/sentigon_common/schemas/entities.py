"""API-facing entity schemas (pydantic v2). `from_attributes` lets these validate
directly off ORM instances.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..redact import redact_url_credentials
from .enums import (
    CameraStatus,
    CaseStatus,
    DetectionMethod,
    IncidentStatus,
    Severity,
    UserRole,
    Verdict,
    ZoneType,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SiteOut(ORMModel):
    id: uuid.UUID
    name: str
    address: str | None = None
    timezone: str = "UTC"
    center: dict | None = None
    created_at: datetime


class ZoneOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID
    building_id: uuid.UUID | None = None
    camera_id: uuid.UUID | None = None
    name: str
    zone_type: ZoneType
    polygon_image: dict | None = None
    polygon_map: dict | None = None
    max_occupancy: int | None = None
    is_active: bool = True


class CameraCreate(BaseModel):
    name: str
    rtsp_uri: str
    onvif_uri: str | None = None
    site_id: uuid.UUID | None = None
    fps: int = 15
    resolution: str | None = None
    ptz_capable: bool = False
    meta: dict = Field(default_factory=dict)


class CameraOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID | None = None
    name: str
    rtsp_uri: str
    onvif_uri: str | None = None
    codec: str | None = None
    resolution: str | None = None
    fps: int
    ptz_capable: bool
    status: CameraStatus
    health: dict | None = None
    last_seen: datetime | None = None
    is_active: bool

    # Never ship camera credentials to the browser: rtsp://user:pass@host -> ***@host.
    # (This is the response schema only; internal code uses the raw ORM value.)
    @field_validator("rtsp_uri", "onvif_uri")
    @classmethod
    def _redact_credentials(cls, v: str | None) -> str | None:
        return redact_url_credentials(v)


class DeviceCreate(BaseModel):
    name: str
    device_class: str = "generic"  # door_contact | motion_pir | environmental | ...
    protocol: str = "webhook"  # webhook | mqtt | http
    external_id: str | None = None
    vendor: str | None = None
    site_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    camera_id: uuid.UUID | None = None
    config: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)


class DeviceUpdate(BaseModel):
    name: str | None = None
    device_class: str | None = None
    protocol: str | None = None
    external_id: str | None = None
    vendor: str | None = None
    site_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    camera_id: uuid.UUID | None = None
    config: dict | None = None
    is_active: bool | None = None


class DeviceOut(ORMModel):
    id: uuid.UUID
    site_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    camera_id: uuid.UUID | None = None
    name: str
    device_class: str
    protocol: str
    external_id: str | None = None
    vendor: str | None = None
    config: dict | None = None
    status: str
    last_seen: datetime | None = None
    is_active: bool
    created_at: datetime


class SensorEventOut(ORMModel):
    id: uuid.UUID
    seq: int
    device_id: uuid.UUID
    site_id: uuid.UUID | None = None
    event_type: str
    ts: datetime
    value: float | None = None
    unit: str | None = None
    state: str | None = None
    severity: str | None = None
    incident_id: uuid.UUID | None = None


class SignatureOut(ORMModel):
    id: uuid.UUID
    name: str
    category: str
    description: str | None = None
    severity: Severity
    detection_method: DetectionMethod
    params: dict | None = None
    cooldown_seconds: int
    enabled: bool
    source: str
    version: int
    detection_count: int


class EventOut(ORMModel):
    id: uuid.UUID
    seq: int
    signature_id: uuid.UUID | None = None
    camera_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    event_type: str
    ts: datetime
    severity: Severity
    confidence: float
    snapshot_ref: str | None = None
    clip_ref: str | None = None


class IncidentOut(ORMModel):
    id: uuid.UUID
    seq: int
    camera_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    title: str
    severity: Severity
    status: IncidentStatus
    verdict: Verdict | None = None
    confidence: float
    sitrep: str | None = None
    snapshot_ref: str | None = None
    clip_ref: str | None = None
    created_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None


class CaseOut(ORMModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    status: CaseStatus
    priority: Severity
    created_at: datetime


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
