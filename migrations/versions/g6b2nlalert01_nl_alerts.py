"""nl alerts

Revision ID: g6b2nlalert01
Revises: f5a1sched01
Create Date: 2026-07-15 01:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g6b2nlalert01"
down_revision: str | None = "f5a1sched01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# reference the existing severity enum type; do NOT recreate it
_SEV = postgresql.ENUM(
    "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", name="severity", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "nl_alerts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=True),
        sa.Column("severity", _SEV, nullable=False, server_default="MEDIUM"),
        sa.Column("eval_interval_s", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("cooldown_s", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("fire_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_eval_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_nl_alerts")),
    )
    op.create_index(op.f("ix_nl_alerts_active"), "nl_alerts", ["active"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_nl_alerts_active"), table_name="nl_alerts")
    op.drop_table("nl_alerts")
