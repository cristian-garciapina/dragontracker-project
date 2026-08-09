"""Alembic environment for Eternal Vanguard.

Uses the Base defined in app.models (where all table classes live).
DB URL is taken from ALEMBIC_DB_URL if set, else from app.db.DB_URL.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context

# Make the project root importable so `app.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Base  # noqa: E402
from app.db import DB_URL as APP_DB_URL  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.environ.get("ALEMBIC_DB_URL", APP_DB_URL)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def _skip_pk_nullable_diff(object_, name, type_, reflected, compare_to):
    if type_ == "column" and name == "id" and getattr(object_, "primary_key", False):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=_skip_pk_nullable_diff,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
