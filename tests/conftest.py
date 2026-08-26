"""
Shared pytest fixtures.

Every test gets its own temporary SQLite databases and Datasets/ folder —
nothing here ever touches the real database/ or Datasets/ directories, so
running the test suite is always safe against a real deployment's data.
"""
import os
import sys

# Make sure `import app` / `import config` resolve to the project root,
# regardless of what directory pytest is invoked from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret-key-not-for-production')

import pytest
import app as app_module
import config as cfg


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Points the app's database and Datasets paths at a throwaway temp
    directory for the duration of one test, then restores them."""
    data_dir = tmp_path / 'Datasets'
    database_path = tmp_path / 'database' / 'app.db'
    face_database_path = tmp_path / 'database' / 'FaceBase.db'

    monkeypatch.setattr(app_module, 'DATA_DIR', str(data_dir))
    monkeypatch.setattr(app_module, 'DATABASE_PATH', str(database_path))
    monkeypatch.setattr(app_module, 'FACE_DATABASE_PATH', str(face_database_path))
    monkeypatch.setattr(cfg, 'DATABASE_DIR', str(tmp_path / 'database'))

    # CAPTCHA and rate limiting are disabled by default so tests unrelated
    # to those features don't have to solve a CAPTCHA or worry about
    # tripping a rate limit. test_captcha.py and test_rate_limiting.py
    # re-enable them explicitly via monkeypatch to test them directly.
    monkeypatch.setattr(cfg, 'CAPTCHA_ENABLED', False)
    monkeypatch.setattr(app_module.limiter, 'enabled', False)

    # The Limiter's in-memory storage backend is a module-level singleton
    # (created once when `app` is imported) that otherwise persists for the
    # whole pytest process. Without a reset, request counts from an earlier
    # test that exercised rate limiting (e.g. test_rate_limiting.py) leak
    # into a later test's counters and make it order-dependent — it can
    # pass in isolation and fail (or pass for the wrong reason) as part of
    # the full suite. Resetting here, before every test, keeps each test's
    # rate-limit counters starting from zero regardless of run order.
    app_module.limiter.storage.reset()

    app_module.init_databases()
    return {
        'data_dir': str(data_dir),
        'database_path': str(database_path),
        'face_database_path': str(face_database_path),
    }


@pytest.fixture
def client(isolated_paths):
    """A Flask test client with CSRF disabled (CSRF enforcement itself is
    covered separately in test_security.py, with it left ON)."""
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def client_with_csrf(isolated_paths):
    """A Flask test client with CSRF protection left ON, for tests that
    specifically verify CSRF enforcement."""
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def login_as_admin(client, username='admin', password=None):
    """Logs the given test client in as the seeded default admin.
    Assumes CAPTCHA is disabled (the default `client`/`isolated_paths`
    fixture behavior) — tests that need CAPTCHA on should solve it
    directly instead of using this helper."""
    if password is None:
        password = cfg.DEFAULT_ADMIN_PASSWORD
    return client.post('/login', data={'username': username, 'password': password}, follow_redirects=True)


def login_as_student(client, roll_no, password):
    return client.post('/student/login', json={'roll_no': roll_no, 'password': password})
