"""add farlight_pull_runs table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-26 00:00:00.000000

Structured audit log for Farlight auto-pull runs. One row per
run_pull() invocation (nightly cron or manual force-pull).
"""
from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farlight_pull_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("rows_ingested", sa.Integer, nullable=True),
        sa.Column("snapshots_created", sa.Integer, nullable=True),
        sa.Column("snapshot_ids_json", sa.Text, nullable=True),
        sa.Column("summary_json", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index(
        "idx_farlight_pull_runs_started_at",
        "farlight_pull_runs",
        [sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_farlight_pull_runs_started_at", table_name="farlight_pull_runs")
    op.drop_table("farlight_pull_runs")
