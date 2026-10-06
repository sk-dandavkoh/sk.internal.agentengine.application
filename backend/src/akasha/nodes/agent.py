"""core.agent: a language model that calls tools in a loop until it answers.

    core.agent ──request──▶ LLMClient ──▶ model
        ▲                                   │ tool calls
        └──── tool results ◀── registered Node ◀┘

An agent is a node, not a second runtime (architecture §16): one node
attempt owns the whole loop, so timeouts, cancellation, retries, credential
scoping, redaction and persistence are the runtime's ordinary ones.

Tools are registered nodes (architecture §135/§136): each tool entry names a
node type and a fixed config written by the workflow author; the model
supplies only the node's *input ports* as arguments, derived into the tool's
JSON Schema from the port declarations. So the model can choose what to
search for or which page to read, but never which host core.http targets,
which credential is used or which program core.command runs. A tool runs
through the same contract the runtime enforces: inputs validated, config
validated (at run start, before the first model call), outputs validated.

A failing tool does not fail the agent: its stable code and redacted
message go back to the model, which may correct itself. Cancellation and
the node's own deadline still end the attempt at once. Tool results are
untrusted data (backend AGENTS §36): they reach the model as tool turns,
never as system instructions.
"""

import asyncio
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from akasha.domain.ai import (
    CostEstimate,
    LLMClient,
    Message,
    ProviderError,
    Role,
    ToolCall,
)
from akasha.domain.credentials import credential_references
from akasha.domain.node import NO_DEFAULT, NodeFactory
from akasha.nodes.core import VERSION, closed_schema
from akasha.nodes.llm import (
    MODEL_CONFIG,
    MODEL_CONFIG_REQUIRED,
    TOOL_NAME_SCHEMA,
    build_request,
    cost_metadata,
    prompt_messages,
    provider_node_error,
    structured_output,
    usage_metadata,
)
from akasha.sdk import (
    CredentialResolver,
    Node,
    NodeCapabilities,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeEvents,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeMetrics,
    NodeOutput,
    NodeResult,
    NodeValidationError,
    PortType,
)
from akasha.security.redaction import mask_known_secrets, redact_text

NODE_ID = "core.agent"
MAX_TOOLS = 32
DEFAULT_MAX_ITERATIONS = 10
MAX_ITERATIONS = 50
DEFAULT_TOOL_TIMEOUT_SECONDS = 60.0
MAX_TOOL_TIMEOUT_SECONDS = 600
DEFAULT_MAX_TOOL_RESULT_CHARS = 20_000
MAX_TOOL_RESULT_CHARS = 100_000
# Per step, so a large file write does not blow the node output limit.
MAX_STEP_ARGUMENTS_CHARS = 1_000

# How a port is offered to the model. BINARY, FILE, ARTIFACT and
# SECRET_REFERENCE ports have no model-writable form: a node needing one
# cannot be a tool.
_PORT_SCHEMAS: dict[PortType, dict[str, Any]] = {
    PortType.STRING: {"type": "string"},
    PortType.NUMBER: {"type": "number"},
    PortType.BOOLEAN: {"type": "boolean"},
    PortType.OBJECT: {"type": "object"},
    PortType.LIST: {"type": "array"},
    PortType.JSON: {},
}


def _error(message: str, kind: NodeErrorKind, code: str, **details: str) -> NodeError:
    return NodeError(message, kind=kind, code=code, details=details or None)


def _tool_invalid(name: str, reason: str) -> NodeError:
    return _error(
        f"tool {name!r} is invalid: {reason}",
        NodeErrorKind.CONFIGURATION,
        "AGENT_TOOL_INVALID",
        tool=name,
    )


def tool_parameters(definition: NodeDefinition) -> dict[str, Any]:
    """The JSON Schema of a node's input ports, as a tool's parameters."""
    properties: dict[str, Any] = {}
    for port in definition.inputs:
        if port.port_type not in _PORT_SCHEMAS:
            raise _tool_invalid(
                definition.node_id, f"input {port.name!r} cannot be supplied by a model"
            )
        schema = dict(port.schema or _PORT_SCHEMAS[port.port_type])
        if port.multiple:
            schema = {"type": "array", "items": schema}
        if port.nullable and "type" in schema:
            schema["type"] = [schema["type"], "null"]
        if port.description:
            schema["description"] = port.description
        properties[port.name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": [p.name for p in definition.inputs if p.required],
        "additionalProperties": False,
    }


class _ToolCredentials:
    """The runtime's credential rule, one level down: a tool resolves only
    references in *its own* config — not the agent's provider key, not
    another tool's credential — through the agent's resolver."""

    def __init__(self, parent: CredentialResolver, config: Mapping[str, Any]) -> None:
        self._parent = parent
        self._allowed = credential_references(config)

    async def resolve(self, reference: str) -> str:
        if reference not in self._allowed:
            raise _error(
                "the credential reference is not part of this tool's configuration",
                NodeErrorKind.CONFIGURATION,
                "CREDENTIAL_NOT_REFERENCED",
            )
        return await self._parent.resolve(reference)


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    factory: NodeFactory
    config: Mapping[str, Any]
    parameters: dict[str, Any]

    @property
    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class AgentNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id=NODE_ID,
            name="Agent",
            description="A language model that calls tools until it can answer.",
            version=VERSION,
            category="agent",
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
            NodeOutput("structured", PortType.JSON, nullable=True),
            NodeOutput(
                "steps",
                PortType.LIST,
                description="Every tool call in order: tool, arguments preview,"
                " outcome.",
            ),
            NodeOutput("metadata", PortType.OBJECT),
        ),
        config_schema=closed_schema(
            {
                **MODEL_CONFIG,
                "tools": {
                    "type": "array",
                    "maxItems": MAX_TOOLS,
                    "description": "Registered nodes the model may call.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": TOOL_NAME_SCHEMA,
                            "description": {
                                "type": "string",
                                "description": "Empty: the node's description.",
                            },
                            # Forms offer a node picker and the picked node's
                            # own settings; the checks run when the agent starts.
                            "node_type": {
                                "type": "string",
                                "minLength": 1,
                                "format": "node-type",
                            },
                            "config": {
                                "type": "object",
                                "format": "node-config",
                                "description": "Fixed config of the tool node.",
                            },
                        },
                        "required": ["name", "node_type"],
                        "additionalProperties": False,
                    },
                },
                "max_iterations": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_ITERATIONS,
                    "default": DEFAULT_MAX_ITERATIONS,
                    "description": "Model calls before the agent gives up.",
                },
                "tool_timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": MAX_TOOL_TIMEOUT_SECONDS,
                    "default": DEFAULT_TOOL_TIMEOUT_SECONDS,
                },
                "max_tool_result_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": MAX_TOOL_RESULT_CHARS,
                    "default": DEFAULT_MAX_TOOL_RESULT_CHARS,
                    "description": "Longer tool results are cut before the"
                    " model sees them.",
                },
            },
            *MODEL_CONFIG_REQUIRED,
        ),
        # Tools may write files, send requests or run programs: an
        # interrupted agent must not be re-run blindly by recovery.
        capabilities=NodeCapabilities(
            supports_cancellation=True, has_side_effects=True
        ),
        metrics=("model_calls", "tool_calls", "tool_errors"),
    )

    def __init__(
        self,
        llm: LLMClient | None = None,
        tools: Mapping[str, NodeFactory] | None = None,
    ) -> None:
        self._llm = llm
        self._catalog = tools or {}

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        if self._llm is None:
            raise _error(
                "no LLM service is configured for this engine",
                NodeErrorKind.CONFIGURATION,
                "LLM_NOT_CONFIGURED",
            )
        cfg = context.config
        tools = self._tools(cfg.get("tools", []))
        specs = [tool.spec for tool in tools.values()]
        messages = prompt_messages(cfg, context.inputs.get("variables", {}))
        steps: list[dict[str, Any]] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        # One unknown price makes the total unknown, not smaller.
        cost: CostEstimate | None = CostEstimate(0.0)
        for iteration in range(
            1, cfg.get("max_iterations", DEFAULT_MAX_ITERATIONS) + 1
        ):
            if context.is_cancelled():
                raise _error(
                    "the agent was cancelled",
                    NodeErrorKind.CANCELLATION,
                    "AGENT_CANCELLED",
                )
            request = build_request(cfg, messages, specs)
            context.metrics.increment("model_calls")
            try:
                response = await self._llm.complete(
                    request, credentials=context.credentials
                )
            except ProviderError as exc:
                raise provider_node_error(exc) from exc
            for key, value in usage_metadata(response.usage).items():
                usage[key] += value
            if cost is not None and response.cost is not None:
                cost = CostEstimate(cost.amount + response.cost.amount, cost.currency)
            else:
                cost = None
            if not response.tool_calls:
                return NodeResult(
                    {
                        "text": response.content,
                        "structured": structured_output(request, response),
                        "steps": steps,
                        "metadata": {
                            "provider": response.provider,
                            "model": response.model,
                            "finish_reason": response.finish_reason.value,
                            "iterations": iteration,
                            "usage": usage,
                            "cost": cost_metadata(cost),
                        },
                    }
                )
            messages.append(
                Message(Role.ASSISTANT, response.content, response.tool_calls)
            )
            # Sequential on purpose: tool order is the model's order, and a
            # tool may depend on an earlier one's side effect (write, then
            # run). ponytail: parallel tool calls when latency matters.
            for call in response.tool_calls:
                content, step = await self._call(context, tools, call, iteration)
                steps.append(step)
                messages.append(Message(Role.TOOL, content, tool_call_id=call.id))
        raise _error(
            "the agent did not finish within max_iterations model calls",
            NodeErrorKind.PERMANENT,
            "AGENT_MAX_ITERATIONS",
            iterations=str(cfg.get("max_iterations", DEFAULT_MAX_ITERATIONS)),
        )

    def _tools(self, entries: list[Mapping[str, Any]]) -> dict[str, _Tool]:
        """Resolve and check every tool before the first model call, so a
        broken tool fails the run without spending a model request.

        ponytail: checked at run start, not at publish — graph validation
        only sees the agent's own config schema. Upgrade path: let node
        definitions declare referenced node types for publish validation.
        """
        tools: dict[str, _Tool] = {}
        for entry in entries:
            name, node_type = entry["name"], entry["node_type"]
            if name in tools:
                raise _tool_invalid(name, "the name is used twice")
            factory = self._catalog.get(node_type)
            if factory is None:
                raise _tool_invalid(name, f"unknown node type {node_type!r}")
            definition = factory.definition
            # No nested agents: bounds agent/tool call depth (architecture
            # §131). Control-flow nodes route graphs, they answer nothing.
            if node_type == NODE_ID or definition.capabilities.is_control_flow:
                raise _tool_invalid(name, f"{node_type!r} cannot be used as a tool")
            config = entry.get("config", {})
            if issues := definition.config_issues(config):
                first = issues[0]
                raise _tool_invalid(name, f"{first.location} {first.message}")
            tools[name] = _Tool(
                name=name,
                description=entry.get("description") or definition.metadata.description,
                factory=factory,
                config=config,
                parameters=tool_parameters(definition),
            )
        return tools

    async def _call(
        self,
        context: NodeExecutionContext,
        tools: Mapping[str, _Tool],
        call: ToolCall,
        iteration: int,
    ) -> tuple[str, dict[str, Any]]:
        """Run one tool call; returns the tool turn's content and the step."""
        context.metrics.increment("tool_calls")
        tool = tools.get(call.name)
        step: dict[str, Any] = {
            "iteration": iteration,
            "tool": call.name,
            "node_type": tool.factory.definition.node_id if tool else None,
            "arguments": _preview(call.arguments),
        }
        try:
            if tool is None:
                raise _error(
                    "there is no tool with this name",
                    NodeErrorKind.VALIDATION,
                    "AGENT_TOOL_UNKNOWN",
                )
            outputs = await self._run_tool(context, tool, call.arguments)
        except NodeError as exc:
            if exc.kind is NodeErrorKind.CANCELLATION:
                raise
            context.metrics.increment("tool_errors")
            context.logger.info(
                "agent_tool_failed",
                extra={"tool": call.name, "iteration": iteration, "code": exc.code},
            )
            message = redact_text(exc.message)
            step |= {"ok": False, "error_code": exc.code}
            return json.dumps({"error": {"code": exc.code, "message": message}}), step
        context.logger.info(
            "agent_tool_completed", extra={"tool": call.name, "iteration": iteration}
        )
        content = json.dumps(
            mask_known_secrets(outputs), ensure_ascii=False, default=str
        )
        limit = context.config.get(
            "max_tool_result_chars", DEFAULT_MAX_TOOL_RESULT_CHARS
        )
        if len(content) > limit:
            content = content[:limit] + f"... [truncated at {limit} characters]"
        return content, step | {"ok": True, "error_code": None}

    async def _run_tool(
        self,
        context: NodeExecutionContext,
        tool: _Tool,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        definition = tool.factory.definition
        defaults = {
            port.name: deepcopy(port.default)
            for port in definition.inputs
            if port.name not in arguments and port.default is not NO_DEFAULT
        }
        inputs = defaults | dict(arguments)
        try:
            definition.validate_inputs(inputs)
        except NodeValidationError as exc:
            raise _error(
                exc.message, NodeErrorKind.VALIDATION, "AGENT_TOOL_INPUT_INVALID"
            ) from None
        # Same execution and workflow as the agent; credentials narrowed to
        # the tool's own config.
        child = replace(
            context,
            node_instance_id=f"{context.node_instance_id}.{tool.name}",
            config=tool.config,
            inputs=inputs,
            credentials=None
            if context.credentials is None
            else _ToolCredentials(context.credentials, tool.config),
            metrics=NodeMetrics(definition.metrics),
            events=NodeEvents(definition.events),
        )
        limit = context.config.get("tool_timeout_seconds", DEFAULT_TOOL_TIMEOUT_SECONDS)
        try:
            async with asyncio.timeout(limit):
                result = await tool.factory().execute(child)
        except TimeoutError:
            raise _error(
                f"the tool did not finish within {limit}s",
                NodeErrorKind.TIMEOUT,
                "AGENT_TOOL_TIMEOUT",
            ) from None
        try:
            definition.validate_outputs(result.outputs)
        except NodeValidationError as exc:
            raise _error(
                exc.message, NodeErrorKind.PERMANENT, "AGENT_TOOL_OUTPUT_INVALID"
            ) from None
        return result.outputs


def _preview(arguments: Mapping[str, Any]) -> str:
    text = json.dumps(mask_known_secrets(dict(arguments)), ensure_ascii=False)
    return redact_text(text[:MAX_STEP_ARGUMENTS_CHARS])
