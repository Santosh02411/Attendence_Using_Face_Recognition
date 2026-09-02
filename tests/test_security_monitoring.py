"""Tests for the anti-spoof/security monitoring feature set: device
fingerprinting and suspicious-device detection, concurrent-session
detection, network-change/impossible-location detection, per-student
risk scoring and automatic escalation, security notifications, event
severity levels, and the security dashboard/settings admin pages.
"""
import sqlite3
from datetime import datetime, timedelta

from conftest import login_as_admin
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg


def _seed_student(db_path, student_id, name, roll_no, password='pw'):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, 'CSE', '1', generate_password_hash(password))
    )
    conn.commit()
    conn.close()


def _login_json(client, roll_no, password='pw', fingerprint=None):
    return client.post('/student/login', json={'roll_no': roll_no, 'password': password, 'device_fingerprint': fingerprint})


class TestEventSeverity:
    def test_known_actions_have_expected_severity(self):
        assert app_module.event_severity('attendance_spoof_suspected') == 'high'
        assert app_module.event_severity('impossible_location_suspected') == 'critical'
        assert app_module.event_severity('student_login_failed') == 'low'
        assert app_module.event_severity('attendance_liveness_challenge_failed') == 'medium'

    def test_unknown_action_defaults_to_low(self):
        assert app_module.event_severity('totally_made_up_action') == 'low'


class TestDeviceFingerprintingAndSuspiciousDevice:
    def test_first_device_is_not_suspicious(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_json(client, 'R1', fingerprint='fp_first')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='suspicious_device_detected'").fetchone()
        fp_count = conn.execute('SELECT COUNT(*) FROM device_fingerprints WHERE student_id=1').fetchone()[0]
        conn.close()
        assert row is None
        assert fp_count == 1

    def test_second_new_device_is_flagged_suspicious(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        client1 = client
        client2 = app_module.app.test_client()

        _login_json(client1, 'R1', fingerprint='fp_first')
        _login_json(client2, 'R1', fingerprint='fp_second')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='suspicious_device_detected'").fetchone()
        conn.close()
        assert row is not None

    def test_same_device_repeated_login_not_flagged(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_json(client, 'R1', fingerprint='fp_same')
        client.get('/logout')
        _login_json(client, 'R1', fingerprint='fp_same')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='suspicious_device_detected'").fetchone()
        seen_count = conn.execute('SELECT seen_count FROM device_fingerprints WHERE student_id=1 AND fingerprint_hash="fp_same"').fetchone()[0]
        conn.close()
        assert row is None
        assert seen_count == 2

    def test_suspicious_device_creates_notification(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        client2 = app_module.app.test_client()
        _login_json(client, 'R1', fingerprint='fp_a')
        _login_json(client2, 'R1', fingerprint='fp_b')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT severity FROM security_notifications WHERE event_type='suspicious_device'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 'high'

    def test_missing_fingerprint_does_not_error(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        resp = _login_json(client, 'R1', fingerprint=None)
        assert resp.get_json()['status'] == 'ok'


class TestConcurrentSessionDetection:
    def test_two_active_sessions_from_different_devices_flagged(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        client2 = app_module.app.test_client()

        _login_json(client, 'R1', fingerprint='fp_a')
        _login_json(client2, 'R1', fingerprint='fp_b')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='concurrent_session_detected'").fetchone()
        conn.close()
        assert row is not None

    def test_relogin_after_logout_not_flagged(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_json(client, 'R1', fingerprint='fp_a')
        client.get('/logout')
        _login_json(client, 'R1', fingerprint='fp_a')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='concurrent_session_detected'").fetchone()
        conn.close()
        assert row is None

    def test_outside_window_not_flagged(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'CONCURRENT_SESSION_WINDOW_MINUTES', 1)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        client2 = app_module.app.test_client()
        _login_json(client, 'R1', fingerprint='fp_a')

        # Backdate the first session's last_seen_at outside the window.
        conn = sqlite3.connect(isolated_paths['database_path'])
        old_time = (datetime.now() - timedelta(minutes=10)).isoformat()
        conn.execute('UPDATE student_login_sessions SET last_seen_at=? WHERE student_id=1', (old_time,))
        conn.commit()
        conn.close()

        _login_json(client2, 'R1', fingerprint='fp_b')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='concurrent_session_detected'").fetchone()
        conn.close()
        assert row is None

    def test_logout_ends_session(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_json(client, 'R1', fingerprint='fp_a')
        client.get('/logout')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT ended_at FROM student_login_sessions WHERE student_id=1').fetchone()
        conn.close()
        assert row[0] is not None


class TestNetworkChangeDetection:
    def test_no_prior_login_is_not_flagged(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_json(client, 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action IN ('network_change_detected', 'impossible_location_suspected')").fetchone()
        conn.close()
        assert row is None

    def test_same_network_not_flagged(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("UPDATE students SET last_login_ip='127.0.0.1', last_login_at=? WHERE id=1", (datetime.now().isoformat(),))
        conn.commit()
        conn.close()
        _login_json(client, 'R1')  # test client also comes from 127.0.0.1
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action IN ('network_change_detected', 'impossible_location_suspected')").fetchone()
        conn.close()
        assert row is None

    def test_different_network_within_impossible_window_flags_critical(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'NETWORK_CHANGE_IMPOSSIBLE_MINUTES', 5)
        monkeypatch.setattr(cfg, 'NETWORK_CHANGE_WINDOW_MINUTES', 60)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        recent = (datetime.now() - timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE students SET last_login_ip='203.0.113.5', last_login_at=? WHERE id=1", (recent,))
        conn.commit()
        conn.close()

        _login_json(client, 'R1')  # test client IP is 127.0.0.1 -- different network

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE action='impossible_location_suspected'").fetchone()
        notif = conn.execute("SELECT severity FROM security_notifications WHERE event_type='impossible_location'").fetchone()
        conn.close()
        assert row is not None
        assert notif[0] == 'critical'

    def test_different_network_outside_impossible_but_within_change_window(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'NETWORK_CHANGE_IMPOSSIBLE_MINUTES', 2)
        monkeypatch.setattr(cfg, 'NETWORK_CHANGE_WINDOW_MINUTES', 60)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        recent = (datetime.now() - timedelta(minutes=20)).isoformat()
        conn.execute("UPDATE students SET last_login_ip='203.0.113.5', last_login_at=? WHERE id=1", (recent,))
        conn.commit()
        conn.close()

        _login_json(client, 'R1')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE action='network_change_detected'").fetchone()
        impossible = conn.execute("SELECT 1 FROM audit_log WHERE action='impossible_location_suspected'").fetchone()
        conn.close()
        assert row is not None
        assert impossible is None

    def test_outside_all_windows_not_flagged(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'NETWORK_CHANGE_WINDOW_MINUTES', 5)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        old = (datetime.now() - timedelta(hours=5)).isoformat()
        conn.execute("UPDATE students SET last_login_ip='203.0.113.5', last_login_at=? WHERE id=1", (old,))
        conn.commit()
        conn.close()

        _login_json(client, 'R1')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action IN ('network_change_detected', 'impossible_location_suspected')").fetchone()
        conn.close()
        assert row is None

    def test_last_login_ip_updated_after_login(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_json(client, 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT last_login_ip FROM students WHERE id=1').fetchone()
        conn.close()
        assert row[0] == '127.0.0.1'


class TestRiskScoreAndEscalation:
    def test_no_events_means_zero_score(self, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        score, breakdown = app_module._compute_risk_score(1)
        assert score == 0
        assert breakdown == {}

    def test_weighted_events_accumulate(self, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        score, breakdown = app_module._compute_risk_score(1)
        assert score == 30  # 15 * 2
        assert breakdown['attendance_spoof_suspected'] == 30

    def test_score_is_capped_at_100(self, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        for _ in range(10):
            app_module.log_audit('student', '1', 'impossible_location_suspected')
        score, _breakdown = app_module._compute_risk_score(1)
        assert score == 100

    def test_events_outside_window_not_counted(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SECURITY_RISK_WINDOW_DAYS', 7)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        old_time = (datetime.now() - timedelta(days=30)).isoformat()
        conn.execute("INSERT INTO audit_log(timestamp, actor_type, actor_name, action) VALUES (?, 'student', '1', 'attendance_spoof_suspected')", (old_time,))
        conn.commit()
        conn.close()
        score, _breakdown = app_module._compute_risk_score(1)
        assert score == 0

    def test_auto_escalation_triggers_above_threshold(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SECURITY_RISK_ESCALATION_THRESHOLD', 20)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')

        app_module._maybe_escalate_student(1)

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT security_escalated FROM students WHERE id=1').fetchone()
        audit_row = conn.execute("SELECT 1 FROM audit_log WHERE action='security_risk_escalated'").fetchone()
        notif = conn.execute("SELECT severity FROM security_notifications WHERE event_type='escalation'").fetchone()
        conn.close()
        assert row[0] == 1
        assert audit_row is not None
        assert notif[0] == 'critical'

    def test_no_escalation_below_threshold(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SECURITY_RISK_ESCALATION_THRESHOLD', 90)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        app_module._maybe_escalate_student(1)
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT security_escalated FROM students WHERE id=1').fetchone()
        conn.close()
        assert row[0] == 0

    def test_escalation_is_idempotent_no_repeat_notification(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'SECURITY_RISK_ESCALATION_THRESHOLD', 10)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        app_module._maybe_escalate_student(1)
        app_module._maybe_escalate_student(1)
        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute("SELECT COUNT(*) FROM security_notifications WHERE event_type='escalation'").fetchone()[0]
        conn.close()
        assert count == 1

    def test_escalated_student_blocked_from_marking_attendance(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT OR IGNORE INTO subjects(id, name, code) VALUES (1, 'Sub', 'S1')")
        now = datetime.now()
        conn.execute(
            'INSERT INTO sessions(id, subject_id, title, date, time, end_date, end_time, active, status) VALUES (1, 1, "S", ?, ?, ?, "23:59", 1, "active")',
            (now.strftime('%d-%m-%Y'), now.strftime('%H:%M'), now.strftime('%d-%m-%Y'))
        )
        conn.execute('UPDATE students SET security_escalated=1 WHERE id=1')
        conn.commit()
        conn.close()

        with client.session_transaction() as sess:
            sess['user_type'] = 'student'
            sess['student_id'] = 1

        resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': ['a', 'b']})
        payload = resp.get_json()
        assert payload['status'] == 'error'
        assert resp.status_code == 403

    def test_admin_can_clear_escalation(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute('UPDATE students SET security_escalated=1, security_escalated_at=? WHERE id=1', (datetime.now().isoformat(),))
        conn.commit()
        conn.close()

        login_as_admin(client)
        client.post('/admin/student/1/clear-escalation')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT security_escalated, security_escalated_at FROM students WHERE id=1').fetchone()
        audit_row = conn.execute("SELECT 1 FROM audit_log WHERE action='security_escalation_cleared'").fetchone()
        conn.close()
        assert row == (0, None)
        assert audit_row is not None

    def test_clear_escalation_requires_admin(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        resp = client.post('/admin/student/1/clear-escalation', follow_redirects=False)
        assert resp.status_code == 302


class TestSecurityDashboardAndHistory:
    def test_dashboard_requires_admin(self, client):
        resp = client.get('/admin/security-dashboard', follow_redirects=False)
        assert resp.status_code == 302

    def test_dashboard_shows_severity_totals_and_risk_leaderboard(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')
        login_as_admin(client)
        resp = client.get('/admin/security-dashboard')
        assert resp.status_code == 200
        assert b'Alice' in resp.data
        assert b'Risk Score Leaderboard' in resp.data

    def test_dashboard_shows_unread_notifications(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module._create_security_notification('high', 1, 'test_event', 'Something happened.')
        login_as_admin(client)
        resp = client.get('/admin/security-dashboard')
        assert b'Something happened' in resp.data
        assert b'1 unread' in resp.data

    def test_mark_notification_read(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module._create_security_notification('high', 1, 'test_event', 'Something happened.')
        notif_id = sqlite3.connect(isolated_paths['database_path']).execute('SELECT id FROM security_notifications').fetchone()[0]

        login_as_admin(client)
        client.post(f'/admin/security-notifications/{notif_id}/read')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT is_read FROM security_notifications WHERE id=?', (notif_id,)).fetchone()
        conn.close()
        assert row[0] == 1

    def test_student_security_history_shows_devices_and_events(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module._record_device_fingerprint(1, 'fp_test', '127.0.0.1', 'TestAgent/1.0')
        app_module.log_audit('student', '1', 'attendance_spoof_suspected')

        login_as_admin(client)
        resp = client.get('/admin/student/1/security-history')
        assert resp.status_code == 200
        assert b'fp_test' in resp.data
        assert b'attendance_spoof_suspected' in resp.data

    def test_security_history_requires_admin(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        resp = client.get('/admin/student/1/security-history', follow_redirects=False)
        assert resp.status_code == 302


class TestSecuritySettings:
    def test_settings_requires_admin(self, client):
        resp = client.get('/admin/security-settings', follow_redirects=False)
        assert resp.status_code == 302

    def test_no_override_returns_default(self, isolated_paths):
        assert app_module.get_effective_setting('CONCURRENT_SESSION_WINDOW_MINUTES') == cfg.CONCURRENT_SESSION_WINDOW_MINUTES

    def test_admin_can_update_security_setting(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/security-settings', data={'SECURITY_RISK_ESCALATION_THRESHOLD': '75'})
        assert app_module.get_effective_setting('SECURITY_RISK_ESCALATION_THRESHOLD') == 75

    def test_invalid_value_rejected(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.post('/admin/security-settings', data={'CONCURRENT_SESSION_WINDOW_MINUTES': 'abc'}, follow_redirects=True)
        assert b'not a whole number' in resp.data.lower()

    def test_reset_restores_default(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/security-settings', data={'NETWORK_CHANGE_WINDOW_MINUTES': '999'})
        assert app_module.get_effective_setting('NETWORK_CHANGE_WINDOW_MINUTES') == 999
        client.post('/admin/security-settings/NETWORK_CHANGE_WINDOW_MINUTES/reset')
        assert app_module.get_effective_setting('NETWORK_CHANGE_WINDOW_MINUTES') == cfg.NETWORK_CHANGE_WINDOW_MINUTES

    def test_settings_change_is_audited(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/security-settings', data={'SECURITY_RISK_ESCALATION_THRESHOLD': '50'})
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='security_settings_updated'").fetchone()
        conn.close()
        assert row is not None


class TestSecurityEventsAuditFilter:
    def test_new_event_types_appear_in_security_filter(self, client, isolated_paths):
        app_module.log_audit('student', '1', 'impossible_location_suspected', details='x')
        app_module.log_audit('student', '1', 'concurrent_session_detected', details='y')
        login_as_admin(client)
        resp = client.get('/admin/audit-log?filter=security')
        assert b'impossible_location_suspected' in resp.data
        assert b'concurrent_session_detected' in resp.data
