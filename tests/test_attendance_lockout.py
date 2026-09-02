"""Tests for the attendance-marking abuse lockout — escalating repeated
attendance_spoof_suspected / attendance_liveness_challenge_failed events
into a temporary lock on that student's attendance-marking endpoint.

Separate from test_lockout.py (the login-form brute-force lockout) and
from test_active_liveness.py (the anti-proxy checks themselves) — this
file is specifically about the escalation wired on top of those checks:
does it count only the right events, does it lock at the right
threshold, does success reset it, and can an admin clear it.
"""
import sqlite3
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from conftest import login_as_admin
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg
import face_security

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


def _seed_student(db_path, student_id, name, roll_no, password):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, 'CSE', '1', generate_password_hash(password))
    )
    conn.commit()
    conn.close()


def _seed_active_session(db_path, session_id=1):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO subjects(id, name, code) VALUES (1, 'Sub', 'S1')")
    conn.execute(
        "INSERT INTO sessions(id, subject_id, title, date, time, active) VALUES (?, 1, 'S', '2026-01-01', '10:00', 1)",
        (session_id,)
    )
    conn.commit()
    conn.close()


def _unit_vector(index):
    v = np.zeros(128, dtype=np.float32)
    v[index] = 1.0
    return v


@pytest.fixture
def mark_attendance_env(client, isolated_paths):
    _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1', 'pass1')
    _seed_active_session(isolated_paths['database_path'])
    embedding = _unit_vector(0)
    app_module.store_embedding(1, embedding)
    return {'embedding': embedding, 'db_path': isolated_paths['database_path']}


def _login_session(client, student_id):
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


def _fail_spoof_check(client, mark_attendance_env, monkeypatch):
    """One attendance-marking attempt that trips the anti-spoof check."""
    monkeypatch.setattr(cfg, 'ANTI_SPOOF_ENABLED', True)
    monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
    mocks = _base_mocks(mark_attendance_env['embedding'])
    with mocks[0], mocks[1], mocks[2], mocks[3], \
         patch.object(face_security, 'is_likely_screen_replay', return_value=True):
        return client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})


class TestAttendanceLockoutEscalation:
    def test_locks_after_max_spoof_failures(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 3)
        _login_session(client, 1)
        for _ in range(2):
            resp = _fail_spoof_check(client, mark_attendance_env, monkeypatch)
            assert resp.status_code == 200
            assert resp.get_json()['status'] == 'error'

        # Third strike crosses the threshold and locks the account.
        resp = _fail_spoof_check(client, mark_attendance_env, monkeypatch)
        assert resp.status_code == 403
        assert 'locked' in resp.get_json()['message'].lower()

    def test_further_attempts_are_blocked_while_locked(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 2)
        _login_session(client, 1)
        for _ in range(2):
            _fail_spoof_check(client, mark_attendance_env, monkeypatch)

        # Even a request that would otherwise succeed (good face match,
        # no spoof) is blocked outright while the lockout is active.
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3], \
             patch.object(face_security, 'is_likely_screen_replay', return_value=False):
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.status_code == 403
        assert 'locked' in resp.get_json()['message'].lower()

    def test_ordinary_low_confidence_mismatch_does_not_count_toward_lockout(self, client, mark_attendance_env, monkeypatch):
        """A genuine student in bad lighting failing the match threshold
        is not the abuse pattern this protects against — only spoof/
        liveness-challenge failures should count."""
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 2)
        monkeypatch.setattr(cfg, 'ANTI_SPOOF_ENABLED', False)
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
        _login_session(client, 1)
        mismatched_embedding = _unit_vector(5)  # far from the stored embedding (index 0)
        mocks = _base_mocks(mismatched_embedding)
        for _ in range(5):
            with mocks[0], mocks[1], mocks[2], mocks[3]:
                resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
            assert resp.status_code == 200
            assert resp.get_json()['status'] == 'error'
            assert 'not recognized' in resp.get_json()['message'].lower()

        conn = sqlite3.connect(mark_attendance_env['db_path'])
        row = conn.execute('SELECT attendance_security_failures, attendance_locked_until FROM students WHERE id=1').fetchone()
        conn.close()
        assert row[0] == 0
        assert row[1] is None

    def test_successful_mark_resets_the_counter(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 5)
        _login_session(client, 1)
        _fail_spoof_check(client, mark_attendance_env, monkeypatch)
        _fail_spoof_check(client, mark_attendance_env, monkeypatch)

        conn = sqlite3.connect(mark_attendance_env['db_path'])
        row = conn.execute('SELECT attendance_security_failures FROM students WHERE id=1').fetchone()
        conn.close()
        assert row[0] == 2

        monkeypatch.setattr(cfg, 'ANTI_SPOOF_ENABLED', False)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3]:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'present'

        conn = sqlite3.connect(mark_attendance_env['db_path'])
        row = conn.execute('SELECT attendance_security_failures, attendance_locked_until FROM students WHERE id=1').fetchone()
        conn.close()
        assert row[0] == 0
        assert row[1] is None

    def test_lockout_generates_a_distinct_audit_event(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 1)
        _login_session(client, 1)
        _fail_spoof_check(client, mark_attendance_env, monkeypatch)

        conn = sqlite3.connect(mark_attendance_env['db_path'])
        actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE actor_name='1'").fetchall()]
        conn.close()
        assert 'attendance_spoof_suspected' in actions
        assert 'attendance_security_lockout' in actions

    def test_lockout_expires_after_duration(self, client, mark_attendance_env, monkeypatch):
        from datetime import datetime, timedelta
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 1)
        _login_session(client, 1)
        _fail_spoof_check(client, mark_attendance_env, monkeypatch)

        # Manually expire the lockout (simulating that the window passed).
        conn = sqlite3.connect(mark_attendance_env['db_path'])
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE students SET attendance_locked_until=? WHERE id=1", (past,))
        conn.commit()
        conn.close()

        monkeypatch.setattr(cfg, 'ANTI_SPOOF_ENABLED', False)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3]:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'present'


class TestAdminClearAttendanceLock:
    def test_admin_can_clear_a_lock(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 1)
        _login_session(client, 1)
        _fail_spoof_check(client, mark_attendance_env, monkeypatch)

        # Log back in as admin (student session above doesn't interfere —
        # each Flask test-client call carries its own session cookie jar,
        # but logging in as admin overwrites session['user_type']).
        login_as_admin(client)
        resp = client.post('/admin/student/1/clear-attendance-lock', follow_redirects=True)
        assert resp.status_code == 200

        conn = sqlite3.connect(mark_attendance_env['db_path'])
        row = conn.execute('SELECT attendance_security_failures, attendance_locked_until FROM students WHERE id=1').fetchone()
        conn.close()
        assert row[0] == 0
        assert row[1] is None

    def test_requires_admin_login(self, client, mark_attendance_env):
        resp = client.post('/admin/student/1/clear-attendance-lock', follow_redirects=False)
        assert resp.status_code == 302

    def test_admin_students_page_shows_locked_badge(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_LOCKOUT_MAX_FAILED_ATTEMPTS', 1)
        _login_session(client, 1)
        _fail_spoof_check(client, mark_attendance_env, monkeypatch)

        login_as_admin(client)
        resp = client.get('/admin/students')
        assert b'Attendance Locked' in resp.data
        assert b'Clear Lock' in resp.data
