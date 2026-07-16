"""Sentigon V2 core ontology (Section 4), SQLAlchemy 2.0 style.

The governed core: Site/Building/Zone/Camera/Signature and the runtime chain
TrackedObject -> Event -> Incident -> Case, plus AccessEvent, EvidenceRecord
(tamper-evident vault), and ModelVersion/EvalRun (champion-challenger governance).
Domain-specific tables (PACS, alarm panels, vehicle analytics, ...) are added by
later phases as additional migrations, ported from Sentigon V1.

All UUID primary keys, all timestamps timezone-aware UTC. Objects that flow on the
bus carry a BigInteger Identity `seq` for stable monotonic ordering.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..schemas.enums import (
    AccessEventType,
    CameraStatus,
    CaseStatus,
    DetectionMethod,
    DispatchState,
    IncidentStatus,
    ModelRole,
    ModelStage,
    RecordingType,
    Severity,
    UserRole,
    Verdict,
    ZoneType,
)
from .base import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _seq() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(), unique=True, index=True, nullable=False)


# ── Facility hierarchy ────────────────────────────────────────


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    center: Mapped[dict | None] = mapped_column(JSONB)  # {lat, lng} for the map view
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()

    buildings: Mapped[list[Building]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )
    zones: Mapped[list[Zone]] = relationship(back_populates="site")
    cameras: Mapped[list[Camera]] = relationship(back_populates="site")


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[uuid.UUID] = _pk()
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    floors: Mapped[int] = mapped_column(Integer, default=1)
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()

    site: Mapped[Site] = relationship(back_populates="buildings")
    zones: Mapped[list[Zone]] = relationship(back_populates="building")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = _pk()
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    building_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("buildings.id", ondelete="SET NULL")
    )
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    zone_type: Mapped[ZoneType] = mapped_column(
        Enum(ZoneType, name="zone_type"), default=ZoneType.GENERAL, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    polygon_image: Mapped[dict | None] = mapped_column(JSONB)  # [[x,y],...] in camera-image space
    polygon_map: Mapped[dict | None] = mapped_column(JSONB)  # GeoJSON in site-map space
    max_occupancy: Mapped[int | None] = mapped_column(Integer)
    schedule: Mapped[dict | None] = mapped_column(JSONB)  # active time windows
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()

    site: Mapped[Site] = relationship(back_populates="zones")
    building: Mapped[Building | None] = relationship(back_populates="zones")
    camera: Mapped[Camera | None] = relationship(back_populates="zones")
    events: Mapped[list[Event]] = relationship(back_populates="zone")


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = _pk()
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    building_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("buildings.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    rtsp_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    onvif_uri: Mapped[str | None] = mapped_column(String(1024))
    credentials_ref: Mapped[str | None] = mapped_column(
        String(255)
    )  # secret store key, never the secret
    codec: Mapped[str | None] = mapped_column(String(32))
    resolution: Mapped[str | None] = mapped_column(String(20))
    fps: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    ptz_capable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    homography: Mapped[dict | None] = mapped_column(JSONB)  # image-to-map calibration
    status: Mapped[CameraStatus] = mapped_column(
        Enum(CameraStatus, name="camera_status"), default=CameraStatus.OFFLINE, nullable=False
    )
    health: Mapped[dict | None] = mapped_column(
        JSONB, default=dict
    )  # {fps, jitter, decode_errors, reconnects, latency_ms}
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    site: Mapped[Site | None] = relationship(back_populates="cameras")
    zones: Mapped[list[Zone]] = relationship(back_populates="camera")
    events: Mapped[list[Event]] = relationship(back_populates="camera")
    recordings: Mapped[list[Recording]] = relationship(back_populates="camera")
    tracked_objects: Mapped[list[TrackedObject]] = relationship(back_populates="camera")


# ── Signatures ────────────────────────────────────────────────


class Signature(Base):
    __tablename__ = "signatures"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.MEDIUM, nullable=False
    )
    detection_method: Mapped[DetectionMethod] = mapped_column(
        Enum(DetectionMethod, name="detection_method"),
        default=DetectionMethod.HYBRID,
        nullable=False,
    )
    # params holds the detector wiring: {yolo_classes, vlm_prompt, keywords, thresholds, dwell_seconds, ...}
    params: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    zone_scope: Mapped[dict | None] = mapped_column(JSONB)  # zone ids / zone types this applies to
    schedule: Mapped[dict | None] = mapped_column(JSONB)  # active windows
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(
        String(50), default="built_in", nullable=False
    )  # built_in | auto_learned | custom
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    detection_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    events: Mapped[list[Event]] = relationship(back_populates="signature")
    incidents: Mapped[list[Incident]] = relationship(back_populates="signature")


# ── Runtime chain ─────────────────────────────────────────────


class TrackedObject(Base):
    __tablename__ = "tracked_objects"
    __table_args__ = (Index("ix_tracked_objects_camera_track", "camera_id", "track_id"),)

    id: Mapped[uuid.UUID] = _pk()
    camera_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)  # tracker-assigned, per camera
    object_class: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trajectory: Mapped[dict | None] = mapped_column(JSONB)  # [[ts,x,y],...] centroid history
    embedding_id: Mapped[str | None] = mapped_column(
        String(128)
    )  # Qdrant point id (ReID appearance)
    attributes: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()

    camera: Mapped[Camera] = relationship(back_populates="tracked_objects")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_camera_ts", "camera_id", "ts"),
        Index("ix_events_type_ts", "event_type", "ts"),
    )

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = _seq()
    signature_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("signatures.id", ondelete="SET NULL")
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.INFO, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    object_refs: Mapped[dict | None] = mapped_column(JSONB)  # tracked-object ids + bbox refs
    snapshot_ref: Mapped[str | None] = mapped_column(String(512))
    clip_ref: Mapped[str | None] = mapped_column(String(512))
    context: Mapped[dict | None] = mapped_column(JSONB)  # full context snapshot that produced it
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _created()

    signature: Mapped[Signature | None] = relationship(back_populates="events")
    camera: Mapped[Camera] = relationship(back_populates="events")
    zone: Mapped[Zone | None] = relationship(back_populates="events")
    incidents: Mapped[list[Incident]] = relationship(back_populates="event")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status_severity", "status", "severity"),
        Index("ix_incidents_created", "created_at"),
        # dedup/grouping hot path (created in migration e4b2dedup01). Declared here
        # too so `alembic revision --autogenerate` does not try to drop it.
        Index("ix_incidents_dedup", "signature_id", "camera_id", "zone_id", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = _seq()
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    signature_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("signatures.id", ondelete="SET NULL")
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.MEDIUM, nullable=False
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, name="incident_status"),
        default=IncidentStatus.NEW,
        nullable=False,
        index=True,
    )
    verdict: Mapped[Verdict | None] = mapped_column(Enum(Verdict, name="verdict"))
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    risk_score: Mapped[int | None] = mapped_column(Integer, index=True)  # composite 0..100 threat score
    sitrep: Mapped[str | None] = mapped_column(Text)  # VLM natural-language SITREP
    reasoning_trace: Mapped[dict | None] = mapped_column(JSONB)  # VLM reasoning steps
    attributes: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    snapshot_ref: Mapped[str | None] = mapped_column(String(512))
    clip_ref: Mapped[str | None] = mapped_column(String(512))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # dedup/grouping: repeated same-signature/camera/zone detections roll up into
    # this one evolving incident instead of flooding the queue and the VLM.
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    signature: Mapped[Signature | None] = relationship(back_populates="incidents")
    event: Mapped[Event | None] = relationship(back_populates="incidents")
    status_logs: Mapped[list[IncidentStatusLog]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    cases: Mapped[list[Case]] = relationship(secondary="case_incidents", back_populates="incidents")


class IncidentStatusLog(Base):
    __tablename__ = "incident_status_logs"

    id: Mapped[uuid.UUID] = _pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = _created()

    incident: Mapped[Incident] = relationship(back_populates="status_logs")


# ── Cases / investigations ────────────────────────────────────

case_incidents = Table(
    "case_incidents",
    Base.metadata,
    Column(
        "case_id", UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "incident_id",
        UUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, name="case_status"), default=CaseStatus.OPEN, nullable=False
    )
    priority: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.MEDIUM, nullable=False
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    tags: Mapped[dict | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    incidents: Mapped[list[Incident]] = relationship(
        secondary="case_incidents", back_populates="cases"
    )
    evidence: Mapped[list[CaseEvidence]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class CaseEvidence(Base):
    __tablename__ = "case_evidence"

    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # event | incident | clip | snapshot | note | file
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    storage_ref: Mapped[str | None] = mapped_column(String(512))
    evidence_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_records.id", ondelete="SET NULL")
    )
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    added_at: Mapped[datetime] = _created()

    case: Mapped[Case] = relationship(back_populates="evidence")


# ── Access control / alarm fusion ─────────────────────────────


class AccessEvent(Base):
    __tablename__ = "access_events"
    __table_args__ = (Index("ix_access_events_ts", "ts"),)

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = _seq()
    panel_id: Mapped[str | None] = mapped_column(String(128))
    door_id: Mapped[str | None] = mapped_column(String(128))
    event_type: Mapped[AccessEventType] = mapped_column(
        Enum(AccessEventType, name="access_event_type"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    badge_id: Mapped[str | None] = mapped_column(String(128))
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL")
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL")
    )
    raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


# ── Recording / storage ───────────────────────────────────────


class Recording(Base):
    __tablename__ = "recordings"
    __table_args__ = (Index("ix_recordings_camera_start", "camera_id", "start_time"),)

    id: Mapped[uuid.UUID] = _pk()
    camera_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    recording_type: Mapped[RecordingType] = mapped_column(
        Enum(RecordingType, name="recording_type"), default=RecordingType.CONTINUOUS, nullable=False
    )
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)  # MinIO key
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()

    camera: Mapped[Camera] = relationship(back_populates="recordings")


# ── Identity / audit ──────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.VIEWER, nullable=False
    )
    site_scope: Mapped[dict | None] = mapped_column(
        JSONB
    )  # site ids this user may access (multi-tenant RBAC)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AuditLogEntry(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_resource_ts", "resource_type", "ts"),)

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = _created()


# ── Tamper-evident evidence vault ─────────────────────────────


class EvidenceRecord(Base):
    """Append-only, hash-chained ledger. Each row's prev_hash points at the prior
    row's content_hash, so exported evidence integrity is verifiable end to end."""

    __tablename__ = "evidence_records"

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = _seq()
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # sha256 hex
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # clip | snapshot | export | incident
    bucket: Mapped[str | None] = mapped_column(String(128))
    object_key: Mapped[str | None] = mapped_column(String(1024))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


# ── Model governance ──────────────────────────────────────────


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (Index("ix_model_versions_role_stage", "role", "stage"),)

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[ModelRole] = mapped_column(Enum(ModelRole, name="model_role"), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[ModelStage] = mapped_column(
        Enum(ModelStage, name="model_stage"), default=ModelStage.CHALLENGER, nullable=False
    )
    artifact_ref: Mapped[str | None] = mapped_column(String(1024))  # Triton repo path or HF id
    params: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    eval_runs: Mapped[list[EvalRun]] = relationship(
        back_populates="model_version", cascade="all, delete-orphan"
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _pk()
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE")
    )
    gold_set: Mapped[str] = mapped_column(String(255), nullable=False)
    metrics: Mapped[dict | None] = mapped_column(
        JSONB
    )  # {per_signature: {precision, recall, fp_rate}, ...}
    passed: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    model_version: Mapped[ModelVersion | None] = relationship(back_populates="eval_runs")


# ── Natural-language activity notifications (VLM-evaluated) ───


class NLAlert(Base):
    """An operator-defined alert in plain English ('a person on a ladder near the
    server racks'). A VLM evaluates the prompt against live camera frames and fires
    an incident on a match. Open-set detection without authoring a signature."""

    __tablename__ = "nl_alerts"
    __table_args__ = (Index("ix_nl_alerts_active", "active"),)

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)  # the NL condition
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE")
    )
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.MEDIUM, nullable=False
    )
    eval_interval_s: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    cooldown_s: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fire_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_eval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


# ── Schedules (roster/delivery-aware alarm suppression) ───────


class ScheduleWindow(Base):
    """An expected-activity window: during it, matching signatures on the scoped
    camera/zone are suppressed (a scheduled 2pm dock delivery does not alarm).
    Times are minutes-since-midnight in the site timezone."""

    __tablename__ = "schedule_windows"
    __table_args__ = (Index("ix_schedules_active", "active"),)

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE")
    )  # null = all cameras
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("zones.id", ondelete="CASCADE")
    )  # null = all zones
    signatures: Mapped[list | None] = mapped_column(JSONB, default=list)  # names; [] = all
    days_of_week: Mapped[list | None] = mapped_column(JSONB, default=list)  # 0=Mon..6=Sun; [] = all
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)  # minutes since local midnight
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    suppressed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = _created()


# ── Zone analytics (segmentation-based occupancy/density) ─────


class ZoneSnapshot(Base):
    """A periodic per-zone metric sample: occupancy (person count), segmentation
    mask-area coverage of the zone (density, 0..1), and rolling average dwell. The
    time-series behind occupancy/density/dwell analytics."""

    __tablename__ = "zone_snapshots"
    __table_args__ = (Index("ix_zone_snapshots_zone_ts", "zone_id", "ts"),)

    id: Mapped[uuid.UUID] = _pk()
    zone_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("zones.id", ondelete="CASCADE"), nullable=False
    )
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL")
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occupancy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mask_coverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_dwell_s: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = _created()


# ── Watchlists (appearance-based BOLO) ────────────────────────


class WatchlistEntry(Base):
    """A be-on-the-lookout entry: one reference appearance embedding (stored in the
    Qdrant `watchlist` collection, keyed by embedding_ref) that live detections are
    matched against. Enrolled from a real captured track, never synthetic."""

    __tablename__ = "watchlist_entries"
    __table_args__ = (Index("ix_watchlist_active", "active"),)

    id: Mapped[uuid.UUID] = _pk()
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # person | vehicle
    object_class: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[float] = mapped_column(Float, default=0.82, nullable=False)
    embedding_ref: Mapped[str] = mapped_column(String(64), nullable=False)  # qdrant point id
    enrolled_from: Mapped[str | None] = mapped_column(String(128))  # camera_id:track_id
    appearances: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = _created()


# ══════════════════════════════════════════════════════════════════════════════
# Enterprise-parity phase: managed-SOC dispatch, fleet health, cross-site intel.
# Added as additive tables (no changes to the governed core above).
# ══════════════════════════════════════════════════════════════════════════════


class Responder(Base):
    """A field/security responder who can be dispatched to a confirmed incident.
    Channels holds real delivery targets ({"email":..,"webhook":..,"webpush":true,
    "sms":..}). site_id None = eligible for all sites."""

    __tablename__ = "responders"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(64), default="responder", nullable=False)
    channels: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()


class OncallShift(Base):
    """On-call rotation window. Resolves which responder(s) are on-call for a site at
    a given local hour. weekday None = every day; site_id None = all sites; tier drives
    escalation order (tier 1 notified first)."""

    __tablename__ = "oncall_shifts"
    __table_args__ = (Index("ix_oncall_shifts_active", "active"),)

    id: Mapped[uuid.UUID] = _pk()
    responder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("responders.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    weekday: Mapped[int | None] = mapped_column(Integer)  # 0=Mon..6=Sun, None=any
    start_hour: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0..23
    end_hour: Mapped[int] = mapped_column(Integer, default=24, nullable=False)  # 1..24
    tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()


class Dispatch(Base):
    """A responder dispatch created from a confirmed incident, with SLA tracking."""

    __tablename__ = "dispatches"
    __table_args__ = (
        Index("ix_dispatches_state", "state"),
        Index("ix_dispatches_created", "created_at"),
        Index("ix_dispatches_incident", "incident_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = _seq()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    camera_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cameras.id", ondelete="SET NULL"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    responder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("responders.id", ondelete="SET NULL")
    )
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.MEDIUM, nullable=False
    )
    risk_score: Mapped[int | None] = mapped_column(Integer)
    signature_name: Mapped[str | None] = mapped_column(String(255))
    sitrep: Mapped[str | None] = mapped_column(Text)
    state: Mapped[DispatchState] = mapped_column(
        Enum(DispatchState, name="dispatch_state"),
        default=DispatchState.PENDING,
        nullable=False,
        index=True,
    )
    tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sla_ack_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    sla_resolve_seconds: Mapped[int] = mapped_column(Integer, default=1800, nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_by: Mapped[str | None] = mapped_column(String(255))
    channels_used: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class SocShift(Base):
    """Operator monitoring shift (check-in / check-out) for the SOC console."""

    __tablename__ = "soc_shifts"
    __table_args__ = (Index("ix_soc_shifts_active", "active"),)

    id: Mapped[uuid.UUID] = _pk()
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = _created()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class FleetSnapshot(Base):
    """A periodic fleet-wide health rollup (cameras + services + host)."""

    __tablename__ = "fleet_snapshots"
    __table_args__ = (Index("ix_fleet_snapshots_ts", "ts"),)

    id: Mapped[uuid.UUID] = _pk()
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cameras_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cameras_online: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    services_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    services_up: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    findings_active: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disk_pct: Mapped[float | None] = mapped_column(Float)
    mem_pct: Mapped[float | None] = mapped_column(Float)
    gpu_pct: Mapped[float | None] = mapped_column(Float)
    load1: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class FleetFinding(Base):
    """A diagnostics finding raised by the fleet health engine. Deduped on
    (kind, target_id): re-raising updates last_seen_at; clearing sets resolved_at."""

    __tablename__ = "fleet_findings"
    __table_args__ = (
        Index("ix_fleet_findings_active", "active"),
        Index("ix_fleet_findings_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.MEDIUM, nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # camera | service | host
    target_id: Mapped[str | None] = mapped_column(String(128))
    target_name: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class PlateSighting(Base):
    """A license-plate reading tied to a site+camera. The salted plate_hash is a
    deterministic, site-agnostic identity key used for cross-site vehicle linking."""

    __tablename__ = "plate_sightings"
    __table_args__ = (
        Index("ix_plate_sightings_hash", "plate_hash"),
        Index("ix_plate_sightings_ts", "ts"),
    )

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = _seq()
    plate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plate_text: Mapped[str | None] = mapped_column(String(32))
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    camera_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cameras.id", ondelete="SET NULL"))
    track_id: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created()


class CrossSiteLink(Base):
    """The same entity (a plate hash or an appearance/ReID vector) observed at two or
    more distinct sites. entity_type = plate | appearance."""

    __tablename__ = "cross_site_links"
    __table_args__ = (
        Index("ix_cross_site_links_active", "active"),
        Index("ix_cross_site_links_key", "entity_type", "entity_key"),
    )

    id: Mapped[uuid.UUID] = _pk()
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # plate | appearance
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    sites: Mapped[list | None] = mapped_column(JSONB, default=list)  # [site_id, ...]
    site_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sighting_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cameras: Mapped[list | None] = mapped_column(JSONB, default=list)
    score: Mapped[float | None] = mapped_column(Float)  # similarity for appearance links
    detail: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


__all__ = [
    "Base",
    "Site",
    "Building",
    "Zone",
    "Camera",
    "Signature",
    "TrackedObject",
    "Event",
    "Incident",
    "IncidentStatusLog",
    "Case",
    "CaseEvidence",
    "case_incidents",
    "AccessEvent",
    "Recording",
    "User",
    "AuditLogEntry",
    "EvidenceRecord",
    "ModelVersion",
    "EvalRun",
    "WatchlistEntry",
    "ZoneSnapshot",
    "ScheduleWindow",
    "NLAlert",
    "Responder",
    "OncallShift",
    "Dispatch",
    "SocShift",
    "FleetSnapshot",
    "FleetFinding",
    "PlateSighting",
    "CrossSiteLink",
]
