"""align fk cascade with models

Revision ID: b4dd9e30d709
Revises: 8ab7146018b8
Create Date: 2026-08-09

Recr\u00e9e trois tables pour appliquer ondelete=CASCADE sur les FK
d\u00e9j\u00e0 d\u00e9clar\u00e9es dans les mod\u00e8les.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4dd9e30d709"
down_revision: Union[str, Sequence[str], None] = "8ab7146018b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rebuild(table: str, parent: str, col: str, parent_col: str, ondelete: str) -> None:
    with op.batch_alter_table(
        table,
        schema=None,
        recreate="always",
        table_args=[
            sa.ForeignKeyConstraint(
                [col], [f"{parent}.{parent_col}"], ondelete=ondelete
            ),
        ],
    ) as batch_op:
        pass


def upgrade() -> None:
    _rebuild("event_participations", "events", "event_id", "id", "CASCADE")
    _rebuild("player_notes", "members", "character_id", "character_id", "CASCADE")
    _rebuild("season_farming_windows", "seasons", "season_id", "id", "CASCADE")


def downgrade() -> None:
    _rebuild("season_farming_windows", "seasons", "season_id", "id", "NO ACTION")
    _rebuild("player_notes", "members", "character_id", "character_id", "NO ACTION")
    _rebuild("event_participations", "events", "event_id", "id", "NO ACTION")
