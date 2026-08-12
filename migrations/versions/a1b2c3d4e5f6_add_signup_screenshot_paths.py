"""add signup screenshot paths

Revision ID: a1b2c3d4e5f6
Revises: b4dd9e30d709
Create Date: 2026-08-12 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "b4dd9e30d709"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("signup_screenshot_path", sa.String(length=255), nullable=True))
    with op.batch_alter_table("applications") as batch_op:
        batch_op.add_column(sa.Column("screenshot_path", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_column("screenshot_path")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("signup_screenshot_path")
