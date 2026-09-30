"""LiteLLM-backed ``ProviderAdapter`` (architecture §27: LiteLLM != domain).

One adapter instance serves one provider route (``openai``, ``anthropic``,
``ollama_chat``, ...). The route and ``api_base`` are operator configuration;
the request's ``model`` is appended to the route, so user data can pick a
model but never a different provider or endpoint.

Keys come only from the Credential Store. Given no key, litellm would fall
back to ambient env vars (``OPENAI_API_KEY``, ...), so keyless providers
(Ollama, an unauthenticated vLLM) get the inert ``NO_API_KEY`` instead.

LiteLLM is imported on first use: the import costs seconds, and an engine
that never calls an LLM should not pay it at startup.
"""

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from functools import cache
from types import ModuleType
from typing import Any

from akasha.domain.ai import (
    CostEstimate,
    FinishReason,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    TokenUsage,
    ToolCall,
)
from akasha.security.redaction import redact_text

MAX_ERROR_MESSAGE = 500
# Sent by keyless providers; blocks litellm's env-var key fallback.
NO_API_KEY = "no-key"

_FINISH_REASONS = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "content_filter": FinishReason.CONTENT_FILTER,
}


@cache
def _litellm() -> ModuleType:
    # Without this, importing litellm downloads its model cost map from
    # GitHub — a hidden network call at startup (AGENTS §124). The bundled
    # map is used instead. setdefault: an operator may still opt in.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    litellm.suppress_debug_info = True  # it prints help banners to stdout
    return litellm


class LiteLLMAdapter:
    def __init__(self, route: str, *, api_base: str | None = None) -> None:
        self._route = route
        self._api_base = api_base

    async def complete(
        self, request: LLMRequest, *, api_key: str | None, timeout_seconds: float
    ) -> LLMResponse:
        litellm = _litellm()
        # Every litellm provider exception derives from openai's base.
        from openai import OpenAIError

        acompletion: Callable[..., Awaitable[Any]] = litellm.acompletion
        try:
            raw = await acompletion(
                **self.request_kwargs(request, api_key, timeout_seconds)
            )
        except OpenAIError as exc:
            raise _provider_error(litellm, exc, request.provider) from exc
        response = _normalize(raw, request.provider)
        return replace(response, cost=self._estimate_cost(litellm, request, response))

    def _estimate_cost(
        self, litellm: ModuleType, request: LLMRequest, response: LLMResponse
    ) -> CostEstimate | None:
        # litellm's price list describes the route's own hosted API. Behind a
        # custom api_base (Ollama, vLLM, a gateway) the same model name says
        # nothing about price, so no estimate beats a wrong one.
        if self._api_base is not None:
            return None
        try:
            prompt, completion = litellm.cost_per_token(
                model=f"{self._route}/{request.model}",
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
            )
        # litellm signals an unpriced model with a bare Exception; an unknown
        # price is not a failed completion.
        except Exception:
            return None
        return CostEstimate(float(prompt) + float(completion))

    def request_kwargs(
        self, request: LLMRequest, api_key: str | None, timeout_seconds: float
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": f"{self._route}/{request.model}",
            "messages": [_message(m) for m in request.messages],
            "api_key": api_key or NO_API_KEY,
            "api_base": self._api_base,
            "timeout": timeout_seconds,
            # Retries belong to the runtime's retry policy, not hidden here:
            # a retried call is a second billed (and side-effecting) request.
            "num_retries": 0,
            "max_retries": 0,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": dict(request.response_schema),
                },
            }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": dict(t.parameters),
                    },
                }
                for t in request.tools
            ]
        return kwargs


def _message(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    if message.role is Role.TOOL:
        out["tool_call_id"] = message.tool_call_id
    return out


def _normalize(raw: Any, provider: str) -> LLMResponse:
    def invalid(reason: str) -> ProviderError:
        return ProviderError(
            f"malformed provider response: {reason}",
            kind=ProviderErrorKind.INVALID_RESPONSE,
            provider=provider,
        )

    if not raw.choices:
        raise invalid("no choices")
    choice = raw.choices[0]
    usage = getattr(raw, "usage", None)
    return LLMResponse(
        provider=provider,
        model=str(raw.model),
        content=choice.message.content,
        tool_calls=tuple(
            _tool_call(call, invalid) for call in choice.message.tool_calls or ()
        ),
        usage=TokenUsage(
            input_tokens=getattr(usage, "prompt_tokens", None) or 0,
            output_tokens=getattr(usage, "completion_tokens", None) or 0,
        ),
        finish_reason=_FINISH_REASONS.get(choice.finish_reason, FinishReason.OTHER),
        request_id=raw.id or None,
    )


def _tool_call(call: Any, invalid: Callable[[str], ProviderError]) -> ToolCall:
    try:
        arguments = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        raise invalid("tool call arguments are not JSON") from None
    if not isinstance(arguments, Mapping):
        raise invalid("tool call arguments are not a JSON object")
    return ToolCall(id=call.id, name=call.function.name, arguments=arguments)


def _provider_error(
    litellm: ModuleType, exc: Exception, provider: str
) -> ProviderError:
    kind = _classify(litellm.exceptions, exc)
    # Provider messages help diagnosis ("model not found") but can echo keys
    # or prompt fragments: redacted (known secrets + patterns) and truncated.
    message = redact_text(str(exc))[:MAX_ERROR_MESSAGE]
    return ProviderError(message, kind=kind, provider=provider)


def _classify(errors: ModuleType, exc: Exception) -> ProviderErrorKind:
    # Most specific first: ContextWindowExceeded/ContentPolicy subclass
    # BadRequestError; Timeout subclasses APIConnectionError.
    table: list[tuple[tuple[type[Exception], ...], ProviderErrorKind]] = [
        ((errors.ContextWindowExceededError,), ProviderErrorKind.CONTEXT_LENGTH),
        ((errors.ContentPolicyViolationError,), ProviderErrorKind.CONTENT_POLICY),
        (
            (errors.AuthenticationError, errors.PermissionDeniedError),
            ProviderErrorKind.AUTHENTICATION,
        ),
        ((errors.RateLimitError,), ProviderErrorKind.RATE_LIMIT),
        ((errors.Timeout,), ProviderErrorKind.TIMEOUT),
        (
            (
                errors.BadRequestError,
                errors.NotFoundError,
                errors.UnprocessableEntityError,
            ),
            ProviderErrorKind.INVALID_REQUEST,
        ),
        ((errors.APIResponseValidationError,), ProviderErrorKind.INVALID_RESPONSE),
        (
            (
                errors.APIConnectionError,
                errors.ServiceUnavailableError,
                errors.InternalServerError,
                errors.BadGatewayError,
            ),
            ProviderErrorKind.UNAVAILABLE,
        ),
    ]
    for types, kind in table:
        if isinstance(exc, types):
            return kind
    # litellm leaves some statuses as a bare APIError (e.g. 403).
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return ProviderErrorKind.AUTHENTICATION
    if isinstance(status, int) and 400 <= status < 500:
        return ProviderErrorKind.INVALID_REQUEST
    if isinstance(status, int) and status >= 500:
        return ProviderErrorKind.UNAVAILABLE
    return ProviderErrorKind.UNKNOWN
