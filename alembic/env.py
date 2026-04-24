from __future__ import annotations

import asyncio
from logging.config import fileConfig

import os
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool
from dotenv import load_dotenv
from app.config import settings
from app.db.models import Base

load_dotenv()

# Get the URL and apply the fail-safe fix for production
raw_url = os.getenv("DATABASE_URL") or settings.database_url
if raw_url.startswith("postgres://"):
    db_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif raw_url.startswith("postgresql://") and "+asyncpg" not in raw_url:
    db_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    db_url = raw_url

config = context.config

# Set the URL at runtime so we never hard-code it
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection.
    Alembic generates SQL that can be applied manually.
    Uses the synchronous psycopg2 dialect for SQL generation.
    """
    # Replace asyncpg with psycopg2 for offline SQL generation
    url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine (required for asyncpg driver)."""
    # Use the same corrected URL set in the config
    url = context.config.get_main_option("sqlalchemy.url")
    engine = create_async_engine(
        url,
        echo=False,
        poolclass=pool.NullPool,  # NullPool is correct here — migrations are one-shot
    )

    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await engine.dispose()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()