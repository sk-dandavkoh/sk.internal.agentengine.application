"""Configuration loading: defaults, env overrides, validation at startup."""

from datetime import timedelta

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


def test_resource_limits_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AKASHA_LIMIT_EXECUTION_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("AKASHA_LIMIT_NODE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("AKASHA_LIMIT_NODE_ATTEMPTS", "2")
    monkeypatch.setenv("AKASHA_LIMIT_NODE_OUTPUT_BYTES", "10")
    limits = load_settings().resource_limits()
    assert limits.execution_timeout == timedelta(seconds=120)
    assert limits.node_timeout == timedelta(seconds=30)
    assert limits.max_node_attempts == 2
    assert limits.max_node_output_bytes == 10


@pytest.mark.parametrize(
    "env",
    [
        # Each limit is valid alone; together they contradict each other.
        {"AKASHA_LIMIT_NODE_TIMEOUT_SECONDS": "7200"},
        {"AKASHA_LIMIT_NODE_OUTPUT_BYTES": str(32 * 1024 * 1024)},
        # Out of range alone.
        {"AKASHA_LIMIT_NODE_ATTEMPTS": "0"},
        {"AKASHA_LIMIT_EXECUTION_CONCURRENCY": "0"},
        {"AKASHA_LIMIT_LLM_OUTPUT_TOKENS": "0"},
        {"AKASHA_LIMIT_EVENT_TRIGGER_DEPTH": "0"},
        {"AKASHA_LIMIT_EVENT_TRIGGER_DEPTH": "101"},
        {"AKASHA_LIMIT_EXECUTION_TIMEOUT_SECONDS": "0"},
    ],
)
def test_invalid_resource_limits_fail_fast(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        load_settings()
