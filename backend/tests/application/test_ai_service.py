"""AIService against deterministic fake adapters: provider selection,
credential resolution, timeout and error behaviour — no network."""

import asyncio
import logging
from dataclasses import dataclass, field

import pytest
from _pytest.logging import LogCaptureFixture

from akasha.application.ai import AIService, LLMProvider
from akasha.bootstrap import create_ai_service
from akasha.config import Settings
from akasha.domain.ai import (
    FinishReason,
    LLMClient,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    TokenUsage,
)
from akasha.domain.credentials import Secret
from akasha.domain.node import NodeError
from akasha.errors import ConfigurationError
from akasha.runtime.credentials import NodeCredentials

KEY = "sk-fake-provider-key-0123456789"
REF = "credential:llm"
PROMPT = "confidential quarterly numbers"


@dataclass
class FakeAdapter:
    """Records calls; answers, raises or hangs as told."""

    reply: str = "ok"
    error: ProviderError | None = None
    hang: bool = False
    calls: list[tuple[LLMRequest, str | None, float]] = field(default_factory=list)

    async def complete(
        self, request: LLMRequest, *, api_key: str | None, timeout_seconds: float
    ) -> LLMResponse:
        self.calls.append((request, api_key, timeout_seconds))
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return LLMResponse(
            provider=request.provider,
            model=request.model,
            content=self.reply,
            tool_calls=(),
            usage=TokenUsage(7, 2),
            finish_reason=FinishReason.STOP,
        )


class FakeSource:
    async def resolve(self, reference: str) -> Secret:
        return Secret({REF: KEY}[reference])


def request(provider: str = "hosted", **kwargs: object) -> LLMRequest:
    return LLMRequest(
        provider,
        "model-x",
        (Message(Role.USER, PROMPT),),
        **kwargs,  # type: ignore[arg-type]
    )


def resolver(config: dict[str, object] | None = None) -> NodeCredentials:
    return NodeCredentials(FakeSource(), config or {"credential": REF})


async def test_routes_to_the_named_provider_with_the_resolved_key() -> None:
    hosted, local = FakeAdapter("hosted"), FakeAdapter("local")
    service = AIService(
        [
            LLMProvider("hosted", hosted),
            LLMProvider("local", local, requires_credential=False),
        ]
    )

    response = await service.complete(request(credential=REF), credentials=resolver())
    assert response.content == "hosted"
    ((_, api_key, _),) = hosted.calls
    assert api_key == KEY

    response = await service.complete(request("local"))
    assert response.content == "local"
    ((_, api_key, _),) = local.calls
    assert api_key is None
    assert service.provider_names == ["hosted", "local"]


async def test_unknown_provider_is_a_configuration_error() -> None:
    service = AIService([LLMProvider("hosted", FakeAdapter())])
    with pytest.raises(ProviderError) as exc:
        await service.complete(request("nope"))
    assert exc.value.kind is ProviderErrorKind.CONFIGURATION
    assert exc.value.code == "PROVIDER_CONFIGURATION"


async def test_credential_is_required_and_needs_a_resolver() -> None:
    adapter = FakeAdapter()
    service = AIService([LLMProvider("hosted", adapter)])
    for call in (
        service.complete(request()),  # no reference
        service.complete(request(credential=REF)),  # no resolver
    ):
        with pytest.raises(ProviderError) as exc:
            await call
        assert exc.value.kind is ProviderErrorKind.CONFIGURATION
    assert adapter.calls == []


async def test_the_node_credential_boundary_applies() -> None:
    """A reference not in the node's own config is refused before any
    provider call: request data cannot choose which secret is unlocked."""
    adapter = FakeAdapter()
    service = AIService([LLMProvider("hosted", adapter)])
    with pytest.raises(NodeError) as exc:
        await service.complete(
            request(credential=REF), credentials=resolver({"other": "x"})
        )
    assert exc.value.code == "CREDENTIAL_NOT_REFERENCED"
    assert adapter.calls == []


async def test_timeout_is_bounded_by_the_provider_ceiling() -> None:
    adapter = FakeAdapter(hang=True)
    service = AIService(
        [
            LLMProvider(
                "hosted", adapter, requires_credential=False, timeout_seconds=0.05
            )
        ]
    )
    # A request may ask for longer; the provider's ceiling wins.
    with pytest.raises(ProviderError) as exc:
        await service.complete(request(timeout_seconds=30))
    assert exc.value.kind is ProviderErrorKind.TIMEOUT
    assert exc.value.retryable
    assert adapter.calls[0][2] == 0.05


async def test_request_may_shorten_the_timeout() -> None:
    adapter = FakeAdapter()
    service = AIService([LLMProvider("hosted", adapter, requires_credential=False)])
    await service.complete(request(timeout_seconds=5))
    assert adapter.calls[0][2] == 5


async def test_cancellation_is_not_turned_into_a_timeout() -> None:
    service = AIService(
        [LLMProvider("hosted", FakeAdapter(hang=True), requires_credential=False)]
    )
    task = asyncio.create_task(service.complete(request()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_provider_errors_pass_through_and_logs_carry_no_payload(
    caplog: LogCaptureFixture,
) -> None:
    error = ProviderError(
        "slow down", kind=ProviderErrorKind.RATE_LIMIT, provider="hosted"
    )
    service = AIService([LLMProvider("hosted", FakeAdapter(error=error))])
    with (
        caplog.at_level(logging.INFO, logger="akasha"),
        pytest.raises(ProviderError) as exc,
    ):
        await service.complete(request(credential=REF), credentials=resolver())
    assert exc.value is error
    (record,) = [r for r in caplog.records if r.getMessage() == "llm_request_failed"]
    assert record.__dict__["error_code"] == "PROVIDER_RATE_LIMIT"

    caplog.clear()
    service = AIService([LLMProvider("hosted", FakeAdapter())])
    with caplog.at_level(logging.INFO, logger="akasha"):
        await service.complete(request(credential=REF), credentials=resolver())
    (record,) = [r for r in caplog.records if r.getMessage() == "llm_request_completed"]
    assert (record.__dict__["input_tokens"], record.__dict__["output_tokens"]) == (7, 2)
    logged = " ".join(str(r.__dict__) for r in caplog.records)
    assert PROMPT not in logged and KEY not in logged


def test_invalid_provider_configuration_fails_fast() -> None:
    with pytest.raises(ConfigurationError):
        AIService([LLMProvider("a", FakeAdapter()), LLMProvider("a", FakeAdapter())])
    with pytest.raises(ConfigurationError):
        AIService([LLMProvider("a", FakeAdapter(), timeout_seconds=0)])


def test_provider_configuration_from_settings() -> None:
    service = create_ai_service(Settings())
    assert service.provider_names == ["anthropic", "openai"]

    service = create_ai_service(
        Settings.model_validate(
            {"ollama_base_url": "http://ollama:11434", "llm_timeout_seconds": 12}
        )
    )
    assert service.provider_names == ["anthropic", "ollama", "openai"]
    ollama = service._providers["ollama"]
    assert not ollama.requires_credential
    assert ollama.timeout_seconds == 12


def test_openai_compatible_provider_from_settings() -> None:
    service = create_ai_service(
        Settings.model_validate(
            {
                "openai_compatible_base_url": "http://vllm:8000/v1",
                "openai_compatible_name": "vllm",
                "openai_compatible_requires_credential": False,
            }
        )
    )
    assert service.provider_names == ["anthropic", "openai", "vllm"]
    assert not service._providers["vllm"].requires_credential
    # Credential required unless the operator says otherwise.
    service = create_ai_service(
        Settings.model_validate({"openai_compatible_base_url": "http://x/v1"})
    )
    assert service._providers["openai_compatible"].requires_credential


def test_openai_compatible_name_must_not_shadow_a_builtin() -> None:
    with pytest.raises(ConfigurationError):
        create_ai_service(
            Settings.model_validate(
                {
                    "openai_compatible_base_url": "http://x/v1",
                    "openai_compatible_name": "openai",
                }
            )
        )


async def test_ai_service_is_the_nodes_llm_port() -> None:
    """Nodes see only ``LLMClient``; the assignment is checked by mypy."""
    client: LLMClient = AIService(
        [LLMProvider("local", FakeAdapter("port"), requires_credential=False)]
    )
    response = await client.complete(request("local"))
    assert response.content == "port"


# -- output-token limit (Phase 24) ------------------------------------------------


async def test_missing_max_tokens_gets_the_engine_limit() -> None:
    adapter = FakeAdapter()
    service = AIService(
        [LLMProvider("local", adapter, requires_credential=False)],
        max_output_tokens=100,
    )
    await service.complete(request("local"))
    ((sent, _, _),) = adapter.calls
    assert sent.max_tokens == 100


async def test_max_tokens_at_and_beyond_the_limit() -> None:
    adapter = FakeAdapter()
    service = AIService(
        [LLMProvider("local", adapter, requires_credential=False)],
        max_output_tokens=100,
    )
    await service.complete(request("local", max_tokens=100))
    assert adapter.calls[0][0].max_tokens == 100

    with pytest.raises(ProviderError) as caught:
        await service.complete(request("local", max_tokens=101))
    assert caught.value.kind is ProviderErrorKind.CONFIGURATION
    assert len(adapter.calls) == 1  # rejected before any provider call


async def test_output_token_limit_from_settings_and_validated() -> None:
    service = create_ai_service(Settings(limit_llm_output_tokens=123))
    # Rejected before credentials or the network are touched.
    with pytest.raises(ProviderError, match="engine limit of 123"):
        await service.complete(request("openai", max_tokens=124))
    adapter = FakeAdapter()
    with pytest.raises(ConfigurationError):
        AIService([LLMProvider("local", adapter)], max_output_tokens=0)
