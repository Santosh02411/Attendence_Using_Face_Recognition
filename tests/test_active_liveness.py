"""Tests for the active-liveness-challenge and anti-spoof wiring around
/student/attend/mark and /student/liveness-challenge — the anti-proxy
features layered on top of the base recognition/liveness flow already
covered by test_attendance.py and test_recognition_hardening.py.

These re-enable cfg.ACTIVE_LIVENESS_ENABLED / cfg.ANTI_SPOOF_ENABLED
explicitly (conftest.py's isolated_paths fixture disables both by
default — see its comment for why) and mock the underlying pure
verification functions (already unit-tested directly in
test_face_security.py) rather than re-deriving real challenge responses
from synthetic frames — the point here is testing the wiring: token
issuance, single-use consumption, expiry, and how failures surface, not
re-testing compute_screen_replay_score's math again.
"""
import sqlite3
import time
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg
import face_security

FAKE_FACE_BOX = np.array([[10, 10, 50, 50]])


def _real_jpeg_data_url():
    import base64
    import io

    from PIL import Image
    img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _frame_burst(n=None):
    n = n or cfg.LIVENESS_FRAME_COUNT
    return [_real_jpeg_data_url() for _ in range(n)]


def _seed_student(db_path, student_id, name, roll_no, password):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, 'CSE', '1', generate_password_hash(password))
    )
    conn.commit()
    conn.close()


def _seed_active_session(db_path, session_id=1):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO subjects(id, name, code) VALUES (1, 'Sub', 'S1')")
    conn.execute(
        "INSERT INTO sessions(id, subject_id, title, date, time, active) VALUES (?, 1, 'S', '2026-01-01', '10:00', 1)",
        (session_id,)
    )
    conn.commit()
    conn.close()


def _unit_vector(index):
    v = np.zeros(128, dtype=np.float32)
    v[index] = 1.0
    return v


@pytest.fixture
def mark_attendance_env(client, isolated_paths):
    _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1', 'pass1')
    _seed_active_session(isolated_paths['database_path'])
    embedding = _unit_vector(0)
    app_module.store_embedding(1, embedding)
    return {'embedding': embedding}


def _login_session(client, student_id):
    with client.session_transaction() as sess:
        sess['user_type'] = 'student'
        sess['student_id'] = student_id


def _base_mocks(embedding):
    """Bypasses face detection/embedding/passive-liveness the same way
    test_attendance.py does — these tests are about the active-challenge
    and anti-spoof layers specifically, not re-testing detection."""
    return (
        patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX),
        patch.object(app_module, 'compute_embedding', return_value=embedding),
        patch.object(app_module, 'check_liveness_sequence', return_value=(True, [FAKE_FACE_BOX[0]] * cfg.LIVENESS_FRAME_COUNT)),
        patch.object(app_module, 'assess_image_quality', return_value=[]),
    )


class TestLivenessChallengeEndpoint:
    def test_requires_student_login(self, client, mark_attendance_env):
        resp = client.get('/student/liveness-challenge', follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_returns_enabled_false_when_disabled(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
        _login_session(client, 1)
        resp = client.get('/student/liveness-challenge')
        assert resp.get_json() == {'enabled': False}

    def test_returns_a_challenge_from_the_configured_types(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', True)
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_CHALLENGE_TYPES', ['blink', 'head_turn'])
        _login_session(client, 1)
        resp = client.get('/student/liveness-challenge')
        data = resp.get_json()
        assert data['enabled'] is True
        assert data['challenge'] in ('blink', 'head_turn')
        assert len(data['token']) > 10
        assert isinstance(data['prompt'], str) and data['prompt']

    def test_issuing_a_challenge_stores_it_in_the_session(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', True)
        _login_session(client, 1)
        resp = client.get('/student/liveness-challenge')
        token = resp.get_json()['token']
        with client.session_transaction() as sess:
            assert sess['liveness_challenge']['token'] == token


class TestConsumeLivenessChallenge:
    """Direct tests of app_module._consume_liveness_challenge — the
    single-use/expiry/mismatch logic underneath the HTTP layer above."""

    def test_valid_token_returns_the_issued_type_and_clears_it(self, client):
        with app_module.app.test_request_context():
            challenge = app_module._issue_liveness_challenge()
            challenge_type, error = app_module._consume_liveness_challenge(challenge['token'])
            assert error is None
            assert challenge_type == challenge['type']
            from flask import session as flask_session
            assert 'liveness_challenge' not in flask_session

    def test_token_is_single_use(self, client):
        with app_module.app.test_request_context():
            challenge = app_module._issue_liveness_challenge()
            app_module._consume_liveness_challenge(challenge['token'])
            challenge_type, error = app_module._consume_liveness_challenge(challenge['token'])
            assert challenge_type is None
            assert error is not None

    def test_mismatched_token_is_rejected(self, client):
        with app_module.app.test_request_context():
            app_module._issue_liveness_challenge()
            challenge_type, error = app_module._consume_liveness_challenge('wrong-token')
            assert challenge_type is None
            assert error is not None

    def test_no_challenge_issued_is_rejected(self, client):
        with app_module.app.test_request_context():
            challenge_type, error = app_module._consume_liveness_challenge('any-token')
            assert challenge_type is None
            assert error is not None

    def test_expired_challenge_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_CHALLENGE_TTL_SECONDS', 0)
        with app_module.app.test_request_context():
            challenge = app_module._issue_liveness_challenge()
            time.sleep(0.01)
            challenge_type, error = app_module._consume_liveness_challenge(challenge['token'])
            assert challenge_type is None
            assert 'expired' in error.lower()


class TestActiveLivenessInMarkAttendance:
    def _get_token(self, client):
        resp = client.get('/student/liveness-challenge')
        return resp.get_json()['token']

    def test_missing_token_is_rejected_when_enabled(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', True)
        _login_session(client, 1)
        self._get_token(client)  # issue one but don't send it
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3]:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'error'

    def test_valid_token_with_passing_challenge_marks_present(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', True)
        _login_session(client, 1)
        token = self._get_token(client)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3], \
             patch.object(app_module, '_verify_active_challenge', return_value=True):
            resp = client.post('/student/attend/mark', json={
                'session_id': 1, 'images': _frame_burst(), 'challenge_token': token,
            })
        assert resp.get_json()['status'] == 'present'

    def test_failing_challenge_response_is_rejected(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', True)
        _login_session(client, 1)
        token = self._get_token(client)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3], \
             patch.object(app_module, '_verify_active_challenge', return_value=False):
            resp = client.post('/student/attend/mark', json={
                'session_id': 1, 'images': _frame_burst(), 'challenge_token': token,
            })
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'confirm' in data['message'].lower()

    def test_token_cannot_be_reused_across_two_attempts(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', True)
        _login_session(client, 1)
        token = self._get_token(client)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3], \
             patch.object(app_module, '_verify_active_challenge', return_value=True):
            first = client.post('/student/attend/mark', json={
                'session_id': 1, 'images': _frame_burst(), 'challenge_token': token,
            })
            # Second attempt (e.g. attacker replaying the same request)
            # reuses the same token — must be rejected even though the
            # first attempt's challenge type would have verified fine.
            second = client.post('/student/attend/mark', json={
                'session_id': 1, 'images': _frame_burst(), 'challenge_token': token,
            })
        assert first.get_json()['status'] == 'present'
        assert second.get_json()['status'] in ('error', 'already_marked')

    def test_disabled_active_liveness_does_not_require_a_token(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
        _login_session(client, 1)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3]:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'present'


class TestAntiSpoofInMarkAttendance:
    def _get_token(self, client):
        resp = client.get('/student/liveness-challenge')
        data = resp.get_json()
        return data.get('token')

    def test_screen_replay_detection_blocks_attendance(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ANTI_SPOOF_ENABLED', True)
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
        _login_session(client, 1)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3], \
             patch.object(face_security, 'is_likely_screen_replay', return_value=True):
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'photo or screen' in data['message'].lower()

    def test_no_false_positive_when_not_a_replay(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ANTI_SPOOF_ENABLED', True)
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
        _login_session(client, 1)
        mocks = _base_mocks(mark_attendance_env['embedding'])
        with mocks[0], mocks[1], mocks[2], mocks[3], \
             patch.object(face_security, 'is_likely_screen_replay', return_value=False):
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'present'

    def test_image_quality_issue_blocks_attendance(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ACTIVE_LIVENESS_ENABLED', False)
        _login_session(client, 1)
        p1, p2, p3, *_rest = _base_mocks(mark_attendance_env['embedding'])
        with p1, p2, p3, patch.object(app_module, 'assess_image_quality', return_value=['blurry']):
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'quality' in data['message'].lower()


class TestFindBestMatchTopK:
    def test_default_top_k_matches_original_single_best_behavior(self, isolated_paths):
        gallery = [(1, _unit_vector(0)), (1, _unit_vector(1)), (2, _unit_vector(2))]
        query = _unit_vector(0)
        student_id, sim = app_module.find_best_match(query, gallery, top_k=1)
        assert student_id == 1
        assert sim == pytest.approx(1.0)

    def test_top_k_averages_a_candidates_best_embeddings(self, isolated_paths):
        # Student 1 has two embeddings: one identical to the query, one
        # orthogonal. Student 2 has one embedding, moderately similar.
        close = _unit_vector(0)
        far = _unit_vector(5)
        gallery = [(1, close), (1, far), (2, (_unit_vector(0) + _unit_vector(1)) / np.sqrt(2))]
        query = _unit_vector(0)

        student_id_top1, sim_top1 = app_module.find_best_match(query, gallery, top_k=1)
        assert student_id_top1 == 1
        assert sim_top1 == pytest.approx(1.0)

        # With top_k=2, student 1's score averages their two embeddings
        # (1.0 and 0.0) -> 0.5, while student 2's single embedding scores
        # ~0.707 — so the best match flips to student 2.
        student_id_top2, sim_top2 = app_module.find_best_match(query, gallery, top_k=2)
        assert student_id_top2 == 2
        assert sim_top2 == pytest.approx(1 / np.sqrt(2), abs=1e-4)

    def test_empty_gallery_returns_none(self, isolated_paths):
        assert app_module.find_best_match(_unit_vector(0), [], top_k=1) == (None, 0.0)
        assert app_module.find_best_match(_unit_vector(0), [], top_k=3) == (None, 0.0)


class TestKioskRecognitionLiveness:
    def _seed_admin_and_session(self, client, isolated_paths):
        _seed_active_session(isolated_paths['database_path'])
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1', 'pass1')
        app_module.store_embedding(1, _unit_vector(0))
        with client.session_transaction() as sess:
            sess['user_type'] = 'admin'
            sess['admin_user'] = 'admin'

    def test_identical_prev_frame_is_rejected_as_not_live(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LIVENESS_CHECK_ENABLED', True)
        self._seed_admin_and_session(client, isolated_paths)
        same_image = _real_jpeg_data_url()
        resp = client.post('/admin/session/1/recognize', json={'image': same_image, 'image_prev': same_image})
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'motion' in data['message'].lower() or 'live' in data['message'].lower()

    def test_no_prev_frame_skips_the_check_backward_compatibly(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'LIVENESS_CHECK_ENABLED', True)
        self._seed_admin_and_session(client, isolated_paths)
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=_unit_vector(0)), \
             patch.object(face_security, 'is_likely_screen_replay', return_value=False):
            resp = client.post('/admin/session/1/recognize', json={'image': _real_jpeg_data_url()})
        assert resp.get_json()['status'] == 'present'
