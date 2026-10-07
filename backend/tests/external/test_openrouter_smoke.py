"""Opt-in smoke test against a real provider (OpenRouter), AGENTS §26/§27.

Skipped unless ``AKASHA_SMOKE_OPENROUTER_API_KEY`` is set, so normal CI never
leaves the machine. Assertions are shape-only: LLM output is nondeterministic.

    AKASHA_SMOKE_OPENROUTER_API_KEY=... uv run pytest tests/external
"""

import os
from dataclasses import replace

import pytest

from akasha.application.ai import AIService, LLMProvider
from akasha.domain.ai import (
    FinishReason,
    LLMRequest,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    ToolDefinition,
)
from akasha.domain.credentials import Secret
from akasha.domain.node import NodeResult, NodeServices
from akasha.nodes.llm import LlmNode
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.credentials import NodeCredentials
from tests.support import make_context

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


# -- core.llm end to end (Phase 15) ------------------------------------------------


def node_config(**overrides: object) -> dict[str, object]:
    return {
        "provider": "openrouter",
        "model": MODEL,
        "credential": REF,
        "user_prompt": "{{ question }}",
        "max_tokens": 1024,
        "temperature": 0,
        **overrides,
    }


async def run_llm_node(config: dict[str, object], question: str) -> NodeResult:
    # No api_base: litellm's own OpenRouter endpoint, so the bundled price
    # list applies and a cost estimate is produced.
    provider = LLMProvider(
        "openrouter", LiteLLMAdapter("openrouter"), timeout_seconds=120
    )
    node = LlmNode()
    node.definition.validate_config(config)
    context = make_context(
        node,
        config=config,
        inputs={"variables": {"question": question}},
        credentials=NodeCredentials(EnvSource(API_KEY), config),
        services=NodeServices(llm=AIService([provider])),
    )
    result = await node.execute(context)
    node.definition.validate_outputs(result.outputs)
    return result


async def test_node_structured_output() -> None:
    config = node_config(
        system_prompt="Reply with JSON only.",
        output_schema={
            "type": "object",
            "properties": {"capital": {"type": "string"}},
            "required": ["capital"],
        },
    )
    result = await run_llm_node(config, "What is the capital of France?")
    assert isinstance(result.outputs["structured"]["capital"], str)
    metadata = result.outputs["metadata"]
    assert metadata["usage"]["input_tokens"] > 0
    assert metadata["cost"] is None or metadata["cost"]["estimated"] is True


async def test_tool_call_is_normalized() -> None:
    """What core.agent relies on: the provider's tool call, normalized."""
    ai, creds = service(API_KEY)
    weather = ToolDefinition(
        "get_weather",
        "Current weather for a city.",
        {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    response = await ai.complete(
        replace(
            request(),
            messages=(Message(Role.USER, "Use the tool: weather in Paris?"),),
            tools=(weather,),
            max_tokens=1024,
        ),
        credentials=creds,
    )
    (call,) = response.tool_calls
    assert call.name == "get_weather"
    assert "city" in call.arguments
