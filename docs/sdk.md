# Python Node SDK

SDK version: **1.3.0** (`akasha.sdk.SDK_VERSION`)

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
How to set up a plugin project, develop it in VS Code against a local
engine and ship it: §11.

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
| Draft save / publish | the config holds no plaintext secrets | the workflow is rejected |
| Publish (and before every run) | the config matches `config_schema` | the workflow is rejected |
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
| `execution_id`, `node_execution_id`, `node_instance_id`, `workflow_id`, `workflow_version` | Ids, for correlation only. |
| `runtime_metadata` | Includes `attempt` (1-based) and `engine_version`. |
| `execution_input` | The payload the execution started with. Entry nodes read it; other nodes use their ports. |
| `resume` | `None`, except on the attempt after a `NodeWait` was answered or expired: `{"response", "responded_by", "timed_out"}`. |
| `services` | (1.3.0) The engine's `NodeServices`, the same for built-in and plugin nodes (ADR 0011): `llm` (an `LLMClient`, or `None`), `network` (the operator's `NetworkPolicy`; check every user-configured destination with `check_destination(host, port, context.services.network)`), `workspace_dir` (the workspace root, or `None`; reach files only through `Workspace(context.services.workspace_dir).resolve(context.workflow_id, path)`, which refuses paths outside the workflow's folder), `nodes` (the node catalog, for nodes that run other nodes). |
| `settings` | (1.3.0) The operator's settings for your plugin, from `AKASHA_PLUGIN_SETTINGS='{"acme": {...}}'` for the plugin with id `acme`, already checked against the manifest's `settings_schema`. Empty when none are set. Never secrets: use credentials. |

### Result and errors

| Name | Purpose |
|---|---|
| `NodeResult(outputs)` | The success value: a mapping from output port name to value. |
| `NodeWait(prompt, response_schema, timeout=None)` | (1.2.0) Ask for outside input instead of finishing: the execution waits persistently until it is answered (validated against `response_schema`, a `NodeConfigSchema`) or `timeout` passes, then `execute` runs again with `context.resume`. Only for nodes declaring `NodeCapabilities(waits_for_input=True)`; such nodes cannot be agent tools. `akasha.sdk.testing.run_node` covers result-returning attempts only. |
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
`node_id`, `node_type`, `workflow_id` and `correlation_id`. Output is
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
  "documentation": "README.md",
  "python_requirements": ["markdown==3.8"]
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
| `settings_schema` | Optional. The JSON Schema (the config-schema subset) of the operator settings your nodes read as `context.settings`. Settings for a plugin without one, or not matching it, keep the plugin from activating (`PLUGIN_SETTINGS_INVALID`). Use `"additionalProperties": false` so a misspelt key fails. |
| `python_requirements` | Optional. The third-party libraries your code imports, as PEP 508 requirements (`"markdown==3.8"`, `"httpx[http2]>=0.28,<1"`). At most 50. No URLs or paths. Do not list `akasha-engine`. See below. |

Unknown fields and component kinds are rejected. V1 plugins provide node
types only. The manifest is validated before any plugin code is imported.

Lifecycle (`akasha.plugins.registry.PluginRegistry`, directory
`AKASHA_PLUGINS_DIR`):

| Step | Behaviour |
|---|---|
| install | validate manifest → check compatibility → copy (regular files only, no symlinks) → install `python_requirements` → import → register nodes. On any failure nothing is left behind. |
| startup | discovers installed plugins, loads enabled ones after their dependencies, and reinstalls missing or stale libraries; one that fails is marked `FAILED`, the Engine still starts. |
| disable / enable | unregisters / re-registers the nodes; the state survives restarts. Blocked (`PLUGIN_IN_USE`) while an enabled plugin depends on it. |
| update | replaces the installed package with a new version of the same id, atomically (on failure the old one is restored); refused while workflows use the plugin unless forced. Published workflow versions stay pinned to the plugin version they were published with and refuse to run on another one (ADR 0006). Over MCP: `plugin_update`. |
| remove | disables, then deletes the installed copy and its libraries. |

Admins drive the same lifecycle over `/api/v1/plugins` and the Plugins page,
and AI agents over MCP (`docs/mcp.md` §6: authoring, static validation,
test runs, packaging, install and update; code-running steps need
`AKASHA_MCP_PLUGIN_CODE=true`).
Upload install is opt-in (`AKASHA_PLUGINS_HTTP_INSTALL`, ADR 0001). The
upload is a ZIP of the package, with the package at the root or in one
top-level directory.

A workflow may declare `Dependency(kind="plugin")`. Validate and publish then
require an enabled plugin whose version satisfies the constraint.

Plugin code is trusted code, like any custom Node (§ trust model above).

Without a plugin, the Engine's composition root can still register a node
directly: `registry.register(MyNode, lambda: MyNode(client))`.

### Python libraries (`python_requirements`)

A plugin may use any library from a package index. Declare it in the
manifest. The Engine installs it, and you never touch the Engine's
environment (ADR 0004):

| Question | Answer |
|---|---|
| Who installs? | The Engine, with `uv pip install --target <plugins_dir>/.deps/<plugin id>`. This runs on install, and again on startup or enable when that directory is missing or stale. Stale means it was resolved for other requirements, another Python or another Engine version. API and worker share the directory through the plugins volume. |
| What gets installed? | Wheels only (`--only-binary :all:`), for the Engine's Python and platform. That is Linux in the container, whatever your laptop runs. A library published only as an sdist cannot be used: ship its code inside your package instead. |
| From where? | uv's configuration on the Engine host: PyPI by default, otherwise `UV_DEFAULT_INDEX`, `UV_INDEX`, `UV_FIND_LINKS` (a wheelhouse directory), `UV_OFFLINE` or a uv.toml. In Compose, put them in `.env`. |
| Versions? | All plugins share the Engine's interpreter, so each library has one version per process. Every library the Engine itself has is pinned to the Engine's version, and every library another plugin installed is pinned to that plugin's version. Your requirement must admit that version, or the install fails. |
| Engine libraries? | Even if the Engine already has a library (`httpx`, `pydantic`, ...), declare it when you import it. Undeclared, it works only by accident and can change with any Engine release. |
| Removal? | Removing the plugin deletes its library directory. Modules already imported stay in memory until the process restarts. |
| Errors | `PLUGIN_DEPENDENCIES_FAILED` (422). `details.output` holds uv's explanation, for example which library conflicts with which version, with index credentials masked. The same error with `details.distributions` means a different version of a library is already loaded: restart the Engine, then install. |

Recommendations:
- Pin exact versions (`==`). Transitive libraries are resolved at install
  time. The resolved set is logged as `plugin_dependencies_installed` and
  stored in `.deps/<id>/akasha-requirements.json`.
- Prefer libraries with few dependencies. A heavy dependency tree is more
  likely to conflict with the Engine or another plugin.
- Plugin code is trusted, and so is every library it pulls in.

## 10. Compatibility

The names exported by `akasha.sdk` (`__all__`) and `akasha.sdk.testing`,
and their documented behaviour, are the SDK contract, versioned by
`SDK_VERSION` (semver):
- a removal, rename or behaviour change that breaks Nodes is a major bump;
- an addition is a minor bump.

`tests/test_sdk.py` pins the exported surface. Built-in nodes use only
this surface too (`tests/test_architecture.py`), so anything a core node
does, a plugin node can do.

1.3.0 added `context.services`, `context.settings` and the names they
need: `NodeServices`, `LLMClient`, `LLMRequest`, `LLMResponse`, `Message`,
`Role`, `ToolCall`, `ToolDefinition`, `TokenUsage`, `CostEstimate`,
`FinishReason`, `ProviderError`, `ProviderErrorKind`,
`LLM_PROVIDER_FORMAT`, `NetworkPolicy`, `check_destination`, `Workspace`,
`mask_known_secrets`, `redact_text`, `credential_references`,
`json_equal`, `NodeFactory`, `NO_DEFAULT`. `run_node` takes `services`
and `settings` to stand in for the engine's.

The manifest has its own format version (`manifest_version`). Additive
fields such as `python_requirements` are rejected by engines that predate
them (unknown fields fail validation), so the install fails loudly, never
silently without libraries.

---

## 11. Development workflow

This section walks through a complete plugin project: setup, editing in
VS Code with live type checking, unit tests, manual testing against a local
Docker engine with automatic reload, and deployment.

### 11.1 Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs Python 3.14, the version
  the Engine image runs.
- Docker with Compose ≥ 2.32 (for `docker compose watch`).
- VS Code with the extensions *Python* (`ms-python.python`), *Pylance*
  (`ms-python.vscode-pylance`) and *Ruff* (`charliermarsh.ruff`).
  *Mypy Type Checker* (`ms-python.mypy-type-checker`) is optional.
- A checkout of this repository: it provides the SDK for type checking and
  tests, and the Compose stack.

### 11.2 Project layout

Keep the plugin package in its own directory inside the project. The
package is exactly what gets installed. Tooling (`.venv`, tests, configs)
stays outside it.

```text
acme-nodes/                  your repository
├── pyproject.toml           dev environment (uv)
├── .python-version          3.14
├── compose.dev.yml          mounts the plugin into the local engine
├── .vscode/settings.json
├── acme/                    ← the plugin package (= plugin id)
│   ├── akasha-plugin.json
│   ├── README.md
│   ├── __init__.py
│   └── nodes.py
└── tests/
    └── test_nodes.py
```

Inside the package, import your own modules **relatively**
(`from .helpers import x`). The Engine imports the package under an internal
name, so `from acme.helpers import x` works in your tests but fails in the
Engine.

### 11.3 Setup

```bash
mkdir -p acme-nodes/acme acme-nodes/tests && cd acme-nodes
echo 3.14 > .python-version
```

`pyproject.toml`:

```toml
[project]
name = "acme-nodes"
version = "1.0.0"
requires-python = "==3.14.*"
# Runtime libraries. Keep them equal to python_requirements in
# acme/akasha-plugin.json (a test below checks this).
dependencies = ["markdown==3.8"]

[dependency-groups]
dev = [
    "akasha-engine",        # akasha.sdk + akasha.sdk.testing
    "pytest~=9.0",
    "pytest-asyncio>=0.25",
    "mypy~=1.18",
    "ruff~=0.14",
    "types-markdown",       # stubs for libraries without inline types
]

[tool.uv]
package = false             # an environment, not a distributable package

[tool.uv.sources]
# The engine you deploy to. A local checkout (edits are picked up live):
akasha-engine = { path = "../akasha-agent-engine/backend", editable = true }
# Or pinned from Git:
# akasha-engine = { git = "https://github.com/<org>/<repo>.git", subdirectory = "backend", rev = "<commit>" }

[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"

[tool.mypy]
strict = true
```

```bash
uv sync          # creates .venv with the SDK, the libraries and dev tools
```

To add a library later, run `uv add <lib>==<version>` and put the same
string into `python_requirements`.

`acme/akasha-plugin.json` and `acme/nodes.py`:

```json
{
  "manifest_version": 1,
  "id": "acme",
  "name": "Acme Nodes",
  "version": "1.0.0",
  "description": "Markdown rendering.",
  "engine": ">=0.1.0,<1.0.0",
  "sdk": ">=1.1.0,<2.0.0",
  "components": {
    "nodes": [{"id": "acme.markdown_to_html", "entry_point": "nodes:MarkdownToHtml"}]
  },
  "python_requirements": ["markdown==3.8"]
}
```

```python
import markdown

from akasha.sdk import (
    Node, NodeDefinition, NodeExecutionContext, NodeInput, NodeMetadata,
    NodeOutput, NodeResult, PortType,
)


class MarkdownToHtml(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="acme.markdown_to_html",
            name="Markdown to HTML",
            description="Renders Markdown as HTML.",
            version="1.0.0",
            category="text",
        ),
        inputs=(NodeInput("markdown", PortType.STRING),),
        outputs=(NodeOutput("html", PortType.STRING),),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        html: str = markdown.markdown(context.inputs["markdown"])
        return NodeResult({"html": html})
```

`acme/__init__.py` may be empty (a docstring is enough).

### 11.4 VS Code

Open the project folder (`code .`). Then run **Python: Select Interpreter**
and choose `./.venv/bin/python`. Pylance now resolves `akasha.sdk` and your
libraries and checks types as you type. `akasha` ships `py.typed`, so mypy
and pyright treat it as fully typed.

`.vscode/settings.json`:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.analysis.typeCheckingMode": "strict",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["tests"],
  "mypy-type-checker.importStrategy": "fromEnvironment",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": { "source.organizeImports": "explicit" }
  },
  "files.exclude": { "**/__pycache__": true }
}
```

What you get:

| Feedback | Where | When |
|---|---|---|
| Type errors (pyright strict) | Pylance, *Problems* panel | as you type |
| Type errors (mypy strict) | *Mypy Type Checker* extension, or `uv run mypy acme tests` | on save |
| Lint and format | *Ruff* extension | as you type, on save |
| Node contract (ids, ports, schema) | `check_node_contract` in a test | Testing panel ▶ |
| Behaviour | `run_node` tests; breakpoints work with *Debug Test* | Testing panel ▶ / 🐞 |

Contract errors (`NodeContractError`) are raised when the class is defined,
so even importing a broken node fails its tests.

### 11.5 The inner loop: tests

Most work happens here: fast, offline, with a debugger.

```python
# tests/test_nodes.py
import json
import tomllib
from pathlib import Path

from akasha.sdk import check_node_contract
from akasha.sdk.testing import run_node

from acme.nodes import MarkdownToHtml

ROOT = Path(__file__).parent.parent


def test_contract() -> None:
    check_node_contract(MarkdownToHtml)


async def test_renders_markdown() -> None:
    run = await run_node(MarkdownToHtml(), inputs={"markdown": "# Hi"})
    assert run.outputs == {"html": "<h1>Hi</h1>"}


def test_manifest_declares_the_runtime_libraries() -> None:
    manifest = json.loads((ROOT / "acme" / "akasha-plugin.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert manifest["python_requirements"] == project["project"]["dependencies"]
```

```bash
uv run pytest                 # or the Testing panel
uv run mypy acme tests
uv run ruff check . && uv run ruff format --check .
```

`run_node` applies the Engine's checks in the Engine's order (§8). What it
does not cover: the package import as a plugin, library installation,
publish, the runtime and the UI. For those, use the next step.

### 11.6 Manual testing against a local engine (live reload)

Run the Engine's Compose stack with your package mounted where installed
plugins live. On startup the Engine discovers the package like an installed
plugin and installs its `python_requirements` inside the container, into
the plugins volume. `docker compose watch` restarts API and worker whenever
a file in the package changes, so the next execution runs your new code.

`compose.dev.yml` (in your project):

```yaml
# Development only: runs this plugin inside a local Akasha stack.
x-dev-plugin: &dev-plugin
  volumes:
    - ${PWD}/acme:/var/lib/akasha/plugins/acme:ro
  develop:
    watch:
      # The engine loads plugins at startup: restart on every change.
      - path: ${PWD}/acme
        action: restart
        ignore: ["**/__pycache__/**"]

services:
  api: *dev-plugin
  worker: *dev-plugin
```

Paths are absolute (`${PWD}`): with several `-f` files, Compose resolves
relative paths against the first file's directory, which is the Engine
checkout.

```bash
export AKASHA_HOME=../akasha-agent-engine        # the engine checkout
alias akdev='docker compose -p akasha-dev -f "$AKASHA_HOME/docker-compose.yml" -f compose.dev.yml'

akdev up -d --build --wait      # first start: builds the images
akdev watch --no-up             # keep running: restarts api+worker on changes
```

Then:

1. Open <http://localhost:8080> and create the first admin. The stack is
   your own: `-p akasha-dev` keeps it apart from other stacks. If port 8080
   is taken, `export AKASHA_HTTP_PORT=8090` first.
2. The plugin appears on the Plugins page as `enabled`. If it is `failed`,
   read the reason in the logs: `akdev logs api | grep plugin_`.
3. Build a workflow with your node in the editor, publish it and run it.
   Inspect outputs, logs, events and metrics in the Execution Inspector.
4. Edit `acme/nodes.py` and save. Watch prints
   `service(s) ["api" "worker"] restarted`, which takes a few seconds. Run
   the workflow again to execute the new code.

Useful commands while the stack runs:

```bash
akdev exec api akasha plugin list
akdev exec api akasha node test acme.markdown_to_html --inputs '{"markdown": "# Hi"}'
akdev logs -f worker            # your context.logger lines, as JSON
akdev restart api worker        # what watch does, by hand
```

Notes:
- Changing `python_requirements` triggers a reinstall on the next start.
  Watch restarts on a manifest change, too.
- Breakpoints in the container are not set up. Debug with tests (§11.5),
  or with `context.logger` and the Execution Inspector.
- Do not remove the mounted plugin in the UI. The mount is read-only.
  Remove the mount from `compose.dev.yml` instead.
- `akdev down -v` deletes the stack, including its database and volumes.

### 11.7 Packaging and deployment

The deliverable is the package directory `acme/`, usually as a ZIP:

```bash
zip -r acme-1.0.0.zip acme -x '*/__pycache__/*' '*.pyc'
```

Install it on a target engine in either of two ways:

- **CLI** (always available). Run it in the engine's Compose directory,
  with the unpacked package at `/path/to/acme`:

  ```bash
  docker compose run --rm -v /path/to/acme:/tmp/plugin:ro api plugin install /tmp/plugin
  docker compose restart api worker
  ```

- **UI**, Plugins page → upload the ZIP. This works only with
  `AKASHA_PLUGINS_HTTP_INSTALL=true` (ADR 0001). Afterwards run
  `docker compose restart worker`.

Either way, the Engine installs the `python_requirements` from its
configured index. An air-gapped host can use a wheelhouse instead:

1. Download the Linux wheels:
   `uvx pip download --only-binary :all: --platform manylinux_2_28_x86_64
   --python-version 3.14 -d wheels markdown==3.8`.
2. Copy them to the host.
3. Mount them into `api` and `worker`, and set `UV_FIND_LINKS=/wheels` and
   `UV_OFFLINE=1`.

Upgrade: bump `version` in the manifest (and each changed node's
`metadata.version`), then remove and install. Workflows keep working as
long as the node ids and their compatibility constraints still match. When
the new version needs a different version of a library the engine already
loaded, restart between remove and install (§9, `details.distributions`).

Checklist before shipping:
- `uv run pytest`, `uv run mypy acme tests` and `uv run ruff check .` pass;
- `python_requirements` equals `[project].dependencies` and is pinned;
- the manifest `version` is bumped, and so is every changed node's
  `metadata.version`;
- `engine` and `sdk` constraints match what you tested against;
- the package was exercised once on a local engine (§11.6).
