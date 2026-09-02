"""Alembic environment.

This project has no SQLAlchemy ORM models — the app talks to sqlite
directly via sqlite3 (see app.py). Migrations here are plain, explicit
op.create_table()/op.add_column() calls rather than autogenerate output, so
target_metadata stays None: there's nothing to diff against, and
`alembic revision --autogenerate` isn't a supported workflow in this repo
(new migrations are written by hand, same as the two ALTER TABLE steps this
migration history replaces).

sqlalchemy.url is not read from alembic.ini's static value — it's set at
runtime by db_migrations.run_migrations() before the Alembic Config object
reaches this module, so the same migration chain works against the default
database/app.db path, a DATABASE_PATH override, and the per-test temp paths
tests/conftest.py uses.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None and config.attributes.get('configure_logger', True):
    # disable_existing_loggers=False is critical here, not just tidiness:
    # logging.config.fileConfig()'s default (True) disables every logger
    # that already exists at the moment it's called — which would
    # silently disable this app's own 'attendance_app' logger (created at
    # app.py's import time) and 'werkzeug'. See logging_config.py /
    # tests/test_logging.py for the structured logging this would
    # otherwise break.
    #
    # This whole block is skipped when db_migrations.py's
    # run_migrations() is the caller (it sets configure_logger=False on
    # the Config object) — see its comment for why: fileConfig() also
    # reapplies alembic.ini's [logger_root] level=WARN on every call,
    # which would override the app's own configured log level on every
    # startup, not just disable a logger. Direct `alembic <command>` CLI
    # usage (nothing sets configure_logger=False) still gets normal
    # Alembic console logging as usual.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None

# Lets the Alembic CLI be pointed at a specific database file directly
# (e.g. `alembic -x database_path=database/app.db current`), for anyone
# inspecting/managing migration state by hand outside of the app's own
# db_migrations.run_migrations() (which sets sqlalchemy.url the same way,
# programmatically, before invoking Alembic's Python API instead of the
# CLI). Falls back to whatever sqlalchemy.url alembic.ini already has
# (blank by default) if -x database_path isn't given.
_x_args = context.get_x_argument(as_dictionary=True)
if 'database_path' in _x_args:
    config.set_main_option('sqlalchemy.url', f"sqlite:///{_x_args['database_path']}")


def run_migrations_offline():
    """Run migrations without a live DB-API connection, emitting SQL to
    stdout instead. Not used by the app itself (db_migrations.py always
    runs online), but kept so `alembic upgrade head --sql` works for
    anyone who wants to inspect the generated SQL."""
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations against a live sqlite connection — this is the path
    db_migrations.run_migrations() exercises via command.upgrade()."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # sqlite can't ALTER/DROP columns outside a "batch" (recreate
            # the table under the hood) — render_as_batch makes
            # op.batch_alter_table() available and keeps `alembic check` /
            # autogenerate-style tooling from tripping over sqlite's
            # limited ALTER TABLE support, even though this repo's
            # migrations only add columns today.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
