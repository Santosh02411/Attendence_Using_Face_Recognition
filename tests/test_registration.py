"""Tests for student registration: face-embedding storage, and the
duplicate-face check."""
import base64
import io
import sqlite3
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

import app as app_module

FAKE_FACE_BOX = np.array([[10, 10, 50, 50]])


def _real_jpeg_data_url():
    img = Image.fromarray(np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _unit_vector(index):
    v = np.zeros(128, dtype=np.float32)
    v[index] = 1.0
    return v


def _register_payload(roll_no='R1', images=None):
    return {
        'name': 'New Student', 'roll_no': roll_no, 'branch': 'CSE',
        'semester': '1', 'password': 'validPass123', 'gender': 'M',
        'images': images if images is not None else [_real_jpeg_data_url()] * 3,
    }


class TestRegistrationHappyPath:
    def test_new_face_registers_successfully(self, client, isolated_paths):
        new_embedding = _unit_vector(50)
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=new_embedding):
            resp = client.post('/student/register', json=_register_payload())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert 'student_id' in data

    def test_registration_stores_one_embedding_per_image(self, client, isolated_paths):
        new_embedding = _unit_vector(50)
        images = [_real_jpeg_data_url()] * 4
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=new_embedding):
            resp = client.post('/student/register', json=_register_payload(images=images))
        student_id = resp.get_json()['student_id']
        assert len(app_module.get_student_embeddings(student_id)) == 4

    def test_no_face_detected_rejects_registration(self, client, isolated_paths):
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=[]):
            resp = client.post('/student/register', json=_register_payload())
        assert resp.status_code == 400
        assert 'error' in resp.get_json()


class TestRegistrationImageQuality:
    def test_all_images_failing_quality_rolls_back_registration(self, client, isolated_paths):
        new_embedding = _unit_vector(50)
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=new_embedding), \
             patch.object(app_module, 'assess_image_quality', return_value=['blurry']):
            resp = client.post('/student/register', json=_register_payload())
        assert resp.status_code == 400
        conn = sqlite3.connect(isolated_paths['database_path'])
        count = conn.execute("SELECT COUNT(*) FROM students WHERE roll_no='R1'").fetchone()[0]
        conn.close()
        assert count == 0  # rolled back, not left as a zero-embedding account

    def test_some_images_failing_quality_still_registers_with_warning(self, client, isolated_paths):
        new_embedding = _unit_vector(50)
        images = [_real_jpeg_data_url()] * 3
        call_count = {'n': 0}

        def quality_side_effect(crop):
            call_count['n'] += 1
            return ['blurry'] if call_count['n'] == 1 else []

        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=new_embedding), \
             patch.object(app_module, 'assess_image_quality', side_effect=quality_side_effect):
            resp = client.post('/student/register', json=_register_payload(images=images))
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'warning' in data
        student_id = data['student_id']
        assert len(app_module.get_student_embeddings(student_id)) == 2  # one of three skipped

    def test_multiple_faces_in_a_registration_photo_is_skipped(self, client, isolated_paths, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, 'REJECT_MULTIPLE_FACES', True)
        new_embedding = _unit_vector(50)
        two_faces = np.array([[10, 10, 50, 50], [200, 10, 50, 50]])
        # First image (for the "has_face" pre-check) still needs a single
        # detectable face; use side_effect to vary per call.
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=two_faces), \
             patch.object(app_module, 'compute_embedding', return_value=new_embedding):
            resp = client.post('/student/register', json=_register_payload())
        # Every image had 2 faces -> nothing usable -> registration rejected.
        assert resp.status_code == 400


class TestDuplicateFaceDetection:
    def test_matching_existing_face_is_rejected(self, client, isolated_paths):
        existing_embedding = _unit_vector(0)
        # Seed an existing student with this embedding directly.
        conn = sqlite3.connect(isolated_paths['database_path'])
        conn.execute(
            "INSERT INTO students(id, name, roll_no, branch, semester, password) VALUES (1,'Existing','R1','CSE','1','x')"
        )
        conn.commit()
        conn.close()
        app_module.store_embedding(1, existing_embedding)

        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=existing_embedding):
            resp = client.post('/student/register', json=_register_payload(roll_no='R99'))
        assert resp.status_code == 400
        assert 'already registered' in resp.get_json()['error'].lower()

    def test_registration_proceeds_when_gallery_is_empty(self, client, isolated_paths):
        """The very first registration must not be blocked just because
        there's nothing to compare against yet."""
        new_embedding = _unit_vector(0)
        with patch.object(cv2.CascadeClassifier, 'detectMultiScale', return_value=FAKE_FACE_BOX), \
             patch.object(app_module, 'compute_embedding', return_value=new_embedding):
            resp = client.post('/student/register', json=_register_payload())
        assert resp.status_code == 200
