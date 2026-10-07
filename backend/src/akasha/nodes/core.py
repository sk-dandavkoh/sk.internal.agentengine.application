"""Core built-in nodes: core.input, core.output, core.log, core.delay,
core.merge, core.set, core.pick.

Pure data plumbing: no I/O besides logging and waiting. Every node here is
stateless and constructed without arguments.
"""

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

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
    Workspace,
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


# A dotted path into mappings and lists (``lead.contacts.0.name``): plain
# lookups only, nothing evaluated (architecture §30/§31).
PATH = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*"
PATH_SCHEMA = {"type": "string", "pattern": f"^{PATH}$"}
MISSING = object()


def lookup(value: Any, path: str) -> Any:
    """The value at ``path``, or ``MISSING``. Digits index lists."""
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value


_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
# A whole template: text, single braces, and well-formed placeholders only,
# so a malformed ``{{ ... }}`` is rejected when the workflow is published.
TEMPLATE = {
    "type": "string",
    # Forms offer a template editor; the pattern below is the real check.
    "format": "template",
    "description": "Reference variables as {{ name }} or {{ lead.name }}.",
    "pattern": rf"^(?:[^{{]|\{{(?!\{{)|\{{\{{\s*{PATH}\s*\}}\}})*$",
}


def render(template: str, variables: Mapping[str, Any]) -> str:
    """Substitute every ``{{ path }}``; a missing variable is an error, never
    an empty string — a silently blank prompt still costs a model call."""

    def substitute(match: re.Match[str]) -> str:
        path = match.group(1).strip()
        if not re.fullmatch(PATH, path):
            raise NodeError(
                "the template has an invalid placeholder",
                kind=NodeErrorKind.CONFIGURATION,
                code="TEMPLATE_INVALID",
            )
        value = lookup(variables, path)
        if value is MISSING:
            raise NodeError(
                f"template variable is missing: {path}",
                kind=NodeErrorKind.VALIDATION,
                code="TEMPLATE_VARIABLE_MISSING",
                details={"variable": path},
            )
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    return _PLACEHOLDER.sub(substitute, template)


def require_workspace(context: NodeExecutionContext) -> Workspace:
    root = context.services.workspace_dir
    if root is None:
        raise NodeError(
            "no workspace directory is configured for this engine",
            kind=NodeErrorKind.CONFIGURATION,
            code="WORKSPACE_NOT_CONFIGURED",
        )
    return Workspace(root)


def value_in(description: str, *, required: bool = True) -> NodeInput:
    if required:
        return NodeInput("value", J, nullable=True, description=description)
    return NodeInput(
        "value", J, required=False, nullable=True, default=None, description=description
    )


def value_out(description: str) -> NodeOutput:
    return NodeOutput("value", J, nullable=True, description=description)


PASSED_ON = value_out("The incoming value, unchanged.")


class InputNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.input",
            name="Input",
            description="Starts the workflow with the data the run was started"
            " with: all of it, or one field of it.",
            version=VERSION,
            category="input",
        ),
        outputs=(
            value_out(
                "The run's input data, or the value of the chosen field. Its type"
                " is whatever was sent: text, a number, a list or an object."
            ),
        ),
        config_schema=closed_schema(
            {
                "field": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Name of one field of the run's input to pass"
                    " on, e.g. email. Leave empty to pass on the whole input.",
                }
            }
        ),
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
            description="Saves a value as a result of the run, shown with the run.",
            version=VERSION,
            category="output",
        ),
        inputs=(value_in("The value to keep as a result of the run."),),
        outputs=(PASSED_ON,),
        config_schema=closed_schema({}),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"value": context.inputs["value"]})


DEFAULT_LEVEL = "info"
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
        inputs=(value_in("The value to log and pass on."),),
        outputs=(PASSED_ON,),
        config_schema=closed_schema(
            {
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Text of the log line.",
                },
                "level": {
                    "type": "string",
                    "enum": sorted(_LEVELS),
                    "default": DEFAULT_LEVEL,
                    "description": "How important the line is; use error for"
                    " problems and debug for detail.",
                },
                "include_value": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also log the value itself. It may hold"
                    " sensitive data; only its type is logged otherwise.",
                },
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
        level = _LEVELS[context.config.get("level", DEFAULT_LEVEL)]
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
        inputs=(value_in("Optional value to pass on after the wait.", required=False),),
        outputs=(PASSED_ON,),
        config_schema=closed_schema(
            {
                "seconds": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": MAX_DELAY_SECONDS,
                    "description": "How long to wait before passing the value on.",
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
        inputs=(
            NodeInput(
                "items",
                J,
                nullable=True,
                multiple=True,
                description="Connect every branch to join here; each one adds"
                " its value to the list.",
            ),
        ),
        outputs=(
            NodeOutput(
                "items",
                PortType.LIST,
                description="The values of the branches that ran, in connection order.",
            ),
        ),
        config_schema=closed_schema({}),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"items": list(context.inputs["items"])})


class SetNode(Node):
    """Builds an object from static ``fields``, optionally placing the
    incoming value under ``key``. The way to bring constants into a graph
    (a scheduled run has no input) and to wrap a list or scalar into the
    object a ``core.llm`` ``variables`` input expects."""

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.set",
            name="Set",
            description="Builds an object from static fields and the input value.",
            version=VERSION,
            category="transform",
        ),
        inputs=(
            value_in(
                "Optional value to add to the object, under the name set in Key.",
                required=False,
            ),
        ),
        outputs=(
            NodeOutput(
                "value",
                PortType.OBJECT,
                description="The object: the fixed fields plus the incoming value.",
            ),
        ),
        config_schema=closed_schema(
            {
                "fields": {
                    "type": "object",
                    "description": "Fixed names and values the object always contains.",
                },
                "key": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Name the incoming value is stored under."
                    " Needed when the input is connected.",
                },
            }
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        result = dict(context.config.get("fields", {}))
        key = context.config.get("key")
        value = context.inputs.get("value")
        if key is not None:
            result[key] = value
        elif value is not None:
            # Dropping a connected value silently would hide a wiring bug.
            raise NodeError(
                "core.set received a value but has no 'key' to store it under",
                kind=NodeErrorKind.CONFIGURATION,
                code="SET_KEY_MISSING",
            )
        return NodeResult({"value": result})


class PickNode(Node):
    """Emits the value at ``path`` of its input, e.g. one field of an LLM's
    structured output, so it can feed a typed port (string, number)."""

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.pick",
            name="Pick",
            description="Emits one field of the input value by dotted path.",
            version=VERSION,
            category="transform",
        ),
        inputs=(value_in("The data to take a field from."),),
        outputs=(value_out("The value found at the path."),),
        config_schema=closed_schema(
            {
                "path": {
                    **PATH_SCHEMA,
                    "description": "Dotted path into the input, e.g."
                    " lead.contacts.0.name (digits index lists).",
                }
            },
            "path",
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        path = context.config["path"]
        value = lookup(context.inputs["value"], path)
        if value is MISSING:
            raise NodeError(
                f"the input has no value at {path!r}",
                kind=NodeErrorKind.VALIDATION,
                code="PICK_PATH_MISSING",
                details={"path": path},
            )
        return NodeResult({"value": value})
