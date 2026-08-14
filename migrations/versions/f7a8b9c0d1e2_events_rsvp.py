"""events: RSVP + discord message tracking + time fields

Revision ID: f7a8b9c0d1e2
Revises: d5e6f7a8b9c0
Create Date: 2026-08-14 21:05:00

"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("events", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("start_time", sa.Time(), nullable=True))
        batch_op.add_column(sa.Column("end_time", sa.Time(), nullable=True))
        batch_op.add_column(sa.Column("discord_message_id", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("discord_channel_id", sa.String(32), nullable=True))

    op.create_table(
        "event_rsvps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column(
            "responded_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("status IN ('yes','no')", name="ck_event_rsvps_status"),
        sa.UniqueConstraint("event_id", "character_id", name="uq_event_rsvps_event_char"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["character_id"], ["members.character_id"]),
    )
    op.create_index("ix_event_rsvps_event_id", "event_rsvps", ["event_id"])


def downgrade():
    op.drop_index("ix_event_rsvps_event_id", table_name="event_rsvps")
    op.drop_table("event_rsvps")
    with op.batch_alter_table("events", recreate="always") as batch_op:
        batch_op.drop_column("discord_channel_id")
        batch_op.drop_column("discord_message_id")
        batch_op.drop_column("end_time")
        batch_op.drop_column("start_time")
