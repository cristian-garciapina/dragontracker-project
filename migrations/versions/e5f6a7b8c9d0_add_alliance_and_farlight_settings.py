"""add alliance and farlight settings

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-24 13:00:00.000000

"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SETTINGS_TO_ADD = [
    # ----- Alliance identity (editable by staff) -----
    {
        "key": "alliance.name",
        "value": "Your Alliance",
        "value_type": "string",
        "category": "alliance",
        "description": "Full display name of the alliance. Shown in page titles, header, footer, and outgoing emails.",
        "editable_by": "staff",
    },
    {
        "key": "alliance.tag",
        "value": "AL",
        "value_type": "string",
        "category": "alliance",
        "description": "Short alliance tag (2-4 characters) used in compact UI contexts.",
        "editable_by": "staff",
    },
    {
        "key": "alliance.kingdom_id",
        "value": 0,
        "value_type": "int",
        "category": "alliance",
        "description": "Kingdom number where the alliance plays (e.g. 193, 544). Used by ingestion and filters.",
        "editable_by": "staff",
    },
    {
        "key": "alliance.server_id",
        "value": 0,
        "value_type": "int",
        "category": "alliance",
        "description": "Farlight server ID for API pulls. Usually equal to kingdom_id.",
        "editable_by": "staff",
    },
    {
        "key": "alliance.tagline",
        "value": "",
        "value_type": "string",
        "category": "alliance",
        "description": "Short baseline shown on the public landing page.",
        "editable_by": "staff",
    },
    {
        "key": "alliance.motto",
        "value": "",
        "value_type": "string",
        "category": "alliance",
        "description": "Optional quote or motto displayed on the landing page.",
        "editable_by": "staff",
    },
    # ----- Farlight ingestion (editable by owner only) -----
    {
        "key": "farlight.pull_enabled",
        "value": False,
        "value_type": "bool",
        "category": "farlight",
        "description": "Kill-switch for the nightly Farlight ingestion cron. Turn on only when a valid JWT is stored in the secrets table.",
        "editable_by": "owner",
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    stmt = sa.text(
        "INSERT OR IGNORE INTO settings "
        "(key, value, value_type, category, description, editable_by, updated_at) "
        "VALUES (:key, :value, :value_type, :category, :description, :editable_by, CURRENT_TIMESTAMP)"
    )
    for entry in SETTINGS_TO_ADD:
        bind.execute(
            stmt,
            {
                "key": entry["key"],
                "value": json.dumps(entry["value"]),
                "value_type": entry["value_type"],
                "category": entry["category"],
                "description": entry["description"],
                "editable_by": entry["editable_by"],
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    keys = [entry["key"] for entry in SETTINGS_TO_ADD]
    bind.execute(
        sa.text("DELETE FROM settings WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": keys},
    )
