"""add secrets table (Fernet-encrypted key/value store)

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-08-17

Stores external API secrets (Farlight JWT, etc.) encrypted at rest via
app/secrets_store.py (Fernet, EV_SECRETS_KEY in env).
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("secret_metadata", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("secrets")
