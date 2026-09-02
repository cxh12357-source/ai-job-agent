import pytest

import access_control
from access_control import MIN_PASSWORD_LENGTH, password_matches


def test_password_matches_only_exact_nonempty_value():
    assert password_matches("correct horse battery", "correct horse battery") is True
    assert password_matches("correct horse battery", "correct horse") is False
    assert password_matches("", "") is False


def test_minimum_password_length_is_not_trivial():
    assert MIN_PASSWORD_LENGTH >= 12


def test_required_gate_fails_closed_without_password(monkeypatch):
    errors: list[str] = []

    monkeypatch.setattr(access_control.st, "error", errors.append)

    def stopped() -> None:
        raise RuntimeError("stopped")

    monkeypatch.setattr(access_control.st, "stop", stopped)

    with pytest.raises(RuntimeError, match="stopped"):
        access_control.require_access("", required=True)

    assert errors and "APP_ACCESS_PASSWORD" in errors[0]
