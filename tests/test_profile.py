"""Tests for the student profile page and self-service password changes
(both student and admin)."""
import sqlite3

from conftest import login_as_admin
from werkzeug.security import check_password_hash


def _seed_student(db_path, password='OldPass123'):
    from werkzeug.security import generate_password_hash
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (1,'Test','R1','CSE','5',?)",
        (generate_password_hash(password),)
    )
    conn.commit()
    conn.close()


def _login_student(client, student_id=1, name='Test'):
    with client.session_transaction() as sess:
        sess['user_type'] = 'student'
        sess['student_id'] = student_id
        sess['student_name'] = name


class TestStudentProfilePage:
    def test_requires_login(self, client):
        resp = client.get('/student/profile', follow_redirects=False)
        assert resp.status_code == 302

    def test_shows_student_details(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'])
        _login_student(client)
        resp = client.get('/student/profile')
        assert resp.status_code == 200
        assert b'Test' in resp.data
        assert b'R1' in resp.data
        assert b'CSE' in resp.data

    def test_shows_zero_attendance_for_new_student(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'])
        _login_student(client)
        resp = client.get('/student/profile')
        assert b'0%' in resp.data or b'0.0%' in resp.data

    def test_shows_embedding_count(self, client, isolated_paths):
        import numpy as np

        import app as app_module
        _seed_student(isolated_paths['database_path'])
        app_module.store_embedding(1, np.zeros(128, dtype=np.float32))
        app_module.store_embedding(1, np.ones(128, dtype=np.float32))
        _login_student(client)
        resp = client.get('/student/profile')
        assert b'2' in resp.data  # embedding count shown somewhere


class TestStudentPasswordChange:
    def test_requires_login(self, client):
        resp = client.post('/student/profile/change-password', data={
            'current_password': 'x', 'new_password': 'newpass123', 'confirm_password': 'newpass123',
        }, follow_redirects=False)
        assert resp.status_code == 302

    def test_wrong_current_password_rejected(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], password='OldPass123')
        _login_student(client)
        resp = client.post('/student/profile/change-password', data={
            'current_password': 'wrong', 'new_password': 'newpass123', 'confirm_password': 'newpass123',
        }, follow_redirects=True)
        assert b'incorrect' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT password FROM students WHERE id=1").fetchone()
        conn.close()
        assert check_password_hash(row[0], 'OldPass123')  # unchanged

    def test_mismatched_confirmation_rejected(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], password='OldPass123')
        _login_student(client)
        resp = client.post('/student/profile/change-password', data={
            'current_password': 'OldPass123', 'new_password': 'newpass123', 'confirm_password': 'different456',
        }, follow_redirects=True)
        assert b'not match' in resp.data.lower() or b'do not match' in resp.data.lower()

    def test_weak_new_password_rejected(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], password='OldPass123')
        _login_student(client)
        client.post('/student/profile/change-password', data={
            'current_password': 'OldPass123', 'new_password': 'weak', 'confirm_password': 'weak',
        }, follow_redirects=True)
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT password FROM students WHERE id=1").fetchone()
        conn.close()
        assert check_password_hash(row[0], 'OldPass123')  # unchanged

    def test_successful_password_change(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], password='OldPass123')
        _login_student(client)
        resp = client.post('/student/profile/change-password', data={
            'current_password': 'OldPass123', 'new_password': 'BrandNewPass123', 'confirm_password': 'BrandNewPass123',
        }, follow_redirects=True)
        assert b'updated' in resp.data.lower()
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT password FROM students WHERE id=1").fetchone()
        conn.close()
        assert check_password_hash(row[0], 'BrandNewPass123')

    def test_password_change_is_logged(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], password='OldPass123')
        _login_student(client)
        client.post('/student/profile/change-password', data={
            'current_password': 'OldPass123', 'new_password': 'BrandNewPass123', 'confirm_password': 'BrandNewPass123',
        })
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='student_changed_own_password'").fetchone()
        conn.close()
        assert row is not None


class TestAdminPasswordChange:
    def test_wrong_current_password_rejected(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.post('/admin/settings/change-password', data={
            'current_password': 'wrong', 'new_password': 'NewAdminPass123', 'confirm_password': 'NewAdminPass123',
        }, follow_redirects=True)
        assert b'incorrect' in resp.data.lower()

    def test_successful_change_updates_hash_and_allows_new_login(self, client, isolated_paths):
        import config as cfg
        login_as_admin(client)
        resp = client.post('/admin/settings/change-password', data={
            'current_password': cfg.DEFAULT_ADMIN_PASSWORD,
            'new_password': 'NewAdminPass123', 'confirm_password': 'NewAdminPass123',
        }, follow_redirects=True)
        assert b'updated' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT password FROM admins WHERE username='admin'").fetchone()
        conn.close()
        assert check_password_hash(row[0], 'NewAdminPass123')
        assert not check_password_hash(row[0], cfg.DEFAULT_ADMIN_PASSWORD)

    def test_mismatched_confirmation_rejected(self, client, isolated_paths):
        import config as cfg
        login_as_admin(client)
        resp = client.post('/admin/settings/change-password', data={
            'current_password': cfg.DEFAULT_ADMIN_PASSWORD,
            'new_password': 'NewAdminPass123', 'confirm_password': 'Different456',
        }, follow_redirects=True)
        assert b'not match' in resp.data.lower() or b'do not match' in resp.data.lower()

    def test_requires_login(self, client):
        resp = client.post('/admin/settings/change-password', data={
            'current_password': 'x', 'new_password': 'y', 'confirm_password': 'y',
        }, follow_redirects=False)
        assert resp.status_code == 302
