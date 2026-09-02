"""Tests for the newer face-recognition helpers: multi-face detection,
image quality assessment, face alignment, and blink-transition logic —
each in isolation from the full attendance-marking flow."""
import numpy as np

import app as app_module
import config as cfg


class _FixedBoxesCascade:
    """Stand-in cascade returning a fixed list of boxes regardless of
    the actual image content."""
    def __init__(self, boxes):
        self.boxes = boxes

    def detectMultiScale(self, img, **kwargs):
        return list(self.boxes)


class TestMultiFaceDetection:
    def test_single_face_returns_box_no_error(self):
        cascade = _FixedBoxesCascade([(10, 10, 50, 50)])
        gray = np.zeros((100, 100), dtype=np.uint8)
        box, error = app_module.detect_single_face(cascade, gray, {})
        assert box == (10, 10, 50, 50)
        assert error is None

    def test_no_face_returns_error(self):
        cascade = _FixedBoxesCascade([])
        gray = np.zeros((100, 100), dtype=np.uint8)
        box, error = app_module.detect_single_face(cascade, gray, {})
        assert box is None
        assert 'no clear face' in error.lower()

    def test_multiple_faces_rejected_by_default(self, monkeypatch):
        monkeypatch.setattr(cfg, 'REJECT_MULTIPLE_FACES', True)
        cascade = _FixedBoxesCascade([(10, 10, 50, 50), (200, 10, 50, 50)])
        gray = np.zeros((300, 300), dtype=np.uint8)
        box, error = app_module.detect_single_face(cascade, gray, {})
        assert box is None
        assert 'multiple faces' in error.lower()

    def test_multiple_faces_allowed_when_configured_off(self, monkeypatch):
        monkeypatch.setattr(cfg, 'REJECT_MULTIPLE_FACES', False)
        cascade = _FixedBoxesCascade([(10, 10, 50, 50), (200, 10, 60, 60)])
        gray = np.zeros((300, 300), dtype=np.uint8)
        box, error = app_module.detect_single_face(cascade, gray, {})
        # Falls back to the largest face when multi-face rejection is off.
        assert box == (200, 10, 60, 60)
        assert error is None

    def test_detect_faces_multiscale_sorts_largest_first(self):
        cascade = _FixedBoxesCascade([(0, 0, 20, 20), (0, 0, 80, 80), (0, 0, 40, 40)])
        gray = np.zeros((100, 100), dtype=np.uint8)
        faces = app_module.detect_faces_multiscale(cascade, gray, {})
        areas = [f[2] * f[3] for f in faces]
        assert areas == sorted(areas, reverse=True)


class TestImageQualityAssessment:
    def test_sharp_well_lit_image_has_no_issues(self, monkeypatch):
        monkeypatch.setattr(cfg, 'IMAGE_QUALITY_CHECK_ENABLED', True)
        # High-frequency checkerboard pattern -> high Laplacian variance (sharp).
        img = np.zeros((100, 100), dtype=np.uint8)
        img[::2, ::2] = 200
        img[1::2, 1::2] = 200
        img[:] = np.clip(img.astype(int) + 60, 0, 255).astype(np.uint8)  # mid brightness
        issues = app_module.assess_image_quality(img)
        assert 'blurry' not in issues

    def test_flat_uniform_image_is_flagged_blurry(self, monkeypatch):
        monkeypatch.setattr(cfg, 'IMAGE_QUALITY_CHECK_ENABLED', True)
        # Perfectly uniform image -> zero Laplacian variance -> blurry.
        img = np.full((100, 100), 128, dtype=np.uint8)
        issues = app_module.assess_image_quality(img)
        assert 'blurry' in issues

    def test_dark_image_is_flagged(self, monkeypatch):
        monkeypatch.setattr(cfg, 'IMAGE_QUALITY_CHECK_ENABLED', True)
        img = np.random.randint(0, 10, (100, 100), dtype=np.uint8)
        issues = app_module.assess_image_quality(img)
        assert 'too dark' in issues

    def test_overexposed_image_is_flagged(self, monkeypatch):
        monkeypatch.setattr(cfg, 'IMAGE_QUALITY_CHECK_ENABLED', True)
        img = np.random.randint(245, 256, (100, 100), dtype=np.uint8)
        issues = app_module.assess_image_quality(img)
        assert 'too bright/overexposed' in issues

    def test_disabled_check_always_returns_no_issues(self, monkeypatch):
        monkeypatch.setattr(cfg, 'IMAGE_QUALITY_CHECK_ENABLED', False)
        img = np.full((100, 100), 128, dtype=np.uint8)  # would normally fail blur check
        assert app_module.assess_image_quality(img) == []


class TestFaceAlignment:
    def test_falls_back_to_plain_crop_when_eyes_not_found(self):
        cascade = _FixedBoxesCascade([])  # no eyes detected
        gray = np.zeros((200, 200), dtype=np.uint8)
        rgb = np.zeros((200, 200, 3), dtype=np.uint8)
        box = (50, 50, 80, 80)
        result = app_module.align_face_crop(gray, rgb, box, cascade)
        assert result.shape == (80, 80, 3)

    def test_alignment_rotates_when_eyes_found_at_angle(self):
        # Two "eyes" with a clear vertical offset -> nonzero rotation angle.
        cascade = _FixedBoxesCascade([(10, 20, 15, 15), (50, 10, 15, 15)])
        gray = np.zeros((200, 200), dtype=np.uint8)
        rgb = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
        box = (50, 50, 80, 80)
        result = app_module.align_face_crop(gray, rgb, box, cascade)
        # Should still produce a same-size crop, just rotated.
        assert result.shape == (80, 80, 3)

    def test_extreme_angle_falls_back_to_plain_crop(self):
        # Eyes positioned to imply a huge rotation angle (spurious detection).
        cascade = _FixedBoxesCascade([(0, 0, 10, 10), (10, 70, 10, 10)])
        gray = np.zeros((200, 200), dtype=np.uint8)
        rgb = np.zeros((200, 200, 3), dtype=np.uint8)
        rgb[:] = 100
        box = (50, 50, 80, 80)
        result = app_module.align_face_crop(gray, rgb, box, cascade)
        expected_plain = rgb[50:130, 50:130]
        assert np.array_equal(result, expected_plain)


class TestBlinkTransition:
    def test_transition_detected_when_eyes_appear_and_disappear(self):
        # 3 frames: eyes visible, eyes hidden, eyes visible again.
        class SequenceCascade:
            def __init__(self):
                self.calls = 0
                self.sequence = [
                    [(0, 0, 10, 10), (20, 0, 10, 10)],  # 2 eyes
                    [],                                    # 0 eyes (blink)
                    [(0, 0, 10, 10), (20, 0, 10, 10)],  # 2 eyes
                ]

            def detectMultiScale(self, img, **kwargs):
                result = self.sequence[self.calls % len(self.sequence)]
                self.calls += 1
                return result

        frames = [np.zeros((100, 100), dtype=np.uint8) for _ in range(3)]
        boxes = [(10, 10, 50, 50)] * 3
        assert app_module.check_blink_transition(frames, boxes, SequenceCascade()) is True

    def test_no_transition_when_eyes_always_visible(self):
        cascade = _FixedBoxesCascade([(0, 0, 10, 10), (20, 0, 10, 10)])
        frames = [np.zeros((100, 100), dtype=np.uint8) for _ in range(3)]
        boxes = [(10, 10, 50, 50)] * 3
        assert app_module.check_blink_transition(frames, boxes, cascade) is False

    def test_no_transition_when_eyes_never_visible(self):
        cascade = _FixedBoxesCascade([])
        frames = [np.zeros((100, 100), dtype=np.uint8) for _ in range(3)]
        boxes = [(10, 10, 50, 50)] * 3
        assert app_module.check_blink_transition(frames, boxes, cascade) is False


class TestIpAllowlist:
    def test_no_restriction_configured_allows_everything(self, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', [])
        assert app_module.is_ip_allowed_for_attendance('8.8.8.8') is True

    def test_ip_within_configured_cidr_is_allowed(self, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['10.0.0.0/24'])
        assert app_module.is_ip_allowed_for_attendance('10.0.0.42') is True

    def test_ip_outside_configured_cidr_is_blocked(self, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['10.0.0.0/24'])
        assert app_module.is_ip_allowed_for_attendance('192.168.1.5') is False

    def test_exact_ip_match_without_cidr(self, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['192.168.1.50'])
        assert app_module.is_ip_allowed_for_attendance('192.168.1.50') is True
        assert app_module.is_ip_allowed_for_attendance('192.168.1.51') is False

    def test_malformed_entry_is_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['not-an-ip', '10.0.0.0/24'])
        assert app_module.is_ip_allowed_for_attendance('10.0.0.5') is True

    def test_missing_ip_is_blocked_when_restriction_configured(self, monkeypatch):
        monkeypatch.setattr(cfg, 'ATTENDANCE_ALLOWED_NETWORKS', ['10.0.0.0/24'])
        assert app_module.is_ip_allowed_for_attendance(None) is False
