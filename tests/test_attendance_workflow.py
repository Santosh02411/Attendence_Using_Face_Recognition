"""Tests for the attendance-workflow feature set: late-entry rules,
Present/Absent/Late status, admin manual editing with a reason,
attendance freeze/lock, the correction-request workflow, and the
subject/date-range/semester reports engine.

Separate from test_attendance.py (base recognition/liveness flow) and
test_attendance_lockout.py (the anti-proxy abuse lockout) — this file is
specifically about the scheduling/workflow layer built on top of a
successful, already-verified attendance mark.
"""
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import cv2
import numpy as np
from conftest import login_as_admin
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg

FAKE_FACE_BOX = np.array([[10, 10, 50, 50]])


def _real_jpeg_data_url():
    import base64
    import io

    from PIL import Image
    img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _frame_burst(n=None):
    n = n or cfg.LIVENESS_FRAME_COUNT
    return [_real_jpeg_data_url() for _ in range(n)]


def _seed_student(db_path, student_id, name, roll_no, semester='1'):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, 'CSE', semester, generate_password_hash('pw'))
    )
    conn.commit()
    conn.close()


def _seed_session(db_path, session_id, date_str, time_str, active=1, end_date=None, subject_id=1):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO subjects(id, name, code) VALUES (?, 'Sub', 'S1')", (subject_id,))
    conn.execute(
        'INSERT INTO sessions(id, subject_id, title, date, time, end_date, active) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (session_id, subject_id, f'Sess{session_id}', date_str, time_str, end_date, active)
    )
    conn.commit()
    conn.close()


def _unit_vector(index):
    v = np.zeros(128, dtype=np.float32)
    v[index] = 1.0
    return v


def _login_student(client, student_id):
    with client.session_transaction() as sess:
        sess['user_type'] = 'student'
        sess['student_id'] = student_id


def _base_mocks(embedding):
    return (
        patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX),
        patch.object(app_module, 'compute_embedding', return_value=embedding),
        patch.object(app_module, 'check_liveness_sequence', return_value=(True, [FAKE_FACE_BOX[0]] * cfg.LIVENESS_FRAME_COUNT)),
        patch.object(app_module, 'assess_image_quality', return_value=[]),
    )


def _mark_attendance(client, session_id, embedding):
    mocks = _base_mocks(embedding)
    with mocks[0], mocks[1], mocks[2], mocks[3]:
        return client.post('/student/attend/mark', json={'session_id': session_id, 'images': _frame_burst()})


class TestLateEntryRule:
    def test_disabled_by_default_always_present(self, client, isolated_paths):
        """LATE_ENTRY_ENFORCEMENT_ENABLED defaults off — a session dated
        far in the past (as most test fixtures use) still marks Present,
        matching the project's historical behavior."""
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '01-01-2026', '10:00')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        assert resp.get_json()['status'] == 'present'
        assert resp.get_json()['attendance_status'] == 'Present'

    def test_within_grace_period_marks_present(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LATE_ENTRY_ENFORCEMENT_ENABLED', True)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_GRACE_MINUTES', 10)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_LATE_WINDOW_MINUTES', 20)
        now = datetime.now()
        start = now - timedelta(minutes=5)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, start.strftime('%d-%m-%Y'), start.strftime('%H:%M'))
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        assert resp.get_json()['status'] == 'present'
        assert resp.get_json()['attendance_status'] == 'Present'

    def test_past_grace_but_within_late_window_marks_late(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LATE_ENTRY_ENFORCEMENT_ENABLED', True)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_GRACE_MINUTES', 10)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_LATE_WINDOW_MINUTES', 20)
        now = datetime.now()
        start = now - timedelta(minutes=15)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, start.strftime('%d-%m-%Y'), start.strftime('%H:%M'))
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        payload = resp.get_json()
        assert payload['status'] == 'present'
        assert payload['attendance_status'] == 'Late'

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        conn.close()
        assert row[0] == 'Late'

    def test_beyond_late_window_is_rejected(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LATE_ENTRY_ENFORCEMENT_ENABLED', True)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_GRACE_MINUTES', 10)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_LATE_WINDOW_MINUTES', 20)
        now = datetime.now()
        start = now - timedelta(minutes=45)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, start.strftime('%d-%m-%Y'), start.strftime('%H:%M'))
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        payload = resp.get_json()
        assert payload['status'] == 'error'
        assert 'closed' in payload['message'].lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT 1 FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        conn.close()
        assert row is None


class TestDuplicateAttendancePrevention:
    def test_second_mark_returns_already_marked(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '01-01-2026', '10:00')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        first = _mark_attendance(client, 1, embedding)
        assert first.get_json()['status'] == 'present'
        second = _mark_attendance(client, 1, embedding)
        assert second.get_json()['status'] == 'already_marked'

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM attendance WHERE student_id=1 AND session_id=1').fetchone()[0]
        conn.close()
        assert count == 1

    def test_atomic_insert_helper_prevents_race(self, isolated_paths):
        """Directly exercises _insert_attendance_if_absent — the second
        call must report nothing was inserted even though the first
        call's check-then-act window is exactly what this defends
        against."""
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '01-01-2026', '10:00')
        monkeypatch_db_path = isolated_paths['database_path']

        first = app_module._insert_attendance_if_absent(1, 1, 'Present', '2026-01-01T10:00:00')
        second = app_module._insert_attendance_if_absent(1, 1, 'Present', '2026-01-01T10:00:05')
        assert first is True
        assert second is False

        conn = sqlite3.connect(monkeypatch_db_path)
        count = conn.execute('SELECT COUNT(*) FROM attendance WHERE student_id=1 AND session_id=1').fetchone()[0]
        conn.close()
        assert count == 1


class TestAdminManualEditingAndFreeze:
    def test_override_can_mark_late_with_reason(self, client, isolated_paths):
        recent_date = (datetime.now() - timedelta(days=1)).strftime('%d-%m-%Y')
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, recent_date, '10:00', active=0)
        login_as_admin(client)

        resp = client.post('/admin/session/1/override', data={'student_id': 1, 'action': 'late', 'reason': 'Bus delay, verified with driver'})
        assert resp.status_code in (302, 200)

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, note FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        conn.close()
        assert row[0] == 'Late'
        assert 'Bus delay' in row[1]

    def test_override_clear_removes_the_record(self, client, isolated_paths):
        recent_date = (datetime.now() - timedelta(days=1)).strftime('%d-%m-%Y')
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, recent_date, '10:00', active=0)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Present', 'x')")
        conn.commit()
        conn.close()
        login_as_admin(client)

        client.post('/admin/session/1/override', data={'student_id': 1, 'action': 'clear', 'reason': 'Entered in error'})

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT 1 FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        conn.close()
        assert row is None

    def test_override_blocked_once_session_is_locked(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_EDIT_LOCK_ENABLED', True)
        monkeypatch.setattr(cfg, 'ATTENDANCE_EDIT_LOCK_DAYS', 30)
        old_date = (datetime.now() - timedelta(days=60)).strftime('%d-%m-%Y')
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, old_date, '10:00', active=0)
        login_as_admin(client)

        resp = client.post('/admin/session/1/override', data={'student_id': 1, 'action': 'present'}, follow_redirects=True)
        assert b'locked' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT 1 FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        conn.close()
        assert row is None

    def test_override_allowed_within_lock_window(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_EDIT_LOCK_ENABLED', True)
        monkeypatch.setattr(cfg, 'ATTENDANCE_EDIT_LOCK_DAYS', 30)
        recent_date = (datetime.now() - timedelta(days=5)).strftime('%d-%m-%Y')
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, recent_date, '10:00', active=0)
        login_as_admin(client)

        client.post('/admin/session/1/override', data={'student_id': 1, 'action': 'present'})

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        conn.close()
        assert row[0] == 'Present'

    def test_override_requires_admin_login(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '01-01-2026', '10:00', active=0)
        resp = client.post('/admin/session/1/override', data={'student_id': 1, 'action': 'present'}, follow_redirects=False)
        assert resp.status_code == 302


class TestCorrectionRequestWorkflow:
    def _seed(self, isolated_paths, session_date=None):
        if session_date is None:
            session_date = (datetime.now() - timedelta(days=1)).strftime('%d-%m-%Y')
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, session_date, '10:00', active=0)

    def test_student_can_submit_a_request(self, client, isolated_paths):
        self._seed(isolated_paths)
        _login_student(client, 1)

        resp = client.post('/student/attendance/correction-request', data={
            'session_id': 1, 'requested_status': 'Present', 'reason': 'I was in the room, camera missed me'
        }, follow_redirects=True)
        assert b'submitted' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT requested_status, status, reason FROM attendance_correction_requests WHERE student_id=1 AND session_id=1").fetchone()
        conn.close()
        assert row == ('Present', 'pending', 'I was in the room, camera missed me')

    def test_reason_is_required(self, client, isolated_paths):
        self._seed(isolated_paths)
        _login_student(client, 1)
        resp = client.post('/student/attendance/correction-request', data={
            'session_id': 1, 'requested_status': 'Present', 'reason': ''
        }, follow_redirects=True)
        assert b'reason' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM attendance_correction_requests').fetchone()[0]
        conn.close()
        assert count == 0

    def test_duplicate_pending_request_blocked(self, client, isolated_paths):
        self._seed(isolated_paths)
        _login_student(client, 1)
        client.post('/student/attendance/correction-request', data={'session_id': 1, 'requested_status': 'Present', 'reason': 'first'})
        resp = client.post('/student/attendance/correction-request', data={'session_id': 1, 'requested_status': 'Present', 'reason': 'second'}, follow_redirects=True)
        assert b'already have a pending' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM attendance_correction_requests').fetchone()[0]
        conn.close()
        assert count == 1

    def test_request_blocked_when_session_locked(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_EDIT_LOCK_ENABLED', True)
        monkeypatch.setattr(cfg, 'ATTENDANCE_EDIT_LOCK_DAYS', 30)
        old_date = (datetime.now() - timedelta(days=60)).strftime('%d-%m-%Y')
        self._seed(isolated_paths, session_date=old_date)
        _login_student(client, 1)

        resp = client.post('/student/attendance/correction-request', data={
            'session_id': 1, 'requested_status': 'Present', 'reason': 'too late now'
        }, follow_redirects=True)
        assert b'closed' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM attendance_correction_requests').fetchone()[0]
        conn.close()
        assert count == 0

    def test_admin_can_approve_a_request(self, client, isolated_paths):
        self._seed(isolated_paths)
        _login_student(client, 1)
        client.post('/student/attendance/correction-request', data={'session_id': 1, 'requested_status': 'Present', 'reason': 'was there'})

        conn = sqlite3.connect(isolated_paths['database_path'])
        request_id = conn.execute('SELECT id FROM attendance_correction_requests').fetchone()[0]
        conn.close()

        login_as_admin(client)
        resp = client.post(f'/admin/attendance/corrections/{request_id}/approve', follow_redirects=True)
        assert b'approved' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        att = conn.execute('SELECT status FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        req_status = conn.execute('SELECT status, resolved_by FROM attendance_correction_requests WHERE id=?', (request_id,)).fetchone()
        conn.close()
        assert att[0] == 'Present'
        assert req_status[0] == 'approved'
        assert req_status[1] == 'admin'

    def test_admin_can_reject_a_request(self, client, isolated_paths):
        self._seed(isolated_paths)
        _login_student(client, 1)
        client.post('/student/attendance/correction-request', data={'session_id': 1, 'requested_status': 'Present', 'reason': 'was there'})

        conn = sqlite3.connect(isolated_paths['database_path'])
        request_id = conn.execute('SELECT id FROM attendance_correction_requests').fetchone()[0]
        conn.close()

        login_as_admin(client)
        resp = client.post(f'/admin/attendance/corrections/{request_id}/reject', data={'admin_note': 'No supporting evidence'}, follow_redirects=True)
        assert b'rejected' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        att = conn.execute('SELECT 1 FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        req_row = conn.execute('SELECT status, admin_note FROM attendance_correction_requests WHERE id=?', (request_id,)).fetchone()
        conn.close()
        assert att is None
        assert req_row == ('rejected', 'No supporting evidence')

    def test_already_resolved_request_cannot_be_resolved_again(self, client, isolated_paths):
        self._seed(isolated_paths)
        _login_student(client, 1)
        client.post('/student/attendance/correction-request', data={'session_id': 1, 'requested_status': 'Present', 'reason': 'was there'})
        conn = sqlite3.connect(isolated_paths['database_path'])
        request_id = conn.execute('SELECT id FROM attendance_correction_requests').fetchone()[0]
        conn.close()

        login_as_admin(client)
        client.post(f'/admin/attendance/corrections/{request_id}/reject')
        resp = client.post(f'/admin/attendance/corrections/{request_id}/approve', follow_redirects=True)
        assert b'already been resolved' in resp.data.lower()

    def test_corrections_page_requires_admin(self, client):
        resp = client.get('/admin/attendance/corrections', follow_redirects=False)
        assert resp.status_code == 302

    def test_student_history_lists_own_requests(self, client, isolated_paths):
        self._seed(isolated_paths)
        _login_student(client, 1)
        client.post('/student/attendance/correction-request', data={'session_id': 1, 'requested_status': 'Present', 'reason': 'was there'})

        resp = client.get('/student/history')
        assert b'was there' in resp.data


class TestAttendanceReports:
    def _seed_report_data(self, db_path):
        _seed_student(db_path, 1, 'Alice', 'R1', semester='1')
        _seed_student(db_path, 2, 'Bob', 'R2', semester='2')
        _seed_session(db_path, 1, '01-01-2026', '10:00', active=0, subject_id=1)
        _seed_session(db_path, 2, '02-01-2026', '10:00', active=0, subject_id=1)
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Present', 'x')")
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 2, 'Present', 'x')")
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (2, 1, 'Present', 'x')")
        # Bob has no row for session 2 -> counts as Absent
        conn.commit()
        conn.close()

    def test_percentage_calculation(self, isolated_paths):
        self._seed_report_data(isolated_paths['database_path'])
        rows, _trend = app_module._compute_attendance_report()
        by_name = {r['name']: r for r in rows}
        assert by_name['Alice']['percentage'] == 100.0
        assert by_name['Bob']['percentage'] == 50.0
        assert by_name['Bob']['below_threshold'] is True

    def test_semester_filter_narrows_students(self, isolated_paths):
        self._seed_report_data(isolated_paths['database_path'])
        rows, _trend = app_module._compute_attendance_report(semester='1')
        names = {r['name'] for r in rows}
        assert names == {'Alice'}

    def test_date_range_filter_narrows_sessions(self, isolated_paths):
        self._seed_report_data(isolated_paths['database_path'])
        from datetime import date
        rows, _trend = app_module._compute_attendance_report(start_date=date(2026, 1, 1), end_date=date(2026, 1, 1))
        by_name = {r['name']: r for r in rows}
        assert by_name['Alice']['total_sessions'] == 1
        assert by_name['Alice']['percentage'] == 100.0
        assert by_name['Bob']['total_sessions'] == 1
        assert by_name['Bob']['percentage'] == 100.0

    def test_late_counts_as_present_when_configured(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LATE_COUNTS_AS_PRESENT', True)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '01-01-2026', '10:00', active=0)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Late', 'x')")
        conn.commit()
        conn.close()

        rows, _trend = app_module._compute_attendance_report()
        assert rows[0]['percentage'] == 100.0
        assert rows[0]['late'] == 1

    def test_late_does_not_count_as_present_when_disabled(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LATE_COUNTS_AS_PRESENT', False)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '01-01-2026', '10:00', active=0)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Late', 'x')")
        conn.commit()
        conn.close()

        rows, _trend = app_module._compute_attendance_report()
        assert rows[0]['percentage'] == 0.0

    def test_trend_buckets_by_day(self, isolated_paths):
        self._seed_report_data(isolated_paths['database_path'])
        _rows, trend = app_module._compute_attendance_report(trend_granularity='day')
        labels = [t['label'] for t in trend]
        assert '2026-01-01' in labels
        assert '2026-01-02' in labels

    def test_reports_page_renders_with_filters(self, client, isolated_paths):
        self._seed_report_data(isolated_paths['database_path'])
        login_as_admin(client)
        resp = client.get('/admin/reports?semester=1&granularity=week')
        assert resp.status_code == 200
        assert b'Alice' in resp.data
        assert b'Bob' not in resp.data

    def test_reports_page_requires_admin(self, client):
        resp = client.get('/admin/reports', follow_redirects=False)
        assert resp.status_code == 302

    def test_export_csv_matches_filters(self, client, isolated_paths):
        self._seed_report_data(isolated_paths['database_path'])
        login_as_admin(client)
        resp = client.get('/admin/reports/export?semester=2')
        text = resp.get_data(as_text=True)
        assert 'Bob' in text
        assert 'Alice' not in text
