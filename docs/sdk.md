# Python Node SDK

SDK version: **1.1.0** (`akasha.sdk.SDK_VERSION`)

The SDK is the supported way to write custom Nodes. A custom Node imports
from two modules only:

- `akasha.sdk`: everything a Node needs at runtime;
- `akasha.sdk.testing`: the test harness for the Node's own tests.

Every other module under `akasha` is internal to the Engine and may change
without notice.

> **Trust model:** custom Python Nodes are trusted code. They run inside the
> Engine process with the Engine's permissions. The SDK is an API boundary,
> not a sandbox (architecture §34).

A complete, tested example lives in
[`backend/examples/hmac_signer`](../backend/examples/hmac_signer/).

---

## 1. A Node in one screen

```python
from akasha.sdk import (
    Node, NodeConfigSchema, NodeDefinition, NodeError, NodeErrorKind,
    NodeExecutionContext, NodeInput, NodeMetadata, NodeOutput, NodeResult,
    PortType,
)


class WordCount(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="acme.word_count",       # globally unique: <namespace>.<name>
            name="Word Count",
            description="Counts the words of a text.",
            version="1.0.0",                  # semver of *this node*
            category="text",
        ),
        inputs=(NodeInput("text", PortType.STRING),),
        outputs=(NodeOutput("count", PortType.NUMBER),),
        config_schema=NodeConfigSchema({
            "type": "object",
            "properties": {"max_words": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        }),
        metrics=("words_counted",),
        events=("limit_exceeded",),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        count = len(context.inputs["text"].split())
        limit = context.config.get("max_words")
        if limit is not None and count > limit:
            context.events.emit("limit_exceeded")
            raise NodeError(
                f"{count} words exceed the limit of {limit}",
                kind=NodeErrorKind.VALIDATION,
                code="WORD_LIMIT_EXCEEDED",
            )
        context.metrics.increment("words_counted", count)
        return NodeResult({"count": count})
```

A Node class declares a `definition` and implements `async execute`. The
Engine creates a fresh instance for each attempt. Do not keep state between
calls on `self`.

---

## 2. What the Engine guarantees around `execute`

| When | What | Failure |
|---|---|---|
| Registration | `definition` exists, `execute` is implemented | `NodeContractError` |
| Draft save / publish | the config holds no plaintext secrets | the project is rejected |
| Publish (and before every run) | the config matches `config_schema` | the project is rejected |
| Before `execute` | inputs match the declared input ports | `NODE_INPUT_INVALID` |
| During `execute` | node timeout, cooperative cancellation | `NODE_TIMEOUT`, cancelled |
| After `execute` | outputs match the declared output ports | `NODE_OUTPUT_INVALID` |
| Always | error messages, logs and outputs are redacted | — |

So `execute` can rely on validated config and inputs. It does not need to
repeat those checks.

---

## 3. Reference

### Definition

| Name | Purpose |
|---|---|
| `NodeDefinition` | Immutable declaration: `metadata`, `inputs`, `outputs`, `config_schema`, `capabilities`, `metrics`, `events`. Invalid definitions fail at import time with `NodeContractError`. |
| `NodeMetadata` | `node_id` (`namespace.name`, lowercase), `name`, `description`, `version` (semver), `category`, `documentation_url`. |
| `NodeInput` | An input port: `name`, `port_type`, `required=True`, `nullable`, `multiple`, `schema`, `default` (optional inputs only), `description`. |
| `NodeOutput` | An output port: `name`, `port_type`, `nullable`, `multiple`, `schema`, `description`. Every declared output must be present in the result. |
| `PortType` | `STRING`, `NUMBER`, `BOOLEAN`, `JSON`, `OBJECT`, `LIST`, `BINARY`, `FILE`, `ARTIFACT`, `SECRET_REFERENCE`. |
| `NodeConfigSchema` | A JSON-Schema object. The supported keywords are `type` (a name, or a list such as `["integer", "null"]` for a nullable value), `required`, `properties`, `additionalProperties` (`False`, or a schema for map values), `items`, `enum`, `minimum`, `maximum`, `minLength`, `maxItems`, `pattern` and `format: CREDENTIAL_REFERENCE_FORMAT`, at every depth. Set `"additionalProperties": False` so a typo fails at publish. `title`, `description` and `default` (what the node does when the key is absent; it must be valid) are shown by the editor's inspector, which builds the settings form from this schema alone. `issues(config)` returns every violation as a `ConfigIssue`. |
| `CREDENTIAL_REFERENCE_FORMAT` | `"credential-reference"`. Declare it on every config key that names a credential (`{"type": "string", "format": CREDENTIAL_REFERENCE_FORMAT}`, or as `additionalProperties` of a map): values must be `credential:<name>`, and the inspector offers a credential picker instead of a text field. |
| `NodeCapabilities` | `supports_cancellation`, `has_side_effects` (relevant for retries and idempotency), `is_control_flow`, `streams_output`. |

### Execution context (`NodeExecutionContext`)

| Member | Purpose |
|---|---|
| `config` | The node instance's configuration (a copy of the frozen snapshot). |
| `inputs` | Validated input values by port name. |
| `logger` | A standard `logging.Logger` named `akasha.nodes.<node_id>`. |
| `metrics` | `increment(name, amount=1.0)` for counters declared in `definition.metrics`. |
| `events` | `emit(name)` for events declared in `definition.events`. |
| `resolve_credential(reference)` | Returns the secret behind a credential reference in `config`. |
| `is_cancelled()` | Poll this during long work; stop promptly when it returns `True`. |
| `execution_id`, `node_execution_id`, `node_instance_id`, `project_id`, `project_version` | Ids, for correlation only. |
| `runtime_metadata` | Includes `attempt` (1-based) and `engine_version`. |
| `execution_input` | The payload the execution started with. Entry nodes read it; other nodes use their ports. |

### Result and errors

| Name | Purpose |
|---|---|
| `NodeResult(outputs)` | The success value: a mapping from output port name to value. |
| `NodeError(message, kind=..., code=..., details=...)` | The only way to report failure. `code` is your stable, upper-case identifier. `details` values are strings. |
| `NodeErrorKind` | `VALIDATION`, `CONFIGURATION`, `PERMANENT` (not retryable); `TRANSIENT`, `TIMEOUT` (retryable); `CANCELLATION`. |
| `NodeContractError`, `NodeConfigurationError`, `NodeValidationError` | Raised by the definition, config and port checks. You meet them in tests. |

Any other exception from `execute` is recorded as `NODE_UNEXPECTED_ERROR`.
Only its type is kept; its message is dropped, because it cannot be
guaranteed secret-free. Raise `NodeError` for every failure you anticipate.

---

## 4. Logging

Use `context.logger` and put structured fields in `extra`:

```python
context.logger.info("payload_signed", extra={"bytes": size})
```

Each line automatically carries `execution_id`, `node_execution_id`,
`node_id`, `node_type`, `project_id` and `correlation_id`. Output is
redacted centrally.

Still, log sizes, counts and ids, never payloads. Workflow data can be
sensitive (AGENTS §76/§77).

Lines written through `context.logger` are also stored with the execution
and shown in the Execution Inspector (`GET /api/v1/executions/{id}/logs`):
per attempt, at most 200 lines of at most 4,000 characters, `extra` fields
up to 8 KiB, redacted like every other output (values you resolved from the
credential store are masked exactly). Lines below the process log level are
not stored either. Lines from other loggers (`logging.getLogger(__name__)`,
libraries) reach the process log only.

## 5. Metrics

Declare counter names in `definition.metrics`, then call
`context.metrics.increment(name, amount)`. Amounts must be finite and
`>= 0`.

The Engine exports them as
`akasha_node_metric_total{node_type="<node_id>", metric="<name>"}`, once
per attempt, including failed and cancelled attempts. An undeclared name
raises `NodeContractError`. This is what keeps label cardinality bounded:
metric names can never come from workflow data.

## 6. Events

Declare event names in `definition.events`, then call
`context.events.emit(name)`.

Each emitted name is persisted as a `node.emitted` event with payload
`{"name", "attempt"}`. It is ordered after `node.started` and before the
attempt's terminal event, and it is recorded for failed attempts too.
Emitted events are visible wherever execution events are: the event store,
subscribers and logs.

Limits:
- Events are name-only by design. Event payloads never carry workflow data.
  Put data in outputs.
- An attempt may emit at most `MAX_EVENTS_PER_ATTEMPT` (100) events.

### Retries, recovery and idempotency

An attempt of your node can run again in two cases: the execution's retry
policy allows another attempt after a `TRANSIENT`/`TIMEOUT` error, or the
worker died while the attempt ran. After a crash the Engine repeats the
attempt only if the retry policy allows it or your definition declares
`NodeCapabilities(has_side_effects=False)` (the default). Declare
`has_side_effects=True` for nodes that send, write or call out: an
interrupted attempt then fails the execution with `NODE_INTERRUPTED`
unless the user opted into retries.

`context.node_execution_id` is the same for every attempt of one node in
one execution. Send it as the idempotency key to systems that support one,
so a repeated attempt cannot duplicate the effect.

## 7. Credentials

The config holds references (`"credential:<name>"`), never secret values.
The Engine rejects a plaintext secret when the draft is saved. Resolve the
reference at execution time:

```python
secret = await context.resolve_credential(context.config["api_key_credential"])
```

Only references that appear in the node's own config resolve. This prevents
data flowing through the graph from choosing which secret a node unlocks.
Failures surface as `NodeError`:

| Code | Kind | Cause |
|---|---|---|
| `CREDENTIALS_UNAVAILABLE` | CONFIGURATION | The Engine runs without a credential store. |
| `CREDENTIAL_NOT_REFERENCED` | CONFIGURATION | The reference is not in this node's config. |
| `CREDENTIAL_NOT_FOUND` | CONFIGURATION | No credential exists with that name. |
| `CREDENTIAL_UNAVAILABLE` | PERMANENT | The stored value cannot be decrypted. |

Resolved values are masked in the node's logs, error messages and outputs.
Still, never return or log them on purpose.

---

## 8. Testing a Node

```python
from akasha.sdk import NodeError, check_node_contract
from akasha.sdk.testing import run_node


def test_contract() -> None:
    check_node_contract(WordCount)


async def test_counts() -> None:
    run = await run_node(WordCount(), inputs={"text": "a b c"})
    assert run.outputs == {"count": 3}
    assert run.metrics == {"words_counted": 3}
    assert run.events == []
```

`run_node(node, config=, inputs=, execution_input=, credentials=,
is_cancelled=)` applies the Engine's checks in the Engine's order:
1. contract;
2. plaintext secrets and the config schema;
3. inputs;
4. `execute`;
5. outputs.

It returns a `NodeRun(outputs, events, metrics)`. `credentials` maps
references to secret values and follows the Engine's access rule.
`NodeError` from `execute` propagates unchanged, so assert on it with
`pytest.raises`.

The harness does not reproduce timeouts, runtime cancellation or output
redaction. Those are Engine behaviour.

For logging, use pytest's `caplog`: the logger is
`akasha.nodes.<node_id>`.

---

## 9. Packaging a Node as a Plugin

A plugin is a directory that is also a Python package, with a manifest
`akasha-plugin.json` at its root. The Engine installs it without any change
to Engine code. Its nodes join the Node Registry like built-in nodes.
Example: [`backend/examples/plugins/text_tools`](../backend/examples/plugins/text_tools/).

```text
acme/
├── akasha-plugin.json
├── README.md
├── __init__.py
└── nodes.py          # class WordCount(Node): ...
```

```json
{
  "manifest_version": 1,
  "id": "acme",
  "name": "Acme Text Nodes",
  "version": "1.0.0",
  "description": "Word counting.",
  "engine": ">=0.1.0,<1.0.0",
  "sdk": ">=1.0.0,<2.0.0",
  "dependencies": [{"kind": "plugin", "id": "acme.base", "version": ">=1.0.0"}],
  "components": {"nodes": [{"id": "acme.word_count", "entry_point": "nodes:WordCount"}]},
  "documentation": "README.md"
}
```

| Field | Rule |
|---|---|
| `id` | Lowercase, e.g. `acme` or `community.web_tools`. It is also the installed directory name. |
| `version` | Semver of the plugin. |
| `engine`, `sdk` | Version constraints (`*` or `>= <= > < ==` clauses joined by commas), matched against the Engine version and `SDK_VERSION`. |
| `dependencies` | `kind` is `plugin` (default) or `node`, `id`, `version` constraint. A plugin dependency must be enabled first. |
| `components.nodes` | 1–100 node types. Each `id` must start with `<plugin id>.`, so a plugin cannot shadow `core.*` or another plugin's nodes. `entry_point` is `module:Class` inside the package. |
| `documentation` | Optional path of a file inside the package. |

Unknown fields and component kinds are rejected. V1 plugins provide node
types only. The manifest is validated before any plugin code is imported.

Lifecycle (`akasha.plugins.registry.PluginRegistry`, directory
`AKASHA_PLUGINS_DIR`):

| Step | Behaviour |
|---|---|
| install | validate manifest → check compatibility → copy (regular files only, no symlinks) → import → register nodes. On any failure nothing is left behind. |
| startup | discovers installed plugins, loads enabled ones after their dependencies; one that fails is marked `FAILED`, the Engine still starts. |
| disable / enable | unregisters / re-registers the nodes; the state survives restarts. Blocked (`PLUGIN_IN_USE`) while an enabled plugin depends on it. |
| remove | disables, then deletes the installed copy. Upgrade = remove + install. |

A project may declare `Dependency(kind="plugin")`. Validate and publish then
require an enabled plugin whose version satisfies the constraint.

Plugin code is trusted code, like any custom Node (§ trust model above).

Without a plugin, the Engine's composition root can still register a node
directly: `registry.register(MyNode, lambda: MyNode(client))`.

## 10. Compatibility

The names exported by `akasha.sdk` (`__all__`) and `akasha.sdk.testing`,
and their documented behaviour, are the SDK contract, versioned by
`SDK_VERSION` (semver):
- a removal, rename or behaviour change that breaks Nodes is a major bump;
- an addition is a minor bump.

`tests/test_sdk.py` pins the exported surface.
