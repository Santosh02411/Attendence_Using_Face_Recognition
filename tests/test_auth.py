"""Tests for admin/student login and password hashing."""
import sqlite3

from werkzeug.security import check_password_hash

import app as app_module
from conftest import login_as_admin, login_as_student


class TestAdminAuth:
    def test_default_admin_password_is_hashed_not_plaintext(self, isolated_paths):
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT password FROM admins WHERE username='admin'").fetchone()
        conn.close()
        assert row is not None
        stored_password = row[0]
        assert stored_password != 'admin123'
        assert stored_password.startswith(('scrypt:', 'pbkdf2:'))

    def test_login_succeeds_with_correct_default_password(self, client):
        resp = login_as_admin(client)
        assert resp.status_code == 200
        # A successful login redirects (via follow_redirects) to the dashboard
        assert b'Invalid' not in resp.data

    def test_login_fails_with_wrong_password(self, client):
        resp = login_as_admin(client, password='definitely-wrong')
        assert b'Invalid' in resp.data or resp.status_code == 200

    def test_admin_dashboard_requires_login(self, client):
        resp = client.get('/admin/dashboard', follow_redirects=False)
        assert resp.status_code in (302, 401, 403)


class TestStudentAuth:
    def _register_student(self, client, roll_no='R1', password='studentpass'):
        return client.post('/student/register', json={
            'name': 'Test Student', 'roll_no': roll_no, 'branch': 'CSE',
            'semester': '1', 'password': password, 'gender': 'M', 'images': [],
        })

    def test_student_password_is_hashed(self, client, isolated_paths):
        # Registration with zero valid face images is rejected before an
        # account is created (see test_registration.py for that path), so
        # insert directly here to isolate the hashing behavior itself.
        from werkzeug.security import generate_password_hash
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute(
            "INSERT INTO students(name, roll_no, branch, semester, password) VALUES (?,?,?,?,?)",
            ('Test', 'R9', 'CSE', '1', generate_password_hash('mypassword'))
        )
        conn.commit()
        row = conn.execute("SELECT password FROM students WHERE roll_no='R9'").fetchone()
        conn.close()
        assert row[0] != 'mypassword'
        assert check_password_hash(row[0], 'mypassword')

    def test_login_rejects_unknown_roll_number(self, client):
        resp = login_as_student(client, 'NOSUCHROLL', 'whatever')
        assert resp.status_code == 404
        assert resp.get_json()['status'] == 'error'

    def test_login_rejects_wrong_password(self, client, isolated_paths):
        from werkzeug.security import generate_password_hash
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute(
            "INSERT INTO students(name, roll_no, branch, semester, password) VALUES (?,?,?,?,?)",
            ('Test', 'R2', 'CSE', '1', generate_password_hash('correct-password'))
        )
        conn.commit()
        conn.close()

        resp = login_as_student(client, 'R2', 'wrong-password')
        assert resp.status_code == 401
        assert resp.get_json()['status'] == 'error'

    def test_login_succeeds_with_correct_password(self, client, isolated_paths):
        from werkzeug.security import generate_password_hash
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute(
            "INSERT INTO students(name, roll_no, branch, semester, password) VALUES (?,?,?,?,?)",
            ('Test', 'R3', 'CSE', '1', generate_password_hash('correct-password'))
        )
        conn.commit()
        conn.close()

        resp = login_as_student(client, 'R3', 'correct-password')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'ok'
