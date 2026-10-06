"""Plugin authoring material for agents: the SDK guide and a starter package.

The guide condenses ``docs/sdk.md`` (not shipped in the image) to what an
agent needs to write a working plugin without reading engine source. The
scaffold is a complete, valid package: ``plugin_validate`` accepts it as is.
Nothing here touches the filesystem.
"""

import json
from typing import Final

from akasha import __version__ as ENGINE_VERSION
from akasha.domain.node import PortType
from akasha.plugins.manifest import MANIFEST_FILE
from akasha.sdk import SDK_VERSION

SDK_GUIDE: Final = f"""\
# Akasha plugin SDK (SDK {SDK_VERSION}, engine {ENGINE_VERSION})

A plugin is a Python package (a set of text files) with a manifest
`{MANIFEST_FILE}` at its root. It provides node types. Plugin code imports
from `akasha.sdk` only; everything else under `akasha` is internal.

Trust: plugin code runs inside the engine process with the engine's
permissions. It is NOT sandboxed. Installing it over MCP needs an admin and
the operator setting AKASHA_MCP_PLUGIN_CODE=true.

## Package layout (paths are relative, '/'-separated, no '..')

    akasha-plugin.json   manifest (required)
    __init__.py          required, may be empty
    nodes.py             node classes (any module names work)
    README.md            optional documentation

## Manifest

    {{
      "manifest_version": 1,
      "id": "acme",                     # lowercase; [a-z][a-z0-9]* joined by . _ -
      "name": "Acme Nodes",
      "version": "1.0.0",               # semver of the plugin
      "description": "...",
      "engine": ">=0.1.0,<1.0.0",       # must match engine {ENGINE_VERSION}
      "sdk": ">=1.0.0,<2.0.0",          # must match SDK {SDK_VERSION}
      "dependencies": [],               # see below
      "components": {{"nodes": [
        {{"id": "acme.word_count", "entry_point": "nodes:WordCount"}}
      ]}},
      "documentation": "README.md",
      "python_requirements": []         # PEP 508; none if you want plugin_test
    }}

A dependency: {{"kind": "plugin" or "node", "id": "...", "version": ">=1.0.0"}}.

Every node id must start with "<plugin id>." and match
[a-z][a-z0-9_]*(.[a-z][a-z0-9_]*)+. `entry_point` is "module:ClassName"
inside the package. Unknown fields are rejected.

## A node

    from akasha.sdk import (
        Node, NodeConfigSchema, NodeDefinition, NodeError, NodeErrorKind,
        NodeExecutionContext, NodeInput, NodeMetadata, NodeOutput, NodeResult,
        PortType,
    )

    class WordCount(Node):
        definition = NodeDefinition(
            metadata=NodeMetadata(
                node_id="acme.word_count", name="Word Count",
                description="Counts the words of a text.", version="1.0.0",
                category="text",
            ),
            inputs=(NodeInput("text", PortType.STRING),),
            outputs=(NodeOutput("count", PortType.NUMBER),),
            config_schema=NodeConfigSchema({{
                "type": "object",
                "properties": {{"max_words": {{"type": "integer", "minimum": 1}}}},
                "additionalProperties": False,
            }}),
            metrics=("words_counted",),
            events=("limit_exceeded",),
        )

        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            count = len(context.inputs["text"].split())
            limit = context.config.get("max_words")
            if limit is not None and count > limit:
                context.events.emit("limit_exceeded")
                raise NodeError("too many words", kind=NodeErrorKind.VALIDATION,
                                code="WORD_LIMIT_EXCEEDED")
            context.metrics.increment("words_counted", count)
            return NodeResult({{"count": count}})

Rules:
- A fresh instance runs each attempt; keep no state on `self`.
- Port names match [a-z][a-z0-9_]*. Port types: {", ".join(p.name for p in PortType)}.
  NodeInput(name, port_type, required=True, nullable=False, multiple=False,
  schema=None, default=<optional inputs only>, description="").
  NodeOutput(name, port_type, nullable=False, multiple=False, schema=None,
  description=""). Every declared output must be in the result.
- Config schema: a JSON-Schema object; supported keywords: type (or list
  for nullable), required, properties, additionalProperties, items, enum,
  minimum, maximum, minLength, maxItems, pattern, format. Use
  "additionalProperties": False. Config is validated at publish; inputs
  before and outputs after `execute`.
- Credentials: config holds references "credential:<name>" (declare the key
  with {{"type": "string", "format": "credential-reference"}}); resolve with
  `await context.resolve_credential(context.config["key"])`. Never put
  secret values in config, outputs or logs.
- Logging: `context.logger.info("event_name", extra={{"count": 3}})`; log
  sizes and ids, never payloads. Stored with the execution (execution_logs_get).
- Metrics/events: declare names in the definition, then
  `context.metrics.increment(name, amount)` / `context.events.emit(name)`.
- Errors: raise `NodeError(message, kind=NodeErrorKind.X, code="UPPER_CODE")`.
  Kinds: VALIDATION, CONFIGURATION, PERMANENT (no retry); TRANSIENT, TIMEOUT
  (retryable); CANCELLATION. Any other exception becomes NODE_UNEXPECTED_ERROR.
- Long work: poll `context.is_cancelled()`. Side effects: set
  `capabilities=NodeCapabilities(has_side_effects=True)` and use
  `context.node_execution_id` as idempotency key.
- Other context members: execution_input (entry nodes), execution_id,
  workflow_id, workflow_version, runtime_metadata["attempt"].

## Lifecycle over MCP (admin only)

plugin_create (starter files) -> edit files -> plugin_validate (static, no
code runs) -> plugin_test (runs one node, needs AKASHA_MCP_PLUGIN_CODE) ->
plugin_package -> plugin_install -> node_types_list (the nodes appear) ->
use them in a workflow draft -> workflow_publish (pins the plugin version)
-> execution_start. plugin_update replaces an installed plugin; published
versions pinned to the old version then refuse to run until it is back.
plugin_disable / plugin_enable / plugin_remove (dependency-aware; force to
override).
"""


def manifest_schema() -> dict[str, object]:
    """JSON Schema of the plugin manifest, from the model that validates it."""
    from akasha.plugins.manifest import PluginManifest

    return PluginManifest.model_json_schema()


def scaffold(
    plugin_id: str, name: str, node_name: str, description: str
) -> dict[str, str]:
    """A complete starter package: one node that upper-cases a string."""
    node_id = f"{plugin_id}.{node_name}"
    class_name = "".join(p.capitalize() for p in node_name.split("_")) or "Node"
    major = int(SDK_VERSION.split(".")[0])
    manifest = {
        "manifest_version": 1,
        "id": plugin_id,
        "name": name,
        "version": "0.1.0",
        "description": description,
        "engine": f">={ENGINE_VERSION},<1.0.0",
        "sdk": f">={major}.0.0,<{major + 1}.0.0",
        "dependencies": [],
        "components": {
            "nodes": [{"id": node_id, "entry_point": f"nodes:{class_name}"}]
        },
        "documentation": "README.md",
        "python_requirements": [],
    }
    nodes = f'''"""Nodes of the {name} plugin."""

from akasha.sdk import (
    Node,
    NodeConfigSchema,
    NodeDefinition,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)


class {class_name}(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id={node_id!r},
            name={name!r},
            description={description!r},
            version="0.1.0",
            category="custom",
        ),
        inputs=(NodeInput("text", PortType.STRING),),
        outputs=(NodeOutput("result", PortType.STRING),),
        config_schema=NodeConfigSchema(
            {{
                "type": "object",
                "properties": {{"suffix": {{"type": "string", "default": ""}}}},
                "additionalProperties": False,
            }}
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        # Replace with the real behavior.
        suffix = context.config.get("suffix", "")
        return NodeResult({{"result": context.inputs["text"].upper() + suffix}})
'''
    return {
        MANIFEST_FILE: json.dumps(manifest, indent=2) + "\n",
        "__init__.py": f'"""{name} plugin."""\n',
        "nodes.py": nodes,
        "README.md": f"# {name}\n\n{description}\n\nNode `{node_id}`.\n",
    }
