"""Tests for cross-cutting security properties: CSRF enforcement and
basic SQL-injection resistance. Uses client_with_csrf (CSRF protection
left ON) unlike most other test modules."""
import re

import app as app_module
import config as cfg


def _extract_csrf_token(html):
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match, 'no csrf_token field found in response HTML'
    return match.group(1)


class TestCSRFProtection:
    def test_login_without_csrf_token_is_blocked(self, client_with_csrf):
        resp = client_with_csrf.post('/login', data={
            'username': 'admin', 'password': cfg.DEFAULT_ADMIN_PASSWORD,
        })
        assert resp.status_code == 400
        assert b'CSRF' in resp.data

    def test_login_with_valid_csrf_token_succeeds(self, client_with_csrf):
        login_page = client_with_csrf.get('/login')
        token = _extract_csrf_token(login_page.get_data(as_text=True))

        resp = client_with_csrf.post('/login', data={
            'username': 'admin', 'password': cfg.DEFAULT_ADMIN_PASSWORD,
            'csrf_token': token,
        })
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/admin/dashboard')

    def test_student_login_json_post_requires_csrf_header(self, client_with_csrf):
        resp = client_with_csrf.post('/student/login', json={
            'roll_no': 'R1', 'password': 'whatever',
        })
        assert resp.status_code == 400
        assert b'CSRF' in resp.data

    def test_student_login_with_csrf_header_is_accepted(self, client_with_csrf):
        # A JSON POST with the token in the X-CSRFToken header should pass
        # CSRF validation (even though the roll number doesn't exist, so
        # the request still fails — just not on CSRF grounds).
        page = client_with_csrf.get('/student/login')
        html = page.get_data(as_text=True)
        match = re.search(r'csrf-token" content="([^"]*)"', html)
        assert match, 'no csrf-token meta tag found'
        token = match.group(1)

        resp = client_with_csrf.post(
            '/student/login',
            json={'roll_no': 'NOSUCHROLL', 'password': 'whatever'},
            headers={'X-CSRFToken': token},
        )
        assert resp.status_code == 404  # not-found, not a CSRF rejection


class TestSQLInjectionResistance:
    def test_login_username_with_sql_metacharacters_does_not_crash_or_bypass(self, client):
        malicious = "admin' OR '1'='1"
        resp = client.post('/login', data={'username': malicious, 'password': 'anything'})
        assert resp.status_code == 200
        assert b'Invalid' in resp.data

    def test_student_roll_no_with_sql_metacharacters_is_handled_safely(self, client):
        resp = client.post('/student/login', json={
            'roll_no': "R1' OR '1'='1", 'password': 'anything',
        })
        # Should be treated as a literal (nonexistent) roll number, not
        # break out of the query — parameterized queries are used
        # throughout app.py, so this must come back as not-found.
        assert resp.status_code == 404
