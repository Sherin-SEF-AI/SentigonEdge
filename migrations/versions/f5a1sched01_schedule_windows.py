"""schedule windows

Revision ID: f5a1sched01
Revises: e4b2dedup01
Create Date: 2026-07-15 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5a1sched01"
down_revision: str | None = "e4b2dedup01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_windows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("camera_id", sa.UUID(), nullable=True),
        sa.Column("zone_id", sa.UUID(), nullable=True),
        sa.Column("signatures", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("days_of_week", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("suppressed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_windows")),
    )
    op.create_index(op.f("ix_schedules_active"), "schedule_windows", ["active"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_schedules_active"), table_name="schedule_windows")
    op.drop_table("schedule_windows")
