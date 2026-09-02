"""Tests for two admin-facing features built on top of the existing
audit log: a "Security Events" filter on /admin/audit-log, and a
Security Alerts widget on /admin/dashboard that groups spoof/liveness/
lockout events per student so an admin doesn't have to scan the raw log
by hand to notice a pattern."""
import sqlite3

from conftest import login_as_admin

import app as app_module


def _seed_student(db_path, student_id, name, roll_no):
    from werkzeug.security import generate_password_hash
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, 'CSE', '1', generate_password_hash('pw'))
    )
    conn.commit()
    conn.close()


class TestAuditLogSecurityFilter:
    def test_requires_admin_login(self, client):
        resp = client.get('/admin/audit-log?filter=security', follow_redirects=False)
        assert resp.status_code == 302

    def test_security_filter_excludes_routine_admin_actions(self, client, isolated_paths):
        login_as_admin(client)
        app_module.log_audit('admin', 'admin', 'session_started', target='session #1')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected', details='screen/print replay pattern detected')

        resp = client.get('/admin/audit-log?filter=security')
        assert b'attendance_spoof_suspected' in resp.data
        assert b'session_started' not in resp.data

    def test_all_filter_shows_everything(self, client, isolated_paths):
        login_as_admin(client)
        app_module.log_audit('admin', 'admin', 'session_started', target='session #1')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')

        resp = client.get('/admin/audit-log')
        assert b'attendance_spoof_suspected' in resp.data
        assert b'session_started' in resp.data

    def test_actor_filter_narrows_to_one_actor(self, client, isolated_paths):
        login_as_admin(client)
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        app_module.log_audit('student', '2', 'attendance_spoof_suspected')

        resp = client.get('/admin/audit-log?filter=security&actor=1')
        rows = resp.data.decode().count('attendance_spoof_suspected')
        assert rows == 1

    def test_security_filter_persists_across_pagination_links(self, client, isolated_paths, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, 'AUDIT_LOG_PER_PAGE', 2)
        login_as_admin(client)
        for i in range(5):
            app_module.log_audit('student', str(i), 'attendance_spoof_suspected')

        resp = client.get('/admin/audit-log?filter=security')
        assert b'filter=security' in resp.data


class TestSecurityAlertsWidget:
    def test_dashboard_lists_students_with_repeated_security_events(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        login_as_admin(client)
        for _ in range(3):
            app_module.log_audit('student', '1', 'attendance_spoof_suspected', details='screen/print replay pattern detected')

        resp = client.get('/admin/dashboard')
        assert b'Alice' in resp.data
        assert b'R1' in resp.data
        assert b'Security Alerts' in resp.data

    def test_dashboard_omits_widget_entries_for_students_with_no_events(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        login_as_admin(client)

        resp = client.get('/admin/dashboard')
        assert b'No spoof or liveness-challenge alerts' in resp.data

    def test_routine_events_do_not_appear_in_security_alerts(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        login_as_admin(client)
        app_module.log_audit('student', '1', 'student_added_face_photos', details='+3 photo(s)')

        resp = client.get('/admin/dashboard')
        assert b'No spoof or liveness-challenge alerts' in resp.data

    def test_widget_links_to_filtered_audit_log_for_that_student(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        login_as_admin(client)
        app_module.log_audit('student', '1', 'attendance_liveness_challenge_failed', details='challenge=blink')

        resp = client.get('/admin/dashboard')
        assert b'filter=security' in resp.data
        assert b'actor=1' in resp.data
