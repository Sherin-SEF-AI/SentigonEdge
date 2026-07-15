"""enterprise parity: dispatch, fleet health, cross-site

Revision ID: h7c3enterprise01
Revises: g6b2nlalert01
Create Date: 2026-07-15 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h7c3enterprise01"
down_revision: str | None = "g6b2nlalert01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# reuse the existing severity enum (labels are the enum member NAMES)
_SEV = postgresql.ENUM(
    "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", name="severity", create_type=False
)
# new enum for dispatch lifecycle
_DSTATE = postgresql.ENUM(
    "PENDING", "NOTIFIED", "ACKNOWLEDGED", "RESOLVED", "ESCALATED", "EXPIRED",
    name="dispatch_state", create_type=False,
)


def upgrade() -> None:
    _DSTATE.create(op.get_bind(), checkfirst=True)

    # ── responders ──────────────────────────────────────────────
    op.create_table(
        "responders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="responder"),
        sa.Column("channels", postgresql.JSONB(), nullable=True),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_responders")),
    )

    # ── oncall_shifts ───────────────────────────────────────────
    op.create_table(
        "oncall_shifts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("responder_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("start_hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_hour", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["responder_id"], ["responders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oncall_shifts")),
    )
    op.create_index(op.f("ix_oncall_shifts_active"), "oncall_shifts", ["active"], unique=False)

    # ── dispatches ──────────────────────────────────────────────
    op.create_table(
        "dispatches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("incident_id", sa.UUID(), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=True),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("responder_id", sa.UUID(), nullable=True),
        sa.Column("severity", _SEV, nullable=False, server_default="MEDIUM"),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("signature_name", sa.String(length=255), nullable=True),
        sa.Column("sitrep", sa.Text(), nullable=True),
        sa.Column("state", _DSTATE, nullable=False, server_default="PENDING"),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sla_ack_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("sla_resolve_seconds", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_by", sa.String(length=255), nullable=True),
        sa.Column("channels_used", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["responder_id"], ["responders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dispatches")),
    )
    op.create_index(op.f("ix_dispatches_seq"), "dispatches", ["seq"], unique=True)
    op.create_index(op.f("ix_dispatches_state"), "dispatches", ["state"], unique=False)
    op.create_index(op.f("ix_dispatches_created"), "dispatches", ["created_at"], unique=False)
    op.create_index(op.f("ix_dispatches_incident"), "dispatches", ["incident_id"], unique=False)
    op.create_index(op.f("ix_dispatches_correlation_id"), "dispatches", ["correlation_id"], unique=False)

    # ── soc_shifts ──────────────────────────────────────────────
    op.create_table(
        "soc_shifts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("operator", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_soc_shifts")),
    )
    op.create_index(op.f("ix_soc_shifts_active"), "soc_shifts", ["active"], unique=False)

    # ── fleet_snapshots ─────────────────────────────────────────
    op.create_table(
        "fleet_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("cameras_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cameras_online", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("services_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("services_up", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_active", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disk_pct", sa.Float(), nullable=True),
        sa.Column("mem_pct", sa.Float(), nullable=True),
        sa.Column("gpu_pct", sa.Float(), nullable=True),
        sa.Column("load1", sa.Float(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fleet_snapshots")),
    )
    op.create_index(op.f("ix_fleet_snapshots_ts"), "fleet_snapshots", ["ts"], unique=False)

    # ── fleet_findings ──────────────────────────────────────────
    op.create_table(
        "fleet_findings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("severity", _SEV, nullable=False, server_default="MEDIUM"),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("target_name", sa.String(length=255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("metric", postgresql.JSONB(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fleet_findings")),
    )
    op.create_index(op.f("ix_fleet_findings_active"), "fleet_findings", ["active"], unique=False)
    op.create_index(
        op.f("ix_fleet_findings_target"), "fleet_findings", ["target_type", "target_id"], unique=False
    )

    # ── plate_sightings ─────────────────────────────────────────
    op.create_table(
        "plate_sightings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("plate_hash", sa.String(length=64), nullable=False),
        sa.Column("plate_text", sa.String(length=32), nullable=True),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("camera_id", sa.UUID(), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plate_sightings")),
    )
    op.create_index(op.f("ix_plate_sightings_seq"), "plate_sightings", ["seq"], unique=True)
    op.create_index(op.f("ix_plate_sightings_hash"), "plate_sightings", ["plate_hash"], unique=False)
    op.create_index(op.f("ix_plate_sightings_ts"), "plate_sightings", ["ts"], unique=False)

    # ── cross_site_links ────────────────────────────────────────
    op.create_table(
        "cross_site_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_key", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("sites", postgresql.JSONB(), nullable=True),
        sa.Column("site_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sighting_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cameras", postgresql.JSONB(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cross_site_links")),
    )
    op.create_index(op.f("ix_cross_site_links_active"), "cross_site_links", ["active"], unique=False)
    op.create_index(
        op.f("ix_cross_site_links_key"), "cross_site_links", ["entity_type", "entity_key"], unique=False
    )


def downgrade() -> None:
    op.drop_table("cross_site_links")
    op.drop_index(op.f("ix_plate_sightings_ts"), table_name="plate_sightings")
    op.drop_index(op.f("ix_plate_sightings_hash"), table_name="plate_sightings")
    op.drop_index(op.f("ix_plate_sightings_seq"), table_name="plate_sightings")
    op.drop_table("plate_sightings")
    op.drop_table("fleet_findings")
    op.drop_index(op.f("ix_fleet_snapshots_ts"), table_name="fleet_snapshots")
    op.drop_table("fleet_snapshots")
    op.drop_index(op.f("ix_soc_shifts_active"), table_name="soc_shifts")
    op.drop_table("soc_shifts")
    op.drop_table("dispatches")
    op.drop_index(op.f("ix_oncall_shifts_active"), table_name="oncall_shifts")
    op.drop_table("oncall_shifts")
    op.drop_table("responders")
    _DSTATE.drop(op.get_bind(), checkfirst=True)
