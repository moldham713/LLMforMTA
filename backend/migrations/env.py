import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.gtfs.schema  # noqa: F401  registers gtfs tables on Base.metadata
from app.models import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Tests pass their own URL; otherwise the app's database.
database_url = config.attributes.get("database_url") or os.environ["DATABASE_URL"]

# Only our schemas; PostGIS adds tiger/topology schemas that autogenerate must ignore.
MANAGED_SCHEMAS = {"gtfs", "analytics"}


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=database_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
