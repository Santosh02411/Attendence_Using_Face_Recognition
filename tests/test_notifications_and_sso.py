"""Tests for the three v28 additions: opt-in email/SMS notifications,
self-service student password reset, and Google SSO login.

Follows the project's existing conventions (see conftest.py): every test
gets an isolated temp database via the `client`/`isolated_paths`
fixtures, CAPTCHA/rate-limiting are disabled by default, and external
integrations (SMTP, Twilio, Google) are never actually contacted —
notifications.send_email/send_sms are monkeypatched or exercised only
in their safe-no-op ("not configured") state.
"""
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import config as cfg
import notifications
from tests.conftest import login_as_student


def _register_student(client, roll_no='R100', email=None, phone_number=None, password='validPass123'):
    """Registers a student the same way test_registration.py does, but
    without needing real face-detection/embedding machinery — this
    module isn't testing recognition, so those pieces are mocked out."""
    import base64
    import io

    import cv2
    import numpy as np
    from PIL import Image

    import app as app_module

    img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    data_url = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

    payload = {
        'name': 'Test Student', 'roll_no': roll_no, 'branch': 'CSE', 'semester': '1',
        'password': password, 'gender': 'other', 'images': [data_url] * 3,
    }
    if email is not None:
        payload['email'] = email
    if phone_number is not None:
        payload['phone_number'] = phone_number

    fake_box = np.array([[10, 10, 50, 50]])
    fake_embedding = np.zeros(128, dtype=np.float32)
    fake_embedding[0] = 1.0
    with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=fake_box), \
         patch.object(app_module, 'compute_embedding', return_value=fake_embedding):
        resp = client.post('/student/register', json=payload)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['student_id']


class TestNotificationsModuleSafeDefaults:
    """notifications.py itself, independent of the Flask app — every
    public function must degrade to a no-op when unconfigured."""

    def test_email_disabled_by_default(self):
        assert notifications.email_enabled() is False

    def test_sms_disabled_by_default(self):
        assert notifications.sms_enabled() is False

    def test_send_email_noop_when_disabled(self):
        assert notifications.send_email('student@example.edu', 'Subject', 'Body') is False

    def test_send_sms_noop_when_disabled(self):
        assert notifications.send_sms('+15551234567', 'Body') is False

    def test_send_email_noop_with_no_recipient(self, monkeypatch):
        monkeypatch.setattr(cfg, 'EMAIL_NOTIFICATIONS_ENABLED', True)
        monkeypatch.setattr(cfg, 'SMTP_HOST', 'smtp.example.com')
        assert notifications.send_email('', 'Subject', 'Body') is False

    def test_send_email_uses_smtplib_when_configured(self, monkeypatch):
        monkeypatch.setattr(cfg, 'EMAIL_NOTIFICATIONS_ENABLED', True)
        monkeypatch.setattr(cfg, 'SMTP_HOST', 'smtp.example.com')
        sent = {}

        class FakeSMTP:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                pass

            def login(self, *a):
                pass

            def send_message(self, msg):
                sent['to'] = msg['To']
                sent['subject'] = msg['Subject']

        with patch('smtplib.SMTP', FakeSMTP):
            result = notifications.send_email('student@example.edu', 'Hello', 'Body text')
        assert result is True
        assert sent['to'] == 'student@example.edu'
        assert sent['subject'] == 'Hello'

    def test_send_email_swallows_smtp_errors(self, monkeypatch):
        monkeypatch.setattr(cfg, 'EMAIL_NOTIFICATIONS_ENABLED', True)
        monkeypatch.setattr(cfg, 'SMTP_HOST', 'smtp.example.com')
        with patch('smtplib.SMTP', side_effect=OSError('connection refused')):
            result = notifications.send_email('student@example.edu', 'Hello', 'Body')
        assert result is False  # never raises


class TestRegistrationContactInfo:
    def test_registration_accepts_optional_email_and_phone(self, client, isolated_paths):
        student_id = _register_student(client, roll_no='R200', email='r200@example.edu', phone_number='+15550001111')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT email, phone_number, notify_email, notify_sms FROM students WHERE id=?', (student_id,)).fetchone()
        conn.close()
        assert row[0] == 'r200@example.edu'
        assert row[1] == '+15550001111'
        assert row[2] == 1  # notifications on by default
        assert row[3] == 1

    def test_registration_rejects_invalid_email(self, client, isolated_paths):
        import base64
        import io

        import numpy as np
        from PIL import Image
        img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        data_url = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
        resp = client.post('/student/register', json={
            'name': 'Bad Email', 'roll_no': 'R201', 'branch': 'CSE', 'semester': '1',
            'password': 'validPass123', 'gender': 'other', 'images': [data_url],
            'email': 'not-an-email',
        })
        assert resp.status_code == 400
        assert 'valid email' in resp.get_json()['error']

    def test_registration_without_contact_info_leaves_columns_null(self, client, isolated_paths):
        student_id = _register_student(client, roll_no='R202')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT email, phone_number FROM students WHERE id=?', (student_id,)).fetchone()
        conn.close()
        assert row[0] is None
        assert row[1] is None

    def test_update_contact_info_requires_login(self, client, isolated_paths):
        resp = client.post('/student/profile/update-contact', data={'email': 'x@example.edu'})
        # Not logged in: falls through to student record lookup with no
        # session, which redirects home rather than 500ing.
        assert resp.status_code in (302, 303)

    def test_update_contact_info_when_logged_in(self, client, isolated_paths):
        _register_student(client, roll_no='R203', password='validPass123')
        login_as_student(client, 'R203', 'validPass123')
        resp = client.post('/student/profile/update-contact', data={
            'email': 'r203@example.edu', 'phone_number': '+15559998888', 'notify_email': 'on',
        }, follow_redirects=True)
        assert resp.status_code == 200
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT email, phone_number, notify_email, notify_sms FROM students WHERE roll_no=?', ('R203',)).fetchone()
        conn.close()
        assert row[0] == 'r203@example.edu'
        assert row[1] == '+15559998888'
        assert row[2] == 1
        assert row[3] == 0  # checkbox omitted -> unchecked


class TestPasswordReset:
    def test_forgot_password_unmatched_email_gives_generic_message(self, client, isolated_paths):
        _register_student(client, roll_no='R300', email='real@example.edu')
        resp = client.post('/student/forgot-password', data={
            'roll_no': 'R300', 'email': 'wrong@example.edu',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'password reset link has been sent' in resp.data
        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM password_reset_tokens').fetchone()[0]
        conn.close()
        assert count == 0  # no token issued for a mismatched email

    def test_forgot_password_matched_email_issues_token(self, client, isolated_paths):
        _register_student(client, roll_no='R301', email='real301@example.edu')
        resp = client.post('/student/forgot-password', data={
            'roll_no': 'R301', 'email': 'real301@example.edu',
        }, follow_redirects=True)
        assert resp.status_code == 200
        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM password_reset_tokens').fetchone()[0]
        conn.close()
        assert count == 1

    def test_reset_password_with_valid_token_changes_password(self, client, isolated_paths):
        import app as app_module
        _register_student(client, roll_no='R302', email='real302@example.edu', password='oldPassword1')

        with patch.object(app_module.secrets, 'token_urlsafe', return_value='fixed-test-token'):
            client.post('/student/forgot-password', data={'roll_no': 'R302', 'email': 'real302@example.edu'})

        resp = client.post('/student/reset-password/fixed-test-token', data={
            'new_password': 'newPassword2', 'confirm_password': 'newPassword2',
        }, follow_redirects=True)
        assert resp.status_code == 200

        # Old password no longer works, new one does.
        bad = login_as_student(client, 'R302', 'oldPassword1')
        assert bad.get_json()['status'] == 'error'
        good = login_as_student(client, 'R302', 'newPassword2')
        assert good.get_json()['status'] == 'ok'

    def test_reset_token_is_single_use(self, client, isolated_paths):
        import app as app_module
        _register_student(client, roll_no='R303', email='real303@example.edu', password='oldPassword1')
        with patch.object(app_module.secrets, 'token_urlsafe', return_value='single-use-token'):
            client.post('/student/forgot-password', data={'roll_no': 'R303', 'email': 'real303@example.edu'})

        first = client.post('/student/reset-password/single-use-token', data={
            'new_password': 'firstNew1', 'confirm_password': 'firstNew1',
        })
        assert first.status_code in (302, 303)

        second = client.get('/student/reset-password/single-use-token', follow_redirects=True)
        assert b'invalid or has expired' in second.data

    def test_reset_token_expired_is_rejected(self, client, isolated_paths):
        student_id = _register_student(client, roll_no='R304', email='real304@example.edu')
        import hashlib
        token_hash = hashlib.sha256(b'expired-token').hexdigest()
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute(
            'INSERT INTO password_reset_tokens(student_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)',
            (student_id, token_hash, datetime.now().isoformat(), (datetime.now() - timedelta(minutes=1)).isoformat())
        )
        conn.commit()
        conn.close()
        resp = client.get('/student/reset-password/expired-token', follow_redirects=True)
        assert b'invalid or has expired' in resp.data

    def test_reset_password_weak_password_rejected(self, client, isolated_paths):
        import app as app_module
        _register_student(client, roll_no='R305', email='real305@example.edu', password='oldPassword1')
        with patch.object(app_module.secrets, 'token_urlsafe', return_value='weak-pw-token'):
            client.post('/student/forgot-password', data={'roll_no': 'R305', 'email': 'real305@example.edu'})
        resp = client.post('/student/reset-password/weak-pw-token', data={
            'new_password': 'short', 'confirm_password': 'short',
        })
        assert resp.status_code == 200  # re-renders the form, doesn't redirect
        good = login_as_student(client, 'R305', 'oldPassword1')
        assert good.get_json()['status'] == 'ok'  # old password still works


class TestGoogleSSO:
    def test_login_page_hides_google_button_when_disabled(self, client, isolated_paths):
        resp = client.get('/student/login')
        assert b'Sign in with Google' not in resp.data

    def test_oauth_login_redirects_with_flash_when_not_configured(self, client, isolated_paths):
        resp = client.get('/auth/google/login', follow_redirects=True)
        assert resp.status_code == 200
        assert b'not configured' in resp.data

    def test_oauth_callback_redirects_with_flash_when_not_configured(self, client, isolated_paths):
        resp = client.get('/auth/google/callback', follow_redirects=True)
        assert resp.status_code == 200
        assert b'not configured' in resp.data
