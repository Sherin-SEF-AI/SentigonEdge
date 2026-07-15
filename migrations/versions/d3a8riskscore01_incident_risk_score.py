"""incident risk score

Revision ID: d3a8riskscore01
Revises: c2f7zonesnap01
Create Date: 2026-07-14 02:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a8riskscore01"
down_revision: str | None = "c2f7zonesnap01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("risk_score", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_incidents_risk_score"), "incidents", ["risk_score"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_incidents_risk_score"), table_name="incidents")
    op.drop_column("incidents", "risk_score")
