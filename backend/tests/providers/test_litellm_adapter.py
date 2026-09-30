"""The real LiteLLM adapter against a local OpenAI-compatible server
(AGENTS §27/§124): request mapping, normalization and error categories are
exercised through litellm itself, never a public provider."""

import json
from typing import Any

import pytest

from akasha.domain.ai import (
    FinishReason,
    LLMRequest,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from akasha.providers.litellm_adapter import NO_API_KEY, LiteLLMAdapter
from tests.support import CHAT_COMPLETION, LocalHttpServer

KEY = "sk-local-test-key-0123456789abcdef"
WEATHER = ToolDefinition(
    "weather",
    "Current weather for a city",
    {"type": "object", "properties": {"city": {"type": "string"}}},
)


def adapter(server: LocalHttpServer) -> LiteLLMAdapter:
    return LiteLLMAdapter("openai", api_base=f"{server.base_url}/v1")


def simple(**kwargs: Any) -> LLMRequest:
    return LLMRequest("openai", "gpt-test", (Message(Role.USER, "Hi"),), **kwargs)


def sent(server: LocalHttpServer) -> dict[str, Any]:
    (recorded,) = server.state.requests  # also proves: no hidden retries
    assert recorded.path == "/v1/chat/completions"
    body: dict[str, Any] = json.loads(recorded.body)
    return body


async def test_request_mapping(http_server: LocalHttpServer) -> None:
    call = ToolCall("call_1", "weather", {"city": "Berlin"})
    request = LLMRequest(
        "openai",
        "gpt-test",
        (
            Message(Role.SYSTEM, "Be brief."),
            Message(Role.USER, "Weather in Berlin?"),
            Message(Role.ASSISTANT, tool_calls=(call,)),
            Message(Role.TOOL, '{"temp": 21}', tool_call_id="call_1"),
        ),
        temperature=0.2,
        max_tokens=64,
        tools=(WEATHER,),
    )
    await adapter(http_server).complete(request, api_key=KEY, timeout_seconds=5)

    body = sent(http_server)
    assert body["model"] == "gpt-test"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert [m["role"] for m in body["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert body["messages"][2]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "weather", "arguments": '{"city": "Berlin"}'},
        }
    ]
    assert body["messages"][3]["tool_call_id"] == "call_1"
    assert body["tools"][0]["function"]["name"] == "weather"
    assert body["tools"][0]["function"]["parameters"] == dict(WEATHER.parameters)
    headers = http_server.state.requests[0].headers
    assert headers["authorization"] == f"Bearer {KEY}"


async def test_optional_parameters_are_omitted(http_server: LocalHttpServer) -> None:
    await adapter(http_server).complete(simple(), api_key=KEY, timeout_seconds=5)
    body = sent(http_server)
    assert (
        "temperature" not in body and "max_tokens" not in body and "tools" not in body
    )


async def test_response_normalization(http_server: LocalHttpServer) -> None:
    response = await adapter(http_server).complete(
        simple(), api_key=KEY, timeout_seconds=5
    )
    assert response.provider == "openai"
    assert response.model == "gpt-test-2026"
    assert response.content == "Hello!"
    assert response.tool_calls == ()
    assert response.usage == TokenUsage(input_tokens=12, output_tokens=3)
    assert response.finish_reason is FinishReason.STOP
    assert response.request_id == "chatcmpl-123"


def with_message(message: dict[str, Any], finish_reason: str) -> dict[str, Any]:
    return CHAT_COMPLETION | {
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]
    }


async def test_tool_calls_are_normalized(http_server: LocalHttpServer) -> None:
    http_server.state.llm_body = with_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city": "Oslo"}'},
                }
            ],
        },
        "tool_calls",
    )
    response = await adapter(http_server).complete(
        simple(tools=(WEATHER,)), api_key=KEY, timeout_seconds=5
    )
    assert response.content is None
    assert response.tool_calls == (ToolCall("call_9", "weather", {"city": "Oslo"}),)
    assert response.finish_reason is FinishReason.TOOL_CALLS


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
async def test_malformed_tool_arguments_are_invalid_responses(
    http_server: LocalHttpServer, arguments: str
) -> None:
    http_server.state.llm_body = with_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {"name": "weather", "arguments": arguments},
                }
            ],
        },
        "tool_calls",
    )
    with pytest.raises(ProviderError) as exc:
        await adapter(http_server).complete(simple(), api_key=KEY, timeout_seconds=5)
    assert exc.value.kind is ProviderErrorKind.INVALID_RESPONSE


async def test_length_finish_and_missing_usage(http_server: LocalHttpServer) -> None:
    body = with_message({"role": "assistant", "content": "trunc"}, "length")
    del body["usage"]
    http_server.state.llm_body = body
    response = await adapter(http_server).complete(
        simple(), api_key=KEY, timeout_seconds=5
    )
    assert response.finish_reason is FinishReason.LENGTH
    assert response.usage.total_tokens == 0


def openai_error(message: str, code: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": "error", "code": code}}


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (401, openai_error("Incorrect API key"), ProviderErrorKind.AUTHENTICATION),
        (403, openai_error("Forbidden"), ProviderErrorKind.AUTHENTICATION),
        (429, openai_error("Rate limit reached"), ProviderErrorKind.RATE_LIMIT),
        (
            400,
            openai_error(
                "This model's maximum context length is 8192 tokens.",
                "context_length_exceeded",
            ),
            ProviderErrorKind.CONTEXT_LENGTH,
        ),
        (400, openai_error("Invalid value for 'x'"), ProviderErrorKind.INVALID_REQUEST),
        (
            404,
            openai_error("The model does not exist"),
            ProviderErrorKind.INVALID_REQUEST,
        ),
        (500, openai_error("Internal error"), ProviderErrorKind.UNAVAILABLE),
        (503, openai_error("Overloaded"), ProviderErrorKind.UNAVAILABLE),
    ],
)
async def test_provider_errors_are_normalized(
    http_server: LocalHttpServer,
    status: int,
    body: dict[str, Any],
    kind: ProviderErrorKind,
) -> None:
    http_server.state.llm_status, http_server.state.llm_body = status, body
    with pytest.raises(ProviderError) as exc:
        await adapter(http_server).complete(simple(), api_key=KEY, timeout_seconds=5)
    assert exc.value.kind is kind
    assert exc.value.provider == "openai"
    assert len(http_server.state.requests) == 1  # litellm/openai retries are off


async def test_error_messages_do_not_echo_the_key(http_server: LocalHttpServer) -> None:
    http_server.state.llm_status = 401
    http_server.state.llm_body = openai_error(f"Incorrect API key provided: {KEY}")
    # Called directly, the key was never registered for known-value
    # redaction: the sk- pattern alone must catch it.
    with pytest.raises(ProviderError) as exc:
        await adapter(http_server).complete(simple(), api_key=KEY, timeout_seconds=5)
    assert KEY not in exc.value.message
    assert KEY not in str(exc.value.details)


async def test_timeout(http_server: LocalHttpServer) -> None:
    http_server.state.llm_hang = True
    with pytest.raises(ProviderError) as exc:
        await adapter(http_server).complete(simple(), api_key=KEY, timeout_seconds=0.3)
    assert exc.value.kind is ProviderErrorKind.TIMEOUT


async def test_unreachable_provider_is_unavailable() -> None:
    down = LiteLLMAdapter("openai", api_base="http://127.0.0.1:9/v1")
    with pytest.raises(ProviderError) as exc:
        await down.complete(simple(), api_key=KEY, timeout_seconds=5)
    assert exc.value.kind is ProviderErrorKind.UNAVAILABLE


async def test_malformed_response_is_classified(http_server: LocalHttpServer) -> None:
    http_server.state.llm_body = CHAT_COMPLETION | {"choices": []}
    with pytest.raises(ProviderError) as exc:
        await adapter(http_server).complete(simple(), api_key=KEY, timeout_seconds=5)
    assert exc.value.kind is ProviderErrorKind.INVALID_RESPONSE


def test_route_and_endpoint_are_operator_configuration() -> None:
    """The model is user data: it can name a model, never another provider
    or endpoint."""
    kwargs = LiteLLMAdapter("anthropic").request_kwargs(
        LLMRequest("anthropic", "ollama/evil", (Message(Role.USER, "Hi"),)),
        api_key=KEY,
        timeout_seconds=5,
    )
    assert kwargs["model"] == "anthropic/ollama/evil"
    assert kwargs["api_base"] is None
    assert kwargs["num_retries"] == 0 and kwargs["max_retries"] == 0


async def test_keyless_provider_never_uses_ambient_env_keys(
    http_server: LocalHttpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a store credential, litellm must not pick up OPENAI_API_KEY."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-env-key-0123456789")
    await adapter(http_server).complete(simple(), api_key=None, timeout_seconds=5)
    (recorded,) = http_server.state.requests
    assert recorded.headers["authorization"] == f"Bearer {NO_API_KEY}"
