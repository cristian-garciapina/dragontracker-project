"""events: add kind + event_time + info_url

Revision ID: a9b0c1d2e3f4
Revises: f7a8b9c0d1e2
Create Date: 2026-08-14 22:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("events", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="ranking"))
        batch_op.add_column(sa.Column("event_time", sa.Time(), nullable=True))
        batch_op.add_column(sa.Column("info_url", sa.String(512), nullable=True))


def downgrade():
    with op.batch_alter_table("events", recreate="always") as batch_op:
        batch_op.drop_column("info_url")
        batch_op.drop_column("event_time")
        batch_op.drop_column("kind")
