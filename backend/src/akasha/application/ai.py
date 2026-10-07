"""AIService: the one entry point for LLM calls (architecture §27).

    LLM Node → AIService → LLMProvider → ProviderAdapter → provider

- ``ProviderAdapter`` is the provider abstraction: it speaks one provider
  library and returns domain types or raises ``ProviderError``.
- ``LLMProvider`` is a configured provider: a name requests address, the
  adapter behind it and its operating limits.
- The service picks the provider, resolves the credential reference through
  the caller's ``CredentialResolver`` (a node's resolver enforces that the
  reference is in its own config), and bounds the call with the timeout
  and the output-token limit (Phase 24): a request without ``max_tokens``
  gets the limit, and one above it is rejected before any provider is
  called.

Adding a provider means adding an ``LLMProvider``; nothing above changes.
Nodes never import this module: they depend on the domain port
``LLMClient``, which ``AIService`` implements.
"""

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from akasha.domain.ai import LLMRequest, LLMResponse, ProviderError, ProviderErrorKind
from akasha.domain.node import CredentialResolver
from akasha.errors import ConfigurationError
from akasha.security.redaction import register_secret

logger = logging.getLogger("akasha.application.ai")


class ProviderAdapter(Protocol):
    async def complete(
        self, request: LLMRequest, *, api_key: str | None, timeout_seconds: float
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class LLMProvider:
    name: str
    adapter: ProviderAdapter
    # Hosted APIs need a key; a local Ollama/vLLM usually does not.
    requires_credential: bool = True
    timeout_seconds: float = 60.0


class AIService:
    def __init__(
        self, providers: Iterable[LLMProvider], *, max_output_tokens: int = 16_384
    ) -> None:
        if max_output_tokens < 1:
            raise ConfigurationError("max_output_tokens must be >= 1")
        self._max_tokens = max_output_tokens
        self._providers: dict[str, LLMProvider] = {}
        for provider in providers:
            if provider.name in self._providers:
                raise ConfigurationError(f"duplicate LLM provider: {provider.name}")
            if provider.timeout_seconds <= 0:
                raise ConfigurationError(
                    f"LLM provider {provider.name}: timeout must be positive"
                )
            self._providers[provider.name] = provider

    @property
    def provider_names(self) -> list[str]:
        return sorted(self._providers)

    @property
    def providers(self) -> list[LLMProvider]:
        """The configured providers, by name."""
        return [self._providers[name] for name in self.provider_names]

    async def complete(
        self, request: LLMRequest, *, credentials: CredentialResolver | None = None
    ) -> LLMResponse:
        provider = self._providers.get(request.provider)
        if provider is None:
            raise ProviderError(
                f"LLM provider is not configured: {request.provider}",
                kind=ProviderErrorKind.CONFIGURATION,
                provider=request.provider,
            )
        if request.max_tokens is None:
            request = replace(request, max_tokens=self._max_tokens)
        elif request.max_tokens > self._max_tokens:
            error = ProviderError(
                f"max_tokens {request.max_tokens} exceeds the engine limit of "
                f"{self._max_tokens}",
                kind=ProviderErrorKind.CONFIGURATION,
                provider=provider.name,
            )
            _log_failure(request, error, time.monotonic())
            raise error
        api_key = await _api_key(provider, request, credentials)
        timeout = min(
            request.timeout_seconds or provider.timeout_seconds,
            provider.timeout_seconds,
        )
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                response = await provider.adapter.complete(
                    request, api_key=api_key, timeout_seconds=timeout
                )
        except TimeoutError:
            error = ProviderError(
                f"LLM request timed out after {timeout:g}s",
                kind=ProviderErrorKind.TIMEOUT,
                provider=provider.name,
            )
            _log_failure(request, error, started)
            raise error from None
        except ProviderError as error:
            _log_failure(request, error, started)
            raise
        # Counts and identifiers only — prompts and completions can hold
        # sensitive business data (AGENTS §76).
        logger.info(
            "llm_request_completed",
            extra={
                "event": "llm_request_completed",
                "provider": provider.name,
                "model": request.model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "finish_reason": response.finish_reason.value,
                "duration_ms": _elapsed_ms(started),
            },
        )
        return response


async def _api_key(
    provider: LLMProvider,
    request: LLMRequest,
    credentials: CredentialResolver | None,
) -> str | None:
    if request.credential is None:
        if provider.requires_credential:
            raise ProviderError(
                f"LLM provider {provider.name} requires a credential reference",
                kind=ProviderErrorKind.CONFIGURATION,
                provider=provider.name,
            )
        return None
    if credentials is None:
        raise ProviderError(
            "credential references cannot be resolved in this context",
            kind=ProviderErrorKind.CONFIGURATION,
            provider=provider.name,
        )
    # Resolver errors (NodeError: not referenced / not found) pass through
    # unchanged: they are credential problems, not provider ones.
    key = await credentials.resolve(request.credential)
    register_secret(key)  # provider errors may echo the key back
    return key


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _log_failure(request: LLMRequest, error: ProviderError, started: float) -> None:
    logger.warning(
        "llm_request_failed",
        extra={
            "event": "llm_request_failed",
            "provider": request.provider,
            "model": request.model,
            "error_code": error.code,
            "duration_ms": _elapsed_ms(started),
        },
    )
