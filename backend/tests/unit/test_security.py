import pytest

from app.core.security import validate_password


def test_six_digit_password_is_allowed():
    validate_password("123456")


def test_password_shorter_than_six_characters_is_rejected():
    with pytest.raises(ValueError, match="至少 6 位"):
        validate_password("12345")
