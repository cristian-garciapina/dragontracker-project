"""migrations: make power_at_migration nullable

Revision ID: 8e7d6c5b4a30
Revises: 9f2e1d3c4b5a
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa


revision = "8e7d6c5b4a30"
down_revision = "9f2e1d3c4b5a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("migrations", recreate="always") as batch:
        batch.alter_column("power_at_migration", existing_type=sa.Integer(), nullable=True)


def downgrade():
    with op.batch_alter_table("migrations", recreate="always") as batch:
        batch.alter_column("power_at_migration", existing_type=sa.Integer(), nullable=False)
