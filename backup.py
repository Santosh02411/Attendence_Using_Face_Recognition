"""
Automated backup for the SQLite databases and registered face images.

Produces one self-contained, timestamped `.tar.gz` archive per run,
containing:
  - database/app.db      (via SQLite's online backup API — see below)
  - database/FaceBase.db  (same, if present — legacy `people` table)
  - Datasets/             (registered students' face-crop images)
  - manifest.json         (what's inside, when, and from where)

Why the SQLite online backup API instead of a plain file copy: a running
app (gunicorn worker, or the dev server) may have app.db open and mid-write
at the exact moment a backup runs. `shutil.copy()`'ing the raw file can
copy it mid-transaction and produce a corrupt snapshot. `sqlite3.Connection
.backup()` (Python's binding to SQLite's own backup API) instead reads
through SQLite's normal locking/consistency machinery, so the copy it
produces is always a clean, consistent snapshot — safe to run against a
live database without stopping the app first.

Usage:
    python backup.py                    # full backup (DBs + face images) to BACKUP_DIR
    python backup.py --skip-images      # DBs only — much faster/smaller, no Datasets/
    python backup.py --output-dir /mnt/backups
    python backup.py --keep 30          # override BACKUP_RETENTION_COUNT for this run

Scheduling this automatically (it does nothing on its own — something
needs to invoke it periodically):
    # cron (host or inside the container via `docker compose exec`):
    0 2 * * * cd /path/to/app && /path/to/venv/bin/python backup.py >> backup.log 2>&1

    # systemd timer: see README "Backup & Restore" for a unit file example.

See restore.py for the other half of this — extracting one of these
archives back into place.
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

import config as cfg
import error_reporting
import logging_config

logging_config.configure_app_logging(level=cfg.LOG_LEVEL, fmt=cfg.LOG_FORMAT)
logger = logging.getLogger('attendance_app.backup')

# Bumped if the archive layout/manifest shape ever changes in a way
# restore.py needs to know about.
_MANIFEST_VERSION = 1


def _sqlite_online_backup(source_path, dest_path):
    """Copies the sqlite database at source_path to dest_path using
    SQLite's own backup API, producing a consistent snapshot even if
    source_path is currently open and being written to by another
    process. No-op (returns False) if source_path doesn't exist yet —
    e.g. a fresh install that has never created FaceBase.db."""
    if not os.path.exists(source_path):
        return False
    src_conn = sqlite3.connect(source_path)
    try:
        dest_conn = sqlite3.connect(dest_path)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()
    return True


def _apply_retention(backup_dir, keep):
    """Deletes the oldest backup-*.tar.gz archives in backup_dir beyond
    the most recent `keep` of them. keep=0 (or negative) disables
    retention entirely — every backup is kept forever."""
    if keep is None or keep <= 0:
        return []
    archives = sorted(
        (f for f in os.listdir(backup_dir) if f.startswith('backup-') and f.endswith('.tar.gz')),
    )
    to_delete = archives[:-keep] if len(archives) > keep else []
    deleted = []
    for name in to_delete:
        path = os.path.join(backup_dir, name)
        try:
            os.remove(path)
            deleted.append(path)
            logger.info(f'Deleted old backup beyond retention limit ({keep}): {path}')
        except OSError as e:
            logger.warning(f'Could not delete old backup {path}: {e}')
    return deleted


def create_backup(database_path=None, face_database_path=None, datasets_dir=None,
                   backup_dir=None, include_images=True, retention_count=None):
    """Creates one timestamped backup-YYYYmmdd-HHMMSS.tar.gz archive under
    backup_dir, then applies retention (see _apply_retention). Returns the
    path to the archive that was just created.

    All path arguments default to the equivalent config.py setting —
    passed explicitly (rather than read from config inside this function)
    so tests, and anyone scripting around this, can point it at an
    isolated location without needing to monkeypatch config.py globals.
    """
    database_path = database_path or cfg.DATABASE_PATH
    face_database_path = face_database_path if face_database_path is not None else cfg.FACE_DATABASE_PATH
    datasets_dir = datasets_dir if datasets_dir is not None else cfg.DATA_DIR
    backup_dir = backup_dir or cfg.BACKUP_DIR
    retention_count = cfg.BACKUP_RETENTION_COUNT if retention_count is None else retention_count

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    archive_path = os.path.join(backup_dir, f'backup-{timestamp}.tar.gz')

    manifest = {
        'manifest_version': _MANIFEST_VERSION,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'includes_images': include_images,
        'contents': [],
    }

    with tempfile.TemporaryDirectory(prefix='attendance-backup-') as tmp_dir:
        db_snapshot_dir = os.path.join(tmp_dir, 'database')
        os.makedirs(db_snapshot_dir, exist_ok=True)

        app_db_snapshot = os.path.join(db_snapshot_dir, 'app.db')
        if _sqlite_online_backup(database_path, app_db_snapshot):
            manifest['contents'].append('database/app.db')
        else:
            logger.warning(f'{database_path} does not exist yet — skipping (nothing to back up).')

        face_db_snapshot = os.path.join(db_snapshot_dir, 'FaceBase.db')
        if _sqlite_online_backup(face_database_path, face_db_snapshot):
            manifest['contents'].append('database/FaceBase.db')

        images_included = False
        if include_images and os.path.isdir(datasets_dir):
            images_included = True
            manifest['contents'].append('Datasets/')
        elif include_images:
            logger.info(f'{datasets_dir} does not exist yet — no face images to include.')

        manifest_path = os.path.join(tmp_dir, 'manifest.json')
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        with tarfile.open(archive_path, 'w:gz') as tar:
            tar.add(manifest_path, arcname='manifest.json')
            if os.path.isdir(db_snapshot_dir):
                tar.add(db_snapshot_dir, arcname='database')
            if images_included:
                tar.add(datasets_dir, arcname='Datasets')

    size_mb = os.path.getsize(archive_path) / (1024 * 1024)
    logger.info(f'Backup created: {archive_path} ({size_mb:.1f} MB, contents={manifest["contents"]})')

    deleted = _apply_retention(backup_dir, retention_count)
    if deleted:
        logger.info(f'Retention: removed {len(deleted)} backup(s) older than the {retention_count} most recent.')

    return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', help=f'Where to write the archive (default: {cfg.BACKUP_DIR}, i.e. config.BACKUP_DIR)')
    parser.add_argument('--skip-images', action='store_true', help='Back up the databases only, skip Datasets/ face images (faster, smaller)')
    parser.add_argument('--keep', type=int, help=f'Override config.BACKUP_RETENTION_COUNT for this run (default: {cfg.BACKUP_RETENTION_COUNT}; 0 = keep every backup forever)')
    args = parser.parse_args()

    try:
        archive_path = create_backup(
            backup_dir=args.output_dir,
            include_images=not args.skip_images,
            retention_count=args.keep,
        )
    except Exception as e:
        logger.error(f'Backup failed: {e}', exc_info=True)
        error_reporting.capture_exception(e)
        print(f'Backup failed: {e}', file=sys.stderr)
        sys.exit(1)

    print(f'Backup created: {archive_path}')


if __name__ == '__main__':
    main()
