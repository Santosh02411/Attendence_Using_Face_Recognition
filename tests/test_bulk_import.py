"""Tests for admin CSV bulk student import."""
import io
import sqlite3

from werkzeug.security import check_password_hash

from conftest import login_as_admin


def _csv_bytes(content):
    return {'csv_file': (io.BytesIO(content.encode()), 'students.csv')}


class TestBulkImportPage:
    def test_requires_login(self, client):
        resp = client.get('/admin/students/bulk-import', follow_redirects=False)
        assert resp.status_code == 302

    def test_template_download_requires_login(self, client):
        resp = client.get('/admin/students/bulk-import/template.csv', follow_redirects=False)
        assert resp.status_code == 302

    def test_template_download_has_expected_columns(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.get('/admin/students/bulk-import/template.csv')
        assert resp.status_code == 200
        assert resp.content_type.startswith('text/csv')
        assert 'roll_no' in resp.get_data(as_text=True)


class TestBulkImportProcessing:
    def test_valid_rows_are_created(self, client, isolated_paths):
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\nJane Doe,21CS001,CSE,5,\nJohn Smith,21CS002,CSE,5,GoodPass123\n'
        resp = client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        assert resp.status_code == 200

        conn = sqlite3.connect(isolated_paths['database_path'])
        rows = conn.execute('SELECT name, roll_no, photo_count FROM students ORDER BY roll_no').fetchall()
        conn.close()
        assert rows == [('Jane Doe', '21CS001', 0), ('John Smith', '21CS002', 0)]

    def test_created_accounts_have_hashed_passwords(self, client, isolated_paths):
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\nJohn Smith,21CS002,CSE,5,GoodPass123\n'
        client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT password FROM students WHERE roll_no='21CS002'").fetchone()
        conn.close()
        assert row[0] != 'GoodPass123'
        assert check_password_hash(row[0], 'GoodPass123')

    def test_missing_password_generates_one_and_shows_it_once(self, client, isolated_paths):
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\nJane Doe,21CS001,CSE,5,\n'
        resp = client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        html = resp.get_data(as_text=True)
        assert '21CS001' in html
        # A generated password should appear somewhere in a <code> tag in results.
        assert '<code>' in html

    def test_duplicate_roll_number_within_file_is_skipped(self, client, isolated_paths):
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\nA,21CS001,CSE,5,Pass1234\nB,21CS001,CSE,5,Pass5678\n'
        resp = client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        html = resp.get_data(as_text=True)
        assert 'duplicate' in html.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute("SELECT COUNT(*) FROM students WHERE roll_no='21CS001'").fetchone()[0]
        conn.close()
        assert count == 1

    def test_roll_number_already_in_db_is_skipped(self, client, isolated_paths):
        from werkzeug.security import generate_password_hash
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute(
            "INSERT INTO students(name, roll_no, branch, semester, password) VALUES ('Existing','21CS001','CSE','5',?)",
            (generate_password_hash('x'),)
        )
        conn.commit()
        conn.close()

        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\nNew Person,21CS001,CSE,5,Pass1234\n'
        resp = client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        assert 'already registered' in resp.get_data(as_text=True).lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute("SELECT COUNT(*) FROM students WHERE roll_no='21CS001'").fetchone()[0]
        conn.close()
        assert count == 1  # still just the original

    def test_missing_required_field_is_skipped(self, client, isolated_paths):
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\n,21CS001,CSE,5,Pass1234\n'
        resp = client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        assert 'missing required field' in resp.get_data(as_text=True).lower()

    def test_weak_provided_password_is_skipped(self, client, isolated_paths):
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\nA,21CS001,CSE,5,weak\n'
        resp = client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute("SELECT COUNT(*) FROM students WHERE roll_no='21CS001'").fetchone()[0]
        conn.close()
        assert count == 0

    def test_bulk_import_is_logged(self, client, isolated_paths):
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\nA,21CS001,CSE,5,GoodPass123\n'
        client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='bulk_student_import'").fetchone()
        conn.close()
        assert row is not None

    def test_missing_file_shows_error(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.post('/admin/students/bulk-import', data={}, content_type='multipart/form-data', follow_redirects=True)
        assert 'choose a csv file' in resp.get_data(as_text=True).lower()

    def test_too_many_rows_rejected(self, client, isolated_paths, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, 'MAX_BULK_IMPORT_ROWS', 2)
        login_as_admin(client)
        csv_content = 'name,roll_no,branch,semester,password\n' + ''.join(
            f'S{i},R{i},CSE,5,GoodPass123\n' for i in range(5)
        )
        resp = client.post('/admin/students/bulk-import', data=_csv_bytes(csv_content), content_type='multipart/form-data', follow_redirects=True)
        assert 'too many rows' in resp.get_data(as_text=True).lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]
        conn.close()
        assert count == 0


class TestSelfServiceFaceRegistration:
    def _seed_student(self, db_path, student_id=1):
        from werkzeug.security import generate_password_hash
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?, 'Test', 'R1', 'CSE', '5', ?)",
            (student_id, generate_password_hash('pass1234'))
        )
        conn.commit()
        conn.close()

    def _login(self, client, student_id=1):
        with client.session_transaction() as sess:
            sess['user_type'] = 'student'
            sess['student_id'] = student_id

    def test_requires_login(self, client):
        resp = client.get('/student/register-face', follow_redirects=False)
        assert resp.status_code == 302

    def test_page_renders_for_logged_in_student(self, client, isolated_paths):
        self._seed_student(isolated_paths['database_path'])
        self._login(client)
        resp = client.get('/student/register-face')
        assert resp.status_code == 200

    def test_adding_photos_updates_embedding_count_and_photo_count(self, client, isolated_paths):
        import base64
        import numpy as np
        from PIL import Image
        from unittest.mock import patch
        import cv2
        import app as app_module

        self._seed_student(isolated_paths['database_path'])
        self._login(client)

        img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
        import io as io_mod
        buf = io_mod.BytesIO()
        img.save(buf, format='JPEG')
        data_url = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

        fake_box = np.array([[10, 10, 50, 50]])
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=fake_box), \
             patch.object(app_module, 'compute_embedding', return_value=np.zeros(128, dtype=np.float32)):
            resp = client.post('/student/register-face', json={'images': [data_url, data_url]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert data['total_photos'] == 2

        conn = sqlite3.connect(isolated_paths['database_path'])
        photo_count = conn.execute('SELECT photo_count FROM students WHERE id=1').fetchone()[0]
        conn.close()
        assert photo_count == 2

    def test_no_images_rejected(self, client, isolated_paths):
        self._seed_student(isolated_paths['database_path'])
        self._login(client)
        resp = client.post('/student/register-face', json={'images': []})
        assert resp.status_code == 400
