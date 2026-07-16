"""core table server_defaults

The initial migration created several NOT NULL columns with only a Python-side ORM
`default=` and no `server_default`, so any non-ORM insert (psql, bulk COPY, a DBA
fix-up, another service/language) violated NOT NULL. The later feature migrations all
set server_defaults; this back-fills the core tables to match.

Revision ID: i8d4srvdef01
Revises: h7c3enterprise01
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "i8d4srvdef01"
down_revision: str | None = "h7c3enterprise01"
branch_labels = None
depends_on = None

# (table, column, server_default) — mirrors the ORM client-side defaults.
_DEFAULTS = {
    "signatures": [
        ("cooldown_seconds", sa.text("30")),
        ("enabled", sa.text("true")),
        ("source", sa.text("'built_in'")),
        ("version", sa.text("1")),
        ("detection_count", sa.text("0")),
    ],
    "cameras": [
        ("fps", sa.text("15")),
        ("ptz_capable", sa.text("false")),
        ("is_active", sa.text("true")),
    ],
    "zones": [
        ("is_active", sa.text("true")),
    ],
}


def upgrade() -> None:
    for table, cols in _DEFAULTS.items():
        for col, default in cols:
            op.alter_column(table, col, server_default=default)


def downgrade() -> None:
    for table, cols in _DEFAULTS.items():
        for col, _default in cols:
            op.alter_column(table, col, server_default=None)
