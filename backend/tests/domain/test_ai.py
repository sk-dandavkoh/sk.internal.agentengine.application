"""Provider-independent LLM types: invariants and error categories."""

import pytest

from akasha.domain.ai import (
    LLMRequest,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    TokenUsage,
    ToolCall,
)
from akasha.errors import DomainValidationError

USER = (Message(Role.USER, "hi"),)


def test_message_invariants() -> None:
    call = ToolCall("c1", "lookup", {"q": "x"})
    Message(Role.ASSISTANT, tool_calls=(call,))  # content optional with calls
    Message(Role.TOOL, "result", tool_call_id="c1")
    with pytest.raises(DomainValidationError):
        Message(Role.USER, "hi", tool_calls=(call,))
    with pytest.raises(DomainValidationError):
        Message(Role.TOOL, "result")  # which call does it answer?
    with pytest.raises(DomainValidationError):
        Message(Role.USER, "hi", tool_call_id="c1")
    with pytest.raises(DomainValidationError):
        Message(Role.ASSISTANT)
    with pytest.raises(ValueError):
        Message("robot", "hi")  # type: ignore[arg-type]  # the point of the test


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": ""},
        {"model": ""},
        {"messages": ()},
        {"temperature": 2.5},
        {"max_tokens": 0},
        {"timeout_seconds": 0},
        # A raw key where a reference belongs is rejected, not forwarded.
        {"credential": "sk-live-abcdefghijklmnop"},
    ],
)
def test_request_rejects_invalid_values(overrides: dict[str, object]) -> None:
    args: dict[str, object] = {"provider": "openai", "model": "m", "messages": USER}
    with pytest.raises(DomainValidationError):
        LLMRequest(**(args | overrides))  # type: ignore[arg-type]


def test_request_accepts_credential_reference() -> None:
    request = LLMRequest("openai", "m", USER, credential="credential:openai")
    assert request.credential == "credential:openai"


def test_token_usage_total() -> None:
    assert TokenUsage(12, 3).total_tokens == 15
    assert TokenUsage().total_tokens == 0


@pytest.mark.parametrize(
    ("kind", "retryable"),
    [
        (ProviderErrorKind.RATE_LIMIT, True),
        (ProviderErrorKind.TIMEOUT, True),
        (ProviderErrorKind.UNAVAILABLE, True),
        (ProviderErrorKind.AUTHENTICATION, False),
        (ProviderErrorKind.CONTEXT_LENGTH, False),
        (ProviderErrorKind.INVALID_REQUEST, False),
        (ProviderErrorKind.CONFIGURATION, False),
    ],
)
def test_provider_error_code_and_retryability(
    kind: ProviderErrorKind, retryable: bool
) -> None:
    error = ProviderError("boom", kind=kind, provider="openai")
    assert error.code == f"PROVIDER_{kind.name}"
    assert error.retryable is retryable
    assert error.details == {"provider": "openai", "kind": kind.value}
