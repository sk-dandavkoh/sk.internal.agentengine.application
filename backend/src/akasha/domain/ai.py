"""Provider-independent LLM vocabulary (architecture §3.4, §27, §28, §133).

These types are what the Engine speaks; adapters (``akasha.providers``)
translate them to and from one provider library. Nothing here knows
LiteLLM, OpenAI or any other provider shape.

A request carries a credential *reference*, never a key: the AIService
resolves it through the caller's ``CredentialResolver`` at call time.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, Protocol

from akasha.domain.credentials import is_credential_reference
from akasha.domain.node import CredentialResolver
from akasha.errors import AkashaError, DomainValidationError


class Role(StrEnum):
    SYSTEM = auto()
    USER = auto()
    ASSISTANT = auto()
    TOOL = auto()


@dataclass(frozen=True)
class ToolCall:
    """A model's structured request to invoke a tool (§135)."""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class Message:
    """One chat turn. ``tool_calls`` only on assistant turns; a TOOL turn
    answers exactly one call via ``tool_call_id``."""

    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", Role(self.role))
        if self.tool_calls and self.role is not Role.ASSISTANT:
            raise DomainValidationError("only assistant messages carry tool calls")
        if (self.role is Role.TOOL) != (self.tool_call_id is not None):
            raise DomainValidationError(
                "tool_call_id is required on, and only on, tool messages"
            )
        if self.content is None and not self.tool_calls:
            raise DomainValidationError("message needs content or tool calls")


@dataclass(frozen=True)
class ToolDefinition:
    """A tool offered to the model; ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class LLMRequest:
    provider: str
    model: str
    messages: tuple[Message, ...]
    temperature: float | None = None
    max_tokens: int | None = None
    tools: tuple[ToolDefinition, ...] = ()
    credential: str | None = None  # "credential:<name>"
    # May shorten, never extend, the provider's configured timeout.
    timeout_seconds: float | None = None
    # Structured output: a JSON Schema object the reply must satisfy. Sent to
    # providers that support it; the caller still validates the reply.
    response_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise DomainValidationError("provider and model are required")
        if not self.messages:
            raise DomainValidationError("at least one message is required")
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise DomainValidationError("temperature must be between 0 and 2")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise DomainValidationError("max_tokens must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise DomainValidationError("timeout_seconds must be positive")
        if self.credential is not None and not is_credential_reference(self.credential):
            raise DomainValidationError(
                "credential must be a credential reference, not a secret value"
            )
        if self.response_schema is not None and (
            self.response_schema.get("type") != "object"
        ):
            raise DomainValidationError("response_schema must be a JSON Schema object")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class CostEstimate:
    """Cost from published list prices (architecture §29): an estimate,
    never billing data — discounts, caching and free tiers are unknown."""

    amount: float
    currency: str = "USD"


class FinishReason(StrEnum):
    STOP = auto()
    LENGTH = auto()
    TOOL_CALLS = auto()
    CONTENT_FILTER = auto()
    OTHER = auto()


@dataclass(frozen=True)
class LLMResponse:
    provider: str
    model: str  # as reported by the provider (may be more specific)
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: TokenUsage
    finish_reason: FinishReason
    request_id: str | None = None  # provider's id, for support/tracing
    cost: CostEstimate | None = None  # None: no pricing known for the model


class ProviderErrorKind(StrEnum):
    """Stable categories every adapter maps its provider's errors onto."""

    AUTHENTICATION = auto()
    RATE_LIMIT = auto()
    TIMEOUT = auto()
    CONTEXT_LENGTH = auto()
    INVALID_REQUEST = auto()
    CONTENT_POLICY = auto()
    UNAVAILABLE = auto()
    INVALID_RESPONSE = auto()
    CONFIGURATION = auto()  # unknown provider, missing credential
    UNKNOWN = auto()


RETRYABLE_PROVIDER_ERRORS = frozenset(
    {
        ProviderErrorKind.RATE_LIMIT,
        ProviderErrorKind.TIMEOUT,
        ProviderErrorKind.UNAVAILABLE,
    }
)


class ProviderError(AkashaError):
    """The only failure an LLM call surfaces. Messages are redacted by the
    adapter; the raw provider exception is chained, never exposed."""

    http_status = 502

    def __init__(self, message: str, *, kind: ProviderErrorKind, provider: str) -> None:
        super().__init__(message, details={"provider": provider, "kind": kind.value})
        self.kind = kind
        self.provider = provider
        self.code = f"PROVIDER_{kind.name}"
        self.retryable = kind in RETRYABLE_PROVIDER_ERRORS


class LLMClient(Protocol):
    """The port through which nodes reach an LLM (architecture §27).

    Nodes may depend only on the SDK, never on the application layer, so
    nodes receive an ``LLMClient`` as ``context.services.llm`` (ADR 0011)
    and the composition root passes ``AIService``, which satisfies this
    structurally. Tests pass a fake. ``credentials`` is the node's own
    ``context.credentials``.
    """

    async def complete(
        self, request: LLMRequest, *, credentials: CredentialResolver | None = None
    ) -> LLMResponse: ...
