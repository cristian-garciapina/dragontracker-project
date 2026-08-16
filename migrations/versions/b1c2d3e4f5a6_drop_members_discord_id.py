"""drop members.discord_id (dead column)

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-08-16

Column added in an early iteration then superseded by User.discord_id.
No code path reads or writes Member.discord_id anymore (grep confirmed).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("members", recreate="always") as bop:
        bop.drop_column("discord_id")


def downgrade() -> None:
    with op.batch_alter_table("members", recreate="always") as bop:
        bop.add_column(sa.Column("discord_id", sa.String(length=32), nullable=True))
