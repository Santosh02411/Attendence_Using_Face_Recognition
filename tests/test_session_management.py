"""Tests for the session-management feature set: status lifecycle,
cancellation/rescheduling, per-session attendance-window/grace-period/
network overrides, student-group (branch/semester) assignment, the
per-session attendance summary, and automatic closing once the
attendance window elapses.
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


def _unit_vector(index):
    v = np.zeros(128, dtype=np.float32)
    v[index] = 1.0
    return v


def _seed_student(db_path, student_id, name, roll_no, branch='CSE', semester='1'):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, branch, semester, generate_password_hash('pw'))
    )
    conn.commit()
    conn.close()


def _seed_session(db_path, session_id, date_str, time_str, active=1, status='active',
                   restrict_branch=None, restrict_semester=None, allowed_networks=None,
                   attendance_window_minutes=None, grace_period_minutes=None, subject_id=1):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO subjects(id, name, code) VALUES (?, 'Sub', 'S1')", (subject_id,))
    conn.execute(
        '''INSERT INTO sessions(id, subject_id, title, date, time, end_date, end_time, active, status,
           restrict_branch, restrict_semester, allowed_networks, attendance_window_minutes, grace_period_minutes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (session_id, subject_id, f'Sess{session_id}', date_str, time_str, date_str, '23:59', active, status,
         restrict_branch, restrict_semester, allowed_networks, attendance_window_minutes, grace_period_minutes)
    )
    conn.commit()
    conn.close()


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


class TestSessionLifecycle:
    def test_new_session_defaults_to_scheduled(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/sessions', data={
            'title': 'T1', 'subject_name': 'Sub', 'subject_code': 'S1',
            'date': '2026-08-26', 'end_date': '2026-08-26',
            'start_h': '10', 'start_m': '0', 'start_p': 'AM',
            'end_h': '11', 'end_m': '0', 'end_p': 'AM',
        })
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active FROM sessions').fetchone()
        conn.close()
        assert row == ('scheduled', 0)

    def test_start_sets_active_status(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=0, status='scheduled')
        login_as_admin(client)
        client.post('/admin/session/1/start')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('active', 1)

    def test_stop_sets_completed_status(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active')
        login_as_admin(client)
        client.post('/admin/session/1/stop')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('completed', 0)


class TestSessionCancellationAndReschedule:
    def test_cancel_sets_status_and_reason(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active')
        login_as_admin(client)
        client.post('/admin/session/1/cancel', data={'reason': 'Instructor unavailable'})
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active, cancellation_reason FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('cancelled', 0, 'Instructor unavailable')

    def test_cancelled_session_blocks_marking_with_specific_message(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        login_as_admin(client)
        client.post('/admin/session/1/cancel', data={'reason': 'rain'})

        _login_student(client, 1)
        resp = _mark_attendance(client, 1, embedding)
        payload = resp.get_json()
        assert payload['status'] == 'error'
        assert 'cancelled' in payload['message'].lower()

    def test_reschedule_updates_date_and_reopens_cancelled_session(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=0, status='cancelled')
        login_as_admin(client)
        client.post('/admin/session/1/reschedule', data={
            'date': '2026-09-01', 'end_date': '2026-09-01', 'time': '11:00', 'end_time': '12:00'
        })
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, date, time FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('scheduled', '01-09-2026', '11:00')

    def test_reschedule_blocked_for_completed_session(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=0, status='completed')
        login_as_admin(client)
        resp = client.post('/admin/session/1/reschedule', data={
            'date': '2026-09-01', 'end_date': '2026-09-01', 'time': '11:00', 'end_time': '12:00'
        }, follow_redirects=True)
        assert b'cannot be rescheduled' in resp.data.lower()
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT date FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row[0] == '26-08-2026'

    def test_reschedule_respects_overlap_detection(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=0, status='scheduled')
        _seed_session(isolated_paths['database_path'], 2, '27-08-2026', '10:00', active=0, status='scheduled')
        login_as_admin(client)
        resp = client.post('/admin/session/1/reschedule', data={
            'date': '2026-08-27', 'end_date': '2026-08-27', 'time': '10:00', 'end_time': '11:00'
        }, follow_redirects=True)
        assert b'overlaps' in resp.data.lower()

    def test_cancelled_session_excluded_from_overlap_detection(self, client, isolated_paths):
        """A cancelled session's time slot is free to reuse."""
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=0, status='cancelled')
        login_as_admin(client)
        resp = client.post('/admin/sessions', data={
            'title': 'New', 'subject_name': 'Sub2', 'subject_code': 'S2',
            'date': '2026-08-26', 'end_date': '2026-08-26',
            'start_h': '10', 'start_m': '0', 'start_p': 'AM',
            'end_h': '11', 'end_m': '0', 'end_p': 'AM',
        }, follow_redirects=True)
        assert b'overlaps' not in resp.data.lower()

    def test_cancel_requires_admin(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00')
        resp = client.post('/admin/session/1/cancel', follow_redirects=False)
        assert resp.status_code == 302


class TestPerSessionAttendanceWindowAndGrace:
    def test_session_override_applies_over_global_defaults(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LATE_ENTRY_ENFORCEMENT_ENABLED', True)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_GRACE_MINUTES', 10)
        monkeypatch.setattr(cfg, 'LATE_ENTRY_LATE_WINDOW_MINUTES', 20)
        # Session overrides: grace=2, window=5 -- tighter than the globals.
        now = datetime.now()
        start = now - timedelta(minutes=3)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, start.strftime('%d-%m-%Y'), start.strftime('%H:%M'),
                      active=1, status='active', grace_period_minutes=2, attendance_window_minutes=5)
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        # 3 minutes elapsed > session's grace (2) but < session's window (5) -> Late
        resp = _mark_attendance(client, 1, embedding)
        payload = resp.get_json()
        assert payload['status'] == 'present'
        assert payload['attendance_status'] == 'Late'

    def test_session_network_override_replaces_global_list(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['192.168.99.0/24'])
        now = datetime.now()
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, now.strftime('%d-%m-%Y'), now.strftime('%H:%M'), active=1, status='active',
                      allowed_networks='127.0.0.1')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        # Test client requests come from 127.0.0.1 -- allowed by the
        # session override even though it's outside the global list.
        resp = _mark_attendance(client, 1, embedding)
        assert resp.get_json()['status'] == 'present'

    def test_session_network_override_can_block_where_global_would_allow(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', [])  # global: no restriction
        now = datetime.now()
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, now.strftime('%d-%m-%Y'), now.strftime('%H:%M'), active=1, status='active',
                      allowed_networks='10.0.0.0/24')  # session: restricted, and 127.0.0.1 isn't in it
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        payload = resp.get_json()
        assert payload['status'] == 'error'
        assert 'network' in payload['message'].lower()


class TestStudentGroupAssignment:
    def test_matching_group_can_mark(self, client, isolated_paths):
        now = datetime.now()
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1', branch='CSE', semester='3')
        _seed_session(isolated_paths['database_path'], 1, now.strftime('%d-%m-%Y'), now.strftime('%H:%M'), active=1, status='active',
                      restrict_branch='CSE', restrict_semester='3')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        assert resp.get_json()['status'] == 'present'

    def test_wrong_branch_is_blocked(self, client, isolated_paths):
        now = datetime.now()
        _seed_student(isolated_paths['database_path'], 1, 'Bob', 'R2', branch='ECE', semester='3')
        _seed_session(isolated_paths['database_path'], 1, now.strftime('%d-%m-%Y'), now.strftime('%H:%M'), active=1, status='active',
                      restrict_branch='CSE', restrict_semester='3')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        payload = resp.get_json()
        assert payload['status'] == 'error'
        assert 'branch' in payload['message'].lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT 1 FROM attendance WHERE student_id=1 AND session_id=1').fetchone()
        conn.close()
        assert row is None

    def test_wrong_semester_is_blocked(self, client, isolated_paths):
        now = datetime.now()
        _seed_student(isolated_paths['database_path'], 1, 'Carl', 'R3', branch='CSE', semester='5')
        _seed_session(isolated_paths['database_path'], 1, now.strftime('%d-%m-%Y'), now.strftime('%H:%M'), active=1, status='active',
                      restrict_branch='CSE', restrict_semester='3')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        assert resp.get_json()['status'] == 'error'

    def test_unrestricted_session_open_to_everyone(self, client, isolated_paths):
        """No restrict_branch/restrict_semester set (the default) -- same
        as every session before this feature existed."""
        now = datetime.now()
        _seed_student(isolated_paths['database_path'], 1, 'Dana', 'R4', branch='ME', semester='7')
        _seed_session(isolated_paths['database_path'], 1, now.strftime('%d-%m-%Y'), now.strftime('%H:%M'), active=1, status='active')
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        assert resp.get_json()['status'] == 'present'

    def test_student_attend_page_blocks_wrong_group(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Eve', 'R5', branch='ECE', semester='1')
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active',
                      restrict_branch='CSE')
        _login_student(client, 1)
        resp = client.get('/student/attend?session_id=1', follow_redirects=True)
        assert b'restricted' in resp.data.lower()

    def test_get_active_sessions_filters_by_group_for_logged_in_student(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Frank', 'R6', branch='ECE', semester='1')
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active',
                      restrict_branch='CSE')
        _login_student(client, 1)
        resp = client.get('/get-active-sessions')
        data = resp.get_json()
        assert data['sessions'] == []


class TestSessionAttendanceSummary:
    def test_summary_counts_present_late_absent(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_student(isolated_paths['database_path'], 2, 'Bob', 'R2')
        _seed_student(isolated_paths['database_path'], 3, 'Carl', 'R3')
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active')
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Present', 'x')")
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (2, 1, 'Late', 'x')")
        conn.commit()
        conn.close()

        login_as_admin(client)
        resp = client.get('/admin/session/1')
        assert resp.status_code == 200
        text = resp.data.decode()
        assert 'Present: 1' in text
        assert 'Late: 1' in text
        assert 'Absent: 1' in text

    def test_summary_counts_failed_attempts_from_audit_log(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected', target='session #1')
        app_module.log_audit('student', '1', 'attendance_liveness_challenge_failed', target='session #1')

        login_as_admin(client)
        resp = client.get('/admin/session/1')
        assert 'Failed Attempts: 2' in resp.data.decode()

    def test_summary_scoped_to_restricted_group(self, client, isolated_paths):
        """A session restricted to CSE/3 should only count CSE/3 students
        in its total, not the whole student body."""
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1', branch='CSE', semester='3')
        _seed_student(isolated_paths['database_path'], 2, 'Bob', 'R2', branch='ECE', semester='1')
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active',
                      restrict_branch='CSE', restrict_semester='3')

        login_as_admin(client)
        resp = client.get('/admin/session/1')
        assert 'Total Students: 1' in resp.data.decode()

    def test_attendance_data_endpoint_includes_late_and_failed(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1, '26-08-2026', '10:00', active=1, status='active')
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Late', 'x')")
        conn.commit()
        conn.close()

        login_as_admin(client)
        resp = client.get('/admin/session/1/attendance-data')
        summary = resp.get_json()['summary']
        assert summary['late'] == 1
        assert summary['present'] == 0
        assert 'failed_attempts' in summary


class TestAutomaticSessionClosing:
    def test_active_session_auto_closes_after_window_elapses(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SESSION_AUTO_CLOSE_ENABLED', True)
        old_start = datetime.now() - timedelta(minutes=90)
        _seed_session(isolated_paths['database_path'], 1, old_start.strftime('%d-%m-%Y'), old_start.strftime('%H:%M'),
                      active=1, status='active', attendance_window_minutes=30)
        login_as_admin(client)

        client.get('/admin/sessions')  # triggers the bulk sweep

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('completed', 0)

    def test_auto_close_logs_an_audit_event(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SESSION_AUTO_CLOSE_ENABLED', True)
        old_start = datetime.now() - timedelta(minutes=90)
        _seed_session(isolated_paths['database_path'], 1, old_start.strftime('%d-%m-%Y'), old_start.strftime('%H:%M'),
                      active=1, status='active', attendance_window_minutes=30)
        login_as_admin(client)
        client.get('/admin/sessions')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE target='session #1'").fetchone()
        conn.close()
        assert row[0] == 'session_auto_closed'

    def test_session_within_window_is_not_closed(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SESSION_AUTO_CLOSE_ENABLED', True)
        recent_start = datetime.now() - timedelta(minutes=5)
        _seed_session(isolated_paths['database_path'], 1, recent_start.strftime('%d-%m-%Y'), recent_start.strftime('%H:%M'),
                      active=1, status='active', attendance_window_minutes=30)
        login_as_admin(client)
        client.get('/admin/sessions')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('active', 1)

    def test_disabled_flag_prevents_auto_close(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SESSION_AUTO_CLOSE_ENABLED', False)
        old_start = datetime.now() - timedelta(minutes=90)
        _seed_session(isolated_paths['database_path'], 1, old_start.strftime('%d-%m-%Y'), old_start.strftime('%H:%M'),
                      active=1, status='active', attendance_window_minutes=30)
        login_as_admin(client)
        client.get('/admin/sessions')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('active', 1)

    def test_expired_session_is_rejected_at_mark_time_even_before_a_sweep(self, client, isolated_paths, monkeypatch):
        """The per-request _maybe_auto_close_session check means a stale
        active session is caught right at marking time too, not only on
        the next listing-page sweep."""
        monkeypatch.setattr(cfg, 'SESSION_AUTO_CLOSE_ENABLED', True)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        old_start = datetime.now() - timedelta(minutes=90)
        _seed_session(isolated_paths['database_path'], 1, old_start.strftime('%d-%m-%Y'), old_start.strftime('%H:%M'),
                      active=1, status='active', attendance_window_minutes=30)
        embedding = _unit_vector(0)
        app_module.store_embedding(1, embedding)
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, embedding)
        assert resp.get_json()['status'] == 'error'

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT status, active FROM sessions WHERE id=1').fetchone()
        conn.close()
        assert row == ('completed', 0)
