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

Tool calls are *returned*, not executed: the node offers tools and reports
the calls the model asked for. Running them is a downstream node's job.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

from akasha.domain.ai import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    ToolDefinition,
)
from akasha.errors import (
    DomainValidationError,
    NodeConfigurationError,
    NodeContractError,
)
from akasha.nodes.core import VERSION, closed_schema
from akasha.sdk import (
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
MAX_TOOLS = 32
MAX_TIMEOUT_SECONDS = 600

_PATH = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*"
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
# A whole template: text, single braces, and well-formed placeholders only,
# so a malformed ``{{ ... }}`` is rejected when the project is published.
_TEMPLATE = {
    "type": "string",
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
        value: Any = variables
        for part in path.split("."):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
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
            NodeOutput("text", PortType.STRING, nullable=True),
            # The parsed reply when ``output_schema`` is configured.
            NodeOutput("structured", PortType.JSON, nullable=True),
            NodeOutput("tool_calls", PortType.LIST),
            # Provider, model, finish reason, token usage and cost estimate.
            NodeOutput("metadata", PortType.OBJECT),
        ),
        config_schema=closed_schema(
            {
                "provider": {"type": "string", "minLength": 1},
                "model": {"type": "string", "minLength": 1},
                "credential": {"type": "string", "pattern": "^credential:"},
                "system_prompt": _TEMPLATE,
                "user_prompt": {**_TEMPLATE, "minLength": 1},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                "max_tokens": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_OUTPUT_TOKENS,
                },
                "output_schema": {"type": "object"},
                "tools": {
                    "type": "array",
                    "maxItems": MAX_TOOLS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "pattern": "^[A-Za-z0-9_-]{1,64}$",
                            },
                            "description": {"type": "string"},
                            "parameters": {"type": "object"},
                        },
                    },
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                },
            },
            "provider",
            "model",
            "user_prompt",
        ),
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
        request = _request(context.config, context.inputs.get("variables", {}))
        try:
            response = await self._llm.complete(
                request, credentials=context.credentials
            )
        except ProviderError as exc:
            # The message is already redacted and truncated by the adapter.
            raise _error(
                exc.message,
                _ERROR_KINDS.get(exc.kind, NodeErrorKind.PERMANENT),
                exc.code,
                provider=exc.provider,
            ) from exc
        return NodeResult(
            {
                "text": response.content,
                "structured": _structured(request, response),
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": dict(c.arguments)}
                    for c in response.tool_calls
                ],
                "metadata": _metadata(response),
            }
        )


def _request(cfg: Mapping[str, Any], variables: Mapping[str, Any]) -> LLMRequest:
    messages = []
    if system := cfg.get("system_prompt"):
        messages.append(Message(Role.SYSTEM, render(system, variables)))
    messages.append(Message(Role.USER, render(cfg["user_prompt"], variables)))
    size = sum(len(m.content or "") for m in messages)
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
            tools=tuple(_tool(t) for t in cfg.get("tools", [])),
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


def _structured(request: LLMRequest, response: LLMResponse) -> Any:
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
    usage = response.usage
    cost = response.cost
    return {
        "provider": response.provider,
        "model": response.model,
        "request_id": response.request_id,
        "finish_reason": response.finish_reason.value,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        },
        "cost": None
        if cost is None
        else {"amount": cost.amount, "currency": cost.currency, "estimated": True},
    }
