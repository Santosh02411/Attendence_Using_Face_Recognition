"""Tests for backup.py / restore.py — the SQLite + face-image backup and
restore scripts (see README "Backup & Restore")."""
import json
import os
import sqlite3
import tarfile

import pytest

import backup
import restore


def _seed_db(database_path):
    """isolated_paths already ran init_databases(), which creates and
    migrates database_path — just add a row so we have something to
    verify survived a round trip."""
    conn = sqlite3.connect(database_path)
    conn.execute("INSERT INTO students(name, roll_no, branch, semester) VALUES ('Alice', '21CS001', 'CSE', '5')")
    conn.commit()
    conn.close()


class TestCreateBackup:
    def test_creates_archive_with_expected_contents(self, isolated_paths, tmp_path):
        _seed_db(isolated_paths['database_path'])
        backup_dir = tmp_path / 'backups'

        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(backup_dir),
        )

        assert os.path.exists(archive_path)
        with tarfile.open(archive_path) as tar:
            names = tar.getnames()
            assert 'manifest.json' in names
            assert 'database/app.db' in names
            manifest = json.loads(tar.extractfile('manifest.json').read())
        assert manifest['manifest_version'] == 1
        assert 'database/app.db' in manifest['contents']

    def test_uses_sqlite_online_backup_not_a_raw_copy(self, isolated_paths, tmp_path):
        """The archived app.db must be a real, independently-openable
        SQLite database (proof create_backup used the online backup API
        correctly), not just a byte-for-byte file copy that happened to
        work in this simple case."""
        _seed_db(isolated_paths['database_path'])
        backup_dir = tmp_path / 'backups'

        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(backup_dir),
        )

        extract_dir = tmp_path / 'extracted'
        with tarfile.open(archive_path) as tar:
            tar.extractall(str(extract_dir), filter='data')

        conn = sqlite3.connect(str(extract_dir / 'database' / 'app.db'))
        rows = conn.execute("SELECT name, roll_no FROM students").fetchall()
        conn.close()
        assert rows == [('Alice', '21CS001')]

    def test_includes_face_images_by_default(self, isolated_paths, tmp_path):
        os.makedirs(isolated_paths['data_dir'], exist_ok=True)
        image_path = os.path.join(isolated_paths['data_dir'], 'User.1.1.jpg')
        with open(image_path, 'wb') as f:
            f.write(b'not-a-real-jpeg-but-thats-fine-for-this-test')
        backup_dir = tmp_path / 'backups'

        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(backup_dir),
        )

        with tarfile.open(archive_path) as tar:
            names = tar.getnames()
            manifest = json.loads(tar.extractfile('manifest.json').read())
        assert 'Datasets/User.1.1.jpg' in names
        assert 'Datasets/' in manifest['contents']

    def test_skip_images_omits_datasets(self, isolated_paths, tmp_path):
        os.makedirs(isolated_paths['data_dir'], exist_ok=True)
        with open(os.path.join(isolated_paths['data_dir'], 'User.1.1.jpg'), 'wb') as f:
            f.write(b'x')
        backup_dir = tmp_path / 'backups'

        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(backup_dir),
            include_images=False,
        )

        with tarfile.open(archive_path) as tar:
            names = tar.getnames()
        assert not any(n.startswith('Datasets/') for n in names)

    def test_retention_keeps_only_the_newest_n(self, isolated_paths, tmp_path, monkeypatch):
        backup_dir = tmp_path / 'backups'
        counter = [0]

        def fake_archive_name(*_a, **_k):
            counter[0] += 1
            return f'backup-2026010{counter[0]}-000000.tar.gz'

        # Rather than sleeping to get distinct real timestamps, seed the
        # directory with pre-made (empty-ish) archives whose names already
        # sort in creation order, then let create_backup add one more and
        # apply retention on top.
        os.makedirs(backup_dir, exist_ok=True)
        for i in range(1, 4):
            with open(os.path.join(str(backup_dir), f'backup-2026010{i}-000000.tar.gz'), 'wb') as f:
                f.write(b'x')

        backup._apply_retention(str(backup_dir), keep=2)
        remaining = sorted(f for f in os.listdir(str(backup_dir)) if f.endswith('.tar.gz'))
        assert remaining == ['backup-20260102-000000.tar.gz', 'backup-20260103-000000.tar.gz']

    def test_retention_zero_keeps_everything(self, tmp_path):
        backup_dir = tmp_path / 'backups'
        os.makedirs(backup_dir, exist_ok=True)
        for i in range(1, 4):
            with open(os.path.join(str(backup_dir), f'backup-2026010{i}-000000.tar.gz'), 'wb') as f:
                f.write(b'x')

        backup._apply_retention(str(backup_dir), keep=0)
        remaining = [f for f in os.listdir(str(backup_dir)) if f.endswith('.tar.gz')]
        assert len(remaining) == 3


class TestRestoreBackup:
    def test_round_trip_restores_database_and_images(self, isolated_paths, tmp_path):
        _seed_db(isolated_paths['database_path'])
        os.makedirs(isolated_paths['data_dir'], exist_ok=True)
        with open(os.path.join(isolated_paths['data_dir'], 'User.1.1.jpg'), 'wb') as f:
            f.write(b'fake-image-bytes')

        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(tmp_path / 'backups'),
        )

        restored_db = tmp_path / 'restored' / 'app.db'
        restored_face_db = tmp_path / 'restored' / 'FaceBase.db'
        restored_datasets = tmp_path / 'restored_Datasets'

        summary = restore.restore_backup(
            archive_path,
            database_path=str(restored_db),
            face_database_path=str(restored_face_db),
            datasets_dir=str(restored_datasets),
        )

        assert summary['restored_images'] is True
        conn = sqlite3.connect(str(restored_db))
        rows = conn.execute("SELECT name, roll_no FROM students").fetchall()
        conn.close()
        assert rows == [('Alice', '21CS001')]
        assert os.path.exists(restored_datasets / 'User.1.1.jpg')

    def test_restored_database_passes_integrity_check(self, isolated_paths, tmp_path):
        _seed_db(isolated_paths['database_path'])
        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(tmp_path / 'backups'),
        )
        restored_db = tmp_path / 'restored' / 'app.db'
        restore.restore_backup(
            archive_path,
            database_path=str(restored_db),
            face_database_path=str(tmp_path / 'restored' / 'FaceBase.db'),
            datasets_dir=str(tmp_path / 'restored_Datasets'),
        )
        conn = sqlite3.connect(str(restored_db))
        result = conn.execute('PRAGMA integrity_check').fetchone()
        conn.close()
        assert result[0] == 'ok'

    def test_restored_database_is_migrated_to_current_schema(self, isolated_paths, tmp_path):
        """A backup made against an older schema (simulated here by
        deleting a column Alembic would have added) must come out fully
        migrated after restore — restore_backup() runs db_migrations
        after extracting."""
        _seed_db(isolated_paths['database_path'])
        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(tmp_path / 'backups'),
        )
        restored_db = tmp_path / 'restored' / 'app.db'
        restore.restore_backup(
            archive_path,
            database_path=str(restored_db),
            face_database_path=str(tmp_path / 'restored' / 'FaceBase.db'),
            datasets_dir=str(tmp_path / 'restored_Datasets'),
        )
        conn = sqlite3.connect(str(restored_db))
        columns = [row[1] for row in conn.execute('PRAGMA table_info(sessions)')]
        conn.close()
        assert 'end_date' in columns  # 0002_add_session_recurrence_columns
        assert 'is_recurring' in columns

    def test_refuses_to_overwrite_existing_data_without_force(self, isolated_paths, tmp_path):
        _seed_db(isolated_paths['database_path'])
        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(tmp_path / 'backups'),
        )

        # database_path already has data (from _seed_db above) — restoring
        # into the SAME path without --force must be refused.
        with pytest.raises(restore.RestoreError):
            restore.restore_backup(
                archive_path,
                database_path=isolated_paths['database_path'],
                face_database_path=isolated_paths['face_database_path'],
                datasets_dir=isolated_paths['data_dir'],
            )

    def test_force_moves_existing_data_aside_instead_of_deleting(self, isolated_paths, tmp_path):
        _seed_db(isolated_paths['database_path'])
        archive_path = backup.create_backup(
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            backup_dir=str(tmp_path / 'backups'),
        )

        summary = restore.restore_backup(
            archive_path,
            database_path=isolated_paths['database_path'],
            face_database_path=isolated_paths['face_database_path'],
            datasets_dir=isolated_paths['data_dir'],
            force=True,
        )

        assert len(summary['moved_aside']) >= 1
        for moved_path in summary['moved_aside']:
            assert os.path.exists(moved_path)

    def test_rejects_missing_archive(self, tmp_path):
        with pytest.raises(restore.RestoreError):
            restore.restore_backup(str(tmp_path / 'does-not-exist.tar.gz'), database_path=str(tmp_path / 'app.db'))

    def test_rejects_corrupt_archive(self, tmp_path):
        bad_archive = tmp_path / 'corrupt.tar.gz'
        with open(bad_archive, 'wb') as f:
            f.write(b'this is not a gzip file')

        with pytest.raises(restore.RestoreError):
            restore.restore_backup(str(bad_archive), database_path=str(tmp_path / 'app.db'))

    def test_rejects_archive_missing_manifest(self, tmp_path):
        bad_archive = tmp_path / 'no-manifest.tar.gz'
        with tarfile.open(bad_archive, 'w:gz') as tar:
            fake_db = tmp_path / 'fake.db'
            fake_db.write_bytes(b'not a real db')
            tar.add(str(fake_db), arcname='database/app.db')

        with pytest.raises(restore.RestoreError):
            restore.restore_backup(str(bad_archive), database_path=str(tmp_path / 'restored' / 'app.db'))
