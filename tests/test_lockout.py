"""Tests for brute-force account lockout (separate from rate limiting,
which is covered in test_rate_limiting.py)."""
import sqlite3

import app as app_module
import config as cfg
from conftest import login_as_admin, login_as_student


class TestAdminLockout:
    def test_account_locks_after_max_failed_attempts(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LOCKOUT_MAX_FAILED_ATTEMPTS', 3)
        for _ in range(3):
            login_as_admin(client, password='wrong')

        # Even the CORRECT password must now be rejected while locked.
        resp = login_as_admin(client, password=cfg.DEFAULT_ADMIN_PASSWORD)
        assert b'locked' in resp.data.lower()

    def test_successful_login_resets_failed_attempts(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LOCKOUT_MAX_FAILED_ATTEMPTS', 5)
        login_as_admin(client, password='wrong')
        login_as_admin(client, password='wrong')
        login_as_admin(client, password=cfg.DEFAULT_ADMIN_PASSWORD)  # succeeds, should reset

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT failed_attempts FROM admins WHERE username='admin'").fetchone()
        conn.close()
        assert row[0] == 0

    def test_lockout_expires_after_duration(self, client, isolated_paths, monkeypatch):
        from datetime import datetime, timedelta
        monkeypatch.setattr(cfg, 'LOCKOUT_MAX_FAILED_ATTEMPTS', 2)
        login_as_admin(client, password='wrong')
        login_as_admin(client, password='wrong')

        # Manually expire the lockout (simulating that the lockout window passed).
        conn = sqlite3.connect(isolated_paths['database_path'])
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE admins SET locked_until=? WHERE username='admin'", (past,))
        conn.commit()
        conn.close()

        resp = login_as_admin(client, password=cfg.DEFAULT_ADMIN_PASSWORD)
        assert b'Invalid' not in resp.data
        assert b'locked' not in resp.data.lower()


class TestStudentLockout:
    def _seed_student(self, db_path, roll_no='R1', password='correctpass'):
        from werkzeug.security import generate_password_hash
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO students(name, roll_no, branch, semester, password) VALUES (?,?,?,?,?)",
            ('Test', roll_no, 'CSE', '1', generate_password_hash(password))
        )
        conn.commit()
        conn.close()

    def test_account_locks_after_max_failed_attempts(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LOCKOUT_MAX_FAILED_ATTEMPTS', 3)
        self._seed_student(isolated_paths['database_path'])
        for _ in range(3):
            login_as_student(client, 'R1', 'wrong')

        resp = login_as_student(client, 'R1', 'correctpass')
        assert resp.status_code == 403
        assert 'locked' in resp.get_json()['message'].lower()

    def test_successful_login_resets_failed_attempts(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LOCKOUT_MAX_FAILED_ATTEMPTS', 5)
        self._seed_student(isolated_paths['database_path'])
        login_as_student(client, 'R1', 'wrong')
        login_as_student(client, 'R1', 'correctpass')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT failed_attempts FROM students WHERE roll_no='R1'").fetchone()
        conn.close()
        assert row[0] == 0
