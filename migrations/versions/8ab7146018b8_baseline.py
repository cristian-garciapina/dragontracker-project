"""baseline

Revision ID: 8ab7146018b8
Revises:
Create Date: 2026-08-09 18:16:47.916788

Aligns production schema with the ORM models:
- applications.reference: add UNIQUE constraint
- event_participations: add UNIQUE (event_id, character_id)
- password_reset_tokens.token: add UNIQUE constraint
- events.date_start / date_end: enforce NOT NULL
- Rename a few indexes to match model names

Faux-positifs autogenerate SQLite (PK NOT NULL, FK re-emit) omitted.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8ab7146018b8"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_applications_created"))
        batch_op.drop_index(
            batch_op.f("ix_applications_reference"),
            sqlite_where=sa.text("reference IS NOT NULL"),
        )
        batch_op.drop_index(batch_op.f("ix_applications_status"))
        batch_op.create_index("ix_app_created", ["created_at"], unique=False)
        batch_op.create_index("ix_app_status", ["status"], unique=False)
        batch_op.create_unique_constraint("uq_applications_reference", ["reference"])

    with op.batch_alter_table("event_participations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_participations_char"))
        batch_op.drop_index(batch_op.f("ix_participations_event"))
        batch_op.create_unique_constraint(
            "uq_event_char", ["event_id", "character_id"]
        )

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.alter_column("date_start", existing_type=sa.DATE(), nullable=False)
        batch_op.alter_column("date_end", existing_type=sa.DATE(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_events_date"))
        batch_op.drop_index(batch_op.f("ix_events_season"))

    with op.batch_alter_table("password_reset_tokens", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_password_reset_tokens_token", ["token"])

    with op.batch_alter_table("season_farming_windows", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("idx_farming_windows_season"))
        batch_op.create_index(
            batch_op.f("ix_season_farming_windows_season_id"),
            ["season_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("season_farming_windows", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_season_farming_windows_season_id"))
        batch_op.create_index(
            batch_op.f("idx_farming_windows_season"), ["season_id"], unique=False
        )

    with op.batch_alter_table("password_reset_tokens", schema=None) as batch_op:
        batch_op.drop_constraint("uq_password_reset_tokens_token", type_="unique")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_events_season"), ["season_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_events_date"), ["event_date"], unique=False
        )
        batch_op.alter_column("date_end", existing_type=sa.DATE(), nullable=True)
        batch_op.alter_column("date_start", existing_type=sa.DATE(), nullable=True)

    with op.batch_alter_table("event_participations", schema=None) as batch_op:
        batch_op.drop_constraint("uq_event_char", type_="unique")
        batch_op.create_index(
            batch_op.f("ix_participations_event"), ["event_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_participations_char"), ["character_id"], unique=False
        )

    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_constraint("uq_applications_reference", type_="unique")
        batch_op.drop_index("ix_app_status")
        batch_op.drop_index("ix_app_created")
        batch_op.create_index(
            batch_op.f("ix_applications_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_applications_reference"),
            ["reference"],
            unique=True,
            sqlite_where=sa.text("reference IS NOT NULL"),
        )
        batch_op.create_index(
            batch_op.f("ix_applications_created"), ["created_at"], unique=False
        )
