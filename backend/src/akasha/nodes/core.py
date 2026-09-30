"""Core built-in nodes: core.input, core.output, core.log, core.delay,
core.merge.

Pure data plumbing: no I/O besides logging and waiting. Every node here is
stateless and constructed without arguments.
"""

import asyncio
import logging

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

J = PortType.JSON
VERSION = "1.0.0"

# ponytail: in-process sleep, capped at one hour; durable delays (WAITING
# state + a timer that survives restarts) need the worker/recovery phases.
MAX_DELAY_SECONDS = 3600


def closed_schema(properties: dict[str, object], *required: str) -> NodeConfigSchema:
    """Config schema that rejects unknown keys, so typos fail at publish."""
    return NodeConfigSchema(
        {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        }
    )


def value_in(*, required: bool = True) -> NodeInput:
    if required:
        return NodeInput("value", J, nullable=True)
    return NodeInput("value", J, required=False, nullable=True, default=None)


VALUE_OUT = NodeOutput("value", J, nullable=True)


class InputNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.input",
            name="Input",
            description="Emits the execution input, or one top-level field of it.",
            version=VERSION,
            category="input",
        ),
        outputs=(VALUE_OUT,),
        config_schema=closed_schema({"field": {"type": "string", "minLength": 1}}),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        payload = context.execution_input
        field = context.config.get("field")
        if field is None:
            return NodeResult({"value": dict(payload)})
        if field not in payload:
            raise NodeError(
                f"the execution input has no field {field!r}",
                kind=NodeErrorKind.VALIDATION,
                code="INPUT_FIELD_MISSING",
                details={"field": field},
            )
        return NodeResult({"value": payload[field]})


class OutputNode(Node):
    """Marks a result of the execution. It re-emits its input because node
    *outputs* are what the execution record persists (inputs are not)."""

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.output",
            name="Output",
            description="Records a value as a result of the execution.",
            version=VERSION,
            category="output",
        ),
        inputs=(value_in(),),
        outputs=(VALUE_OUT,),
        config_schema=closed_schema({}),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"value": context.inputs["value"]})


_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class LogNode(Node):
    """Writes one structured log line and passes its input through.

    The value itself is logged only with ``include_value: true``: workflow
    data may be sensitive (AGENTS §76/§77), so the default logs its type.
    """

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.log",
            name="Log",
            description="Writes a structured log line and passes the value on.",
            version=VERSION,
            category="observability",
        ),
        inputs=(value_in(),),
        outputs=(VALUE_OUT,),
        config_schema=closed_schema(
            {
                "message": {"type": "string", "minLength": 1},
                "level": {"type": "string", "enum": sorted(_LEVELS)},
                "include_value": {"type": "boolean"},
            },
            "message",
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        value = context.inputs["value"]
        extra: dict[str, object] = {
            "event": "node_log",
            "execution_id": context.execution_id,
            "node_execution_id": context.node_execution_id,
            "node_id": context.node_instance_id,
            "value_type": type(value).__name__,
        }
        if context.config.get("include_value", False):
            extra["value"] = value
        level = _LEVELS[context.config.get("level", "info")]
        context.logger.log(level, context.config["message"], extra=extra)
        return NodeResult({"value": value})


class DelayNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.delay",
            name="Delay",
            description="Waits for a number of seconds, then passes the value on.",
            version=VERSION,
            category="control_flow",
        ),
        inputs=(value_in(required=False),),
        outputs=(VALUE_OUT,),
        config_schema=closed_schema(
            {
                "seconds": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": MAX_DELAY_SECONDS,
                }
            },
            "seconds",
        ),
        capabilities=NodeCapabilities(supports_cancellation=True),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        # asyncio.sleep is a cancellation point: runtime cancellation and
        # node timeouts interrupt the wait immediately.
        await asyncio.sleep(context.config["seconds"])
        return NodeResult({"value": context.inputs.get("value")})


class MergeNode(Node):
    """Collects every live incoming value into one list, in graph edge
    order. Behind an if/switch only the taken branch contributes; the
    runtime skips the merge only when no branch produced anything."""

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.merge",
            name="Merge",
            description="Joins incoming branches into one list of values.",
            version=VERSION,
            category="control_flow",
        ),
        inputs=(NodeInput("items", J, nullable=True, multiple=True),),
        outputs=(NodeOutput("items", PortType.LIST),),
        config_schema=closed_schema({}),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"items": list(context.inputs["items"])})
