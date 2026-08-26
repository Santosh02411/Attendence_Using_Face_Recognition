"""Tests for the student attendance-marking flow, covering both the
happy path and the security properties fixed earlier (session-derived
identity, 1:1 verification against the logged-in student only)."""
import base64
import io
import sqlite3
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg

FAKE_FACE_BOX = np.array([[10, 10, 50, 50]])


def _real_jpeg_data_url():
    """A syntactically valid JPEG the app can decode — its pixel content
    doesn't matter since face detection/embedding are mocked in these
    tests (a real Haar cascade won't reliably find a face in random
    noise, which isn't the thing under test here)."""
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
    """Two students with distinct known embeddings, plus an active session."""
    _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1', 'pass1')
    _seed_student(isolated_paths['database_path'], 2, 'Bob', 'R2', 'pass2')
    _seed_active_session(isolated_paths['database_path'])
    alice_embedding = _unit_vector(0)
    bob_embedding = _unit_vector(1)
    app_module.store_embedding(1, alice_embedding)
    app_module.store_embedding(2, bob_embedding)
    return {'alice_embedding': alice_embedding, 'bob_embedding': bob_embedding}


def _login_session(client, student_id):
    with client.session_transaction() as sess:
        sess['user_type'] = 'student'
        sess['student_id'] = student_id


def _mocked_detection_and_liveness(embedding):
    """Common patch set: single-face detection always finds FAKE_FACE_BOX,
    the embedder returns a fixed vector, and the (real) liveness sequence
    check is bypassed since it depends on genuine pixel/eye patterns that
    synthetic random-noise test images can't reliably produce."""
    return (
        patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX),
        patch.object(app_module, 'compute_embedding', return_value=embedding),
        patch.object(app_module, 'check_liveness_sequence', return_value=(True, [FAKE_FACE_BOX[0]] * cfg.LIVENESS_FRAME_COUNT)),
    )


class TestMarkAttendanceHappyPath:
    def test_matching_own_face_marks_present(self, client, mark_attendance_env):
        _login_session(client, 1)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            resp = client.post('/student/attend/mark', json={
                'session_id': 1, 'images': _frame_burst(),
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'present'

    def test_legacy_two_field_payload_still_works(self, client, mark_attendance_env):
        """Back-compat: an older client sending {image, image2} instead
        of {images: [...]} should still be accepted."""
        _login_session(client, 1)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            resp = client.post('/student/attend/mark', json={
                'session_id': 1, 'image': _real_jpeg_data_url(), 'image2': _real_jpeg_data_url(),
            })
        assert resp.get_json()['status'] == 'present'

    def test_already_marked_is_reported_without_duplicate_insert(self, client, mark_attendance_env, isolated_paths):
        _login_session(client, 1)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'already_marked'
        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM attendance WHERE student_id=1').fetchone()[0]
        conn.close()
        assert count == 1


class TestMarkAttendanceSecurity:
    """Regression tests for the fixed student_id trust boundary: identity
    always comes from the server-side session, and matching is 1:1
    against the logged-in student's own embeddings only."""

    def test_mismatched_face_is_rejected_not_marked_present(self, client, mark_attendance_env):
        """Bob is logged in, but the captured face embeds like Alice.
        This must be rejected — never silently marked present for
        whichever identity the face happens to match."""
        _login_session(client, 2)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'error'

    def test_mismatched_attempt_creates_no_attendance_record_for_anyone(self, client, mark_attendance_env, isolated_paths):
        _login_session(client, 2)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        conn = sqlite3.connect(isolated_paths['database_path'])
        rows = conn.execute('SELECT student_id FROM attendance').fetchall()
        conn.close()
        assert rows == []

    def test_payload_student_id_is_ignored(self, client, mark_attendance_env):
        """Even if the client sends a student_id in the JSON body, the
        server must use the session's student_id, never the payload's."""
        _login_session(client, 1)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            resp = client.post('/student/attend/mark', json={
                'session_id': 1, 'student_id': 2,  # attacker-supplied, should be ignored
                'images': _frame_burst(),
            })
        # Alice (session id=1) is logged in and her own face matched -> present.
        # If the payload's student_id=2 had been trusted instead, this
        # would have compared against Bob's gallery and failed.
        assert resp.get_json()['status'] == 'present'

    def test_requires_student_login(self, client, mark_attendance_env):
        resp = client.post('/student/attend/mark', json={
            'session_id': 1, 'images': _frame_burst(),
        }, follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_missing_second_frame_is_rejected(self, client, mark_attendance_env):
        _login_session(client, 1)
        resp = client.post('/student/attend/mark', json={
            'session_id': 1, 'images': [_real_jpeg_data_url()],
        })
        assert resp.status_code == 400

    def test_inactive_session_is_rejected(self, client, mark_attendance_env, isolated_paths):
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute('UPDATE sessions SET active=0 WHERE id=1')
        conn.commit()
        conn.close()

        _login_session(client, 1)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.status_code == 400
        assert 'not active' in resp.get_json()['message'].lower()

    def test_static_frame_burst_fails_liveness(self, client, mark_attendance_env):
        """With the real (non-mocked) liveness check, an identical frame
        burst must be rejected even if the face would otherwise match."""
        _login_session(client, 1)
        same_image = _real_jpeg_data_url()
        p1, p2, _p3_unused = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2:
            # check_liveness_sequence is NOT mocked here — real pixel-diff
            # and blink logic runs against an identical frame burst.
            resp = client.post('/student/attend/mark', json={
                'session_id': 1, 'images': [same_image] * cfg.LIVENESS_FRAME_COUNT,
            })
        assert resp.get_json()['status'] == 'error'
        assert 'live' in resp.get_json()['message'].lower()

    def test_multiple_faces_rejected(self, client, mark_attendance_env):
        _login_session(client, 1)
        two_faces = np.array([[10, 10, 50, 50], [200, 10, 50, 50]])
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=two_faces), \
             patch.object(app_module, 'compute_embedding', return_value=mark_attendance_env['alice_embedding']):
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'error'
        assert 'multiple faces' in resp.get_json()['message'].lower()


class TestAttendanceNetworkRestriction:
    def test_unconfigured_allowlist_permits_any_ip(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', [])
        _login_session(client, 1)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] != 'error' or 'network' not in resp.get_json().get('message', '').lower()

    def test_request_from_disallowed_network_is_blocked(self, client, mark_attendance_env, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['10.0.0.0/24'])
        _login_session(client, 1)
        resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.status_code == 403
        assert 'network' in resp.get_json()['message'].lower()

    def test_request_from_allowed_network_proceeds(self, client, mark_attendance_env, monkeypatch):
        # Flask's test client makes requests appear to come from 127.0.0.1.
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['127.0.0.1/32'])
        _login_session(client, 1)
        p1, p2, p3 = _mocked_detection_and_liveness(mark_attendance_env['alice_embedding'])
        with p1, p2, p3:
            resp = client.post('/student/attend/mark', json={'session_id': 1, 'images': _frame_burst()})
        assert resp.get_json()['status'] == 'present'
