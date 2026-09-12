"""
E2E test fixtures — spin up a real Flask server (in a background
thread, via werkzeug's own dev server) and drive it with a real
Chromium browser through Playwright.

Kept in tests_e2e/ rather than tests/ deliberately: these need a
browser binary (`playwright install chromium`) and take noticeably
longer than the rest of the suite, so they're not part of the default
`pytest` run (see pytest.ini's `testpaths = tests`) — run them
explicitly with `pytest tests_e2e/`. See the README's "Browser/E2E
Tests" section for setup instructions.
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('FLASK_SECRET_KEY', 'e2e-test-secret-key-not-for-production')

import numpy as np
import pytest
from werkzeug.serving import make_server

import app as app_module
import config as cfg

# A fixed, deterministic "face embedding" used in place of the real
# OpenFace model's output for every captured frame in these tests. The
# fake camera device Chromium provides (via
# --use-fake-device-for-media-stream) renders a synthetic animated test
# pattern, not a real face, and it changes slightly frame to frame — so
# without this, a registration capture and a later attendance-marking
# capture would very plausibly compute two different real embeddings
# from two different garbage inputs and simply fail to match, for
# reasons that have nothing to do with whatever the test is actually
# exercising. Same reasoning as the unit test suite's
# compute_embedding mocking (see tests/test_registration.py) — this
# just applies it at the server level so the real HTTP/JS flow through
# a real browser is what's actually under test, not the ML model.
_FAKE_EMBEDDING = np.zeros(128, dtype=np.float32)
_FAKE_EMBEDDING[0] = 1.0


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """Starts a real Flask server in a background thread against an
    isolated temp database, with the same CAPTCHA/rate-limit/anti-spoof
    relaxations as the unit-test suite's `isolated_paths` fixture (see
    tests/conftest.py), plus face-detection/embedding mocked to a fixed
    result (see _FAKE_EMBEDDING above) so a synthetic fake-camera frame
    can pass server-side validation deterministically.

    Yields {'base_url': ..., 'database_path': ...}; shuts the server
    down and restores every patch at the end of the test.
    """
    data_dir = tmp_path / 'Datasets'
    database_path = tmp_path / 'database' / 'app.db'
    face_database_path = tmp_path / 'database' / 'FaceBase.db'

    monkeypatch.setattr(app_module, 'DATA_DIR', str(data_dir))
    monkeypatch.setattr(app_module, 'DATABASE_PATH', str(database_path))
    monkeypatch.setattr(app_module, 'FACE_DATABASE_PATH', str(face_database_path))
    monkeypatch.setattr(cfg, 'DATABASE_DIR', str(tmp_path / 'database'))

    monkeypatch.setattr(cfg, 'CAPTCHA_ENABLED', False)
    # app.py snapshots this into app.config at import time (so the
    # login template's {% if config.CAPTCHA_ENABLED %} can't see a
    # later change to cfg.CAPTCHA_ENABLED alone) -- the unit test
    # suite's Flask test client never actually renders that template's
    # CAPTCHA input, so tests/conftest.py's isolated_paths fixture
    # never needed this too, but a real browser does.
    monkeypatch.setitem(app_module.app.config, 'CAPTCHA_ENABLED', False)
    monkeypatch.setattr(app_module.limiter, 'enabled', False)
    monkeypatch.setattr(cfg, 'ANTI_SPOOF_ENABLED', False)
    monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
    monkeypatch.setattr(cfg, 'LIVENESS_CHECK_ENABLED', False)
    monkeypatch.setattr(cfg, 'LIVENESS_REQUIRE_BLINK', False)
    monkeypatch.setattr(cfg, 'IMAGE_QUALITY_CHECK_ENABLED', False)
    monkeypatch.setattr(cfg, 'REJECT_MULTIPLE_FACES', False)

    import cv2
    fake_box = np.array([[10, 10, 80, 80]])
    monkeypatch.setattr(cv2.CascadeClassifier, 'detectMultiScale', lambda self, *a, **k: fake_box)
    monkeypatch.setattr(app_module, 'compute_embedding', lambda face_rgb: _FAKE_EMBEDDING)

    app_module.limiter.storage.reset()
    app_module.init_databases()

    port = _free_port()
    server = make_server('127.0.0.1', port, app_module.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{port}'

    for _ in range(50):  # give the server a moment to actually start accepting connections
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError('E2E live_server did not start accepting connections in time')

    yield {'base_url': base_url, 'database_path': str(database_path)}

    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope='session')
def browser():
    """One Chromium instance for the whole E2E session, launched with
    fake-camera flags so getUserMedia() succeeds without real hardware
    (see Playwright/Chromium's --use-fake-device-for-media-stream)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch(args=[
            '--use-fake-device-for-media-stream',
            '--use-fake-ui-for-media-stream',
        ])
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser):
    """A fresh browser context/page per test, with camera permission
    pre-granted (no permission prompt to dismiss)."""
    context = browser.new_context(viewport={'width': 1280, 'height': 900}, permissions=['camera'])
    pg = context.new_page()
    yield pg
    context.close()
