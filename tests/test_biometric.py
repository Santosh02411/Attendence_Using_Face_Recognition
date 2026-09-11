"""Tests for biometric.py (iris-authentication SCAFFOLDING — see that
module's docstring: no real hardware/SDK, a non-biometric mock provider
for exercising the plumbing only) and the /admin/biometric-diagnostics
route built on it.
"""
import sqlite3

import biometric
import config as cfg
from tests.conftest import login_as_admin


class TestGetIrisProvider:
    def test_returns_none_when_disabled(self):
        assert biometric.get_iris_provider('mock', False) is None

    def test_returns_none_for_unknown_provider_even_when_enabled(self):
        assert biometric.get_iris_provider('acme-vendor-sdk', True) is None

    def test_returns_mock_provider_when_enabled_and_valid(self):
        provider = biometric.get_iris_provider('mock', True)
        assert provider is not None
        assert provider.name == 'mock'


class TestMockIrisProvider:
    def setup_method(self):
        self.provider = biometric.MockIrisProvider()

    def test_capture_template_is_deterministic(self):
        t1 = self.provider.capture_template(b'sample-a')
        t2 = self.provider.capture_template(b'sample-a')
        assert (t1 == t2).all()

    def test_capture_template_has_fixed_dimension(self):
        template = self.provider.capture_template(b'anything')
        assert template.shape == (biometric.TEMPLATE_DIM,)

    def test_self_comparison_scores_1(self):
        t1 = self.provider.capture_template(b'sample-a')
        t2 = self.provider.capture_template(b'sample-a')
        assert self.provider.compare(t1, t2) == 1.0

    def test_different_inputs_score_lower_than_self_comparison(self):
        t1 = self.provider.capture_template(b'sample-a')
        t2 = self.provider.capture_template(b'a-completely-different-sample')
        score = self.provider.compare(t1, t2)
        assert 0.0 <= score < 1.0

    def test_compare_mismatched_shapes_returns_zero(self):
        import numpy as np
        t1 = self.provider.capture_template(b'sample-a')
        t2 = np.zeros(3, dtype=np.float32)
        assert self.provider.compare(t1, t2) == 0.0

    def test_serialize_roundtrip_preserves_template(self):
        template = self.provider.capture_template(b'sample-a')
        blob = biometric.serialize_template(template)
        restored = biometric.deserialize_template(blob)
        assert self.provider.compare(template, restored) == 1.0


class TestBiometricDiagnosticsRoute:
    def test_requires_admin_login(self, client, isolated_paths):
        resp = client.get('/admin/biometric-diagnostics', follow_redirects=False)
        assert resp.status_code == 302

    def test_shows_disabled_state_by_default(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.get('/admin/biometric-diagnostics')
        assert resp.status_code == 200
        assert b'not a working biometric security feature' in resp.data
        assert b'IRIS_AUTH_ENABLED' in resp.data  # points at the setting to flip

    def test_compare_action_when_enabled(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'IRIS_AUTH_ENABLED', True)
        login_as_admin(client)
        resp = client.post('/admin/biometric-diagnostics', data={
            'action': 'compare', 'sample_a': 'hello', 'sample_b': 'hello',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'1.0' in resp.data

    def test_enroll_action_stores_template(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'IRIS_AUTH_ENABLED', True)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (1, 'A', 'R1', 'CSE', '1', 'x')")
        conn.commit()
        conn.close()

        login_as_admin(client)
        resp = client.post('/admin/biometric-diagnostics', data={
            'action': 'enroll', 'student_id': '1', 'sample': 'synthetic-sample',
        }, follow_redirects=True)
        assert resp.status_code == 200

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT provider_name FROM iris_templates WHERE student_id=1').fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 'mock'

    def test_enroll_action_replaces_existing_template(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'IRIS_AUTH_ENABLED', True)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (1, 'A', 'R1', 'CSE', '1', 'x')")
        conn.commit()
        conn.close()

        login_as_admin(client)
        client.post('/admin/biometric-diagnostics', data={'action': 'enroll', 'student_id': '1', 'sample': 'first'})
        client.post('/admin/biometric-diagnostics', data={'action': 'enroll', 'student_id': '1', 'sample': 'second'})

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM iris_templates WHERE student_id=1').fetchone()[0]
        conn.close()
        assert count == 1  # replaced, not accumulated

    def test_lookup_compare_against_stored_template(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'IRIS_AUTH_ENABLED', True)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (1, 'A', 'R1', 'CSE', '1', 'x')")
        conn.commit()
        conn.close()

        login_as_admin(client)
        client.post('/admin/biometric-diagnostics', data={'action': 'enroll', 'student_id': '1', 'sample': 'matching-sample'})
        resp = client.post('/admin/biometric-diagnostics', data={
            'action': 'lookup_compare', 'lookup_student_id': '1', 'lookup_sample': 'matching-sample',
        }, follow_redirects=True)
        assert b'1.0' in resp.data

    def test_lookup_compare_without_enrollment_shows_error(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'IRIS_AUTH_ENABLED', True)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (1, 'A', 'R1', 'CSE', '1', 'x')")
        conn.commit()
        conn.close()

        login_as_admin(client)
        resp = client.post('/admin/biometric-diagnostics', data={
            'action': 'lookup_compare', 'lookup_student_id': '1', 'lookup_sample': 'anything',
        }, follow_redirects=True)
        assert b'No test template enrolled yet' in resp.data

    def test_diagnostic_actions_are_noop_when_disabled(self, client, isolated_paths):
        """IRIS_AUTH_ENABLED stays off by default -- posting an action
        should not silently do anything (no provider to run it)."""
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (1, 'A', 'R1', 'CSE', '1', 'x')")
        conn.commit()
        conn.close()

        login_as_admin(client)
        client.post('/admin/biometric-diagnostics', data={'action': 'enroll', 'student_id': '1', 'sample': 'x'})

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM iris_templates').fetchone()[0]
        conn.close()
        assert count == 0
