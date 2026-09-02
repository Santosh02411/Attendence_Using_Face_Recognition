"""Tests for the face-recognition/biometric feature set: enrollment
quality scoring and status, per-photo removal, re-enrollment, embedding
model versioning, runtime-configurable recognition thresholds,
recognition-confidence reporting, and unknown-face detection.
"""
import base64
import io
import os
import sqlite3
from datetime import datetime
from unittest.mock import patch

import cv2
import numpy as np
from conftest import login_as_admin
from PIL import Image
from werkzeug.security import generate_password_hash

import app as app_module
import config as cfg

FAKE_FACE_BOX = np.array([[10, 10, 50, 50]])


def _real_jpeg_data_url():
    img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _frame_burst(n=None):
    n = n or cfg.LIVENESS_FRAME_COUNT
    return [_real_jpeg_data_url() for _ in range(n)]


def _unit_vector(index):
    v = np.zeros(128, dtype=np.float32)
    v[index] = 1.0
    return v


def _seed_student(db_path, student_id, name, roll_no):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (?,?,?,?,?,?)',
        (student_id, name, roll_no, 'CSE', '1', generate_password_hash('pw'))
    )
    conn.commit()
    conn.close()


def _seed_session(db_path, session_id, date_str=None, time_str=None, active=1, status='active'):
    if date_str is None or time_str is None:
        now = datetime.now()
        date_str = date_str or now.strftime('%d-%m-%Y')
        time_str = time_str or now.strftime('%H:%M')
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO subjects(id, name, code) VALUES (1, 'Sub', 'S1')")
    conn.execute(
        'INSERT INTO sessions(id, subject_id, title, date, time, end_date, end_time, active, status) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)',
        (session_id, f'Sess{session_id}', date_str, time_str, date_str, '23:59', active, status)
    )
    conn.commit()
    conn.close()


def _login_student(client, student_id):
    with client.session_transaction() as sess:
        sess['user_type'] = 'student'
        sess['student_id'] = student_id


def _base_mocks(embedding):
    return (
        patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX),
        patch.object(app_module, 'compute_embedding', return_value=embedding),
        patch.object(app_module, 'check_liveness_sequence', return_value=(True, [FAKE_FACE_BOX[0]] * cfg.LIVENESS_FRAME_COUNT)),
        patch.object(app_module, 'assess_image_quality', return_value=[]),
    )


def _mark_attendance(client, session_id, embedding):
    mocks = _base_mocks(embedding)
    with mocks[0], mocks[1], mocks[2], mocks[3]:
        return client.post('/student/attend/mark', json={'session_id': session_id, 'images': _frame_burst()})


class TestQualityScoreAndEnrollmentStatus:
    def test_not_enrolled_with_zero_photos(self, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        status = app_module._enrollment_status(1)
        assert status == {'status': 'not_enrolled', 'photo_count': 0, 'avg_quality': None}

    def test_pending_with_too_few_photos(self, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=90.0)
        status = app_module._enrollment_status(1)
        assert status['status'] == 'pending'
        assert status['photo_count'] == 1

    def test_pending_with_enough_photos_but_low_quality(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ENROLLMENT_MIN_PHOTOS', 2)
        monkeypatch.setattr(cfg, 'ENROLLMENT_QUALITY_REENROLL_THRESHOLD', 50.0)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=10.0)
        app_module.store_embedding(1, _unit_vector(1), quality_score=10.0)
        status = app_module._enrollment_status(1)
        assert status['status'] == 'pending'
        assert status['avg_quality'] == 10.0

    def test_complete_with_enough_good_quality_photos(self, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ENROLLMENT_MIN_PHOTOS', 2)
        monkeypatch.setattr(cfg, 'ENROLLMENT_QUALITY_REENROLL_THRESHOLD', 50.0)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=80.0)
        app_module.store_embedding(1, _unit_vector(1), quality_score=90.0)
        status = app_module._enrollment_status(1)
        assert status['status'] == 'complete'
        assert status['avg_quality'] == 85.0

    def test_compute_quality_score_sharp_well_lit_scores_higher_than_blurry(self, isolated_paths):
        sharp = (np.random.rand(100, 100) * 255).astype(np.uint8)
        blurry = np.full((100, 100), 128, dtype=np.uint8)
        sharp_score = app_module._compute_quality_score(sharp)
        blurry_score = app_module._compute_quality_score(blurry)
        assert sharp_score > blurry_score

    def test_quality_score_is_bounded_0_to_100(self, isolated_paths):
        img = (np.random.rand(100, 100) * 255).astype(np.uint8)
        score = app_module._compute_quality_score(img)
        assert 0.0 <= score <= 100.0

    def test_student_profile_shows_pending_badge(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ENROLLMENT_MIN_PHOTOS', 3)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=90.0)
        _login_student(client, 1)
        resp = client.get('/student/profile')
        assert b'Pending' in resp.data

    def test_admin_students_page_shows_enrollment_column(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        login_as_admin(client)
        resp = client.get('/admin/students')
        assert b'Not Enrolled' in resp.data


class TestFacePhotoManagement:
    def test_save_face_images_records_filename_and_quality(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=_unit_vector(0)):
            saved, _reasons = app_module.save_face_images(1, [_real_jpeg_data_url()])
        assert saved == 1
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute('SELECT image_filename, quality_score, model_version FROM face_embeddings WHERE student_id=1').fetchone()
        conn.close()
        assert row[0] is not None and row[0].startswith('User.1.')
        assert row[1] is not None
        assert row[2] == cfg.EMBEDDER_MODEL_VERSION

    def test_student_can_remove_one_photo(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=80.0)
        app_module.store_embedding(1, _unit_vector(1), quality_score=80.0)
        embedding_id = sqlite3.connect(isolated_paths['database_path']).execute(
            'SELECT id FROM face_embeddings WHERE student_id=1 LIMIT 1').fetchone()[0]

        _login_student(client, 1)
        resp = client.post(f'/student/face-photos/{embedding_id}/delete', follow_redirects=True)
        assert resp.status_code == 200

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM face_embeddings WHERE student_id=1').fetchone()[0]
        photo_count = conn.execute('SELECT photo_count FROM students WHERE id=1').fetchone()[0]
        conn.close()
        assert count == 1
        assert photo_count == 1

    def test_removing_photo_deletes_its_file(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        os.makedirs(isolated_paths['data_dir'], exist_ok=True)
        filename = 'User.1.1.jpg'
        with open(os.path.join(isolated_paths['data_dir'], filename), 'wb') as f:
            f.write(b'fake image bytes')
        app_module.store_embedding(1, _unit_vector(0), image_filename=filename, quality_score=80.0)
        embedding_id = sqlite3.connect(isolated_paths['database_path']).execute(
            'SELECT id FROM face_embeddings WHERE student_id=1').fetchone()[0]

        assert os.path.exists(os.path.join(isolated_paths['data_dir'], filename))
        app_module._delete_single_embedding(1, embedding_id)
        assert not os.path.exists(os.path.join(isolated_paths['data_dir'], filename))

    def test_cannot_delete_another_students_photo(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_student(isolated_paths['database_path'], 2, 'Bob', 'R2')
        app_module.store_embedding(2, _unit_vector(0), quality_score=80.0)
        embedding_id = sqlite3.connect(isolated_paths['database_path']).execute(
            'SELECT id FROM face_embeddings WHERE student_id=2').fetchone()[0]

        _login_student(client, 1)
        client.post(f'/student/face-photos/{embedding_id}/delete')

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM face_embeddings WHERE student_id=2').fetchone()[0]
        conn.close()
        assert count == 1  # Bob's photo untouched

    def test_admin_can_remove_any_students_photo(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=80.0)
        embedding_id = sqlite3.connect(isolated_paths['database_path']).execute(
            'SELECT id FROM face_embeddings WHERE student_id=1').fetchone()[0]

        login_as_admin(client)
        client.post(f'/admin/student/1/face-photos/{embedding_id}/delete')

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM face_embeddings WHERE student_id=1').fetchone()[0]
        conn.close()
        assert count == 0

    def test_face_photo_removal_is_audited(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=80.0)
        embedding_id = sqlite3.connect(isolated_paths['database_path']).execute(
            'SELECT id FROM face_embeddings WHERE student_id=1').fetchone()[0]

        _login_student(client, 1)
        client.post(f'/student/face-photos/{embedding_id}/delete')

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE action='face_photo_removed'").fetchone()
        conn.close()
        assert row is not None


class TestReEnrollment:
    def test_reenroll_wipes_all_embeddings_and_photo_count(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=80.0)
        app_module.store_embedding(1, _unit_vector(1), quality_score=80.0)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute('UPDATE students SET photo_count=2 WHERE id=1')
        conn.commit()
        conn.close()

        _login_student(client, 1)
        resp = client.post('/student/reenroll', follow_redirects=True)
        assert resp.status_code == 200

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM face_embeddings WHERE student_id=1').fetchone()[0]
        photo_count = conn.execute('SELECT photo_count FROM students WHERE id=1').fetchone()[0]
        conn.close()
        assert count == 0
        assert photo_count == 0

    def test_reenroll_redirects_to_register_face(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_student(client, 1)
        resp = client.post('/student/reenroll', follow_redirects=False)
        assert resp.status_code == 302
        assert '/student/register-face' in resp.headers['Location']

    def test_reenroll_is_audited(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _login_student(client, 1)
        client.post('/student/reenroll')
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE action='face_reenrollment_started'").fetchone()
        conn.close()
        assert row is not None

    def test_admin_reset_face_data(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=80.0)
        login_as_admin(client)
        resp = client.post('/admin/student/1/reset-face-data', follow_redirects=True)
        assert resp.status_code == 200

        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute('SELECT COUNT(*) FROM face_embeddings WHERE student_id=1').fetchone()[0]
        conn.close()
        assert count == 0

    def test_reenroll_requires_login(self, client, isolated_paths):
        resp = client.post('/student/reenroll', follow_redirects=False)
        assert resp.status_code == 302


class TestRuntimeConfigurableThresholds:
    def test_no_override_falls_back_to_config_default(self, isolated_paths):
        assert app_module.get_effective_setting('RECOGNIZE_MATCH_THRESHOLD') == cfg.RECOGNIZE_MATCH_THRESHOLD

    def test_set_and_get_override(self, isolated_paths):
        app_module.set_effective_setting('RECOGNIZE_MATCH_THRESHOLD', '0.42', 'admin')
        assert app_module.get_effective_setting('RECOGNIZE_MATCH_THRESHOLD') == 0.42

    def test_admin_can_update_threshold_via_page(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/recognition-settings', data={'MARK_ATTENDANCE_MATCH_THRESHOLD': '0.77'})
        assert app_module.get_effective_setting('MARK_ATTENDANCE_MATCH_THRESHOLD') == 0.77

    def test_invalid_value_is_rejected(self, client, isolated_paths):
        login_as_admin(client)
        resp = client.post('/admin/recognition-settings', data={'RECOGNIZE_MATCH_THRESHOLD': 'not-a-number'}, follow_redirects=True)
        assert b'not a number' in resp.data.lower()
        assert app_module.get_effective_setting('RECOGNIZE_MATCH_THRESHOLD') == cfg.RECOGNIZE_MATCH_THRESHOLD

    def test_reset_removes_override(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/recognition-settings', data={'DUPLICATE_FACE_MATCH_THRESHOLD': '0.9'})
        assert app_module.get_effective_setting('DUPLICATE_FACE_MATCH_THRESHOLD') == 0.9
        client.post('/admin/recognition-settings/DUPLICATE_FACE_MATCH_THRESHOLD/reset')
        assert app_module.get_effective_setting('DUPLICATE_FACE_MATCH_THRESHOLD') == cfg.DUPLICATE_FACE_MATCH_THRESHOLD

    def test_recognition_settings_requires_admin(self, client, isolated_paths):
        resp = client.get('/admin/recognition-settings', follow_redirects=False)
        assert resp.status_code == 302

    def test_threshold_update_is_audited(self, client, isolated_paths):
        login_as_admin(client)
        client.post('/admin/recognition-settings', data={'RECOGNIZE_MATCH_THRESHOLD': '0.6'})
        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action FROM audit_log WHERE action='recognition_settings_updated'").fetchone()
        conn.close()
        assert row is not None

    def test_effective_setting_used_at_mark_attendance_time(self, client, isolated_paths):
        """A saved override for MARK_ATTENDANCE_MATCH_THRESHOLD should
        actually change self-service marking behavior, not just be
        stored inertly."""
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        app_module.store_embedding(1, _unit_vector(0), quality_score=80.0)
        app_module.set_effective_setting('MARK_ATTENDANCE_MATCH_THRESHOLD', '1.5', 'admin')
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, _unit_vector(0))
        payload = resp.get_json()
        assert payload['status'] == 'error'


class TestRecognitionConfidenceReporting:
    def test_confidence_recorded_on_self_service_mark(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        app_module.store_embedding(1, _unit_vector(0))
        _login_student(client, 1)

        resp = _mark_attendance(client, 1, _unit_vector(0))
        assert resp.get_json()['status'] == 'present'

        conn = sqlite3.connect(isolated_paths['database_path'])
        confidence = conn.execute('SELECT confidence FROM attendance WHERE student_id=1 AND session_id=1').fetchone()[0]
        conn.close()
        assert confidence is not None
        assert 0.9 <= confidence <= 1.0

    def test_confidence_appears_in_admin_attendance_records(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp, confidence) VALUES (1, 1, 'Present', 'x', 0.87)")
        conn.commit()
        conn.close()

        login_as_admin(client)
        resp = client.get('/admin/attendance')
        assert b'87.0' in resp.data

    def test_confidence_included_in_session_csv_export(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute("INSERT INTO attendance(student_id, session_id, status, timestamp, confidence) VALUES (1, 1, 'Present', 'x', 0.75)")
        conn.commit()
        conn.close()

        login_as_admin(client)
        resp = client.get('/admin/session/1/export')
        assert b'75.0' in resp.data


class TestUnknownFaceDetection:
    def test_kiosk_unmatched_face_logs_unknown_face_event(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        _seed_session(isolated_paths['database_path'], 1)
        app_module.store_embedding(1, _unit_vector(0))
        login_as_admin(client)

        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=_unit_vector(99)):
            resp = client.post('/admin/session/1/recognize', json={'image': _real_jpeg_data_url()})
        assert resp.get_json()['status'] == 'not_found'

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT action, target FROM audit_log WHERE action='unknown_face_detected'").fetchone()
        conn.close()
        assert row is not None
        assert row[1] == 'session #1'

    def test_no_face_detected_does_not_log_unknown_face(self, client, isolated_paths):
        """A detection failure (no face at all) is different from an
        unmatched-but-detected face -- only the latter is "unknown"."""
        _seed_session(isolated_paths['database_path'], 1)
        login_as_admin(client)

        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=[]):
            client.post('/admin/session/1/recognize', json={'image': _real_jpeg_data_url()})

        conn = sqlite3.connect(isolated_paths['database_path'])
        row = conn.execute("SELECT 1 FROM audit_log WHERE action='unknown_face_detected'").fetchone()
        conn.close()
        assert row is None

    def test_unknown_face_appears_in_security_filter(self, client, isolated_paths):
        app_module.log_audit('anonymous', 'unknown', 'unknown_face_detected', target='session #1', details='best similarity 0.30')
        login_as_admin(client)
        resp = client.get('/admin/audit-log?filter=security')
        assert b'unknown_face_detected' in resp.data


class TestEnrollmentReminderWidget:
    def test_dashboard_lists_students_needing_reenrollment(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ENROLLMENT_MIN_PHOTOS', 3)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=90.0)
        login_as_admin(client)

        resp = client.get('/admin/dashboard')
        assert b'Enrollment Reminders' in resp.data
        assert b'Alice' in resp.data

    def test_dashboard_omits_fully_enrolled_students(self, client, isolated_paths, monkeypatch):
        monkeypatch.setattr(cfg, 'ENROLLMENT_MIN_PHOTOS', 1)
        monkeypatch.setattr(cfg, 'ENROLLMENT_QUALITY_REENROLL_THRESHOLD', 10.0)
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), quality_score=90.0)
        login_as_admin(client)

        resp = client.get('/admin/dashboard')
        assert b'No enrollment issues found' in resp.data


class TestModelVersioning:
    def test_new_embedding_tagged_with_current_model_version(self, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0))
        conn = sqlite3.connect(isolated_paths['database_path'])
        version = conn.execute('SELECT model_version FROM face_embeddings WHERE student_id=1').fetchone()[0]
        conn.close()
        assert version == cfg.EMBEDDER_MODEL_VERSION

    def test_explicit_model_version_override(self, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), model_version='old-model-v0')
        conn = sqlite3.connect(isolated_paths['database_path'])
        version = conn.execute('SELECT model_version FROM face_embeddings WHERE student_id=1').fetchone()[0]
        conn.close()
        assert version == 'old-model-v0'

    def test_recognition_settings_page_shows_model_version_breakdown(self, client, isolated_paths):
        _seed_student(isolated_paths['database_path'], 1, 'Alice', 'R1')
        app_module.store_embedding(1, _unit_vector(0), model_version='old-model-v0')
        app_module.store_embedding(1, _unit_vector(1))
        login_as_admin(client)
        resp = client.get('/admin/recognition-settings')
        assert b'old-model-v0' in resp.data
        assert b'Outdated' in resp.data
