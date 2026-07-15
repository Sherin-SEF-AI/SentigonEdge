"""incident dedup grouping

Revision ID: e4b2dedup01
Revises: d3a8riskscore01
Create Date: 2026-07-14 03:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b2dedup01"
down_revision: str | None = "d3a8riskscore01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("incidents", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_incidents_dedup",
        "incidents",
        ["signature_id", "camera_id", "zone_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_dedup", table_name="incidents")
    op.drop_column("incidents", "last_seen_at")
    op.drop_column("incidents", "occurrence_count")
