"""add migrations table

Revision ID: 9f2e1d3c4b5a
Revises: f6a7b8c9d0e1
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa


revision = "9f2e1d3c4b5a"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "migrations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("other_kingdom", sa.Integer(), nullable=False),
        sa.Column("migration_date", sa.Date(), nullable=False),
        sa.Column("migration_score", sa.Integer(), nullable=True),
        sa.Column("power_at_migration", sa.Integer(), nullable=False),
        sa.Column("name_at_migration", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "character_id", "direction", "migration_date", "other_kingdom",
            name="uq_migration",
        ),
        sa.CheckConstraint(
            "direction IN ('IN', 'OUT')", name="ck_migration_direction"
        ),
    )
    op.create_index("ix_migration_character", "migrations", ["character_id"])
    op.create_index("ix_migration_date", "migrations", ["migration_date"])
    op.create_index("ix_migration_direction", "migrations", ["direction"])


def downgrade():
    op.drop_index("ix_migration_direction", table_name="migrations")
    op.drop_index("ix_migration_date", table_name="migrations")
    op.drop_index("ix_migration_character", table_name="migrations")
    op.drop_table("migrations")
