"""Alembic environment.

The schema of truth is ``akasha.infrastructure.db_models.metadata`` — the
migration scripts are hand-written against it. ``target_metadata`` enables
``alembic revision --autogenerate`` as a *drafting* aid only; generated
scripts must be reviewed before being applied (backend/AGENTS.md §14: no
automatic production migration from ORM schema creation).

Logging is owned by the application (akasha.observability.logging), so
env.py deliberately does not touch fileConfig.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from akasha.infrastructure.db_models import metadata

config = context.config

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
