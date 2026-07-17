"""Kafka bus message contracts (pydantic v2).

One envelope per pipeline stage. Every message carries a correlation_id so an
incident can be traced end to end, and a monotonic seq where ordering matters.
Topic names are centralized in `Topics`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import AccessEventType, Severity, Verdict


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Topics:
    PERCEPTION_OBJECTS = "perception.objects"
    PERCEPTION_EMBEDDINGS = "perception.embeddings"
    EVENTS_CANDIDATE = "events.candidate"
    INCIDENTS_VERIFIED = "incidents.verified"
    INGEST_HEALTH = "ingest.health"
    ACCESS_EVENTS = "access.events"
    SENSOR_EVENTS = "sensor.events"

    ALL = [
        PERCEPTION_OBJECTS,
        PERCEPTION_EMBEDDINGS,
        EVENTS_CANDIDATE,
        INCIDENTS_VERIFIED,
        INGEST_HEALTH,
        ACCESS_EVENTS,
        SENSOR_EVENTS,
    ]


class BusMessage(BaseModel):
    """Common envelope fields carried by every bus message."""

    model_config = ConfigDict(extra="forbid")

    message_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: str | None = None
    producer: str = "unknown"
    ts: datetime = Field(default_factory=_utcnow)


# ── ingest.health ─────────────────────────────────────────────


class StreamHealthMsg(BusMessage):
    camera_id: uuid.UUID
    name: str
    status: str  # online | offline | error | connecting
    fps: float = 0.0
    target_fps: float = 0.0
    jitter_ms: float = 0.0
    decode_errors: int = 0
    reconnects: int = 0
    latency_ms: float = 0.0
    bitrate_kbps: float = 0.0
    resolution: str | None = None


# ── perception.objects ────────────────────────────────────────


class DetectedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: int
    object_class: str
    confidence: float
    bbox: list[float]  # [x, y, w, h] in pixels
    zone_hits: list[str] = Field(default_factory=list)  # zone ids the centroid falls in
    keypoints: list[list[float]] | None = None  # pose [[x,y,conf],...]
    mask: list[list[float]] | None = None  # instance-seg polygon [[x,y],...] normalized 0..1
    attributes: dict = Field(default_factory=dict)


class ObjectDetectionMsg(BusMessage):
    camera_id: uuid.UUID
    seq: int
    frame_ts: datetime
    frame_width: int
    frame_height: int
    objects: list[DetectedObject] = Field(default_factory=list)
    inference_ms: float = 0.0


# ── perception.embeddings ─────────────────────────────────────


class EmbeddingMsg(BusMessage):
    camera_id: uuid.UUID
    track_id: int
    object_class: str
    embedding: list[float]
    model: str
    frame_ts: datetime
    snapshot_ref: str | None = None


# ── events.candidate ──────────────────────────────────────────


class CandidateEventMsg(BusMessage):
    camera_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    signature_name: str
    event_type: str
    severity: Severity
    confidence: float
    ts: datetime
    object_refs: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)  # full context snapshot
    preroll_ref: str | None = None
    snapshot_ref: str | None = None


# ── incidents.verified ────────────────────────────────────────


class VerifiedIncidentMsg(BusMessage):
    incident_id: uuid.UUID
    camera_id: uuid.UUID
    signature_name: str
    severity: Severity
    verdict: Verdict
    sitrep: str | None = None
    reasoning_trace: dict = Field(default_factory=dict)
    attributes: dict = Field(default_factory=dict)
    clip_ref: str | None = None
    snapshot_ref: str | None = None


# ── access.events ─────────────────────────────────────────────


class AccessEventMsg(BusMessage):
    panel_id: str | None = None
    door_id: str | None = None
    event_type: AccessEventType
    ts: datetime
    badge_id: str | None = None
    camera_id: uuid.UUID | None = None
    raw: dict = Field(default_factory=dict)


# ── sensor.events (generic non-camera signal plane) ───────────


class SensorEventMsg(BusMessage):
    """A normalized reading/event from any non-camera Device. Carries the device's
    class + optional camera/zone bindings so the context engine can fuse it with
    live detections and trip sensor signatures."""

    device_id: uuid.UUID
    external_id: str | None = None
    device_class: str = "generic"
    site_id: uuid.UUID | None = None
    zone_id: uuid.UUID | None = None
    camera_id: uuid.UUID | None = None
    event_type: str
    ts: datetime
    value: float | None = None
    unit: str | None = None
    state: str | None = None
    severity: str | None = None
    raw: dict = Field(default_factory=dict)
