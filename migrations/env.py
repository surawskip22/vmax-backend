from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from panmajster.config import get_settings
from panmajster.db import Base, ensure_database_schema
from panmajster import models  # noqa: F401


config = context.config
config.set_main_option("sqlalchemy.url", get_settings().normalized_database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_schema = target_metadata.schema


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_schemas=bool(database_schema),
        version_table_schema=database_schema,
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
        ensure_database_schema(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=bool(database_schema),
            version_table_schema=database_schema,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
