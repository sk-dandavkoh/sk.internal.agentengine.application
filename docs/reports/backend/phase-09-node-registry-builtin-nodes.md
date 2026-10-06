# Phase 09 — Node Registry + Built-in Nodes

## 1. Executive Summary

**Goal.** Roadmap §13 asks for a unified Node Registry (NodeRegistry,
NodeResolver, NodeMetadataRegistry) and the first production nodes
(`core.input` … `core.smtp`), all executable through the existing Phase 08
runtime.

**Registry.** One `akasha.nodes.registry.NodeRegistry` class covers all three
roadmap roles:
- register / unregister;
- get / resolve with a version constraint;
- sorted metadata listing;
- two live read-only views, `definitions` and `catalog`.

**Built-in nodes.** All ten roadmap nodes exist. They are registered by
`akasha.nodes.builtins.create_builtin_registry`, and the composition root
builds that registry from settings. `WorkflowService` in production therefore
validates against real node definitions. Before this phase the production
catalog was `{}`, so nothing could be published.

**Foundations added to make the nodes work:**
- **Generic control flow.** A node with the `is_control_flow` capability may
  omit declared outputs. An omitted port is an inactive branch, and the
  runtime skips (transitively) the nodes that depend only on it.
- **Execution input.** `Execution.input`, migration `0004_execution_input`,
  exposed to nodes as `NodeExecutionContext.execution_input`.
- **Node-version compatibility.** `Dependency` constraints are checked on the
  draft, at publish and before every run. The resolved versions are recorded
  in `ExecutionMetadata.node_versions`.
- **SSRF destination guard.** `akasha.security.network`.
- **Config-schema keywords.** The contract now enforces a few more JSON
  Schema keywords.

**Verification.**
- 753 tests pass; the baseline before this phase was 559.
- Real services only:
  - PostgreSQL (Testcontainers);
  - Mailpit (Testcontainers);
  - an in-process loopback HTTP server.
- ruff, ruff format, mypy (strict) and `uv build` pass.

**Acceptance criterion** ("Nodes are discoverable through the registry and
executable through the same runtime"): satisfied, with evidence in §14. Real
limitations remain; the most important are:
- the DNS-rebinding window in the SSRF guard;
- SMTP sends cannot be interrupted;
- no credential resolver exists before Phase 13, so authenticated SMTP cannot
  run through the runtime yet;
- `ExecutionRuntime` still has no production caller.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

### Roadmap (`/docs/roadmap-backend.md` §13)

> **Goal:** Create the unified Node Registry and first production Nodes.
> **Scope:** NodeRegistry, NodeResolver, NodeMetadataRegistry; built-in
> nodes core.input, core.output, core.log, core.if, core.switch, core.delay,
> core.merge, core.http, core.web_search, core.smtp ("may be adjusted
> according to implementation feasibility").
> **Tests:** registration, lookup, version compatibility, Node execution,
> schema validation.
> **Acceptance Criteria:** Nodes are discoverable through the registry and
> executable through the same runtime.

### Phase prompt (§37)

- Use the existing Node contract and the existing execution runtime.
- Do not create a second execution mechanism.
- Create Node contract tests and runtime integration tests.
- Do not implement the Python SDK or the Plugin System.

### Session instructions (additional)

- HTTP and SMTP tests must use local test infrastructure.
- Web search must have deterministic test boundaries.
- There must be no special-case runtime logic for individual nodes.

### Explicitly not this phase's responsibility

- Triggers (Phase 10) and events (Phase 11).
- Credentials and redaction (Phase 13).
- The LLM node (Phase 15), the Python SDK (Phase 16) and plugins (Phase 17).
- Workers and queue (Phase 22), retries and recovery (Phase 23), resource
  limits (Phase 24), security hardening (Phase 25).

---

## 3. Scope Implemented

### Registry
- `NodeRegistry` (`nodes/registry.py`):
  - `register(node_cls, factory=None)` runs `check_node_contract`, checks
    once that the factory builds instances of `node_cls`, and rejects
    duplicate ids.
  - `unregister`, `get`, `resolve(node_id, constraint="*")`, `list_nodes()`
    (sorted by id).
  - Live read-only views: `definitions: Mapping[str, NodeDefinition]` and
    `catalog: Mapping[str, NodeFactory]`, both `MappingProxyType`.
- `NodeRegistration` (a frozen dataclass: definition + factory, and itself
  callable).
- Errors: `NodeNotFoundError` (`NODE_NOT_FOUND`, 404),
  `NodeAlreadyRegisteredError` (`NODE_ALREADY_REGISTERED`, 409),
  `NodeVersionIncompatibleError` (`NODE_VERSION_INCOMPATIBLE`, 409).

### Built-in nodes
- `nodes/core.py`: `InputNode`, `OutputNode`, `LogNode`, `DelayNode`,
  `MergeNode`, and the shared helpers `closed_schema`, `value_in`,
  `VALUE_OUT`.
- `nodes/control.py`: `IfNode` (8 operators), `SwitchNode` (5 case ports +
  default).
- `nodes/http.py`: `HttpNode`, `WebSearchNode` (SearXNG), and the shared
  `fetch`, `decode_body` and `status_error`.
- `nodes/smtp.py`: `SmtpNode`, and the blocking helper `_send` (runs in a
  thread).
- `nodes/builtins.py`: `create_builtin_registry(network, web_search_url,
  http_max_response_bytes)`.

### Domain / contract
- **`domain/node.py`:**
  - the `NodeFactory` protocol;
  - `json_equal`;
  - new config-schema keywords: `enum`, `minimum`, `maximum`, `minLength`,
    `maxItems`, `pattern`;
  - control-flow nodes may omit outputs;
  - `NodeExecutionContext.execution_input`;
  - an optional `code` on `NodeError`.
- **`domain/primitives.py`:** `semver_key`, `version_satisfies`.
- **`domain/graph_validation.py`:** new `ValidationCode` values
  `MISSING_DEPENDENCY`, `INCOMPATIBLE_DEPENDENCY`, `INVALID_DEPENDENCY`.
- **`domain/workflow.py`:**
  - `validate_dependencies`;
  - the shared `_validate`;
  - `WorkflowDraft.validate` now includes dependencies;
  - new `WorkflowVersion.validate`.
- **`domain/execution.py`:**
  - `ExecutionMetadata.node_versions`;
  - `Execution.input`, validated as storable JSON;
  - `Execution.create(input=…)`;
  - `Execution.start(node_versions=…)`.

### Runtime
- **`runtime/plan.py`:**
  - `NodeCatalog = Mapping[str, NodeFactory]`;
  - `ExecutionPlan.readiness` replaces `ready_after`;
  - `ExecutionPlan.node_versions`;
  - `inputs_for` uses live edges only (via `_live`);
  - `load_plan` calls `WorkflowVersion.validate`, so dependencies are
    included.
- **`runtime/runtime.py`:**
  - `_advance`: breadth-first READY/SKIPPED decisions with skip cascade;
  - `node_skipped` log event;
  - passes `execution_input`;
  - records `node_versions` at start;
  - does not advance after a failure in the same batch.
- **`runtime/executor.py`:** type change only, `type[Node]` → `NodeFactory`.

### Security
- `security/network.py`: `NetworkPolicy(allow_private_networks,
  allowed_hosts)` and `check_destination(host, port, policy)`.

### Persistence
- **Migration `0004_execution_input`:** adds the
  `executions.input JSONB NOT NULL DEFAULT '{}'::jsonb` column.
- **`db_models.executions`:** gains the `input` column.
- **Repository:**
  - writes `input` on insert only;
  - reads `input`;
  - (de)serializes `metadata.node_versions`, defaulting to `{}` for older
    rows.

### Application / composition
- `ExecutionService.create_execution(input=…)`.
- `bootstrap.Container.nodes`.
- `bootstrap.create_node_registry(settings)`.
- `WorkflowService` is built with `nodes.definitions`.
- `config.Settings` gains four settings; see §18.

### Testing
- Nine new test modules, one new support module and test-package
  `__init__.py` files; see §4 and §12.
- The architecture guard now forbids `nodes` from importing `api`,
  `application`, `infrastructure` or `runtime`.

---

## 4. Files Changed

All paths are relative to the repository root.

### Created

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/nodes/registry.py` | Created (135 lines) | NodeRegistry, NodeRegistration, registry errors |
| `backend/src/akasha/nodes/builtins.py` | Created (46) | Registers the ten built-ins with their dependencies |
| `backend/src/akasha/nodes/core.py` | Created (205) | input, output, log, delay, merge |
| `backend/src/akasha/nodes/control.py` | Created (149) | if, switch |
| `backend/src/akasha/nodes/http.py` | Created (418) | http, web_search, bounded `fetch` |
| `backend/src/akasha/nodes/smtp.py` | Created (235) | smtp |
| `backend/src/akasha/security/network.py` | Created (79) | SSRF destination guard |
| `backend/migrations/versions/0004_execution_input.py` | Created (40) | `executions.input` column |
| `backend/tests/support.py` | Created (253) | Shared helpers: `RunNode`, `make_context`, loopback HTTP server, Mailpit container, `docker_available` |
| `backend/tests/nodes/conftest.py` | Created | `run_node` fixture (contract-boundary driver) |
| `backend/tests/nodes/test_registry.py` | Created (10 tests) | Registry lifecycle, lookup, versions |
| `backend/tests/nodes/test_builtin_contract.py` | Created (17) | Shared contract suite and settings wiring |
| `backend/tests/nodes/test_core_nodes.py` | Created (23) | Core node behaviour |
| `backend/tests/nodes/test_control_nodes.py` | Created (30) | if/switch behaviour |
| `backend/tests/nodes/test_http_node.py` | Created (35) | HTTP node and SSRF |
| `backend/tests/nodes/test_web_search_node.py` | Created (18) | Web search |
| `backend/tests/nodes/test_smtp_node.py` | Created (21) | SMTP against Mailpit |
| `backend/tests/integration/test_builtin_nodes_runtime.py` | Created (16) | Full stack with the built-ins |
| `backend/tests/__init__.py`, `tests/{domain,integration,nodes,runtime}/__init__.py` | Created (empty) | Makes `tests.support` importable (§10, P5) |
| `docs/reports/backend/phase-09-node-registry-builtin-nodes.md` | Created | This report |

New directory: `backend/tests/nodes/`.

### Modified

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/node.py` | Modified (+109/−) | NodeFactory; schema keywords; control-flow outputs; `execution_input`; `NodeError.code`; `json_equal`; message fix (§11, F1) |
| `backend/src/akasha/domain/primitives.py` | Modified | Semver key and constraint matching |
| `backend/src/akasha/domain/workflow.py` | Modified | Dependency validation, `WorkflowVersion.validate` |
| `backend/src/akasha/domain/graph_validation.py` | Modified | Three new codes; docstrings updated |
| `backend/src/akasha/domain/execution.py` | Modified | `input`, `node_versions`, `start(node_versions)` |
| `backend/src/akasha/runtime/plan.py` | Modified | Factory catalog, readiness/skip, live-edge inputs, dependency re-validation |
| `backend/src/akasha/runtime/runtime.py` | Modified | `_advance`, execution input, node versions |
| `backend/src/akasha/runtime/executor.py` | Modified | Type annotation only |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | `input` column |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | Persist `input` and `node_versions` |
| `backend/src/akasha/application/executions.py` | Modified | `create_execution(input=…)` |
| `backend/src/akasha/application/workflows.py` | Modified | Docstring only |
| `backend/src/akasha/bootstrap.py` | Modified | Registry wiring |
| `backend/src/akasha/config.py` | Modified | Four new settings |
| `backend/src/akasha/nodes/__init__.py` | Modified | Package docstring and dependency rules |
| `backend/pyproject.toml`, `backend/uv.lock` | Modified | httpx moved from dev to runtime dependencies |
| `backend/tests/conftest.py` | Modified | `http_server`, `mailpit`, `mailbox` fixtures |
| `backend/tests/integration/conftest.py` | Modified | `_docker_available` moved to `tests.support.docker_available` |
| `backend/tests/integration/test_persistence.py` | Modified | `_HEAD` = `0004_execution_input` |
| `backend/tests/integration/test_execution_persistence.py` | Modified | Round-trip extended; new 0003→0004 upgrade test |
| `backend/tests/runtime/test_runtime_units.py` | Modified | `ready_after` test replaced by readiness/skip tests (+4) |
| `backend/tests/domain/test_node_contract.py` | Modified | +4 tests |
| `backend/tests/domain/test_primitives_and_graph.py` | Modified | Semver tests (+3) |
| `backend/tests/domain/test_workflow.py` | Modified | Dependency validation (+6) |
| `backend/tests/domain/test_execution.py` | Modified | Input and node_versions (+6) |
| `backend/tests/test_architecture.py` | Modified | Internal rule for `nodes` |
| `backend/README.md` | Modified | Settings table; test infrastructure note |
| `docs/architecture.md` | Modified | §11, §14, §37, §53, §63 |

Deleted: none. Generated files: only `uv.lock`, via `uv add` / `uv remove`.

---

## 5. Architecture Changes

### 5.1 One registry class for the three roadmap names
- **Decision:** `NodeRegistry` implements all three roles:
  - registration (NodeRegistry);
  - resolution (NodeResolver: `get`, `resolve`);
  - metadata (NodeMetadataRegistry: `list_nodes`, `definitions`).
- **Context:** the roadmap names three components, but they would all wrap
  the same id → definition/implementation map.
- **Alternatives:** three classes sharing one store.
- **Reason:** separate objects over one map add synchronization points and
  ways to disagree, without any behavioural gain. Architecture §37 asks for
  "a unified lookup mechanism".
- **Consequences:**
  - Plugins (Phase 17) register into the same object.
  - If a real separation becomes necessary (for example, read-only access
    for the API), it can be introduced then.

### 5.2 One version per node type id; compatibility through workflow dependencies
- **Decision:** the registry holds exactly one version per node id.
  Compatibility is expressed by `Dependency(kind="node", name, version_constraint)`
  on the workflow and is checked:
  - in `WorkflowDraft.validate` (validate, publish);
  - in `WorkflowVersion.validate`, called by `load_plan` before every run.
- **Context:**
  - `NodeInstance` has no version field.
  - Architecture §3.8 and §144 require node-version checks at publish and
    execution.
- **Alternatives:**
  - side-by-side versions per id: ambiguous without a version on the
    instance, and a contract change;
  - a version on `NodeInstance`: a Workflow schema change, with no current
    consumer.
- **Reason:** this is the smallest model consistent with the existing
  `Dependency` concept (from Phase 02, previously unused).
- **Consequences:**
  - Upgrading a built-in version affects every workflow that does not pin a
    constraint.
  - An incompatible upgrade fails queued runs with `EXECUTION_GRAPH_INVALID`
    before any node runs.

### 5.3 Generic control flow via the `is_control_flow` capability
- **Decision:**
  - A node declaring `NodeCapabilities.is_control_flow=True` may omit
    declared outputs (`NodeDefinition._validate_ports`).
  - `ExecutionPlan.readiness` marks a node SKIPPED when it has no live edge
    or a required input without a live edge. Skips cascade.
- **Context:**
  - The Phase 08 runtime treated every edge as a hard dependency.
  - Architecture §14 requires control flow without hard-coding node types
    ("prefer explicit interfaces and capabilities").
  - The Phase 08 report (§25) identified readiness and the verdict as the
    extension points.
- **Alternatives:**
  - per-port "optional" flags on `NodeOutput`, which is a larger contract
    change;
  - runtime branches on `node_type in {"core.if", "core.switch"}`, which is
    forbidden (backend AGENTS §23).
- **Reason:** it reuses the existing, previously unused capability flag and
  the existing `skip` transition from Phase 07.
- **Consequences:**
  - Any future routing node (plugins included) gets branching for free.
  - A node that consumes only optional inputs from a dead branch is skipped,
    not run with defaults.
  - Loops still need a separate design, and cycle rejection is unchanged.
  - Try/Catch will still need a change to `_verdict` / `_end`.

### 5.4 Node dependency injection through factories
- **Decision:** the runtime catalog maps node ids to `NodeFactory`
  (`definition` + `__call__() -> Node`). Nodes that need dependencies
  (`HttpNode(policy, max_response_bytes)`, `WebSearchNode(endpoint)`,
  `SmtpNode(policy)`) are registered with closures.
- **Context:**
  - The Phase 08 executor instantiated `node_cls()` with no arguments.
  - HTTP and SMTP need an operator-configured network policy, and web search
    needs an endpoint.
  - Backend AGENTS §21 forbids global state for nodes.
- **Alternatives:**
  - class attributes set by dynamically created subclasses (clever, and
    hard to trace);
  - typed services inside `NodeExecutionContext` (per-node fields in a
    shared context).
- **Reason:**
  - A `Node` class structurally satisfies the protocol, so no existing
    catalog or test had to change.
  - A fresh instance per attempt is preserved.
- **Consequences:** plugins and the SDK can register factories the same way.

### 5.5 Execution input introduced ahead of the trigger system
- **Decision:**
  - Add `Execution.input` (a JSON object), the migration
    `0004_execution_input`, `ExecutionService.create_execution(input=…)` and
    `NodeExecutionContext.execution_input`.
  - `core.input` reads it.
- **Context:** `core.input` is Phase 09 scope, but no execution payload
  existed before this phase. Triggers (Phase 10) will supply payloads.
- **Alternatives:** a config-constant `core.input`. That would have been a
  "Set" node, not an input node.
- **Reason:** AGENTS §3.1 allows "the smallest stable foundation necessary",
  documented.
- **Consequences:**
  - Phase 10 only passes payloads through.
  - Every node can see `execution_input`. It is an explicit context field,
    but it is not restricted to entry nodes.

### 5.6 Node versions recorded at run start
- **Decision:** `Execution.start(node_versions=plan.node_versions)` stores
  the resolved node type → version map in `ExecutionMetadata`, which is
  persisted inside the existing `metadata` JSONB document.
- **Reason:**
  - It records what actually ran, not what was expected at queue time.
  - It needs no schema change.
- **Consequences:**
  - `node_versions` is empty for queued or never-started executions.
  - Rows written before 0004 read as `{}`.

### 5.7 SSRF policy: default-deny for private destinations
- **Decision:** destinations that users configure (core.http and every
  redirect hop, core.smtp host) must resolve only to global addresses.
- **Operator overrides:** `AKASHA_NETWORK_ALLOW_PRIVATE`, and
  `AKASHA_NETWORK_ALLOWED_HOSTS` for exact, case-insensitive host names.
- **Trusted endpoints:** operator-configured endpoints
  (`AKASHA_WEB_SEARCH_URL`) are not checked.
- **Consequences:**
  - Self-hosted installs that talk to LAN services must opt in.
  - The DNS-rebinding gap is documented (§20 L1).

### 5.8 Stdlib `smtplib` instead of an async SMTP client
- **Decision:** `smtplib` inside `asyncio.to_thread`.
- **Alternatives:** `aiosmtplib`, which would be a new dependency.
- **Reason:** no new dependency (backend AGENTS §3 and AGENTS §53); the
  thread is bounded by the socket timeout.
- **Consequences:**
  - Cancellation and timeouts stop waiting immediately, but the SMTP
    dialogue in the thread completes. The node therefore declares
    `supports_cancellation=False` (§20 L2).

Architecture documentation is updated in `docs/architecture.md` §11, §14,
§37, §53 and §63. No ADR was written: none of these decisions contradicts the
documented architecture; they refine it.

---

## 6. Domain / Data Model Changes

### Domain

| Object | Change | Compatibility |
|---|---|---|
| `NodeFactory` (Protocol) | New | `type[Node]` satisfies it structurally |
| `NodeExecutionContext.execution_input` | New field, default `{}` | Additive |
| `NodeError.__init__(…, code=None)` | New optional kwarg; instance-level `code` override | Additive; the default stays `NODE_EXECUTION_ERROR` |
| `NodeDefinition.validate_outputs` | Control-flow nodes may omit outputs; message now says "missing required output" | Behaviour unchanged for non-control-flow nodes |
| `NodeConfigSchema.validate_config` | Enforces `enum`, `minimum`, `maximum`, `minLength`, `maxItems`, `pattern` | Schemas that used these keywords were previously *not* enforced; now they are |
| `json_equal` | New function | – |
| `semver_key`, `version_satisfies` | New functions | – |
| `ValidationCode` | `MISSING_DEPENDENCY`, `INCOMPATIBLE_DEPENDENCY`, `INVALID_DEPENDENCY` | Additive enum values (also visible in the API) |
| `validate_dependencies`, `WorkflowVersion.validate` | New | `WorkflowDraft.validate` now also reports dependency issues |
| `ExecutionMetadata.node_versions` | New `Mapping[str, str]`, default `{}`, copied | Additive |
| `Execution.input` | New `dict[str, Any]`, default `{}`; must be a JSON object without NaN/Infinity/NUL | Additive; invalid input raises `DomainValidationError` |
| `Execution.create(input=…)` | New kwarg, deep-copied | Additive |
| `Execution.start(node_versions=…)` | New kwarg | Additive |

No state enum or transition table changed. SKIPPED and `skip_node` already
existed (Phase 07) and are now used for the first time.

### Database (migration `0004_execution_input`, revises `0003_executions`)

| Object | Change |
|---|---|
| `executions.input` | New column `JSONB NOT NULL DEFAULT '{}'::jsonb`. Existing rows are backfilled with `{}` by the default. `downgrade` drops the column. |
| `executions.metadata` document | New optional key `node_versions` (no DDL) |

No new indexes or constraints were added.

Compatibility is tested by:
- `test_migrations_upgrade_downgrade_upgrade_on_empty_database` (now to head
  0004);
- `test_upgrade_from_0003_keeps_existing_executions_runnable`.

---

## 7. API / Contract Changes

### HTTP API
- No endpoints were added, changed or removed.
- The OpenAPI `ValidationCode` enum (used by `ValidationIssueOut.code` in the
  validate and publish responses) gains `missing_dependency`,
  `incompatible_dependency` and `invalid_dependency`. This was verified by
  generating the schema via `create_app(...).openapi()`.
- The change is additive, but consumers with exhaustive enum handling must
  add the three values.
- Behavioural change: `POST /api/v1/workflows/{id}/validate` and `…/publish`
  now accept graphs of `core.*` nodes in production (catalog wired). Before,
  every node was reported as `unknown_node_type`.

### Node contract (for built-ins, the future SDK and plugins)
- `NodeFactory` protocol for registration and the runtime catalog.
- The `is_control_flow` capability now has runtime semantics: omitted output
  = inactive branch.
- More schema keywords are enforced (see §6).
- `NodeError(code=…)` gives stable, node-specific error codes. They surface
  as `ExecutionError.code`.

### Node catalogue contract

| Node | Inputs | Outputs | Required config |
|---|---|---|---|
| `core.input` | – | `value` JSON? | – (`field` optional) |
| `core.output` | `value` JSON? | `value` JSON? | – |
| `core.log` | `value` JSON? | `value` JSON? | `message` (`level`, `include_value` optional) |
| `core.if` | `value` JSON? | `true` / `false` JSON? | `operator` (`operand`) |
| `core.switch` | `value` JSON? | `case_0`…`case_4`, `default` JSON? | `cases` (≤5) |
| `core.delay` | `value` JSON? (optional) | `value` JSON? | `seconds` 0–3600 |
| `core.merge` | `items` JSON? multiple | `items` LIST | – |
| `core.http` | `body` JSON? (optional) | `status` NUMBER, `headers` OBJECT, `body` JSON? | `url` (`^https?://`); optional `method`, `headers`, `timeout_seconds` 0.1–120, `follow_redirects`, `fail_on_error_status` |
| `core.web_search` | `query` STRING | `results` LIST of `{title,url,snippet}` | – (`max_results` 1–20) |
| `core.smtp` | `subject`, `body` STRING | `message_id` STRING, `rejected_recipients` LIST | `host`, `sender`, `recipients` (≤50); optional `port`, `security`, `username`, `password_credential`, `timeout_seconds` |

All ten definitions:
- use version `1.0.0`;
- use closed config schemas (`additionalProperties: false`).

### Node error codes introduced

| Area | Codes |
|---|---|
| core.input | `INPUT_FIELD_MISSING` |
| core.if | `CONDITION_TYPE_MISMATCH`, `CONDITION_OPERAND_MISSING` |
| core.http (and web search, via `fetch`) | `HTTP_STATUS_ERROR`, `HTTP_TIMEOUT`, `HTTP_CONNECTION_ERROR`, `HTTP_MALFORMED_RESPONSE`, `HTTP_RESPONSE_TOO_LARGE`, `HTTP_TOO_MANY_REDIRECTS`, `HTTP_INVALID_URL`, `HTTP_INVALID_HEADERS` |
| core.web_search | `SEARCH_NOT_CONFIGURED`, `SEARCH_EMPTY_QUERY`, `SEARCH_PROVIDER_ERROR`, `SEARCH_MALFORMED_RESPONSE` |
| core.smtp | `SMTP_NO_RECIPIENTS`, `SMTP_CREDENTIALS_UNAVAILABLE`, `SMTP_INVALID_MESSAGE`, `SMTP_AUTH_FAILED`, `SMTP_NOT_SUPPORTED`, `SMTP_REJECTED`, `SMTP_TEMPORARY_FAILURE`, `SMTP_TLS_ERROR`, `SMTP_TIMEOUT`, `SMTP_CONNECTION_ERROR` |
| Network guard | `DESTINATION_INVALID`, `DESTINATION_BLOCKED`, `DNS_ERROR` |

### Application contracts
- `ExecutionService.create_execution(workflow_id, version, *, input=None, metadata=None, timeout_policy=None)`.
- `ExecutionRuntime(uow, catalog)` now takes `Mapping[str, NodeFactory]`.
- `Container.nodes: NodeRegistry`.

---

## 8. Implementation Details

### Readiness and skip propagation
- `ExecutionPlan.readiness(node_id, execution)`:
  - returns `None` while any upstream node is neither COMPLETED nor SKIPPED;
  - otherwise returns READY if `fed` is non-empty and every required input
    port is in `fed`;
  - otherwise returns SKIPPED.
- `fed` is the set of target ports with a live edge.
- `_live(edge)` means: the source is COMPLETED and `edge.source_port` is in
  the source's outputs.
- `ExecutionRuntime._advance` runs after each batch of completions without a
  failure. It walks downstream breadth-first (`deque`, downstream ids sorted):
  - PENDING nodes are marked READY or SKIPPED;
  - skipped nodes are enqueued so their own downstream is decided.
- `inputs_for` ignores dead edges. `multiple` inputs therefore collect only
  live values, and defaults still apply to unfed optional ports.
- Order of changes in `_schedule`: `_record` → (if no failure) `_advance` →
  next loop iteration: `_verdict` → `_launch` (persists RUNNING) or save if
  dirty. Skips are persisted with the next save; the single-writer loop and
  CAS saves are unchanged.
- **Behaviour change vs. Phase 08:** when a batch contains a failure,
  downstream nodes of completed siblings are no longer marked READY before
  the fail-fast stop. The end state is the same (CANCELLED via `_finish`).

### Registration lifecycle
- `register`:
  1. `check_node_contract(node_cls)`;
  2. calls the factory once and checks `isinstance` against `node_cls`
     (catches wiring mistakes at startup);
  3. rejects a duplicate id;
  4. stores the entry.
- `unregister` affects new publishes and runs. A running execution keeps the
  implementations captured in its `ExecutionPlan`.

### Version check
- `version_satisfies` accepts:
  - `*`, or
  - comma-separated clauses `(>=|<=|>|<|==|=)?MAJOR.MINOR.PATCH[-pre]`.
- A missing operator means `==`.
- The key is `(major, minor, patch, 0 if pre else 1, pre)`, and build
  metadata is ignored.
- A malformed constraint is a validation issue (`INVALID_DEPENDENCY`), not an
  exception at construction. This keeps persisted workflows loadable.

### HTTP (`nodes/http.py`)
- **`fetch`:**
  - wraps the whole exchange, across all redirect hops, in
    `asyncio.timeout`;
  - `httpx.AsyncClient(timeout=…, follow_redirects=False, trust_env=False, verify=_tls())`;
  - builds the request, then per hop: `_check_url` (scheme http/https, no
    userinfo, `check_destination`) → `client.send(stream=True)` →
    `next_request` when redirects are enabled → max 5 hops.
- **`_read_limited`:** rejects an oversized `Content-Length` before reading,
  and counts streamed bytes.
- **Exception mapping:**

  | Exception | Code | Kind |
  |---|---|---|
  | timeout | `HTTP_TIMEOUT` | TIMEOUT |
  | `InvalidURL` | `HTTP_INVALID_URL` | CONFIGURATION |
  | `TransportError` | `HTTP_CONNECTION_ERROR` | TRANSIENT |
  | other `HTTPError` | `HTTP_MALFORMED_RESPONSE` | PERMANENT |

  Messages contain only the exception type name, never the request.
- **`status_error`:** 429 and ≥500 are TRANSIENT; other ≥400 are PERMANENT.
- **`decode_body`:** JSON media types (`application/json`, `*+json`) are
  parsed, and invalid JSON → `HTTP_MALFORMED_RESPONSE`. Anything else is
  decoded as text with the declared charset or UTF-8, `errors="replace"`.
- **Body input:** `HttpNode` serializes the `body` input as JSON. It adds
  `content-type: application/json` only when the user did not set a
  Content-Type in any casing.
- **`WebSearchNode`:**
  - `GET {endpoint}/search?q=…&format=json` with a trusted policy;
  - `_normalize` accepts only a dict with a list `results`, keeps items with
    an http(s) `url`, coerces `title` and `content` to strings, then applies
    `max_results`.
- **TLS context:** `_tls()` is a `functools.cache`d
  `ssl.create_default_context()` (system trust store); see §9 F4.

### SMTP (`nodes/smtp.py`)
- **Before connecting:**
  1. resolve the password: `password_credential` → `context.credentials.resolve`,
     or `SMTP_CREDENTIALS_UNAVAILABLE` if no resolver;
  2. build the `EmailMessage`. `policy.default` rejects CR/LF in the subject
     → `SMTP_INVALID_MESSAGE`. `Message-ID = <node_execution_id@akasha>`;
  3. `check_destination`;
  4. only then `asyncio.to_thread(_send, …)`.
- **`_send`:**
  - one connection per send, closed by `with`;
  - STARTTLS is mandatory when `security="starttls"` (no plaintext
    fallback);
  - returns the refused-recipient map. Partial refusal → success with
    `rejected_recipients`.
- **Timeout mapping:** `SMTPServerDisconnected` whose `__context__` is a
  `TimeoutError` is mapped to `SMTP_TIMEOUT` (see §10, B1).

### Network guard (`security/network.py`)
- `loop.getaddrinfo(host, port, SOCK_STREAM)`.
  - `gaierror` → `DNS_ERROR`: TRANSIENT if `EAI_AGAIN`, else PERMANENT.
- A host is blocked if any resolved address is not `is_global` or is
  multicast (after stripping the IPv6 scope and unwrapping IPv4-mapped
  addresses).
- The allowlist is checked by exact, case-insensitive host name.
- Error details name the host, never the resolved IPs.

### Logging
- **`core.log`** writes `logger.log(level, message, extra={event: "node_log",
  execution_id, node_execution_id, node_id, value_type[, value]})` via the
  context logger `akasha.nodes.<type>`.
- **Runtime:** logs `node_skipped` with ids only.

### Configuration and dependency injection
- `bootstrap.create_node_registry(settings)` → `create_builtin_registry`,
  given a `NetworkPolicy` built from the settings.
- The registry is created even without a database URL (`Container.nodes`).

---

## 9. Findings

### F1 — Unsupported config-schema keywords were silently ignored
- **Impact:** MEDIUM.
- **Resolution:** FIXED for `enum`, `minimum`, `maximum`, `minLength`,
  `maxItems` and `pattern`. OPEN for all others.
- **Notes:**
  - The Phase 03 validator enforced only `type`, `required`, `properties`,
    `additionalProperties` and `items`. A node declaring `enum` got no
    enforcement and no error.
  - Other keywords (`oneOf`, `format`, `minItems`, nested
    `additionalProperties` schemas, …) are still silently ignored. The
    docstring lists exactly what is enforced.

### F2 — Some config rules cannot be expressed in the supported schema subset
- **Impact:** LOW.
- **Resolution:** WORKAROUND. They are checked at execute time with
  CONFIGURATION errors.
- **Notes:** affected rules:
  - non-empty `recipients`;
  - `operand` required for binary `if` operators;
  - string header values.

  These fail at run time, not at publish.

### F3 — smtplib reports read timeouts as `SMTPServerDisconnected`
- **Impact:** MEDIUM (wrong error classification: TRANSIENT instead of
  TIMEOUT).
- **Resolution:** FIXED (§10, B1).

### F4 — Creating an `httpx.AsyncClient` per request cost ~0.2 s (TLS context)
- **Impact:** MEDIUM (a per-execution latency for every HTTP or search node).
- **Resolution:** FIXED. There is now one cached `ssl.SSLContext`.
- **Notes:**
  - Measured via test durations: `test_private_destinations_are_blocked_by_default`
    went from 3.00 s to below the top-5 durations (<0.12 s), 13 requests.
  - Side effect: the trust store is now the OS store
    (`ssl.create_default_context()`), not httpx's default (certifi). This
    matters for images without CA certificates and for corporate CAs.

### F5 — IPv4-mapped IPv6 unwrap is redundant on Python 3.14
- **Impact:** LOW.
- **Resolution:** kept, and commented.
- **Notes:** the mutation "remove unwrap" survived, because Python 3.14's
  `is_global` already classifies `::ffff:127.0.0.1` as non-global. It is kept
  for older supported 3.12 releases (`requires-python >= 3.12`).

### F6 — Test helpers could not be shared through conftest
- **Impact:** LOW (test infrastructure).
- **Resolution:** FIXED. `tests/` and its subdirectories are packages, and
  shared code lives in `tests/support.py`.

### F7 — Validation of a graph with an unknown node type reports more than one code
- **Impact:** LOW.
- **Resolution:** not a defect; the test expectation was corrected.
- **Notes:**
  - After `unregister("core.log")`, validation also reports
    `missing_required_input` for the downstream `core.output`: its input's
    source can no longer be resolved.
  - `details.issue_codes` is `missing_required_input,unknown_node_type`.

### F8 — Windows connection-refused latency
- **Impact:** LOW (environment).
- **Resolution:** accepted.
- **Notes:** a refused connection to `127.0.0.1` takes about 2 s on this
  Windows host, because Windows retries the connection attempt. Three tests
  have that duration.

---

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Area / origin |
|---|---|---|---|---|---|
| B1 | SMTP read timeout classified as `SMTP_CONNECTION_ERROR`/TRANSIENT | smtplib converts `TimeoutError` into `SMTPServerDisconnected` | Check `exc.__context__` for `TimeoutError` → `SMTP_TIMEOUT` | `test_smtp_node.py::test_silent_server_times_out` | nodes/smtp; introduced in this phase, caught by its test |
| B2 | Missing *output* reported as "missing required input: X" | `_validate_ports` hard-coded "input" in the message | Message uses `direction` | `test_control_flow_node_may_omit_outputs` (matches "missing required output: no") | domain/node; pre-existing (Phase 03) |
| B3 | ~0.2 s per HTTP request | New TLS context per `AsyncClient` | Cached `ssl.SSLContext` | Covered by the HTTP suite runtime (not an assertion) | nodes/http; introduced in this phase |
| P1 | `tests.conftest` import failed (`ModuleNotFoundError: tests`) | Test dirs were not packages | Packages + `tests/support.py` | Whole suite collects (753) | Test tooling |
| P2 | `RunNode` alias typed `Awaitable`, rejected by `asyncio.create_task` (mypy) | Wrong alias type | `Coroutine[Any, Any, NodeResult]` | mypy strict | Test code |
| P3 | ruff findings: `ASYNC109` (`timeout` parameter), `S105` (constant named `SECRET`), `ASYNC110` (sleep-polling loop) | – | Renamed to `time_limit` / `CANARY`; replaced polling with a server-side `threading.Event` (`hang_entered`) awaited via `to_thread` | ruff | Code/tests introduced in this phase |
| P4 | `mypy`: `no-any-return` in `_number` | Narrowing via negated isinstance | Positive isinstance branch | mypy strict | nodes/control |
| P5 | HTTP test server teardown ~0.5 s per test | `serve_forever` default `poll_interval=0.5` | `poll_interval=0.05` | Duration check | Test tooling |
| P6 | SMTP cancellation test teardown 5.6 s | Accepted socket kept open until the SMTP thread's timeout | Fixture closes accepted sockets on teardown | Duration check (teardown no longer in the top list) | Test tooling |
| P7 | Test expected only `unknown_node_type` | Wrong expectation (F7) | Assert membership | `test_node_unregistered_after_publish_fails_before_any_node_runs` | Test code |

No environmental failures occurred. Docker, PostgreSQL and Mailpit were
available throughout.

---

## 11. Fixes Applied

1. **SMTP timeout classification (B1).**
   - Symptom: a silent server returned `SMTP_CONNECTION_ERROR`.
   - Implementation: `_timeout()` helper plus a `__context__` check in
     `_send`.
   - Verification: the regression test passes; the full SMTP suite (21
     tests) passes.
2. **Output-validation message (B2).**
   - Implementation: `f"missing required {direction}: …"`.
   - Verification: the existing input-message test (`match="missing required
     input"`) and the new output test both pass.
3. **TLS context reuse (B3/F4).**
   - Implementation: `@functools.cache _tls()`, passed as `verify=`.
   - Verification: HTTP suite 35/35; wall time for the suite 26.5 s → 6.5 s
     (the remaining time is mostly the Windows connection-refused tests).
4. **Test infrastructure (P1, P5, P6).**
   - Verification: 3 consecutive runs of `tests/nodes` +
     `test_builtin_nodes_runtime.py` all passed (170 tests each).
   - No Mailpit or PostgreSQL containers remained (`docker ps -a` count 0).

---

## 12. Testing & Verification

Final full run: **753 passed, 0 failed, 0 skipped** (`uv run pytest`,
64.5 s). The only warning is the pre-existing Starlette/httpx
`StarletteDeprecationWarning` from `fastapi.testclient`.

### Unit tests (no infrastructure)

| Module | Tests | Covers |
|---|---|---|
| `tests/domain/test_node_contract.py` | 47 (+4) | Schema keywords (7 invalid cases), `json_equal`, control-flow output rule, class-as-factory |
| `tests/domain/test_primitives_and_graph.py` | 18 (+3) | Semver ordering, pre-release, build metadata, constraint grammar, malformed input |
| `tests/domain/test_workflow.py` | 20 (+6) | Satisfied, unsatisfied, missing, malformed constraints; plugin skip; draft and version validation |
| `tests/domain/test_execution.py` | 266 (+6) | `start(node_versions)` copy semantics; input copy; NaN/bytes/NUL/object rejected |
| `tests/runtime/test_runtime_units.py` | 21 (+4, 1 rewritten) | Readiness: open upstream, skipped upstream with a live edge (merge), all edges dead, omitted control-flow port; `node_versions`; dependency re-check in `load_plan` |
| `tests/nodes/test_registry.py` | 10 | Register/get/resolve, duplicates, unknown ids for get/resolve/unregister, unregister frees the id, live read-only views, sorted listing, version compatibility, contract check, factory injection, wrong-factory rejection |
| `tests/nodes/test_core_nodes.py` | 23 | input (whole, field, missing field, invalid config); output; log (level, message, structured fields, no value by default, opt-in value, invalid config); delay (pass-through, bounds, cancellation mid-sleep, timeout); merge |
| `tests/nodes/test_control_nodes.py` | 30 | 15 operator cases incl. bool≠1, `is_null`, missing operand, 5 type mismatches, invalid config/operators, switch first match, default, invalid config |
| `tests/test_architecture.py` | 2 | Dependency direction including the new `nodes` internal rule |

### Component tests (contract suite)
- `tests/nodes/test_builtin_contract.py` (17 tests):
  - exact roadmap set; every built-in: contract, fresh instance, metadata,
    semver, closed schema, typo rejected;
  - truthful capabilities; duplicate registration of built-ins;
  - container exposes the registry;
  - settings → network policy (strict blocks, allowlist reaches the local
    server); settings → web search URL; invalid URL fails fast.
- Result: PASS.

### Integration tests (real local services)

**`tests/nodes/test_http_node.py` (35 tests, loopback server):**
- methods: GET, POST (body), PUT (explicit content type), HEAD;
- text charset decoding;
- statuses 400, 404, 429, 500, 503; `fail_on_error_status=false`;
- redirects: off, on, loop (6 requests), allowlisted first hop then blocked
  hop, `file:` target;
- size limits: Content-Length and streamed, over and within the limit;
- malformed JSON, timeout, connection refused, in-flight cancellation
  (synchronized on a server-side event);
- config validation, header types, URL credentials;
- 13 blocked destination forms; public literals allowed; allowlist casing;
  secret canary.

**`tests/nodes/test_web_search_node.py` (18 tests, loopback SearXNG-shaped
responses):**
- normalization and limit, default limit 5, empty results;
- 4 malformed bodies;
- 429, 502, 403 classification;
- timeout, oversized response, unconfigured provider, blank query;
- config and input validation.

**`tests/integration/test_builtin_nodes_runtime.py` (16 tests; PostgreSQL +
loopback + anonymous Mailpit):**
- if true and false branches with transitive skips, persisted input and
  `node_versions`;
- switch + delay + merge, both branches;
- node failure code; missing input field; delay vs. node timeout; log
  correlation;
- HTTP POST; HTTP 503 → transient; SSRF inside the runtime;
- web search; SMTP send with the Message-ID check; SMTP without a resolver;
- invalid config at publish; incompatible and compatible dependency at
  publish;
- unregistered node → `EXECUTION_GRAPH_INVALID` before any node runs.

**`tests/integration/test_execution_persistence.py` (11, +1 new, 1
extended):** round-trip of `input` and `node_versions`; the 0003 → head
upgrade.

**`tests/integration/test_persistence.py`:** migration base → head → base →
head, with head `0004_execution_input`.

All PASS.

### Infrastructure tests
- **PostgreSQL:** `postgres:17.2` (Testcontainers, session-scoped).
- **Mailpit:** `axllent/mailpit:v1.21.8`:
  - session instance with `MP_SMTP_AUTH` (`tests/conftest.py::mailpit`);
  - module-scoped anonymous instance (`test_builtin_nodes_runtime.py::open_mailpit`);
  - readiness is checked by bounded polling of `/readyz`.
- **`tests/nodes/test_smtp_node.py` (21 tests):**
  - authenticated delivery to 2 recipients, verified via the Mailpit API:
    subject, from, to, text, Message-ID;
  - wrong password; anonymous mail rejected; STARTTLS unavailable;
    implicit TLS against plaintext;
  - refused connection; silent server timeout; header injection; no
    resolver; private host blocked by default;
  - empty recipients; 8 invalid configs; password absent from logs;
  - cancellation returns within 1 s while the SMTP thread is blocked.
- Result: PASS.
- Cleanup: no containers left after the runs.

### API tests
No new API endpoints. The existing 37 API tests pass, and they still use the
injected test catalog. The OpenAPI enum extension was verified manually (§7);
no automated OpenAPI contract test exists.

### Security tests
See §15. All are automated and pass.

### Mutation spot-checks (manual, not automated tooling)
Six mutants were applied temporarily and then reverted (the diff was
verified afterwards):

| Mutant | Killed? |
|---|---|
| readiness `fed and required <= fed` → `fed` | yes |
| readiness `fed and required <= fed` → `required <= fed` | yes |
| `_live` `and` → `or` | yes |
| `_blocked` without the multicast check | yes |
| `_blocked` → `is_private` | yes |
| remove the IPv4-mapped unwrap | no (F5) |

### Not performed
- No E2E tests.
- No performance or benchmark tests.
- No accessibility tests (backend-only).

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 92 files already formatted |
| Linter (`uv run ruff check .`) | PASS | Earlier ASYNC109/S105/ASYNC110 findings fixed (P3) |
| Type Checker (`uv run mypy .`, strict, src + tests) | PASS | 90 source files; earlier `no-any-return` / `arg-type` / `valid-type` errors fixed |
| Unit Tests | PASS | Part of 753 |
| Integration Tests | PASS | PostgreSQL + Mailpit + loopback HTTP |
| API Tests | PASS | Existing suite, unchanged |
| E2E Tests | N/A | None exist yet |
| Build (`uv build`) | PASS | sdist + wheel built into a temp dir, then deleted |
| Security Checks | PASS | Automated security tests (§15); no dependency audit tool configured |
| Accessibility | N/A | Backend |
| Flakiness | PASS | 3× repeat of node + builtin-runtime suites (170 tests each) |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Nodes are discoverable through the registry | PASS | `test_registry_contains_exactly_the_roadmap_set`; `NodeRegistry.list_nodes/get/definitions`; `test_container_exposes_the_builtin_registry` |
| Nodes are executable through the same runtime | PASS | `test_builtin_nodes_runtime.py`: each of the 10 built-ins runs via `ExecutionRuntime(uow, registry.catalog)` on PostgreSQL. input, if, log, output, switch, delay, merge, http, web_search and smtp each appear in at least one completed execution. |
| No second execution mechanism / no special-case runtime logic per node | PASS | `runtime/*.py` contains no node type ids (only a generic `is_control_flow`-driven readiness rule); `grep "core\."` over `src/akasha/runtime/` has no matches outside comments/docstrings |
| Roadmap tests: registration | PASS | `test_registry.py` (register, duplicate, unregister, contract/factory checks) |
| Roadmap tests: lookup | PASS | `test_register_then_get_and_resolve`, `test_unknown_node_type` |
| Roadmap tests: version compatibility | PASS | `test_version_compatibility`; `TestDependencyValidation`; `test_declared_dependency_versions_are_rechecked_at_load`; `test_incompatible_node_version_blocks_publish` |
| Roadmap tests: node execution | PASS | Per-node suites (§12) and runtime integration |
| Roadmap tests: schema validation | PASS | `test_config_constraint_keywords`; `test_invalid_config*` per node; `test_invalid_builtin_config_is_rejected_at_publish` |
| Session requirement: HTTP and SMTP tests use local infrastructure | PASS | Loopback `ThreadingHTTPServer`; Mailpit container; no public host is contacted (the only non-local literals are guard-only IPs that are never connected to) |
| Session requirement: deterministic web-search boundary | PASS | Fixed payloads from the local `/search` route (`ServerState.search_body`) |
| Roadmap prompt: no Python SDK / Plugin System | PASS | Neither was implemented; plugin dependencies are explicitly left unchecked (L7) |

The acceptance criteria are met. The limitations in §20 do not block the
criteria; they bound the quality of individual nodes, for example SMTP
cancellation and authenticated SMTP only at node level.

---

## 15. Security Considerations

### SSRF
- **Guard:** user-configured HTTP and SMTP hosts are resolved and must be
  global.
- **Blocked by default:**
  - loopback, RFC 1918, link-local (cloud metadata 169.254.169.254);
  - CGNAT, ULA, IPv6 link-local, multicast, `0.0.0.0`, IPv4-mapped.
- **Redirects:** off by default; each hop is re-checked (max 5).
- **No proxies:** environment proxies are ignored (`trust_env=False`).
- **Tests:** 13 blocked forms, blocked redirect hop, runtime-level block,
  SMTP host block.
- **Open:** the DNS-rebinding window (L1).

### URLs
- Only http/https (schema pattern plus runtime check).
- Userinfo in URLs is rejected, so credentials cannot leak into logs or
  redirects.

### Secrets
- **Config:** holds only references (`password_credential`). Closed schemas
  reject a `password` key.
- **Resolution:** the password is resolved at execute time only.
- **Errors and logs:** messages carry exception type names and never server
  replies or request contents. Canary tests confirm this for headers, URLs
  and SMTP passwords.
- **HTTP headers:** request headers are never logged.
- **Response headers:** persisted as outputs without redaction (OI-2).

### Header injection
- The SMTP subject goes through `policy.default`, which rejects CR/LF.
- Address fields have a strict pattern (no whitespace, `<>`, `,`).

### Transport security
- **STARTTLS:** never downgraded to plaintext.
- **Plaintext SMTP:** `security="none"` is allowed as an explicit user
  choice; AUTH over plaintext is then possible.
- **TLS verification:** on for HTTP and SMTP, using the OS trust store.

### Expression evaluation
- `if` and `switch` use enumerated operators and JSON equality. There is no
  `eval`, and unknown operators are rejected by the schema.

### Logging
- `core.log` does not log values unless `include_value: true`. When enabled,
  values are logged unredacted (explicit opt-in).

### Untrusted data
- Web-search results pass through verbatim as data. The node docstring notes
  that they must never be used as instructions.

### Resource exhaustion
- **HTTP:** response cap (default 5 MiB) and a total time limit (≤120 s).
- **Delay:** ≤3600 s.
- **Switch:** ≤5 cases.
- **SMTP:** ≤50 recipients.
- Execution input must be storable JSON; there is no size limit (Phase 24).

### Trust model
- The operator-configured search endpoint is trusted and bypasses the guard
  by design.

---

## 16. Performance Considerations

- **Measured:** TLS context creation cost ~0.2 s per client and is now cached
  (F4). The HTTP suite went from 26.5 s to 6.5 s.
- **Per-request clients:** one `AsyncClient` per node attempt means no
  connection pooling across attempts. This is deliberate (clear ownership),
  not measured beyond the TLS cost.
- **Readiness cost:** each completion costs O(downstream edges), plus the
  cascade over skipped subgraphs. `readiness` recomputes the required-port
  set per call (not cached). No benchmark exists.
- **Persistence:** unchanged. Every save still rewrites all node rows (Phase
  08 L6). Skips add no extra saves; they ride on the next save.
- **Deep copies:** `execution_input` is deep-copied per node context, which
  is O(input size × nodes). This is acceptable for small payloads; large
  payloads belong in artifacts (Phase 24).
- **SMTP:** each send occupies a default-executor thread until the socket
  timeout at worst (≤120 s).
- **No performance tests** were written.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §11 | Runtime rules changed | Readiness/skip rule, dependency re-validation, `node_versions` |
| `docs/architecture.md` §14 | Control flow implemented | Capability-based branching; what is not implemented |
| `docs/architecture.md` §37 | Registry implemented | API, one-version rule, dependency constraints, factories, built-in list |
| `docs/architecture.md` §53 | Persistence changed | `executions.input`, `metadata.node_versions` |
| `docs/architecture.md` §63 | HTTP controls implemented | SSRF policy, settings, limits, error classes, known gap |
| `backend/README.md` | New settings and test infrastructure | Settings table; Mailpit/loopback note |
| Module docstrings | Code-level documentation | `nodes/__init__.py`, `registry.py`, `builtins.py`, `core.py`, `control.py`, `http.py`, `smtp.py`, `security/network.py`, `runtime/plan.py`, `runtime/runtime.py`, `domain/node.py`, `domain/execution.py`, `graph_validation.py`, `application/workflows.py` |
| `docs/reports/backend/phase-09-node-registry-builtin-nodes.md` | Phase report | This document |

Should be updated in future phases:
- `docs/roadmap-backend.md`: phase status tracking (§35), which is not
  maintained here.
- `backend/README.md`: when an execution entry point exists (Phase 21/22).
- Architecture §63: when IP pinning closes L1 (Phase 25).

---

## 18. Dependencies / Infrastructure Changes

- **httpx:** moved from the `dev` group to runtime `dependencies`
  (`httpx>=0.28.1`; lock unchanged at 0.28.1, with httpcore 1.0.9). It is
  needed by `core.http` and `core.web_search` at runtime. It was already
  vetted and locked for tests.
- **Added / removed packages:** none. SMTP uses stdlib `smtplib`; the guard
  uses stdlib `ipaddress`/`socket`.
- **New environment variables:**

  | Variable | Default |
  |---|---|
  | `AKASHA_NETWORK_ALLOW_PRIVATE` | `false` |
  | `AKASHA_NETWORK_ALLOWED_HOSTS` (JSON list) | `[]` |
  | `AKASHA_HTTP_MAX_RESPONSE_BYTES` | `5242880`, must be > 0 |
  | `AKASHA_WEB_SEARCH_URL` | unset; validated as `AnyHttpUrl` |

- **Database:** migration `0004_execution_input`.
- **Test infrastructure:**
  - new Testcontainers image `axllent/mailpit:v1.21.8` (pinned), using the
    generic `testcontainers.core.container.DockerContainer` (no new extra);
  - in-process loopback HTTP server;
  - `tests/` became a package;
  - `docker_available` moved to `tests/support.py`.
- **Unchanged:** Docker/Compose files, Redis.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Blocker | Affects Phase 09 completion |
|---|---|---|---|---|
| Credential resolver in the runtime context (SMTP auth via the runtime) | Credential store is Phase 13 scope | 13 | Credential service | No (auth proven at node level) |
| Redaction of persisted outputs (HTTP headers, bodies) | Redaction architecture | 13 | – | No |
| Passing trigger payloads to `create_execution(input=…)` | Trigger system | 10 | – | No |
| Registry-backed `ExecutionRuntime` in production | No execution entry point exists | 21/22 | Worker/CLI | No (the runtime is proven with `registry.catalog` in tests) |
| IP pinning against DNS rebinding | Security hardening | 25 | Custom transport | No |
| Hard cancellation of SMTP sends | Would need an async SMTP dependency | Open | Decision | No |
| Loop/ForEach, Try/Catch, subgraphs | Not in the Phase 09 node list | Later | Cycle-validation design | No |
| Plugin dependency validation | No plugin system | 17 | – | No |
| Retry execution of TRANSIENT node errors | Retry phase | 23 | – | No |
| Payload/output size limits beyond HTTP | Resource limits | 24 | – | No |
| Automated OpenAPI contract test | Not in scope; no tooling exists | Open | – | No |

---

## 20. Known Limitations

| # | Limitation | Details |
|---|---|---|
| L1 | DNS rebinding | Check-then-connect: httpx and smtplib resolve again after the check. Marked `ponytail:` in `security/network.py`. |
| L2 | SMTP not hard-cancellable | The thread completes its dialogue, bounded by `timeout_seconds` (default 30, max 120). A mail may be sent after cancellation or a node timeout. The node declares `supports_cancellation=False`. |
| L3 | `core.switch` has 5 fixed cases | Ports are per type. Marked `ponytail:`. |
| L4 | `core.delay` is in-process, ≤3600 s | Not durable across restarts. Marked `ponytail:`. |
| L5 | Non-JSON HTTP bodies are decoded as text | Binary content is lossy (replacement characters). Marked `ponytail:`. |
| L6 | Pre-release precedence | Compared as one string (`rc.10 < rc.9`). Marked `ponytail:`. |
| L7 | Plugin dependencies unchecked | Documented in `validate_dependencies`. |
| L8 | Execute-time config checks | See F2. |
| L9 | NUL check false positive | The literal text `\u0000` in execution input is also rejected. Documented in `_is_json`. |
| L10 | Every node sees `execution_input` | Not restricted to entry nodes. |
| L11 | Nodes skipped behind a dead branch | A node whose only incoming edges feed optional inputs is SKIPPED when those edges are dead; it is not run with defaults (a design choice, §5.3). |
| L12 | OS trust store for TLS | Hosts without CA certificates fail TLS (F4). |
| L13 | Web search supports only SearXNG | There is no provider abstraction (one implementation). |
| L14 | No attachments or HTML mail in core.smtp | – |
| L15 | Phase 08 limitations still apply | L1–L11 of the Phase 08 report, notably no production runtime caller, stale RUNNING executions, unredacted persisted errors. |

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| OI-1 DNS-rebinding window in the SSRF guard (L1) | A malicious DNS server can reach internal hosts despite the guard | MEDIUM | security/network, nodes/http, nodes/smtp | Connect to the checked IP (custom httpx transport; smtplib `source`/socket preconnect) | 25 |
| OI-2 Persisted HTTP response headers and bodies unredacted | `Set-Cookie`/tokens returned by servers are stored in PostgreSQL | MEDIUM | persistence, nodes/http | Apply redaction on output persistence | 13 |
| OI-3 SMTP send continues after cancellation (L2) | Possible e-mail after a user cancelled | MEDIUM | nodes/smtp | Decide: accept, or adopt an async SMTP client | 23/25 |
| OI-4 No production caller passes `registry.catalog` to `ExecutionRuntime` | Runtime unusable outside tests (inherited Phase 08 L8) | MEDIUM | bootstrap | Wire `container.nodes.catalog` when creating the runtime | 21/22 |
| OI-5 Unsupported JSON Schema keywords still silently ignored (F1) | A node author may believe a constraint is enforced | LOW | domain/node | Reject unknown keywords at `NodeConfigSchema` construction, or adopt `jsonschema` | 16/17 (SDK/plugins) |
| OI-6 No automated OpenAPI contract check | Enum changes (e.g. new `ValidationCode`s) go unnoticed by the frontend | LOW | api | Snapshot/contract test of `openapi.json` | Open |

---

## 22. Architectural / Product Decisions

- **Omitted output = inactive branch, only for `is_control_flow` nodes.**
  Regular nodes must still produce every declared output, so missing outputs
  remain a detectable bug for them.
- **Merge semantics:** one live incoming edge suffices. Merge never waits for
  a branch that will not run, because readiness waits until every upstream
  node is decided.
- **Skip vs. defaults:** a node is skipped if it has no live edge, even when
  all of its inputs are optional. Otherwise, nodes behind an untaken branch
  (for example a delay) would run.
- **Stable node error codes** (`NodeError(code=…)`) make failures
  machine-classifiable without parsing messages. Messages never include
  third-party replies.
- **Closed config schemas for all built-ins:** typos fail at publish, and
  plaintext secret keys cannot be smuggled into config.
- **Default-deny network policy.** The operator opts in to private
  destinations. The search endpoint is operator-trusted.
- **Partial SMTP recipient refusal is a success** with `rejected_recipients`,
  because failing would make a retry send duplicates to the accepted
  recipients.
- **Deterministic `Message-ID`** from the node execution id, so a retry of
  the same node execution is recognisable as a duplicate by receivers.
- **`core.output` re-emits its input,** because only node outputs are
  persisted (Phase 08: inputs are not stored).
- **Node versions are recorded at start,** not creation, so they reflect what
  ran.
- **The registry is a live view,** so that plugin registration (Phase 17)
  does not require rebuilding services.
- **SearXNG** was chosen as the search backend because it is self-hostable
  and has a JSON API, which fits the self-hosted vision. No commercial
  search API is coupled.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **HEAD:** `57d541e backend phase 08 - execution runtime`. No commit was
  made for Phase 09, because none was requested.
- **Working tree:** uncommitted; nothing staged.
- **Modified:** 29 tracked files (+898/−126), listed in §4, including
  `docs/architecture.md`, `backend/pyproject.toml` and `backend/uv.lock`.
- **Untracked:**
  - `backend/migrations/versions/0004_execution_input.py`;
  - `backend/src/akasha/nodes/{builtins,control,core,http,registry,smtp}.py`;
  - `backend/src/akasha/security/network.py`;
  - `backend/tests/__init__.py`, `tests/{domain,integration,nodes,runtime}/__init__.py`;
  - `backend/tests/support.py`;
  - `backend/tests/nodes/` (conftest + 7 test modules);
  - `backend/tests/integration/test_builtin_nodes_runtime.py`;
  - this report.
- **Unrelated changes:** none detected.
- **Line endings:** all changed and new source, test and doc files use LF.
  CR bytes were found only in git-ignored `__pycache__/*.pyc` files.
- **Temporary artifacts:** build output in `%TEMP%\opencode` was deleted.
  The mutation edits were reverted, and `git diff` shows only the intended
  changes. No containers remained.

---

## 24. Phase Completion Assessment

### Implemented
- **Registry:** NodeRegistry (registration, resolution, metadata) with live
  views.
- **Built-ins:** ten built-in nodes, plus the registration factory.
- **Runtime:** generic control-flow readiness and skip cascade.
- **Versions:** node-version compatibility via dependencies, and resolved
  versions recorded at start.
- **Execution input:** domain, persistence, migration 0004, context field.
- **Security and wiring:** SSRF guard; settings; composition-root wiring.

### Verified
- 753 tests pass, including 16 full-stack built-in runtime tests on
  PostgreSQL, 21 Mailpit SMTP tests, and 53 loopback HTTP/search tests.
- Migrations tested from empty and from 0003.
- 3× repeat without flakes.
- 5 of 6 mutants killed.
- Format, lint, mypy (strict) and build all pass.

### Not Implemented
- Nothing within the roadmap Phase 09 scope is missing.

### Deferred
- See §19: credentials and redaction, triggers, runtime entry point, IP
  pinning, hard SMTP cancellation, loops/try-catch, plugin dependencies,
  retries, limits, OpenAPI contract test.

### Open
- See §21: OI-1 … OI-6.

### Blocking Issues
- None for this phase.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

### Phase 10 (Trigger System) can assume
- **`ExecutionService.create_execution(workflow_id, version, *, input=…, metadata=…, timeout_policy=…)`**
  is the single entry point. `input` must be a JSON object; it is persisted
  and deep-copied into `NodeExecutionContext.execution_input`. `core.input`
  reads the whole object or `config.field`.
- **`Container.nodes`** (`NodeRegistry`) exists at startup.
  - `nodes.definitions` backs `WorkflowService`.
  - Any runtime created by triggers or workers must use `nodes.catalog`, so
    that publish-time and run-time validation agree.
- **Registry contract:**
  - `register(node_cls, factory=None)`, `unregister`, `get`,
    `resolve(id, constraint)`, `list_nodes()`, `definitions`, `catalog`.
  - Errors: `NODE_NOT_FOUND`, `NODE_ALREADY_REGISTERED`,
    `NODE_VERSION_INCOMPATIBLE`.
- **Validation codes:** they now include `missing_dependency`,
  `incompatible_dependency` and `invalid_dependency`.

### Known constraints for the next phases
- **Phase 13:**
  - The runtime creates contexts with `credentials=None`; supply a
    `CredentialResolver` there.
  - Redaction must cover persisted outputs (OI-2) and error details.
- **Phase 17:**
  - Plugins must register through the same `NodeRegistry`, and may register
    factories.
  - Plugin `Dependency` entries are currently skipped by
    `validate_dependencies`.
- **Phases 21/22:** construct `ExecutionRuntime(uow, container.nodes.catalog)`.

### Must not be changed casually
- The readiness rule in `ExecutionPlan.readiness`, and "omitted output =
  inactive branch" being limited to `is_control_flow`.
- `_advance` running only when the batch had no failure. This keeps the
  fail-fast semantics of Phase 08.
- Closed config schemas for built-ins, and references-only secrets.
- The default-deny `NetworkPolicy`, and re-checking every redirect hop.
- One version per node type id. Changing it requires a Workflow schema change
  (a version on `NodeInstance`).
- The deterministic SMTP `Message-ID` (idempotency hint).
- All Phase 08 invariants: single writer, CAS on `revision`, RUNNING
  persisted before node code, stop precedence, `FOR SHARE` load.
