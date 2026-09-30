"""Opt-in smoke test against a real provider (OpenRouter), AGENTS §26/§27.

Skipped unless ``AKASHA_SMOKE_OPENROUTER_API_KEY`` is set, so normal CI never
leaves the machine. Assertions are shape-only: LLM output is nondeterministic.

    AKASHA_SMOKE_OPENROUTER_API_KEY=... uv run pytest tests/external
"""

import os

import pytest

from akasha.application.ai import AIService, LLMProvider
from akasha.domain.ai import (
    FinishReason,
    LLMRequest,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
)
from akasha.domain.credentials import Secret
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.credentials import NodeCredentials

API_KEY = os.environ.get("AKASHA_SMOKE_OPENROUTER_API_KEY", "")
MODEL = os.environ.get("AKASHA_SMOKE_OPENROUTER_MODEL", "qwen/qwen3.8-27b:free")
BASE_URL = os.environ.get(
    "AKASHA_SMOKE_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
REF = "credential:openrouter"

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="AKASHA_SMOKE_OPENROUTER_API_KEY not set (external smoke)"
)


class EnvSource:
    """Stands in for the Credential Store; the key never touches a file."""

    def __init__(self, key: str) -> None:
        self._key = key

    async def resolve(self, reference: str) -> Secret:
        return Secret(self._key)


def service(key: str) -> tuple[AIService, NodeCredentials]:
    adapter = LiteLLMAdapter("openrouter", api_base=BASE_URL)
    provider = LLMProvider("openrouter", adapter, timeout_seconds=120)
    return AIService([provider]), NodeCredentials(EnvSource(key), {"c": REF})


def request(model: str = MODEL) -> LLMRequest:
    return LLMRequest(
        "openrouter",
        model,
        (
            Message(Role.SYSTEM, "Answer with a single word."),
            Message(Role.USER, "What is the capital of France?"),
        ),
        max_tokens=512,  # reasoning models spend tokens before answering
        credential=REF,
    )


async def test_completion_is_normalized() -> None:
    ai, creds = service(API_KEY)
    response = await ai.complete(request(), credentials=creds)
    assert response.provider == "openrouter"
    assert response.content
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0
    assert response.finish_reason in {FinishReason.STOP, FinishReason.LENGTH}


async def test_invalid_key_is_an_authentication_error() -> None:
    ai, creds = service("sk-or-v1-" + "0" * 64)
    with pytest.raises(ProviderError) as exc:
        await ai.complete(request(), credentials=creds)
    assert exc.value.kind is ProviderErrorKind.AUTHENTICATION
    assert API_KEY not in exc.value.message


async def test_unknown_model_is_an_invalid_request() -> None:
    ai, creds = service(API_KEY)
    with pytest.raises(ProviderError) as exc:
        await ai.complete(request("akasha/no-such-model"), credentials=creds)
    assert exc.value.kind is ProviderErrorKind.INVALID_REQUEST
    assert API_KEY not in exc.value.message
