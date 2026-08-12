"""add staff_events audit table

Revision ID: c2d3e4f5a6b7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-12

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("entity_ref", sa.String(64), nullable=True),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("detail", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_staff_events_lookup",
        "staff_events",
        ["entity_type", "entity_id", "at"],
    )


def downgrade() -> None:
    op.drop_index("ix_staff_events_lookup", table_name="staff_events")
    op.drop_table("staff_events")
