"""Tests for face_security.py — active liveness challenges (blink /
head_turn / head_nod) and the screen-replay ("spoof") heuristic. See
README "Anti-Proxy / Face Recognition Security" for the full picture."""
import numpy as np

import face_security as fs


class TestGenerateLivenessChallenge:
    def test_picks_one_of_the_enabled_types(self):
        for _ in range(20):
            challenge = fs.generate_liveness_challenge(['blink', 'head_turn'])
            assert challenge['type'] in ('blink', 'head_turn')

    def test_returns_a_token_and_a_prompt(self):
        challenge = fs.generate_liveness_challenge(['blink'])
        assert isinstance(challenge['token'], str) and len(challenge['token']) > 10
        assert isinstance(challenge['prompt'], str) and len(challenge['prompt']) > 0

    def test_tokens_are_not_repeated(self):
        tokens = {fs.generate_liveness_challenge(['blink'])['token'] for _ in range(50)}
        assert len(tokens) == 50

    def test_raises_on_empty_type_list(self):
        import pytest
        with pytest.raises(ValueError):
            fs.generate_liveness_challenge([])

    def test_unknown_type_gets_a_generic_prompt(self):
        challenge = fs.generate_liveness_challenge(['some_future_challenge_type'])
        assert challenge['prompt'] == 'Please follow the on-screen instruction.'


class TestVerifyHeadTurn:
    def test_no_shift_fails(self):
        boxes = [(50, 50, 80, 80)] * 5
        assert fs.verify_head_turn(boxes, min_shift_ratio=0.12) is False

    def test_shift_right_passes(self):
        # Face box center moves right by 20px against an 80px-wide face
        # (0.25 ratio) across the burst — comfortably over a 0.12 threshold.
        boxes = [(50, 50, 80, 80), (55, 50, 80, 80), (60, 50, 80, 80), (70, 50, 80, 80)]
        assert fs.verify_head_turn(boxes, min_shift_ratio=0.12) is True

    def test_shift_left_also_passes(self):
        """Direction-agnostic by design — see config.ACTIVE_LIVENESS_HEAD_TURN_MIN_SHIFT_RATIO."""
        boxes = [(70, 50, 80, 80), (60, 50, 80, 80), (50, 50, 80, 80)]
        assert fs.verify_head_turn(boxes, min_shift_ratio=0.12) is True

    def test_small_shift_below_threshold_fails(self):
        boxes = [(50, 50, 80, 80), (52, 50, 80, 80)]  # 2px / 80px = 0.025
        assert fs.verify_head_turn(boxes, min_shift_ratio=0.12) is False

    def test_fewer_than_two_boxes_fails(self):
        assert fs.verify_head_turn([(50, 50, 80, 80)], min_shift_ratio=0.12) is False
        assert fs.verify_head_turn([], min_shift_ratio=0.12) is False

    def test_none_boxes_are_ignored_not_fatal(self):
        boxes = [None, (50, 50, 80, 80), None, (70, 50, 80, 80)]
        assert fs.verify_head_turn(boxes, min_shift_ratio=0.12) is True

    def test_vertical_only_shift_does_not_count(self):
        """head_turn is specifically about horizontal motion — a pure
        vertical shift (which head_nod checks separately) must not pass it."""
        boxes = [(50, 50, 80, 80), (50, 80, 80, 80)]
        assert fs.verify_head_turn(boxes, min_shift_ratio=0.12) is False


class TestVerifyHeadNod:
    def test_no_shift_fails(self):
        boxes = [(50, 50, 80, 80)] * 4
        assert fs.verify_head_nod(boxes, min_shift_ratio=0.10) is False

    def test_vertical_shift_passes(self):
        boxes = [(50, 50, 80, 80), (50, 65, 80, 80), (50, 50, 80, 80)]
        assert fs.verify_head_nod(boxes, min_shift_ratio=0.10) is True

    def test_horizontal_only_shift_does_not_count(self):
        boxes = [(50, 50, 80, 80), (80, 50, 80, 80)]
        assert fs.verify_head_nod(boxes, min_shift_ratio=0.10) is False


class TestVerifyChallengeResponse:
    def test_blink_uses_the_passed_in_blink_flag(self):
        boxes = [(50, 50, 80, 80)] * 3
        assert fs.verify_challenge_response('blink', boxes, True, 0.12, 0.10) is True
        assert fs.verify_challenge_response('blink', boxes, False, 0.12, 0.10) is False

    def test_head_turn_dispatches_correctly(self):
        boxes = [(50, 50, 80, 80), (80, 50, 80, 80)]
        assert fs.verify_challenge_response('head_turn', boxes, False, 0.12, 0.10) is True

    def test_head_nod_dispatches_correctly(self):
        boxes = [(50, 50, 80, 80), (50, 80, 80, 80)]
        assert fs.verify_challenge_response('head_nod', boxes, False, 0.10, 0.10) is True

    def test_unknown_challenge_type_fails_closed(self):
        boxes = [(50, 50, 80, 80), (80, 90, 80, 80)]  # would pass either real check
        assert fs.verify_challenge_response('not_a_real_challenge', boxes, True, 0.01, 0.01) is False


class TestScreenReplayScore:
    def test_too_small_crop_returns_zero(self):
        tiny = np.zeros((10, 10), dtype=np.uint8)
        assert fs.compute_screen_replay_score(tiny) == 0.0

    def test_none_crop_returns_zero(self):
        assert fs.compute_screen_replay_score(None) == 0.0

    def test_smooth_face_like_image_scores_low(self):
        rng = np.random.default_rng(42)
        size = 150
        yy, xx = np.mgrid[0:size, 0:size]
        smooth = 128 + 20 * np.sin(yy / 40) + 15 * np.cos(xx / 35)
        face_like = np.clip(smooth + rng.normal(0, 8, (size, size)), 0, 255).astype(np.uint8)
        assert fs.compute_screen_replay_score(face_like) < 10

    def test_diagonal_periodic_grid_scores_very_high(self):
        """An angled screen/monitor pixel grid photographed by another
        camera typically produces real off-axis periodic energy (unlike
        JPEG block artifacts, which concentrate on-axis — see
        compute_screen_replay_score's docstring) — this should score
        dramatically higher than any real face content."""
        rng = np.random.default_rng(7)
        size = 150
        yy, xx = np.mgrid[0:size, 0:size]
        diagonal_grid = 128 + 35 * np.sin((xx + yy) * 0.7)
        screen_like = np.clip(diagonal_grid + rng.normal(0, 5, (size, size)), 0, 255).astype(np.uint8)
        assert fs.compute_screen_replay_score(screen_like) > 100

    def test_pure_on_axis_pattern_is_not_flagged(self):
        """Purely axis-aligned periodicity (the JPEG-blocking confound
        this function is specifically designed to ignore) must NOT
        dominate the score — see the on_axis_mask in the implementation."""
        rng = np.random.default_rng(3)
        size = 150
        yy, xx = np.mgrid[0:size, 0:size]
        axis_grid = 128 + 40 * np.sin(xx * 0.9) + 40 * np.sin(yy * 0.9)
        axis_like = np.clip(axis_grid + rng.normal(0, 5, (size, size)), 0, 255).astype(np.uint8)
        assert fs.compute_screen_replay_score(axis_like) < 10


class TestIsLikelyScreenReplay:
    def test_below_threshold_is_not_flagged(self):
        rng = np.random.default_rng(1)
        smooth = np.full((100, 100), 128, dtype=np.uint8)
        img = np.clip(smooth.astype(int) + rng.integers(-5, 5, (100, 100)), 0, 255).astype(np.uint8)
        assert fs.is_likely_screen_replay(img, max_ratio=1000) is False

    def test_above_threshold_is_flagged(self):
        rng = np.random.default_rng(9)
        size = 120
        yy, xx = np.mgrid[0:size, 0:size]
        diagonal_grid = 128 + 35 * np.sin((xx + yy) * 1.0)
        screen_like = np.clip(diagonal_grid + rng.normal(0, 5, (size, size)), 0, 255).astype(np.uint8)
        assert fs.is_likely_screen_replay(screen_like, max_ratio=30.0) is True
