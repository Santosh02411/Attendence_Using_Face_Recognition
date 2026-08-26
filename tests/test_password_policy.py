"""Tests for the password-strength policy."""
import config as cfg
from app import validate_password_strength


class TestPasswordStrengthValidation:
    def test_too_short_password_is_rejected(self):
        error = validate_password_strength('a1')
        assert error is not None
        assert str(cfg.PASSWORD_MIN_LENGTH) in error

    def test_empty_password_is_rejected(self):
        assert validate_password_strength('') is not None
        assert validate_password_strength(None) is not None

    def test_letters_only_password_is_rejected_when_digit_required(self, monkeypatch):
        monkeypatch.setattr(cfg, 'PASSWORD_REQUIRE_LETTER_AND_DIGIT', True)
        error = validate_password_strength('onlyletters')
        assert error is not None

    def test_digits_only_password_is_rejected_when_letter_required(self, monkeypatch):
        monkeypatch.setattr(cfg, 'PASSWORD_REQUIRE_LETTER_AND_DIGIT', True)
        error = validate_password_strength('12345678')
        assert error is not None

    def test_valid_password_is_accepted(self):
        assert validate_password_strength('goodpass123') is None

    def test_requirement_can_be_relaxed_via_config(self, monkeypatch):
        monkeypatch.setattr(cfg, 'PASSWORD_REQUIRE_LETTER_AND_DIGIT', False)
        # Long enough, but letters-only — fine once the letter+digit rule is off.
        assert validate_password_strength('onlylettersnodigits') is None


class TestPasswordStrengthAtRegistration:
    def test_weak_password_blocks_registration(self, client, isolated_paths):
        resp = client.post('/student/register', json={
            'name': 'Test', 'roll_no': 'R1', 'branch': 'CSE', 'semester': '1',
            'password': 'weak', 'gender': 'M', 'images': ['data:image/jpeg;base64,AAAA'],
        })
        assert resp.status_code == 400
        assert 'password' in resp.get_json()['error'].lower()
