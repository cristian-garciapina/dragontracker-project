"""drop members.troop_tier

Revision ID: d5e6f7a8b9c0
Revises: c2d3e4f5a6b7
Create Date: 2026-08-13
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("members", recreate="always") as batch_op:
        batch_op.drop_column("troop_tier")


def downgrade() -> None:
    with op.batch_alter_table("members", recreate="always") as batch_op:
        batch_op.add_column(
            __import__("sqlalchemy").Column(
                "troop_tier",
                __import__("sqlalchemy").String(length=8),
                nullable=False,
                server_default="unknown",
            )
        )
