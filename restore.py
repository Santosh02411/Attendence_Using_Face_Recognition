"""
Restores a backup.py archive back into place.

Safety properties:
  - Never silently overwrites live data: if the destination database file
    or Datasets/ directory already has content, restore refuses to touch
    it unless --force is given.
  - Even with --force, existing data isn't deleted — it's moved aside to
    a `<name>.pre-restore-<timestamp>` sibling first, so a restore can
    itself be undone (or at least isn't a one-way door) if it turns out
    to have been the wrong archive.
  - After extracting a database, runs `PRAGMA integrity_check` on it
    before declaring success — catches a truncated/corrupt archive rather
    than silently leaving a broken database in place.
  - Runs the restored database through the app's own Alembic migrations
    (see db_migrations.py) after restoring — a backup made by an older
    version of the app, with an older schema, ends up fully up to date
    automatically, the same way a fresh startup would upgrade it.

Usage:
    python restore.py backups/backup-20260820-020000.tar.gz
    python restore.py backups/backup-20260820-020000.tar.gz --force
    python restore.py backups/backup-20260820-020000.tar.gz --skip-images
    python restore.py backups/backup-20260820-020000.tar.gz --database-dir /tmp/restored --datasets-dir /tmp/restored/Datasets
"""
import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

import config as cfg
import db_migrations
import error_reporting
import logging_config

logging_config.configure_app_logging(level=cfg.LOG_LEVEL, fmt=cfg.LOG_FORMAT)
logger = logging.getLogger('attendance_app.restore')


class RestoreError(Exception):
    """Raised for any restore precondition failure (missing/corrupt
    archive, existing data present without --force, failed integrity
    check) — always caught and reported cleanly by main(), never lets a
    partial restore look like it succeeded."""


def _extract_with_manual_safety_check(tar, dest_dir):
    """Fallback for Python 3.9.x patch releases older than 3.9.17, which
    predate tarfile's built-in `filter='data'` protection (PEP 706) — only
    reached if that kwarg raised TypeError. Rejects the same class of
    unsafe members (absolute paths, `..` traversal, symlinks/hardlinks
    pointing outside dest_dir, device/FIFO special files) before falling
    back to a plain extractall(), since backup.py-created archives never
    legitimately contain any of these."""
    dest_dir_real = os.path.realpath(dest_dir)
    for member in tar.getmembers():
        member_path = os.path.realpath(os.path.join(dest_dir, member.name))
        if os.path.isabs(member.name) or '..' in member.name.split('/'):
            raise RestoreError(f'Archive contains an unsafe path, refusing to extract: {member.name}')
        if not member_path.startswith(dest_dir_real + os.sep):
            raise RestoreError(f'Archive member escapes the extraction directory: {member.name}')
        if member.issym() or member.islnk() or member.isdev():
            raise RestoreError(f'Archive contains a symlink/hardlink/device file, refusing to extract: {member.name}')
    # Every member above was already checked for absolute paths, `..`
    # traversal, and symlink/hardlink/device types, so this is safe.
    tar.extractall(dest_dir)  # nosec B202


def _integrity_check(sqlite_path):
    conn = sqlite3.connect(sqlite_path)
    try:
        result = conn.execute('PRAGMA integrity_check').fetchone()
        return result is not None and result[0] == 'ok'
    finally:
        conn.close()


def _move_aside(path):
    """Renames an existing file/directory out of the way rather than
    deleting it, so a restore is recoverable if it turns out to have
    been a mistake. Returns the new path, or None if path didn't exist."""
    if not os.path.exists(path):
        return None
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    moved_path = f'{path}.pre-restore-{timestamp}'
    shutil.move(path, moved_path)
    logger.info(f'Moved existing {path} aside to {moved_path} before restoring.')
    return moved_path


def _read_manifest(extracted_dir):
    manifest_path = os.path.join(extracted_dir, 'manifest.json')
    if not os.path.exists(manifest_path):
        raise RestoreError(
            f'{manifest_path} not found inside the archive — this does not look like a backup.py archive.'
        )
    with open(manifest_path) as f:
        return json.load(f)


def restore_backup(archive_path, database_path=None, face_database_path=None,
                    datasets_dir=None, include_images=True, force=False):
    """Restores archive_path into place. Returns a summary dict. Raises
    RestoreError for any precondition failure (see module docstring) —
    always before anything destination-side has been touched, except for
    the "move existing data aside" step, which never deletes anything.
    """
    database_path = database_path or cfg.DATABASE_PATH
    face_database_path = face_database_path if face_database_path is not None else cfg.FACE_DATABASE_PATH
    datasets_dir = datasets_dir if datasets_dir is not None else cfg.DATA_DIR

    if not os.path.exists(archive_path):
        raise RestoreError(f'Archive not found: {archive_path}')

    existing_targets = []
    if os.path.exists(database_path) and os.path.getsize(database_path) > 0:
        existing_targets.append(database_path)
    if include_images and os.path.isdir(datasets_dir) and os.listdir(datasets_dir):
        existing_targets.append(datasets_dir)
    if existing_targets and not force:
        raise RestoreError(
            'Refusing to restore over existing data without --force: ' + ', '.join(existing_targets)
        )

    with tempfile.TemporaryDirectory(prefix='attendance-restore-') as tmp_dir:
        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                try:
                    # 'data' filter (PEP 706): rejects absolute paths,
                    # symlinks/hardlinks escaping tmp_dir, device files,
                    # etc. Backported to 3.9.17+/3.10.12+/3.11.4+ and
                    # default from 3.12 — but guard for anyone still on
                    # an older 3.9.x patch release where the `filter`
                    # kwarg doesn't exist yet.
                    tar.extractall(tmp_dir, filter='data')
                except TypeError:
                    _extract_with_manual_safety_check(tar, tmp_dir)
        except tarfile.TarError as e:
            raise RestoreError(f'Archive is corrupt or not a valid tar.gz: {e}') from e

        manifest = _read_manifest(tmp_dir)

        extracted_app_db = os.path.join(tmp_dir, 'database', 'app.db')
        extracted_face_db = os.path.join(tmp_dir, 'database', 'FaceBase.db')
        extracted_datasets = os.path.join(tmp_dir, 'Datasets')

        if not os.path.exists(extracted_app_db):
            raise RestoreError('Archive does not contain database/app.db — nothing to restore.')
        if not _integrity_check(extracted_app_db):
            raise RestoreError('database/app.db in the archive failed PRAGMA integrity_check — archive may be corrupt.')
        if os.path.exists(extracted_face_db) and not _integrity_check(extracted_face_db):
            raise RestoreError('database/FaceBase.db in the archive failed PRAGMA integrity_check — archive may be corrupt.')

        moved_aside = []
        aside = _move_aside(database_path)
        if aside:
            moved_aside.append(aside)
        os.makedirs(os.path.dirname(database_path) or '.', exist_ok=True)
        shutil.move(extracted_app_db, database_path)

        if os.path.exists(extracted_face_db):
            aside = _move_aside(face_database_path)
            if aside:
                moved_aside.append(aside)
            os.makedirs(os.path.dirname(face_database_path) or '.', exist_ok=True)
            shutil.move(extracted_face_db, face_database_path)

        restored_images = False
        if include_images and os.path.isdir(extracted_datasets):
            aside = _move_aside(datasets_dir)
            if aside:
                moved_aside.append(aside)
            shutil.move(extracted_datasets, datasets_dir)
            restored_images = True

    # Bring the restored database up to the current schema — a backup
    # made by an older version of this app may predate a migration that
    # has since been added (see db_migrations.py / migrations/versions/).
    db_migrations.run_migrations(database_path)

    summary = {
        'archive': archive_path,
        'manifest': manifest,
        'restored_database': database_path,
        'restored_images': restored_images,
        'moved_aside': moved_aside,
    }
    logger.info(f'Restore complete: {summary}')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('archive', help='Path to a backup-*.tar.gz archive created by backup.py')
    parser.add_argument('--force', action='store_true', help='Restore even if existing data is present at the destination (existing data is moved aside, not deleted)')
    parser.add_argument('--skip-images', action='store_true', help="Don't restore Datasets/ face images, even if the archive contains them")
    parser.add_argument('--database-dir', help=f'Directory to restore database/ into (default: {cfg.DATABASE_DIR})')
    parser.add_argument('--datasets-dir', help=f'Directory to restore Datasets/ into (default: {cfg.DATA_DIR})')
    args = parser.parse_args()

    database_dir = args.database_dir or cfg.DATABASE_DIR
    database_path = os.path.join(database_dir, os.path.basename(cfg.DATABASE_PATH))
    face_database_path = os.path.join(database_dir, os.path.basename(cfg.FACE_DATABASE_PATH))

    try:
        summary = restore_backup(
            args.archive,
            database_path=database_path,
            face_database_path=face_database_path,
            datasets_dir=args.datasets_dir,
            include_images=not args.skip_images,
            force=args.force,
        )
    except RestoreError as e:
        print(f'Restore refused: {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.error(f'Restore failed: {e}', exc_info=True)
        error_reporting.capture_exception(e)
        print(f'Restore failed: {e}', file=sys.stderr)
        sys.exit(1)

    print(f'Restored database to {summary["restored_database"]}.')
    if summary['restored_images']:
        print('Restored Datasets/ face images.')
    if summary['moved_aside']:
        print('Existing data was preserved (moved aside) at:')
        for path in summary['moved_aside']:
            print(f'  {path}')


if __name__ == '__main__':
    main()
