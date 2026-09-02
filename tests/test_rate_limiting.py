"""Tests for Flask-Limiter-based per-IP rate limiting on login endpoints.
This is separate from (and complementary to) account lockout, tested in
test_lockout.py — rate limiting throttles by IP regardless of which
account is targeted."""
from conftest import login_as_admin, login_as_student

import app as app_module
import config as cfg


class TestAdminLoginRateLimiting:
    def test_requests_within_limit_are_not_blocked(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(app_module.limiter, 'enabled', True)
        monkeypatch.setattr(cfg, 'RATE_LIMIT_LOGIN', '5 per minute')
        for _ in range(5):
            resp = login_as_admin(client, password='wrong')
            assert resp.status_code != 429

    def test_exceeding_limit_returns_429(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(app_module.limiter, 'enabled', True)
        monkeypatch.setattr(cfg, 'RATE_LIMIT_LOGIN', '3 per minute')
        for _ in range(3):
            login_as_admin(client, password='wrong')
        resp = login_as_admin(client, password='wrong')
        assert resp.status_code == 429

    def test_rate_limit_disabled_by_default_in_tests(self, client, isolated_paths):
        # isolated_paths disables the limiter by default so unrelated
        # tests aren't affected — verify that holds.
        assert app_module.limiter.enabled is False
        for _ in range(20):
            resp = login_as_admin(client, password='wrong')
            assert resp.status_code != 429


class TestStudentLoginRateLimiting:
    def test_exceeding_limit_returns_429(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(app_module.limiter, 'enabled', True)
        monkeypatch.setattr(cfg, 'RATE_LIMIT_LOGIN', '3 per minute')
        for _ in range(3):
            login_as_student(client, 'NOSUCHROLL', 'whatever')
        resp = login_as_student(client, 'NOSUCHROLL', 'whatever')
        assert resp.status_code == 429
