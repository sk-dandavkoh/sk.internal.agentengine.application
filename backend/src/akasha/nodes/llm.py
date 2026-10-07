"""core.llm: one chat completion through the engine's ``LLMClient``.

    LLM Node → LLMClient (AIService) → provider adapter → provider

The node knows no provider: ``provider`` and ``model`` are plain config
strings that the AIService routes. Provider API keys arrive only as a
credential reference in the config, resolved by the AIService through the
node's own ``context.credentials``.

Prompts are templates over the ``variables`` input. ``{{ lead.name }}`` is
a dotted lookup into mappings and lists — no expressions, no attribute
access, nothing evaluated (architecture §30/§31). Strings are inserted as
they are; any other value as JSON. Interpolated values are data the model
reads, not instructions it can trust: untrusted input (web search results,
webhook payloads) belongs in ``user_prompt``, not ``system_prompt``
(backend AGENTS §36). Rendering cannot tell the two apart.

One prompt, one reply: no tools. A model that calls tools and works with
their results is core.agent (ADR 0005), which shares this module's request
building (``build_request``) and model settings (``MODEL_CONFIG``).
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from akasha.domain.ai import (
    CostEstimate,
    LLMClient,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    TokenUsage,
    ToolDefinition,
)
from akasha.domain.node import LLM_PROVIDER_FORMAT
from akasha.errors import (
    DomainValidationError,
    NodeConfigurationError,
    NodeContractError,
)
from akasha.nodes.core import MISSING, PATH, VERSION, closed_schema, lookup
from akasha.sdk import (
    CREDENTIAL_REFERENCE_FORMAT,
    Node,
    NodeCapabilities,
    NodeConfigSchema,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)

# Resource limits (AGENTS §43). A rendered prompt beyond ~100k tokens is a
# wiring mistake (a whole upstream payload interpolated), not a prompt.
MAX_PROMPT_CHARS = 400_000
MAX_OUTPUT_TOKENS = 100_000
MAX_TIMEOUT_SECONDS = 600

_PATH = PATH
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
# A whole template: text, single braces, and well-formed placeholders only,
# so a malformed ``{{ ... }}`` is rejected when the workflow is published.
TEMPLATE = {
    "type": "string",
    # Forms offer a prompt editor; the pattern below is the real check.
    "format": "template",
    "description": "Reference variables as {{ name }} or {{ lead.name }}.",
    "pattern": rf"^(?:[^{{]|\{{(?!\{{)|\{{\{{\s*{_PATH}\s*\}}\}})*$",
}

# Retry semantics follow the provider's category (phase 14 handoff).
_ERROR_KINDS = {
    ProviderErrorKind.RATE_LIMIT: NodeErrorKind.TRANSIENT,
    ProviderErrorKind.UNAVAILABLE: NodeErrorKind.TRANSIENT,
    ProviderErrorKind.TIMEOUT: NodeErrorKind.TIMEOUT,
    ProviderErrorKind.CONFIGURATION: NodeErrorKind.CONFIGURATION,
    ProviderErrorKind.AUTHENTICATION: NodeErrorKind.CONFIGURATION,
}


def _error(message: str, kind: NodeErrorKind, code: str, **details: str) -> NodeError:
    return NodeError(message, kind=kind, code=code, details=details or None)


def provider_node_error(exc: ProviderError) -> NodeError:
    """A provider failure as the node error the runtime classifies; the
    message is already redacted and truncated by the adapter."""
    return _error(
        exc.message,
        _ERROR_KINDS.get(exc.kind, NodeErrorKind.PERMANENT),
        exc.code,
        provider=exc.provider,
    )


# The model settings core.llm and core.agent share (the prompt is rendered
# from ``variables``; the credential is a reference resolved per call).
MODEL_CONFIG: dict[str, object] = {
    "provider": {
        "type": "string",
        "minLength": 1,
        "format": LLM_PROVIDER_FORMAT,
        "description": "Which configured AI service to use, e.g. openai,"
        " anthropic or a self-hosted one.",
    },
    "model": {
        "type": "string",
        "minLength": 1,
        "description": "Model name as the provider calls it, e.g. gpt-4o.",
    },
    "credential": {
        "type": "string",
        "format": CREDENTIAL_REFERENCE_FORMAT,
        "description": "Credential holding the provider API key.",
    },
    "system_prompt": {
        **TEMPLATE,
        "description": "Optional standing instructions, e.g. the role or tone."
        " Reference variables as {{ name }} or {{ lead.name }}.",
    },
    "user_prompt": {
        **TEMPLATE,
        "minLength": 1,
        "description": "The request sent to the model. Reference variables as"
        " {{ name }} or {{ lead.name }}.",
    },
    "temperature": {
        "type": "number",
        "minimum": 0,
        "maximum": 2,
        "description": "Higher is more creative, lower more predictable."
        " Empty: the provider's default.",
    },
    "max_tokens": {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_OUTPUT_TOKENS,
        "description": "Longest reply allowed, in tokens (about ¾ of a word"
        " each). Empty: the engine's limit, which is also the most allowed"
        " (see the run limits).",
    },
    "output_schema": {
        "type": "object",
        "description": "JSON Schema the reply must match; it is"
        " returned parsed on the structured output.",
    },
    "timeout_seconds": {
        "type": "number",
        "minimum": 1,
        "maximum": MAX_TIMEOUT_SECONDS,
        "description": "How long to wait for the reply, in seconds, before giving up.",
    },
}
MODEL_CONFIG_REQUIRED = ("provider", "model", "user_prompt")
TOOL_NAME_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}


def render(template: str, variables: Mapping[str, Any]) -> str:
    """Substitute every ``{{ path }}``; a missing variable is an error, never
    an empty string — a silently blank prompt still costs a model call."""

    def substitute(match: re.Match[str]) -> str:
        path = match.group(1).strip()
        if not re.fullmatch(_PATH, path):
            raise _error(
                "the prompt template has an invalid placeholder",
                NodeErrorKind.CONFIGURATION,
                "PROMPT_TEMPLATE_INVALID",
            )
        value = lookup(variables, path)
        if value is MISSING:
            raise _error(
                f"prompt variable is missing: {path}",
                NodeErrorKind.VALIDATION,
                "PROMPT_VARIABLE_MISSING",
                variable=path,
            )
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    return _PLACEHOLDER.sub(substitute, template)


class LlmNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.llm",
            name="LLM",
            description="Sends a templated prompt to a language model.",
            version=VERSION,
            category="ai",
        ),
        inputs=(
            NodeInput(
                "variables",
                PortType.OBJECT,
                required=False,
                default={},
                description="Values the prompt templates can reference.",
            ),
        ),
        outputs=(
            NodeOutput(
                "text",
                PortType.STRING,
                nullable=True,
                description="The model's reply as text.",
            ),
            # The parsed reply when ``output_schema`` is configured.
            NodeOutput(
                "structured",
                PortType.JSON,
                nullable=True,
                description="The reply as data, when an output schema is set;"
                " empty otherwise.",
            ),
            # Provider, model, finish reason, token usage and cost estimate.
            NodeOutput(
                "metadata",
                PortType.OBJECT,
                description="Details of the call: provider, model, token usage"
                " and estimated cost.",
            ),
        ),
        config_schema=closed_schema(MODEL_CONFIG, *MODEL_CONFIG_REQUIRED),
        capabilities=NodeCapabilities(supports_cancellation=True),
    )

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        if self._llm is None:
            raise _error(
                "no LLM service is configured for this engine",
                NodeErrorKind.CONFIGURATION,
                "LLM_NOT_CONFIGURED",
            )
        cfg = context.config
        request = build_request(
            cfg, prompt_messages(cfg, context.inputs.get("variables", {})), ()
        )
        try:
            response = await self._llm.complete(
                request, credentials=context.credentials
            )
        except ProviderError as exc:
            raise provider_node_error(exc) from exc
        return NodeResult(
            {
                "text": response.content,
                "structured": structured_output(request, response),
                "metadata": _metadata(response),
            }
        )


def prompt_messages(
    cfg: Mapping[str, Any], variables: Mapping[str, Any]
) -> list[Message]:
    """The rendered system (if any) and user turn of a model config."""
    messages = []
    if system := cfg.get("system_prompt"):
        messages.append(Message(Role.SYSTEM, render(system, variables)))
    messages.append(Message(Role.USER, render(cfg["user_prompt"], variables)))
    return messages


def build_request(
    cfg: Mapping[str, Any],
    messages: Sequence[Message],
    tools: Iterable[Mapping[str, Any]],
) -> LLMRequest:
    """An ``LLMRequest`` from a model config, a conversation and tool specs
    (``name``, ``description``, ``parameters``). The whole conversation is
    bounded by ``MAX_PROMPT_CHARS``, tool calls and results included."""
    size = sum(
        len(m.content or "") + sum(len(json.dumps(c.arguments)) for c in m.tool_calls)
        for m in messages
    )
    if size > MAX_PROMPT_CHARS:
        raise _error(
            f"the rendered prompt exceeds {MAX_PROMPT_CHARS} characters",
            NodeErrorKind.VALIDATION,
            "PROMPT_TOO_LARGE",
            limit=str(MAX_PROMPT_CHARS),
        )
    schema = cfg.get("output_schema")
    try:
        if schema is not None:
            NodeConfigSchema(schema)  # the validator below needs an object schema
        return LLMRequest(
            provider=cfg["provider"],
            model=cfg["model"],
            messages=tuple(messages),
            temperature=cfg.get("temperature"),
            max_tokens=cfg.get("max_tokens"),
            tools=tuple(_tool(t) for t in tools),
            credential=cfg.get("credential"),
            timeout_seconds=cfg.get("timeout_seconds"),
            response_schema=schema,
        )
    except (DomainValidationError, NodeContractError) as exc:
        raise _error(
            exc.message, NodeErrorKind.CONFIGURATION, "LLM_CONFIGURATION_INVALID"
        ) from None


def _tool(spec: Mapping[str, Any]) -> ToolDefinition:
    if "name" not in spec or "parameters" not in spec:
        raise DomainValidationError("every tool needs a name and parameters")
    return ToolDefinition(spec["name"], spec.get("description", ""), spec["parameters"])


def structured_output(request: LLMRequest, response: LLMResponse) -> Any:
    """The reply parsed and checked against ``output_schema``.

    Checked here even for providers that enforce schemas: "where supported"
    means some ignore ``response_format``. The check uses the engine's JSON
    Schema subset (``NodeConfigSchema``); unsupported keywords are ignored.
    """
    if request.response_schema is None or response.tool_calls:
        return None  # a tool call is the model's answer for this turn
    text = (response.content or "").strip()
    # Models without native structured output often fence their JSON.
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    try:
        if not isinstance(value, dict):
            raise NodeConfigurationError("reply is not a JSON object")
        NodeConfigSchema(dict(request.response_schema)).validate_config(value)
    except NodeConfigurationError as exc:
        raise _error(
            f"the model reply does not match the output schema: {exc.message}",
            NodeErrorKind.PERMANENT,
            "LLM_STRUCTURED_OUTPUT_INVALID",
        ) from None
    except NodeContractError as exc:
        raise _error(
            exc.message, NodeErrorKind.CONFIGURATION, "LLM_CONFIGURATION_INVALID"
        ) from None
    return value


def _metadata(response: LLMResponse) -> dict[str, Any]:
    return {
        "provider": response.provider,
        "model": response.model,
        "request_id": response.request_id,
        "finish_reason": response.finish_reason.value,
        "usage": usage_metadata(response.usage),
        "cost": cost_metadata(response.cost),
    }


def usage_metadata(usage: TokenUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


def cost_metadata(cost: CostEstimate | None) -> dict[str, Any] | None:
    if cost is None:
        return None
    return {"amount": cost.amount, "currency": cost.currency, "estimated": True}
