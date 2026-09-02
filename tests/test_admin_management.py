"""Tests for the admin account management UI (/admin/settings) and the
audit log it (and other admin actions) write to."""
import sqlite3

from conftest import login_as_admin


def _get_admin_id(db_path, username):
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT id FROM admins WHERE username=?', (username,)).fetchone()
    conn.close()
    return row[0] if row else None


class TestAdminAccountManagement:
    def test_settings_page_requires_login(self, client):
        resp = client.get('/admin/settings', follow_redirects=False)
        assert resp.status_code == 302

    def test_create_new_admin_account(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.post('/admin/settings', data={
            'new_username': 'secondadmin', 'new_password': 'Secondpass123',
        }, follow_redirects=True)
        assert b'created' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT password FROM admins WHERE username='secondadmin'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] != 'Secondpass123'  # must be hashed

    def test_duplicate_username_is_rejected(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.post('/admin/settings', data={
            'new_username': 'admin', 'new_password': 'Whatever123',
        }, follow_redirects=True)
        assert b'already taken' in resp.data.lower()

    def test_weak_password_is_rejected_for_new_admin(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/settings', data={
            'new_username': 'weakadmin', 'new_password': 'abc',
        }, follow_redirects=True)
        conn = sqlite3.connect(isolated_paths['database_path'])
        exists = conn.execute("SELECT 1 FROM admins WHERE username='weakadmin'").fetchone()
        conn.close()
        assert exists is None

    def test_cannot_delete_own_account(self, client, isolated_paths):
        login_as_admin(client)
        # Add a second admin so "last admin" protection doesn't also apply.
        client.post('/admin/settings', data={'new_username': 'other', 'new_password': 'Otherpass123'})
        admin_id = _get_admin_id(isolated_paths['database_path'], 'admin')

        resp = client.post(f'/admin/settings/admins/{admin_id}/delete', follow_redirects=True)
        assert b'currently' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        still_exists = conn.execute('SELECT 1 FROM admins WHERE id=?', (admin_id,)).fetchone()
        conn.close()
        assert still_exists is not None

    def test_cannot_delete_last_remaining_admin(self, client, isolated_paths):
        """Even logged in as a *different* session concept, deleting the
        only admin account left must be blocked — otherwise the app has
        no admin at all and no self-service way back in."""
        login_as_admin(client)
        admin_id = _get_admin_id(isolated_paths['database_path'], 'admin')
        # Only one admin exists at this point.
        resp = client.post(f'/admin/settings/admins/{admin_id}/delete', follow_redirects=True)
        assert b'last remaining' in resp.data.lower() or b'currently' in resp.data.lower()

    def test_can_delete_a_different_admin_account(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/settings', data={'new_username': 'other', 'new_password': 'Otherpass123'})
        other_id = _get_admin_id(isolated_paths['database_path'], 'other')

        resp = client.post(f'/admin/settings/admins/{other_id}/delete', follow_redirects=True)
        assert b'deleted' in resp.data.lower()

        conn = sqlite3.connect(isolated_paths['database_path'])
        still_exists = conn.execute('SELECT 1 FROM admins WHERE id=?', (other_id,)).fetchone()
        conn.close()
        assert still_exists is None


class TestAuditLog:
    def test_audit_log_requires_login(self, client):
        resp = client.get('/admin/audit-log', follow_redirects=False)
        assert resp.status_code == 302

    def test_successful_admin_login_is_logged(self, client, isolated_paths):
        login_as_admin(client)
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE action='admin_login_success'").fetchone()
        conn.close()
        assert row is not None

    def test_failed_admin_login_is_logged(self, client, isolated_paths):
        login_as_admin(client, password='wrong')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE action='admin_login_failed'").fetchone()
        conn.close()
        assert row is not None

    def test_admin_account_creation_is_logged(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/settings', data={'new_username': 'other', 'new_password': 'Otherpass123'})
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute(
            "SELECT actor_name, target FROM audit_log WHERE action='admin_account_created'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 'admin'
        assert row[1] == 'other'

    def test_audit_log_view_renders_recorded_entries(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.get('/admin/audit-log')
        assert resp.status_code == 200
        assert b'admin_login_success' in resp.data
