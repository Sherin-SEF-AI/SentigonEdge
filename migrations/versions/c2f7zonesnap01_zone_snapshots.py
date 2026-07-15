"""zone snapshots

Revision ID: c2f7zonesnap01
Revises: b1f4watchlist01
Create Date: 2026-07-14 01:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2f7zonesnap01"
down_revision: str | None = "b1f4watchlist01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zone_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("zone_id", sa.UUID(), nullable=False),
        sa.Column("camera_id", sa.UUID(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occupancy", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mask_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_dwell_s", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zones.id"],
            name=op.f("fk_zone_snapshots_zone_id_zones"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.id"],
            name=op.f("fk_zone_snapshots_camera_id_cameras"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zone_snapshots")),
    )
    op.create_index(
        op.f("ix_zone_snapshots_zone_ts"), "zone_snapshots", ["zone_id", "ts"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_zone_snapshots_zone_ts"), table_name="zone_snapshots")
    op.drop_table("zone_snapshots")
