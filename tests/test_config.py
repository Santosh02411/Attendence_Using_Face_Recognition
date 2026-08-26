"""Tests that config.py's environment-variable overrides actually take
effect, and that defaults match what's documented."""
import importlib

import config as cfg


def test_defaults_are_present_and_correctly_typed():
    # Values may come back as int or float depending on whether they were
    # cast from an env var or returned as a literal default — both are
    # fine anywhere they're used (comparisons, arithmetic), so check
    # "numeric", not a specific numeric type.
    assert isinstance(cfg.LOW_ATTENDANCE_THRESHOLD_PERCENT, (int, float))
    assert isinstance(cfg.RECOGNIZE_MATCH_THRESHOLD, (int, float))
    assert isinstance(cfg.LIVENESS_CHECK_ENABLED, bool)
    assert isinstance(cfg.REGISTRATION_DETECT_PARAMS, dict)
    assert 'scaleFactor' in cfg.REGISTRATION_DETECT_PARAMS


def test_env_int_helper_falls_back_on_invalid_value(monkeypatch):
    monkeypatch.setenv('SOME_TEST_INT', 'not-a-number')
    assert cfg._env_int('SOME_TEST_INT', 42) == 42


def test_env_int_helper_parses_valid_value(monkeypatch):
    monkeypatch.setenv('SOME_TEST_INT', '7')
    assert cfg._env_int('SOME_TEST_INT', 42) == 7


def test_env_float_helper_falls_back_on_invalid_value(monkeypatch):
    monkeypatch.setenv('SOME_TEST_FLOAT', 'nope')
    assert cfg._env_float('SOME_TEST_FLOAT', 3.5) == 3.5


def test_env_float_helper_parses_valid_value(monkeypatch):
    monkeypatch.setenv('SOME_TEST_FLOAT', '0.75')
    assert cfg._env_float('SOME_TEST_FLOAT', 3.5) == 0.75


def test_low_attendance_threshold_env_override(monkeypatch):
    monkeypatch.setenv('LOW_ATTENDANCE_THRESHOLD_PERCENT', '60')
    reloaded = importlib.reload(cfg)
    try:
        assert reloaded.LOW_ATTENDANCE_THRESHOLD_PERCENT == 60.0
    finally:
        monkeypatch.delenv('LOW_ATTENDANCE_THRESHOLD_PERCENT', raising=False)
        importlib.reload(cfg)  # restore defaults for subsequent tests


def test_match_threshold_env_override(monkeypatch):
    monkeypatch.setenv('MARK_ATTENDANCE_MATCH_THRESHOLD', '0.8')
    reloaded = importlib.reload(cfg)
    try:
        assert reloaded.MARK_ATTENDANCE_MATCH_THRESHOLD == 0.8
    finally:
        monkeypatch.delenv('MARK_ATTENDANCE_MATCH_THRESHOLD', raising=False)
        importlib.reload(cfg)
