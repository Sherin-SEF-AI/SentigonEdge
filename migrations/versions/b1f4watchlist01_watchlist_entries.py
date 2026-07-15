"""watchlist entries

Revision ID: b1f4watchlist01
Revises: abc0b050fb65
Create Date: 2026-07-14 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1f4watchlist01"
down_revision: str | None = "abc0b050fb65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("object_class", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=False, server_default="0.82"),
        sa.Column("embedding_ref", sa.String(length=64), nullable=False),
        sa.Column("enrolled_from", sa.String(length=128), nullable=True),
        sa.Column("appearances", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("added_by", sa.UUID(), nullable=True),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], name=op.f("fk_watchlist_entries_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlist_entries")),
    )
    op.create_index(op.f("ix_watchlist_active"), "watchlist_entries", ["active"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_watchlist_active"), table_name="watchlist_entries")
    op.drop_table("watchlist_entries")
