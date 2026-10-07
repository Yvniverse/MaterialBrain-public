from logging.config import fileConfig

from sqlalchemy import MetaData, engine_from_config, pool

from alembic import context
from app import models  # noqa: F401
from app.core.config import settings
from app.core.database import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

# The frozen initial migration creates live ORM metadata while intentionally
# excluding warehouse tables until 0012. Spatial tables belong to 0016 and must
# not be bootstrapped before their warehouse foreign keys exist.
SPATIAL_TABLES = {
    "warehouse_spatial_assets",
    "warehouse_semantic_zones",
    "warehouse_spatial_docks",
    "warehouse_dynamic_overlays",
}


def run_revision_chain():
    original = Base.metadata
    bootstrap = MetaData()
    for table in original.tables.values():
        if table.name not in SPATIAL_TABLES:
            table.to_metadata(bootstrap)
    Base.metadata = bootstrap
    try:
        context.run_migrations()
    finally:
        Base.metadata = original


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        run_revision_chain()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            run_revision_chain()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
