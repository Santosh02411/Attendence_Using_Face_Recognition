"""Runs schema migrations for database/app.db via Alembic.

This replaces the old hand-rolled `PRAGMA table_info(...)` /
`ALTER TABLE ... ADD COLUMN` conditional blocks that used to live in
app.py's init_databases(). The migration steps those blocks performed
still exist — they're just versioned, ordered Alembic revisions now (see
migrations/versions/), instead of two ad-hoc "does this column exist yet"
checks re-run on every startup.

Public entry point: run_migrations(database_path). Called from
app.init_databases() with the *current* value of app.DATABASE_PATH (a
module-level global that tests/conftest.py monkeypatches to a per-test
temp path), not a value imported once from config at module load time —
so this must take the path as an argument rather than reading it from
config itself.
"""
import logging
import os
import sqlite3

logger = logging.getLogger('attendance_app.db_migrations')

# Must run before `from alembic import command` below: importing that
# module transitively imports alembic.autogenerate's submodules, which
# log a handful of "setup plugin ..." lines at INFO as an import-time
# side effect — setting this after the import would be too late to
# suppress that first batch. See the longer explanation further down for
# why this can't just go back to being set via alembic.ini/fileConfig.
logging.getLogger('alembic').setLevel(logging.WARNING)

from alembic import command  # noqa: E402 - see comment above for why this isn't at the top
from alembic.config import Config  # noqa: E402

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
_ALEMBIC_INI = os.path.join(_PROJECT_ROOT, 'alembic.ini')
_MIGRATIONS_DIR = os.path.join(_PROJECT_ROOT, 'migrations')


def _alembic_config(database_path):
    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option('script_location', _MIGRATIONS_DIR)
    cfg.set_main_option('sqlalchemy.url', f'sqlite:///{database_path}')
    # Tells migrations/env.py to skip its fileConfig() call. Without this,
    # every run_migrations() call (i.e. every app startup, since
    # init_databases() calls this unconditionally) would re-apply
    # alembic.ini's [logger_root] level = WARN, silently overriding
    # logging_config.configure_app_logging()'s INFO level — the app's own
    # request/error logs would go dark after the very first migration
    # check. The Alembic CLI (`alembic upgrade head` run by hand, per the
    # README) doesn't go through this function, so it's unaffected and
    # still gets normal Alembic console logging.
    cfg.attributes['configure_logger'] = False
    return cfg


def _existing_tables(database_path):
    conn = sqlite3.connect(database_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def run_migrations(database_path):
    """Brings database_path's schema up to the latest Alembic revision,
    creating the file (and its parent directory) if it doesn't exist yet.

    Two starting points are handled:
      - No file, or a file with no tables yet: a genuinely fresh database.
        `alembic upgrade head` runs every revision from scratch, creating
        all tables and columns.
      - A file that already has tables (e.g. `admins`) but no
        `alembic_version` table: a pre-Alembic database created by an
        older version of this app, which always fully applied its own
        ALTER TABLE checks on every startup — so by construction its
        schema already matches revision 0003_add_lockout_columns (the
        last revision that mirrors what that old ad-hoc block did).
        Running `upgrade head` straight from nothing against it would
        fail (CREATE TABLE / ADD COLUMN on things that already exist),
        so it's stamped at 0003 first — marking exactly the history it
        actually has, no more — and then upgraded normally from there,
        so any revision added *after* 0003 (which the old ad-hoc block
        never applied) still gets created.
    A database that already has `alembic_version` (already migrated by
    this code before) just goes through a normal, idempotent
    `upgrade head` — a no-op unless a newer revision has been added since.
    """
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    cfg = _alembic_config(database_path)

    if os.path.exists(database_path):
        tables = _existing_tables(database_path)
        if 'alembic_version' not in tables and 'admins' in tables:
            command.stamp(cfg, '0003_add_lockout_columns')
            logger.info(f'Database at {database_path} was pre-Alembic — stamped at 0003_add_lockout_columns (matching its actual schema history).')

    command.upgrade(cfg, 'head')
    logger.info(f'Database at {database_path} is at the latest schema revision.')
