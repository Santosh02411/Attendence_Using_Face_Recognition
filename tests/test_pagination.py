"""Tests for pagination: student list, attendance records, and audit log."""
import sqlite3

from conftest import login_as_admin
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg


def _seed_students(db_path, count):
    conn = sqlite3.connect(db_path)
    for i in range(count):
        conn.execute(
            'INSERT INTO students(name, roll_no, branch, semester, password) VALUES (?,?,?,?,?)',
            (f'Student {i:03d}', f'R{i:03d}', 'CSE', '1', generate_password_hash('pass1234'))
        )
    conn.commit()
    conn.close()


class TestGetPaginationHelper:
    def test_single_page_when_under_per_page(self):
        with app_module.app.test_request_context('/?page=1'):
            p = app_module.get_pagination(total_count=5, per_page=25)
        assert p['total_pages'] == 1
        assert p['has_prev'] is False
        assert p['has_next'] is False

    def test_multiple_pages_computed_correctly(self):
        with app_module.app.test_request_context('/?page=1'):
            p = app_module.get_pagination(total_count=55, per_page=25)
        assert p['total_pages'] == 3

    def test_zero_items_still_yields_one_page(self):
        with app_module.app.test_request_context('/?page=1'):
            p = app_module.get_pagination(total_count=0, per_page=25)
        assert p['total_pages'] == 1
        assert p['offset'] == 0


class TestStudentListPagination:
    def test_first_page_shows_per_page_count(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'STUDENTS_PER_PAGE', 10)
        _seed_students(isolated_paths['database_path'], 25)
        login_as_admin(client)
        resp = client.get('/admin/students')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'R009' in html
        assert 'R010' not in html

    def test_second_page_shows_next_batch(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'STUDENTS_PER_PAGE', 10)
        _seed_students(isolated_paths['database_path'], 25)
        login_as_admin(client)
        resp = client.get('/admin/students?page=2')
        html = resp.get_data(as_text=True)
        assert 'R010' in html
        assert 'R009' not in html

    def test_out_of_range_page_is_clamped(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'STUDENTS_PER_PAGE', 10)
        _seed_students(isolated_paths['database_path'], 15)
        login_as_admin(client)
        resp = client.get('/admin/students?page=999')
        assert resp.status_code == 200

    def test_search_filters_and_paginates_together(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'STUDENTS_PER_PAGE', 10)
        _seed_students(isolated_paths['database_path'], 15)
        login_as_admin(client)
        resp = client.get('/admin/students?q=R001')
        html = resp.get_data(as_text=True)
        assert 'R001' in html
        assert 'R002' not in html


class TestAuditLogPagination:
    def test_paginates_entries(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'AUDIT_LOG_PER_PAGE', 5)
        login_as_admin(client)
        for i in range(10):
            app_module.log_audit('admin', 'admin', f'test_action_{i}')
        resp = client.get('/admin/audit-log')
        assert resp.status_code == 200
        assert 'Page 1 of' in resp.get_data(as_text=True)

    def test_requires_login(self, client):
        resp = client.get('/admin/audit-log', follow_redirects=False)
        assert resp.status_code == 302


class TestAttendanceRecordsPagination:
    def test_requires_login(self, client):
        resp = client.get('/admin/attendance', follow_redirects=False)
        assert resp.status_code == 302

    def test_paginates_records(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_RECORDS_PER_PAGE', 5)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO students(id,name,roll_no,branch,semester,password) VALUES (1,'A','R1','CSE','1','x')")
        conn.execute("INSERT INTO subjects(id,name,code) VALUES (1,'Sub','S1')")
        conn.execute("INSERT INTO sessions(id,subject_id,title,date,time,active) VALUES (1,1,'Sess','01-01-2026','10:00',0)")
        for i in range(12):
            conn.execute(
                "INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Present', ?)",
                (f'2026-01-0{(i % 9) + 1}T10:00:00',)
            )
        conn.commit()
        conn.close()

        login_as_admin(client)
        resp = client.get('/admin/attendance')
        assert resp.status_code == 200
        assert 'Page 1 of 3' in resp.get_data(as_text=True)

        resp2 = client.get('/admin/attendance?page=3')
        assert resp2.status_code == 200
