from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from panelist.config import get_settings
from panelist.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata

# Columns the models stopped mapping but the previous release still reads; they stay in the schema
# until the migration of the next major release drops them, so autogenerate must not propose it.
RETIRED_COLUMNS = {("experts", "hourly_rate_cents")}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "column" and reflected and (obj.table.name, name) in RETIRED_COLUMNS)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_object=include_object,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
