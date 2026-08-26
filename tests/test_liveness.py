"""Tests for the basic two-frame liveness check."""
import numpy as np

import app as app_module
import config as cfg


class _FixedBoxCascade:
    """Stand-in for cv2.CascadeClassifier that always reports one fixed
    face box, so these tests can exercise check_liveness()'s pixel-diff
    logic without depending on Haar cascade actually detecting a face in
    synthetic test images."""

    def __init__(self, box=(10, 10, 80, 80)):
        self.box = box

    def detectMultiScale(self, img, **kwargs):
        return [self.box]


def test_identical_frames_are_rejected_as_not_live(monkeypatch):
    monkeypatch.setattr(cfg, 'LIVENESS_CHECK_ENABLED', True)
    frame = np.random.randint(100, 105, (100, 100), dtype=np.uint8)
    cascade = _FixedBoxCascade()
    assert app_module.check_liveness(cascade, frame, frame.copy(), {}) is False


def test_frames_with_real_change_pass_as_live(monkeypatch):
    monkeypatch.setattr(cfg, 'LIVENESS_CHECK_ENABLED', True)
    frame1 = np.random.randint(100, 105, (100, 100), dtype=np.uint8)
    frame2 = frame1.copy()
    frame2[10:90, 10:90] = frame2[10:90, 10:90] + 20
    cascade = _FixedBoxCascade()
    assert app_module.check_liveness(cascade, frame1, frame2, {}) is True


def test_disabled_check_always_passes(monkeypatch):
    monkeypatch.setattr(cfg, 'LIVENESS_CHECK_ENABLED', False)
    frame = np.zeros((100, 100), dtype=np.uint8)
    cascade = _FixedBoxCascade()
    # Even identical frames (which would normally fail) pass when disabled.
    assert app_module.check_liveness(cascade, frame, frame.copy(), {}) is True


def test_no_face_in_either_frame_fails():
    class _NoFaceCascade:
        def detectMultiScale(self, img, **kwargs):
            return []

    frame = np.zeros((100, 100), dtype=np.uint8)
    assert app_module.check_liveness(_NoFaceCascade(), frame, frame.copy(), {}) is False


def test_mean_diff_threshold_is_configurable(monkeypatch):
    """A frame pair with a small but nonzero difference should flip from
    rejected to accepted as the configured threshold is lowered."""
    monkeypatch.setattr(cfg, 'LIVENESS_CHECK_ENABLED', True)
    frame1 = np.full((100, 100), 100, dtype=np.uint8)
    frame2 = np.full((100, 100), 101, dtype=np.uint8)  # tiny uniform diff
    cascade = _FixedBoxCascade()

    monkeypatch.setattr(cfg, 'LIVENESS_MIN_MEAN_PIXEL_DIFF', 5.0)
    assert app_module.check_liveness(cascade, frame1, frame2, {}) is False

    monkeypatch.setattr(cfg, 'LIVENESS_MIN_MEAN_PIXEL_DIFF', 0.5)
    assert app_module.check_liveness(cascade, frame1, frame2, {}) is True
