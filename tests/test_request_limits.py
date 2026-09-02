"""Tests for session expiry configuration and request payload size limits."""

from conftest import login_as_admin

import app as app_module
import config as cfg


class TestSessionExpiry:
    def test_session_is_marked_permanent_after_admin_login(self, client, isolated_paths):
        login_as_admin(client)
        with client.session_transaction() as sess:
            assert sess.permanent is True

    def test_permanent_session_lifetime_matches_config(self):
        assert app_module.app.config['PERMANENT_SESSION_LIFETIME'].total_seconds() == cfg.SESSION_LIFETIME_MINUTES * 60


class TestPayloadSizeLimits:
    def test_max_content_length_is_configured(self):
        assert app_module.app.config['MAX_CONTENT_LENGTH'] == cfg.MAX_CONTENT_LENGTH_MB * 1024 * 1024

    def test_oversized_request_body_is_rejected(self, client, isolated_paths, monkeypatch):
        monkeypatch.setitem(app_module.app.config, 'MAX_CONTENT_LENGTH', 1000)  # 1KB cap for this test
        oversized_payload = {'name': 'A' * 5000}
        resp = client.post('/student/register', json=oversized_payload)
        assert resp.status_code == 413

    def test_too_many_registration_images_rejected(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'MAX_REGISTRATION_IMAGES', 3)
        resp = client.post('/student/register', json={
            'name': 'Test', 'roll_no': 'R1', 'branch': 'CSE', 'semester': '1',
            'password': 'goodpass123', 'gender': 'M',
            'images': ['data:image/jpeg;base64,AAAA'] * 10,
        })
        assert resp.status_code == 400
        assert 'too many' in resp.get_json()['error'].lower()

    def test_oversized_single_image_rejected(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'MAX_IMAGE_BASE64_CHARS', 100)
        huge_fake_image = 'data:image/jpeg;base64,' + ('A' * 500)
        resp = client.post('/student/register', json={
            'name': 'Test', 'roll_no': 'R1', 'branch': 'CSE', 'semester': '1',
            'password': 'goodpass123', 'gender': 'M', 'images': [huge_fake_image],
        })
        assert resp.status_code == 400
        assert 'too large' in resp.get_json()['error'].lower()
