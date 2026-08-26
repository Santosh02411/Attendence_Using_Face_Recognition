"""Tests for configure_proxy_fix() — the ProxyFix wiring used when the app
is deployed behind a reverse proxy (see README "Deploying behind a
reverse proxy" and config.py's BEHIND_REVERSE_PROXY).

Exercised on a throwaway Flask instance (not the shared `app` object,
which is already wrapped or not at import time) so each test can control
BEHIND_REVERSE_PROXY independently of import order.
"""
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

import app as app_module
import config as cfg


def test_proxy_fix_not_applied_by_default(monkeypatch):
    monkeypatch.setattr(cfg, 'BEHIND_REVERSE_PROXY', False)
    dummy = Flask(__name__)

    result = app_module.configure_proxy_fix(dummy)

    assert result is dummy
    # dummy.wsgi_app is Flask's own bound method (a fresh bound-method
    # object on every access, so compare __func__ rather than identity),
    # not a ProxyFix wrapper around it.
    assert dummy.wsgi_app.__func__ is Flask.wsgi_app
    assert not isinstance(dummy.wsgi_app, ProxyFix)


def test_proxy_fix_applied_when_enabled(monkeypatch):
    monkeypatch.setattr(cfg, 'BEHIND_REVERSE_PROXY', True)
    monkeypatch.setattr(cfg, 'PROXY_FIX_NUM_PROXIES', 2)
    dummy = Flask(__name__)

    result = app_module.configure_proxy_fix(dummy)

    assert result is dummy
    assert isinstance(dummy.wsgi_app, ProxyFix)
    assert dummy.wsgi_app.x_for == 2
    assert dummy.wsgi_app.x_proto == 2
    assert dummy.wsgi_app.x_host == 2
    assert dummy.wsgi_app.x_port == 2


def test_remote_addr_reflects_forwarded_header_when_enabled(monkeypatch):
    """End-to-end sanity check: with ProxyFix wired in, a request carrying
    X-Forwarded-For is trusted for remote_addr — the exact behavior the
    rate limiter, account lockout, IP allowlist, and audit log all rely
    on to see the real client IP instead of the proxy's."""
    monkeypatch.setattr(cfg, 'BEHIND_REVERSE_PROXY', True)
    monkeypatch.setattr(cfg, 'PROXY_FIX_NUM_PROXIES', 1)
    dummy = Flask(__name__)
    app_module.configure_proxy_fix(dummy)

    seen = {}

    @dummy.route('/whoami')
    def whoami():
        from flask import request
        seen['remote_addr'] = request.remote_addr
        return 'ok'

    client = dummy.test_client()
    client.get('/whoami', headers={'X-Forwarded-For': '203.0.113.5'})

    assert seen['remote_addr'] == '203.0.113.5'
