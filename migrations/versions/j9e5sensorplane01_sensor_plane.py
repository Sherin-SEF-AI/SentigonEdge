"""sensor plane: generic devices + sensor events

Revision ID: j9e5sensorplane01
Revises: i8d4srvdef01
Create Date: 2026-07-17 06:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "j9e5sensorplane01"
down_revision: str | None = "i8d4srvdef01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── devices: generic non-camera signal sources ──────────────
    op.create_table(
        "devices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("zone_id", sa.UUID(), nullable=True),
        sa.Column("camera_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("device_class", sa.String(length=64), nullable=False, server_default="generic"),
        sa.Column("protocol", sa.String(length=32), nullable=False, server_default="webhook"),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("vendor", sa.String(length=128), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
    )
    op.create_index(op.f("ix_devices_external_id"), "devices", ["external_id"], unique=False)

    # ── sensor_events: normalized readings/events from devices ──
    op.create_table(
        "sensor_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=64), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=True),
        sa.Column("incident_id", sa.UUID(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sensor_events")),
        sa.UniqueConstraint("seq", name=op.f("uq_sensor_events_seq")),
    )
    op.create_index(op.f("ix_sensor_events_seq"), "sensor_events", ["seq"], unique=True)
    op.create_index(op.f("ix_sensor_events_ts"), "sensor_events", ["ts"], unique=False)
    op.create_index(
        "ix_sensor_events_device_ts", "sensor_events", ["device_id", "ts"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sensor_events_device_ts", table_name="sensor_events")
    op.drop_index(op.f("ix_sensor_events_ts"), table_name="sensor_events")
    op.drop_index(op.f("ix_sensor_events_seq"), table_name="sensor_events")
    op.drop_table("sensor_events")
    op.drop_index(op.f("ix_devices_external_id"), table_name="devices")
    op.drop_table("devices")
