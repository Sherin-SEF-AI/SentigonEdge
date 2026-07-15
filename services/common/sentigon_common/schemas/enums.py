"""Shared string enums. Used by both the ORM (Enum columns) and pydantic schemas."""

from __future__ import annotations

import enum


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IncidentStatus(str, enum.Enum):
    NEW = "new"
    ACK = "ack"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class Verdict(str, enum.Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


class CameraStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class ZoneType(str, enum.Enum):
    ENTRY = "entry"
    RESTRICTED = "restricted"
    PERIMETER = "perimeter"
    PARKING = "parking"
    LOADING_DOCK = "loading_dock"
    PRODUCTION_FLOOR = "production_floor"
    EXCLUSION = "exclusion"
    GENERAL = "general"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    INVESTIGATOR = "investigator"
    OPERATOR = "operator"
    VIEWER = "viewer"


class CaseStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    CLOSED = "closed"
    ARCHIVED = "archived"


class DetectionMethod(str, enum.Enum):
    YOLO = "yolo"
    VLM = "vlm"
    HYBRID = "hybrid"
    GEOMETRIC = "geometric"
    POSE = "pose"
    AUDIO = "audio"
    OPEN_VOCAB = "open_vocab"


class AccessEventType(str, enum.Enum):
    ACCESS_GRANTED = "access_granted"
    ACCESS_DENIED = "access_denied"
    DOOR_FORCED = "door_forced"
    DOOR_HELD = "door_held"
    ZONE_TRIP = "zone_trip"


class RecordingType(str, enum.Enum):
    CONTINUOUS = "continuous"
    EVENT = "event"
    PREROLL = "preroll"
    MANUAL = "manual"


class ModelRole(str, enum.Enum):
    DETECTOR = "detector"
    POSE = "pose"
    REID = "reid"
    VLM = "vlm"
    WEAPON = "weapon"


class ModelStage(str, enum.Enum):
    CHAMPION = "champion"
    CHALLENGER = "challenger"
    SHADOW = "shadow"
    RETIRED = "retired"
