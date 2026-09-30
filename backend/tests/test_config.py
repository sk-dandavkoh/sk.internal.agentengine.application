"""Configuration loading: defaults, env overrides, validation at startup."""

import pytest
from pydantic import ValidationError

from akasha.config import load_settings


def test_defaults() -> None:
    settings = load_settings()
    assert settings.environment == "development"
    assert settings.port == 8000
    assert settings.log_level == "INFO"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AKASHA_PORT", "9001")
    monkeypatch.setenv("AKASHA_ENVIRONMENT", "test")
    settings = load_settings()
    assert settings.port == 9001
    assert settings.environment == "test"


def test_invalid_config_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AKASHA_PORT", "not-a-number")
    with pytest.raises(ValidationError):
        load_settings()


def test_stale_threshold_must_span_several_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A threshold below a few heartbeats would recover live runs.
    monkeypatch.setenv("AKASHA_WORKER_HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("AKASHA_EXECUTION_STALE_SECONDS", "29")
    with pytest.raises(ValidationError):
        load_settings()
    monkeypatch.setenv("AKASHA_EXECUTION_STALE_SECONDS", "30")
    assert load_settings().execution_stale_seconds == 30
