"""Tests for the three "broader feature gap" additions: a student-facing
low-attendance alert, the /metrics endpoint, and real-time (long-poll)
updates on the admin session-monitor page.
"""
import sqlite3
import threading
import time

from conftest import login_as_admin, login_as_student
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg


def _seed_student(db_path, student_id, name, roll_no):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, 'CSE', '1', generate_password_hash('pw'))
    )
    conn.commit()
    conn.close()


def _seed_session(db_path, session_id=1):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO subjects(id, name, code) VALUES (1, 'Sub', 'S1')")
    from datetime import datetime
    today = datetime.now().strftime('%d-%m-%Y')
    conn.execute(
        "INSERT INTO sessions(id, subject_id, title, date, time, end_date, end_time, active, status) VALUES (?, 1, 'S', ?, '10:00', ?, '23:59', 1, 'active')",
        (session_id, today, today)
    )
    conn.commit()
    conn.close()


class TestStudentLowAttendanceAlert:
    def test_profile_shows_banner_below_threshold(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LOW_ATTENDANCE_THRESHOLD_PERCENT', 75)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Absent', 'x')")
        conn.commit()
        conn.close()
        login_as_student(client, 'R1', 'pw')

        resp = client.get('/student/profile')
        assert b'below the 75' in resp.data.lower() or b'below the 75.0' in resp.data.lower() or b'threshold' in resp.data.lower()

    def test_profile_no_banner_above_threshold(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LOW_ATTENDANCE_THRESHOLD_PERCENT', 50)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp) VALUES (1, 1, 'Present', 'x')")
        conn.commit()
        conn.close()
        login_as_student(client, 'R1', 'pw')

        resp = client.get('/student/profile')
        assert b'below the' not in resp.data.lower()

    def test_no_banner_with_zero_sessions(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        login_as_student(client, 'R1', 'pw')
        resp = client.get('/student/profile')
        assert b'below the' not in resp.data.lower()

    def test_history_page_shows_banner_below_threshold(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LOW_ATTENDANCE_THRESHOLD_PERCENT', 75)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        login_as_student(client, 'R1', 'pw')
        resp = client.get('/student/history')
        assert b'threshold' in resp.data.lower()


class TestMetricsEndpoint:
    def test_metrics_returns_prometheus_text(self, client, isolated_paths):
        resp = client.get('/metrics')
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert '# HELP http_requests_total' in text
        assert '# TYPE http_requests_total counter' in text

    def test_metrics_disabled_returns_404(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'METRICS_ENABLED', False)
        resp = client.get('/metrics')
        assert resp.status_code == 404

    def test_metrics_requires_token_when_configured(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'METRICS_AUTH_TOKEN', 'secret123')
        resp = client.get('/metrics')
        assert resp.status_code == 401
        resp2 = client.get('/metrics?token=secret123')
        assert resp2.status_code == 200

    def test_metrics_accepts_bearer_header(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'METRICS_AUTH_TOKEN', 'secret123')
        resp = client.get('/metrics', headers={'Authorization': 'Bearer secret123'})
        assert resp.status_code == 200

    def test_gauges_reflect_current_state(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        resp = client.get('/metrics')
        text = resp.get_data(as_text=True)
        assert 'students_total 1' in text
        assert 'sessions_active 1' in text

    def test_audit_events_counted(self, client, isolated_paths):
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        resp = client.get('/metrics')
        text = resp.get_data(as_text=True)
        assert 'audit_events_total{action="attendance_spoof_suspected"} 2' in text

    def test_no_login_required(self, client, isolated_paths):
        """Consistent with other lightweight status endpoints like
        /captcha-image -- /metrics isn't gated behind admin login,
        only optionally behind METRICS_AUTH_TOKEN."""
        resp = client.get('/metrics', follow_redirects=False)
        assert resp.status_code == 200


class TestRealtimeSessionUpdates:
    def test_long_poll_returns_immediately_on_version_match_miss(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'REALTIME_LONGPOLL_TIMEOUT_SECONDS', 5)
        _seed_session(isolated_paths['database_path'], 1)
        login_as_admin(client)
        app_module._publish_session_update(1)

        start = time.time()
        resp = client.get('/admin/session/1/updates?since=0')
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 2
        assert resp.get_json()['version'] >= 1

    def test_long_poll_times_out_and_still_returns_data(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'REALTIME_LONGPOLL_TIMEOUT_SECONDS', 1)
        _seed_session(isolated_paths['database_path'], 1)
        login_as_admin(client)
        # _SESSION_UPDATE_VERSIONS is a module-global counter that persists
        # across tests in-process -- use the session's actual current
        # version as "since" so nothing has "changed" and it genuinely
        # waits out the timeout, rather than an arbitrary mismatched value
        # (which would just look like an update already happened).
        current_version = app_module._SESSION_UPDATE_VERSIONS.get(1, 0)

        start = time.time()
        resp = client.get(f'/admin/session/1/updates?since={current_version}')
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed >= 1
        payload = resp.get_json()
        assert 'students' in payload
        assert 'summary' in payload

    def test_publish_wakes_a_waiting_long_poll(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'REALTIME_LONGPOLL_TIMEOUT_SECONDS', 5)
        _seed_session(isolated_paths['database_path'], 1)
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess['user_type'] = 'admin'
            sess['admin_user'] = 'admin'
        current_version = app_module._SESSION_UPDATE_VERSIONS.get(1, 0)

        def publish_soon():
            time.sleep(0.5)
            app_module._publish_session_update(1)
        t = threading.Thread(target=publish_soon)
        t.start()
        start = time.time()
        resp = client.get(f'/admin/session/1/updates?since={current_version}')
        elapsed = time.time() - start
        t.join()
        assert elapsed < 3  # much less than the 5s timeout
        assert resp.get_json()['version'] > current_version

    def test_disabled_flag_returns_immediately(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'REALTIME_UPDATES_ENABLED', False)
        monkeypatch.setattr(cfg, 'REALTIME_LONGPOLL_TIMEOUT_SECONDS', 5)
        _seed_session(isolated_paths['database_path'], 1)
        login_as_admin(client)

        start = time.time()
        resp = client.get('/admin/session/1/updates?since=0')
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 1

    def test_requires_admin(self, client, isolated_paths):
        _seed_session(isolated_paths['database_path'], 1)
        resp = client.get('/admin/session/1/updates', follow_redirects=False)
        assert resp.get_json() is None or resp.status_code in (302, 401)

    def test_override_publishes_update(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        login_as_admin(client)
        before = app_module._SESSION_UPDATE_VERSIONS.get(1, 0)

        client.post('/admin/session/1/override', data={'student_id': 1, 'action': 'present'})

        after = app_module._SESSION_UPDATE_VERSIONS.get(1, 0)
        assert after > before
