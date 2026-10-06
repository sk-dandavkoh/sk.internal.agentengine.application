# Phase 16 — Python Node SDK

## 1. Executive Summary

**What the phase had to achieve.** Roadmap §20 asks for custom Node development without modifying Engine core. The SDK must provide:
- the Node base class, metadata, inputs/outputs and config schema;
- the execution context;
- logging, metrics, credentials, artifacts, events and errors;
- testing helpers, plus `node test / validate / package` utilities "where practical".

It also needs an external-style example Node that uses only the SDK.

Acceptance: "A developer can implement a custom Node without importing internal Engine implementation modules."

**What was implemented.**
- **`akasha.sdk`** (new package): the supported public surface.
  - It re-exports the existing domain Node contract from `akasha.domain.node` plus the three contract errors, and versions them with `SDK_VERSION = "1.0.0"`.
  - Three additions to the Node contract were needed:
    - declared metric and event names on `NodeDefinition`;
    - per-attempt `NodeMetrics` and `NodeEvents` on the execution context;
    - `NodeExecutionContext.resolve_credential`.
- **`akasha.sdk.testing.run_node`:** a harness that runs a node with the Engine's checks, in the Engine's order.
- **Runtime:** events a node emits become a new persisted event type, `node.emitted`. Node counters become a new Prometheus counter, `akasha_node_metric_total{node_type, metric}`.
- **Example Node** `community.hmac_sign` in `backend/examples/hmac_signer/`. It imports only `akasha.sdk` and has its own tests.
- **Developer guide:** `docs/sdk.md`.
- **Built-in nodes** (`core`, `control`, `http`, `smtp`, `llm`) now import the contract from `akasha.sdk`, enforced by an architecture test. This was done in a follow-up that resolved technical debt from the first pass.

**Resulting system state.**
- A node that imports only `akasha.sdk` can be:
  - registered in the `NodeRegistry`;
  - saved and published in a Workflow;
  - executed by the unchanged scheduling logic of `ExecutionRuntime`, with its secret coming from the encrypted Credential Store.

  Its emitted events are persisted in the event store and its counters are exported to Prometheus. The integration test proves this against PostgreSQL 17 (Testcontainers).
- Full suite: 1245 passed, 5 skipped (the opt-in external smoke tests). Ruff, ruff format and strict mypy pass.

**Acceptance.** The roadmap criterion is satisfied with direct evidence (§14). Two scope items from the roadmap list are not delivered:
- **Artifacts:** no artifact subsystem exists.
- **`node package`:** packaging belongs to the Plugin System.

`node validate` / `node test` exist as library functions, not as commands.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §20 (Phase 16 — Python Node SDK):
- **Goal:** "Make custom Node development possible without modifying Engine core."
- **Scope:** SDK components `Node`, `NodeMetadata`, `Input`, `Output`, `ConfigSchema`, `ExecutionContext`, `Logger`, `Metrics`, `Credentials`, `Artifacts`, `Events` and `NodeError`, plus the developer utilities `node test`, `node validate` and `node package` "where practical".
- **Tests:** "Create an external-style example custom Node using only the SDK."
- **Acceptance:** "A developer can implement a custom Node without importing internal Engine implementation modules."

The phase prompt (roadmap §37 "Phase 16 Prompt", and the session request) adds:
- testing helpers;
- tests for registration/definition, configuration, execution context, inputs/outputs, errors, logging, credentials and SDK compatibility;
- the completion criterion: "A developer can understand and implement a custom Node without depending on internal Engine classes. Quality gates pass."

**Explicitly not in scope:**
- the Plugin System: manifest, loader, installer, discovery, SDK-compatibility matching (Phase 17);
- the CLI (Phase 21);
- workers and queues (Phase 22);
- retries and recovery (Phase 23);
- node isolation beyond the V1 trusted-code model (architecture §34).

---

## 3. Scope Implemented

### SDK (`backend/src/akasha/sdk/`, new)
- **`__init__.py`:**
  - `SDK_VERSION: Final = "1.0.0"` and an explicit `__all__` of 21 names:
    - `MAX_EVENTS_PER_ATTEMPT`, `SDK_VERSION`, `CredentialResolver`, `Node`;
    - `NodeCapabilities`, `NodeConfigSchema`, `NodeDefinition`, `NodeMetadata`;
    - `NodeInput`, `NodeOutput`, `PortType`, `NodeResult`;
    - `NodeExecutionContext`, `NodeMetrics`, `NodeEvents`;
    - `NodeError`, `NodeErrorKind`, `NodeConfigurationError`, `NodeContractError`, `NodeValidationError`;
    - `check_node_contract`.
  - The module docstring contains a minimal node and states the trust model.
- **`testing.py`:** `run_node(node, *, config, inputs, execution_input, credentials, is_cancelled) -> NodeRun`.
  - `NodeRun` is a frozen dataclass with `outputs`, `events` and `metrics`.
  - A private `_Secrets` source turns a reference→value mapping into `Secret`s and raises `CredentialNotFoundError` for unknown references.

### Domain (`backend/src/akasha/domain/`)
- **`node.py`:**
  - `NodeDefinition.metrics: tuple[str, ...] = ()` and `NodeDefinition.events: tuple[str, ...] = ()`. Names are validated with the port-name pattern (`^[a-z][a-z0-9_]*$`) and must be unique.
  - `NodeMetrics`: `increment(name, amount=1.0)` and `totals`.
  - `NodeEvents`: `emit(name)` and `emitted`.
  - `MAX_EVENTS_PER_ATTEMPT = 100`.
  - `NodeExecutionContext` gains the fields `metrics` and `events` (default: an instance that accepts no names) and the async method `resolve_credential(reference)`.
- **`event.py`:** `EventType.NODE_EMITTED = "node.emitted"`, with `PAYLOAD_KEYS` set to `{"name", "attempt"}`.
- **`execution.py`:** `Execution.record_node_event(node_id, name, *, now)`. It is allowed while the execution is started and only for a RUNNING node; otherwise it raises `InvalidStateTransitionError`.

### Runtime (`backend/src/akasha/runtime/runtime.py`)
- `_Running` now maps each task to an `_Attempt(node_id, node_type, context)` instead of a node id.
- `_launch` builds each context with `NodeMetrics(definition.metrics)` and `NodeEvents(definition.events)`.
- New method `_flush`: records `node.emitted` for every emitted name and increments `node_metric`. It is called:
  - from `_record` for each finished attempt, before `complete_node` / `fail_node`;
  - from `_stop` for attempts that were cancelled.

### Observability (`backend/src/akasha/observability/`)
- **`metrics.py`:** `EngineMetrics.node_metric`, a Counter `akasha_node_metric` with the labels `node_type` and `metric`.
- **`execution.py`:** `observe_execution` excludes `NODE_EMITTED` from the finished/duration metrics, alongside `NODE_STARTED`.

### Built-in nodes (`backend/src/akasha/nodes/`)
- `core.py`, `control.py`, `http.py`, `smtp.py` and `llm.py` import the contract names from `akasha.sdk`.
- `control.py` still imports `json_equal` from `akasha.domain.node`, because it is an engine helper and not part of the SDK.
- `nodes/__init__.py` documents the rule.
- No node behaviour or error code changed.

### Example (`backend/examples/`, new)
- `hmac_signer/node.py` defines `HmacSignNode`:
  - node id `community.hmac_sign`, version 1.0.0, category `security`;
  - input `payload` (JSON); outputs `body` (STRING) and `signature` (STRING);
  - a closed config schema with `secret_credential` (required), `algorithm` (`sha256` | `sha512`) and `max_payload_bytes` (1..1 048 576, default 65 536);
  - metrics `payloads_signed` and `bytes_signed`; events `payload_signed` and `payload_rejected`;
  - `verify(body, signature, secret)`, which models the receiver.
- `hmac_signer/test_node.py`: 15 tests, using the SDK and pytest only.
- `hmac_signer/README.md`, plus `__init__.py` files that make `examples.hmac_signer` importable.

### Testing
- `backend/tests/test_sdk.py` (new, 18 tests).
- `backend/tests/integration/test_sdk_node_runtime.py` (new, 3 tests, PostgreSQL).
- `backend/tests/domain/test_execution.py`: 2 new tests.
- `backend/tests/test_architecture.py`:
  - `sdk` layer rules;
  - the new test `test_builtin_nodes_use_the_sdk_for_the_node_contract`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/sdk/__init__.py` | Created | Public SDK surface and `SDK_VERSION` |
| `backend/src/akasha/sdk/testing.py` | Created | `run_node` / `NodeRun` test harness |
| `backend/src/akasha/domain/node.py` | Modified (+80) | Declared metrics and events, `NodeMetrics`, `NodeEvents`, `MAX_EVENTS_PER_ATTEMPT`, context fields, `resolve_credential` |
| `backend/src/akasha/domain/event.py` | Modified (+3) | `NODE_EMITTED` and its payload keys |
| `backend/src/akasha/domain/execution.py` | Modified (+22) | `record_node_event` |
| `backend/src/akasha/runtime/runtime.py` | Modified (+48/−5) | `_Attempt`, context wiring, `_flush` |
| `backend/src/akasha/observability/metrics.py` | Modified | `node_metric` counter; label docstring |
| `backend/src/akasha/observability/execution.py` | Modified (1 line) | Exclude `node.emitted` from lifecycle metrics |
| `backend/src/akasha/nodes/{core,control,http,smtp,llm}.py` | Modified (imports only) | Built-ins use `akasha.sdk`; ruff re-sorted the import blocks |
| `backend/src/akasha/nodes/__init__.py` | Modified (docstring) | Documents the SDK import rule |
| `backend/examples/` (directory) | Created | External-style example package |
| `backend/examples/__init__.py`, `hmac_signer/__init__.py` | Created | Make `examples.hmac_signer` importable for pytest and mypy |
| `backend/examples/hmac_signer/node.py` | Created | Example Node |
| `backend/examples/hmac_signer/test_node.py` | Created | Developer-style tests |
| `backend/examples/hmac_signer/README.md` | Created | What the example demonstrates |
| `backend/tests/test_sdk.py` | Created | Surface/compatibility, boundary, telemetry limits, harness paths |
| `backend/tests/integration/test_sdk_node_runtime.py` | Created | SDK node through the real Engine and PostgreSQL |
| `backend/tests/domain/test_execution.py` | Modified (+23) | `record_node_event` legal and illegal cases |
| `backend/tests/test_architecture.py` | Modified (+25) | `sdk` layer rules; built-in SDK import guard |
| `backend/pyproject.toml` | Modified | `testpaths` += `examples`; ruff `src` += `examples`; S101 ignored in `examples/**/test_*.py` |
| `docs/sdk.md` | Created | Developer guide (roadmap §2.2 lists it as a phase document) |
| `docs/architecture.md` | Modified (+34) | §19 `node.emitted`; §33 implementation note; metrics table row |
| `docs/reports/backend/phase-16-python-node-sdk.md` | Created | This report |

There are no migrations, no deleted files, no generated files and no changes to dependencies or `uv.lock`.

---

## 5. Architecture Changes

### 5.1 The SDK is a facade over the domain Node contract

- **Decision:** `akasha.sdk` re-exports `akasha.domain.node` names; it defines no SDK-specific Node types.
- **Context:** Phase 03 had already built a complete, stdlib-only Node contract (`Node`, `NodeDefinition`, ports, `NodeExecutionContext`, `NodeResult`, `NodeError`), and every built-in node uses it.
- **Chosen approach:** a curated `__all__` and `SDK_VERSION`, with the surface pinned by `tests/test_sdk.py::PUBLIC_API`.
- **Alternatives considered:** SDK-owned classes (for example the roadmap names `Input`, `Output`, `ConfigSchema`, `ExecutionContext`) with an adapter to the domain contract.
- **Reason:**
  - The alternative would create two contracts that must be kept in sync, plus a conversion layer in the runtime.
  - Aliases would add a second name for the same concept (AGENTS §6).
  - The domain contract has no framework dependencies, so exposing it leaks no infrastructure.
- **Consequences:**
  - Changing a re-exported domain type is now an SDK change and needs an `SDK_VERSION` bump according to semver.
  - The roadmap names map onto the domain names: `Input`→`NodeInput`, `Output`→`NodeOutput`, `ConfigSchema`→`NodeConfigSchema`, `ExecutionContext`→`NodeExecutionContext`, `Logger`→`context.logger`, `Credentials`→`context.resolve_credential` / `CredentialResolver`.

### 5.2 Metric and event names are declared in `NodeDefinition`

- **Decision:** `context.metrics` and `context.events` accept only names listed in `definition.metrics` and `definition.events`. Anything else raises `NodeContractError`.
- **Context:** AGENTS §41 requires bounded metric cardinality. The Phase 11 event rules require closed payloads, never workflow data.
- **Chosen approach:** names are validated when the definition is constructed. The per-attempt collectors check membership.
- **Alternatives considered:**
  - free-form names with a runtime cardinality cap;
  - label values taken from node code at runtime.
- **Reason:** with declared names, the label set is bounded by the installed node set (the same argument that already justifies the `node_type` label), and the names can be reviewed in code.
- **Consequences:**
  - Plugin validation (Phase 17) can inspect declared metrics and events statically.
  - Adding a name is a node version change.

### 5.3 Node events are name-only `node.emitted` events

- **Decision:** one new event type, `node.emitted`, with payload `{name, attempt}`.
- **Context:** the SDK must offer events, but event payloads are closed and scalar by design (`domain/event.py`).
- **Alternatives considered:**
  - one event type per node-declared name (this would make the event catalog open-ended);
  - free payloads (rejected for security reasons);
  - structured log lines only (not events in the architecture's sense).
- **Consequences:**
  - Frontend and WebSocket consumers (future phases) must tolerate `node.emitted`.
  - The event type was added without an `EVENT_SCHEMA_VERSION` bump, following the documented rule that a new type needs no bump.

### 5.4 Node telemetry is flushed per attempt, not streamed

- **Decision:** the scheduler loop records emitted events and exports counters when an attempt finishes or is cancelled.
- **Context:** the runtime's concurrency model says only the scheduler loop mutates and persists the `Execution` aggregate; node tasks never do.
- **Chosen approach:** `ExecutionRuntime._flush`, called from `_record` and `_stop`. Emitted events are saved in the same save as the attempt's outcome.
- **Alternatives considered:** live streaming through a channel from node tasks to the loop, with one save per emission.
- **Reason:** streaming breaks nothing today only if it is not built. No consumer of live progress exists, and the backend roadmap has no live-update phase.
- **Consequences:**
  - Progress is not visible before an attempt ends.
  - The trigger for revisiting is documented in the `_flush` docstring and architecture §33: a live-update consumer such as a WebSocket gateway.
  - The first report listed this as a `ponytail:` debt item. It was reclassified as a deliberate decision (§22).

### 5.5 `resolve_credential` on the context

- **Decision:** add an async `NodeExecutionContext.resolve_credential(reference)`. It raises `NodeError(CONFIGURATION, code="CREDENTIALS_UNAVAILABLE")` when no resolver is configured.
- **Context:** `context.credentials` is `CredentialResolver | None`, and every credential-using node repeated a `None` check.
- **Alternatives considered:** changing the `credentials` default to a refusing resolver. This would have silently turned the built-ins' `is None` branches into dead code and changed their error codes.
- **Consequences:**
  - Built-ins keep their node-specific codes (`HTTP_CREDENTIALS_UNAVAILABLE`, `SMTP_CREDENTIALS_UNAVAILABLE`).
  - SDK nodes get one generic code.

### 5.6 Built-in nodes are SDK consumers

- **Decision:** built-in node modules must import SDK-exported names from `akasha.sdk`, not from `akasha.domain.node`.
- **Reason:** every built-in then exercises the public surface. If the SDK stopped exporting something a node needs, the built-ins would fail first.
- **Enforcement:** `tests/test_architecture.py::test_builtin_nodes_use_the_sdk_for_the_node_contract`.
  - Exempt: `registry.py`, which is engine code, and non-SDK helpers such as `json_equal`.
  - The guard was checked against a deliberate violation: a temporary edit to `core.py` made it fail, and the edit was reverted.

### 5.7 SDK layer dependency rules

- `tests/test_architecture.py`: `sdk` must not import `fastapi`, `starlette`, `uvicorn`, `sqlalchemy`, `redis` or the provider libraries, nor `akasha.api`, `akasha.infrastructure` or `akasha.providers`.
- `akasha.sdk.testing` does import `akasha.runtime.credentials` and `akasha.application.credentials`. This is deliberate, so tests resolve credentials with the runtime's exact rule.
- `akasha.sdk/__init__` imports only `akasha.domain.node` and `akasha.errors`.

---

## 6. Domain / Data Model Changes

| Element | Change | Compatibility |
|---|---|---|
| `NodeDefinition` | New fields `metrics` and `events` (`tuple[str, ...]`, default `()`) | Additive; existing definitions unchanged. Not persisted (executions persist node versions only). |
| `NodeMetrics`, `NodeEvents` | New classes | New |
| `MAX_EVENTS_PER_ATTEMPT` | New constant (100) | New |
| `NodeExecutionContext` | New fields `metrics` and `events` (defaults), new method `resolve_credential` | Additive; every existing constructor call still works |
| `EventType` | New member `NODE_EMITTED = "node.emitted"` | Additive |
| `PAYLOAD_KEYS` | `NODE_EMITTED: {"name", "attempt"}` | Additive |
| `Execution` | New method `record_node_event` | Additive; no state transition. The node stays RUNNING. |

- **Database:** no schema change. `events.event_type` is an unconstrained `String` column (migration `0005_events`), so the new value needs no migration.
- **State machine:** no new states or transitions.

---

## 7. API / Contract Changes

**HTTP API:** no endpoints added, changed or removed.

**SDK contract (new):** `akasha.sdk` `__all__` (21 names, §3) and `akasha.sdk.testing` `__all__` = `NodeRun`, `run_node`, versioned by `SDK_VERSION = "1.0.0"`. The compatibility rule is documented in `docs/sdk.md` §10:
- a breaking removal, rename or behaviour change is a major bump;
- an addition is a minor bump.

**Node contract additions:**

| Contract element | Input | Output | Errors |
|---|---|---|---|
| `context.metrics.increment(name, amount=1.0)` | a declared name; a finite `int`/`float` `>= 0` (not `bool`) | accumulates into `totals` | `NodeContractError` for an undeclared name or an invalid amount |
| `context.events.emit(name)` | a declared name | appended to `emitted` | `NodeContractError` for an undeclared name or more than 100 events per attempt |
| `await context.resolve_credential(reference)` | a credential reference that appears in the node's config | the secret `str` | `NodeError`: `CREDENTIALS_UNAVAILABLE`, `CREDENTIAL_NOT_REFERENCED`, `CREDENTIAL_NOT_FOUND` (all CONFIGURATION); `CREDENTIAL_UNAVAILABLE` (PERMANENT) |

A `NodeContractError` raised inside `execute` is not a `NodeError`, so the executor records it as `NODE_UNEXPECTED_ERROR` (PERMANENT, with only the type name kept). This is the existing handling for unexpected exceptions.

**Event contract:** `node.emitted` has execution and node scope, and payload `{"name": str, "attempt": int}`.
- It is ordered after `node.started` and before the attempt's `node.completed` / `node.failed`, or before the cancellation cascade.
- It is logged by the execution observer like every other event.
- It is not counted in `akasha_node_executions_finished_total` or `akasha_node_execution_duration_seconds`.

**Metrics contract:** `akasha_node_metric_total{node_type, metric}` (counter). It is exported per attempt, for completed, failed and cancelled attempts.

---

## 8. Implementation Details

- **Where attempt context lives.** `_launch` builds each context and stores it in `_Running` as `_Attempt(node_id, node_type, context)`. This gives the scheduler loop access to the node's `NodeMetrics` and `NodeEvents` once the task ends, without node tasks touching the aggregate. The collectors are plain objects mutated only by the node's own task. The loop reads them only after the task has finished (`_record`) or has been awaited after cancellation (`_stop`), so there is no concurrent access.
- **Ordering.** In `_record`, `_flush` runs before `complete_node` / `fail_node`, so the persisted order within one save is started → emitted… → terminal. In `_stop`, finished tasks go through `_record` first; the cancelled rest are flushed while their nodes are still RUNNING, before the cancellation cascade.
- **Not flushed.** When `_schedule` exits through an exception (a persistence error, or outer cancellation of `run`), the `finally` block cancels the remaining tasks without flushing them. That execution state is not saved anyway.
- **`run_node` order:**
  1. `check_node_contract(type(node))`;
  2. `plaintext_secret_paths(config)`, which raises `NodeConfigurationError` with `details.paths`;
  3. `definition.validate_config`;
  4. `definition.validate_inputs`;
  5. `execute`;
  6. a `NodeResult`/`Mapping` check, then `definition.validate_outputs`, which raise `NodeValidationError`.

  Credentials use `NodeCredentials(_Secrets(mapping), config)` from the runtime, so the "only references in own config" rule and `register_secret` behave identically. The context uses fresh ids, `workflow_version="1"`, `runtime_metadata={"attempt": 1}` and the logger `akasha.nodes.<node_id>`.
- **Example node behaviour.**
  - Canonical JSON: `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`.
  - The size check comes before credential resolution, so an oversized payload never unlocks the secret.
  - `hmac.new(secret, body, algorithm)`; the output is `"<algorithm>=<hex>"`.
  - It logs `payload_signed` with `algorithm` and `bytes` only.
  - `verify` uses `hmac.compare_digest`.

---

## 9. Findings

### F1 — The execution observer would have counted `node.emitted` as a finished node
- **Finding:** `observe_execution` treated every node event other than `NODE_STARTED` as terminal. It incremented `node_executions_finished{status=<suffix>}` and recorded a duration. A new node event type would have produced `status="emitted"` samples and false durations.
- **Impact:** MEDIUM (wrong metrics).
- **Resolution:** FIXED. `NODE_EMITTED` is excluded; the integration test asserts that no `status="emitted"` sample exists.
- **Notes:** the observer infers "terminal" from "not started". Any future non-terminal node event needs the same exclusion.

### F2 — A plaintext secret in config is rejected at draft save, not first at publish
- **Finding:** my working assumption was rejection at publish (`WorkflowValidationFailedError`). `WorkflowService.update_draft` already raises `PlaintextSecretError` (`domain/workflow.py`).
- **Impact:** LOW. The behaviour is stricter than assumed and correct.
- **Resolution:** FIXED (in the test). The integration test expects `PlaintextSecretError` and is named `..._rejected_at_draft_save`. `docs/sdk.md` §2 states "Draft save / publish".

### F3 — The "execute not implemented" branch of `check_node_contract` is unreachable
- **Finding:** `Node` is an ABC with an abstract `execute`. A subclass without `execute` cannot be instantiated, so `run_node` can never receive one. mypy flagged the first test (`Cannot instantiate abstract class`).
- **Impact:** LOW.
- **Resolution:** WORKAROUND. The test now covers the reachable contract violation, a missing `definition`. The domain check itself was not changed.

### F4 — `context.credentials` being optional pushed a `None` check into every credential-using node
- **Finding:** `http.py` and `smtp.py` each carry a node-specific "credentials unavailable" branch.
- **Impact:** LOW (ergonomics).
- **Resolution:** FIXED for SDK users with `resolve_credential`. The built-ins were deliberately left with their existing codes (§5.5).

### F5 — The Docker availability probe intermittently skips Testcontainers tests (pre-existing)
- **Finding:** on one run, `tests/integration/test_sdk_node_runtime.py` reported 3 skipped. The next run executed and exposed F2. This is the same behaviour recorded as F7 in the Phase 15 report.
- **Impact:** MEDIUM. A skipped integration test can hide failures.
- **Resolution:** OPEN (pre-existing, not addressed in this phase).
- **Notes:** the final full run executed all Docker tests; its only skips were the 5 external smoke tests.

### F6 — Ruff `S105` flags secret-looking test constants named `SECRET`
- **Resolution:** FIXED by renaming them to `CANARY`, the naming convention the existing credential and LLM tests already use.
- **Impact:** LOW (tooling).

---

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Affected area | Origin |
|---|---|---|---|---|---|
| `node.emitted` counted as a finished node (F1) | Observer inferred terminal events as "not `NODE_STARTED`" | Exclude `NODE_EMITTED` | `test_sdk_node_runs_through_the_engine` (`status="emitted"` sample is `None`; `completed` == 1) | `observability/execution.py` | Would have been introduced in this phase; caught before any test run |
| Integration test expected the wrong exception (F2) | Wrong assumption about when plaintext secrets are rejected | Test expects `PlaintextSecretError` | `test_plaintext_secret_in_config_is_rejected_at_draft_save` | Test only | Introduced in this phase (test) |
| Contract test did not type-check (F3) | Instantiated an abstract class | Test missing `definition` instead | `test_run_node_rejects_contract_violations` | Test only | Introduced in this phase (test) |
| Oversized-payload test expected the wrong byte count | Arithmetic slip in the test (25 vs. 27 bytes) | Corrected expectation | `test_logs_sizes_but_never_the_secret_or_body` | Example test | Introduced in this phase (test) |
| Docker probe skips integration tests (F5) | Pre-existing probe flakiness | None | — | Test infrastructure | Pre-existing |

---

## 11. Fixes Applied

1. **Observer metrics for `node.emitted` (F1).**
   - Symptom: the event would have been counted as a node completion with `status="emitted"`.
   - Root cause: terminal status was derived by exclusion.
   - Implementation: `if kind not in (EventType.NODE_STARTED, EventType.NODE_EMITTED)`.
   - Verification: integration test assertions on both samples.
2. **Credential ergonomics (F4).**
   - Symptom: every SDK node would repeat the `credentials is None` check.
   - Implementation: `NodeExecutionContext.resolve_credential`.
   - Verification: `test_engine_without_credential_store` (`CREDENTIALS_UNAVAILABLE`), `test_unknown_credential_is_a_configuration_error`, and `test_credentials_resolve_only_references_in_the_config`.
3. **Built-ins moved onto the SDK** (follow-up debt resolution).
   - Implementation: import changes in five node modules plus an architecture guard.
   - Verification: the guard fails on a deliberate violation; the full suite passes.

---

## 12. Testing & Verification

### Unit Tests
- **`tests/test_sdk.py`** (18 tests, all pass):
  - public surface pinned for `akasha.sdk` and `akasha.sdk.testing`; every name resolves;
  - `SDK_VERSION` matches `SEMVER_RE`;
  - an AST scan proves `backend/examples/**` imports no `akasha.*` module other than `akasha.sdk*`;
  - malformed and duplicate metric/event names raise `NodeContractError` (4 cases);
  - `NodeMetrics` accumulates declared names and rejects undeclared ones;
  - increments of −1, NaN, inf, `True` and `"1"` are rejected (5 cases);
  - `NodeEvents` rejects undeclared names and enforces the cap of 100;
  - `run_node` returns outputs, events and metrics;
  - `run_node` rejects a node without a definition, a wrong output type, and a return value that is not a `NodeResult`;
  - `CREDENTIAL_NOT_REFERENCED` for a reference outside the config, and success for one inside it.
- **`tests/domain/test_execution.py`** (+2 tests, pass):
  - a running node records `node.emitted` with `{name, attempt: 1}`;
  - completed and pending nodes are rejected with `InvalidStateTransitionError`.

### Component Tests
- **`backend/examples/hmac_signer/test_node.py`** (15 tests, all pass). Written as an external developer would, with `akasha.sdk` / `akasha.sdk.testing` and pytest only:
  - contract;
  - canonical body and independently computed HMAC;
  - key-order independence;
  - sha512;
  - metrics and events;
  - `caplog`: structured fields are present, the secret and the payload are absent;
  - invalid config (5 cases: missing credential, enum, minimum, unknown key, plaintext secret);
  - missing input;
  - oversized payload: VALIDATION, `HMAC_PAYLOAD_TOO_LARGE`, not retryable, string details;
  - unknown credential;
  - no credential store.
- **`tests/nodes/`** (existing built-in tests): pass after the import change.

### Integration Tests
- **`tests/integration/test_sdk_node_runtime.py`** (3 tests, PostgreSQL 17 via Testcontainers, pass):
  - `core.input → community.hmac_sign` is registered, saved, published and run by `ExecutionRuntime` with the secret from `CredentialService` (encrypted with `SecretCipher`). It asserts:
    - status COMPLETED;
    - a verifiable signature;
    - the persisted event order `node.started, node.emitted:payload_signed, node.completed`;
    - `akasha_node_metric_total` samples;
    - lifecycle metric correctness.
  - A failed attempt (`max_payload_bytes: 5`) gives status FAILED, `HMAC_PAYLOAD_TOO_LARGE`, the order `node.started, node.emitted:payload_rejected, node.failed`, and no `payloads_signed` sample.
  - A plaintext secret in the config is rejected at draft save.

### Infrastructure Tests
- PostgreSQL through the existing Testcontainers fixtures (`tests/integration/conftest.py`). No new infrastructure. See F5 for the flaky Docker probe.

### Security Tests
- plaintext secret rejected (harness and Engine);
- credential scope (`CREDENTIAL_NOT_REFERENCED`);
- secret and payload absent from logs;
- undeclared metric and event names rejected;
- event cap;
- SDK and example import boundaries (AST guards).

### Not performed
- API tests: no API changes.
- E2E tests.
- Performance tests.
- Accessibility: not applicable to the backend.

---

## 13. Quality Gates

Final run on the current working tree:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 143 files already formatted |
| Linter (`uv run ruff check .`) | PASS | S105 was hit during development and fixed (F6); I001 import order was auto-fixed |
| Type Checker (`uv run mypy .`, strict) | PASS | 140 source files, including `examples/`. One error during development was fixed (F3). |
| Unit + component tests | PASS | Part of the full suite |
| Integration Tests (Testcontainers PostgreSQL) | PASS | Full run: `1245 passed, 5 skipped` (the skips are the opt-in OpenRouter smoke tests). One earlier targeted run skipped the 3 Docker tests (F5). |
| API Tests | PASS (unchanged) | Existing API tests are in the suite; none added |
| E2E Tests | N/A | Not in phase scope |
| Build | N/A | No build step beyond the `uv` package install, which succeeded (the local package was rebuilt by `uv run`) |
| Security Checks | PASS | Security tests in §12; no dependency-audit tool exists in the repo |
| Accessibility | N/A | Backend |
| Warnings | 1 pre-existing | `StarletteDeprecationWarning` from `fastapi.testclient`, unrelated to this phase |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Roadmap: "A developer can implement a custom Node without importing internal Engine implementation modules." | PASS | `examples/hmac_signer/node.py` and `test_node.py` import only `akasha.sdk` / `akasha.sdk.testing`, enforced by `test_examples_import_only_the_sdk`. The same node runs inside the real Engine (`test_sdk_node_runs_through_the_engine`). |
| Roadmap tests: "Create an external-style example custom Node using only the SDK." | PASS | `backend/examples/hmac_signer/` with 15 developer-style tests and a README |
| Scope: Node, NodeMetadata, Input, Output, ConfigSchema, ExecutionContext | PASS | Exported by `akasha.sdk` (names in §5.1) |
| Scope: Logger | PASS | `context.logger`; `caplog` test; `docs/sdk.md` §4 |
| Scope: Metrics | PASS | `NodeMetrics` + `akasha_node_metric_total`; unit and integration tests |
| Scope: Credentials | PASS | `resolve_credential`; unit, component and integration tests |
| Scope: Events | PASS | `NodeEvents` + `node.emitted`; unit, domain and integration tests |
| Scope: NodeError | PASS | `NodeError` / `NodeErrorKind`; error-path tests |
| Scope: Artifacts | FAIL (deferred) | No artifact storage or service exists in the Engine (architecture §106 is conceptual only). Not blocking: the acceptance criterion does not depend on it. |
| Utilities "where practical": `node test` | PARTIAL | `akasha.sdk.testing.run_node` as a library function; no CLI command (CLI is Phase 21) |
| Utilities "where practical": `node validate` | PARTIAL | `check_node_contract` plus `run_node`'s config and port checks; no CLI command |
| Utilities "where practical": `node package` | FAIL (deferred) | Packaging is part of the Plugin System (Phase 17). Not blocking. |
| Prompt: tests for registration/definition, configuration, execution context, inputs/outputs, errors, logging, credentials, SDK compatibility | PASS | §12 |
| Prompt: "A developer can understand ... a custom Node" | PASS | `docs/sdk.md` (guide, reference, guarantees table, testing, compatibility), example README, `akasha.sdk` module docstring |
| Prompt: quality gates pass | PASS | §13 |

The status COMPLETE WITH LIMITATIONS follows from the artifacts gap and the utility items that were delivered as library functions or deferred.

---

## 15. Security Considerations

- **Trust model:** custom nodes are trusted, in-process code. `docs/sdk.md`, the `akasha.sdk` docstring and architecture §34 state that the SDK is not a sandbox. Nothing in this phase changes isolation.
- **Credentials:**
  - Only references in the node's own config resolve (the existing `NodeCredentials` rule, reused by the harness).
  - Resolved values are registered for known-value redaction (existing behaviour).
  - The example resolves the secret only after its size check.
- **Secrets in config:** rejected at draft save and publish by the existing checks; the harness applies the same `plaintext_secret_paths` check.
- **Logging:** the example logs sizes and the algorithm only, as tested. Central redaction is unchanged.
- **Events:** name-only payloads, so workflow data cannot enter the event store through the SDK. Names come from the declaration, not from data.
- **Metrics:** declared names only, so no label can be derived from user input (AGENTS §41).
- **Resource limits:** at most 100 events per attempt, so a buggy loop cannot flood the event store. Metrics have no cap: counter increments do not create new series.
- **Error disclosure:** `NodeContractError` raised by node code becomes `NODE_UNEXPECTED_ERROR` with only the type name kept (existing executor behaviour).
- **Timing:** `verify` in the example uses `hmac.compare_digest`.

Not applicable to this phase: authentication and authorization (Phase 20), SSRF, path traversal, XSS/CSRF, and expression evaluation.

---

## 16. Performance Considerations

- **Per attempt:** two small collector objects and, at attempt end, one `record_node_event` per emitted name (bounded to 100) and one counter increment per declared metric. Emitted events are written in the save that already persists the attempt's outcome, so no extra database round trip is added.
- **Event store volume:** grows by up to 100 rows per attempt for nodes that emit events. No measurement was performed.
- **Cardinality:** `akasha_node_metric_total` has at most (installed node types × their declared metrics) series.

No benchmarks were run.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/sdk.md` (new) | The developer guide the roadmap lists as a phase document | Minimal node; Engine guarantees table; reference (definition, context, results, errors); logging; metrics; events; credentials (error-code table); testing with `run_node`; registration until Phase 17; compatibility policy |
| `docs/architecture.md` §19 | New event type | `node.emitted` semantics, ordering, metrics exclusion |
| `docs/architecture.md` §33 | Implementation note | Facade design, contract additions, per-attempt flush rationale and revisit trigger, built-ins as SDK consumers, what is not provided |
| `docs/architecture.md` metrics table | New metric | `akasha_node_metric_total` row |
| `backend/examples/hmac_signer/README.md` (new) | Example documentation | Feature→code map, how to run its tests, pointer to the integration test |
| `backend/src/akasha/nodes/__init__.py` | Layer docstring | Built-ins use `akasha.sdk` |

**Should be updated later:**
- The frontend roadmap and event consumers need to learn about `node.emitted` when live execution views are built.
- `docs/sdk.md` §9 (registration) must be rewritten in Phase 17.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or changed; `uv.lock` is unchanged.
- **Configuration (`backend/pyproject.toml`):**
  - `[tool.pytest.ini_options] testpaths = ["tests", "examples"]`, so the example's own tests run in CI;
  - `[tool.ruff] src` += `"examples"`, for first-party import sorting;
  - `[tool.ruff.lint.per-file-ignores] "examples/**/test_*.py" = ["S101"]`, because `assert` is the pytest idiom (same reason as for `tests/**`).
- **Environment variables, Docker, database, Redis:** no changes.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Blocker | Affects this phase's completion |
|---|---|---|---|---|
| Artifacts in the SDK | No artifact storage or service exists | Not scheduled as its own backend phase; lands with the artifact subsystem | Artifact subsystem | No (limitation) |
| `node package` | Packaging is part of the Plugin System | Phase 17 | — | No |
| `node test` / `node validate` as commands | No CLI exists yet | Phase 21 | — | No (library equivalents exist) |
| Registering custom nodes without composition-root code | Discovery and installation are part of the Plugin System | Phase 17 | — | No |
| SDK-compatibility matching (`PluginMetadata.sdk_compatibility` vs. `SDK_VERSION`) | Part of plugin compatibility checks | Phase 17 | — | No |
| Live streaming of node events and metrics | No consumer; conflicts with the single-writer runtime model (§5.4) | When a live-update consumer exists | Live-update gateway | No |

---

## 20. Known Limitations

- **Artifacts:** no SDK artifact API.
- **Telemetry timing:** node events and metrics become visible only when the attempt ends. Emissions from attempts that are still running when `_schedule` aborts through an exception (a persistence error or outer cancellation) are not recorded.
- **Event payloads:** node events carry a name only; data belongs in outputs.
- **Metric kind:** only counters are offered. There are no gauges or histograms; the engine already measures node duration.
- **Harness scope:** `run_node` does not reproduce:
  - Engine timeouts;
  - runtime-initiated cancellation (only `is_cancelled` can be injected);
  - known-secret masking of outputs;
  - graph-level validation.
- **Config defaults:** `NodeConfigSchema` does not apply `default` values, so nodes read defaults with `config.get(key, default)`, as the example does.
- **Error codes:** built-in HTTP and SMTP nodes report credentials-unavailable with their own codes, while SDK nodes using `resolve_credential` get `CREDENTIALS_UNAVAILABLE`.
- **Sandboxing:** custom nodes run in the Engine process with full privileges.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target phase |
|---|---|---|---|---|---|
| Docker availability probe intermittently skips all Testcontainers tests (F5, pre-existing) | Integration failures can be hidden by a green run | MEDIUM | Test infrastructure (`tests/support.docker_available`, integration conftest) | Make CI fail instead of skip when Docker is expected; investigate the probe's timeout | Before or with Phase 26 (operations), earlier if CI is set up |
| Observer infers terminal node events by exclusion (F1 note) | A future non-terminal node event would again be miscounted | LOW | `observability/execution.py` | Switch to an explicit set of terminal node events when the next node event type is added | Next change to `EventType` |

No other known open issues result from this phase.

---

## 22. Architectural / Product Decisions

- **The SDK is the domain contract, curated.** No wrapper types exist. The `akasha.sdk.__all__` list plus `SDK_VERSION` is the compatibility contract, and `tests/test_sdk.py::PUBLIC_API` pins it.
- **Metric and event names are part of a node's definition.** This is how the SDK keeps cardinality bounded and event vocabularies reviewable. Plugins inherit the rule.
- **Node events are name-only by design.** Adding payloads would reopen the closed-payload security rule and needs its own decision.
- **Telemetry is flushed per attempt.** This is a deliberate consequence of the single-writer runtime (only the scheduler loop touches the aggregate), not a shortcut. Revisit when a live-update consumer exists.
- **Built-in nodes consume the SDK**, enforced by an architecture test. They act as the SDK's largest regression suite.
- **`akasha.sdk.testing` depends on runtime and application internals** so that the test harness applies exactly the production credential rule. Custom node code never imports those internals itself.
- **Custom nodes are trusted code (V1)**, restated in the public SDK documentation.

---

## 23. Repository / Git State

- **Branch:** `main`. HEAD is `e332fff backend phase 15 - llm node`. Nothing from this phase has been committed.
- **Modified (16):**
  - `backend/pyproject.toml`;
  - `backend/src/akasha/domain/{event,execution,node}.py`;
  - `backend/src/akasha/nodes/{__init__,control,core,http,llm,smtp}.py`;
  - `backend/src/akasha/observability/{execution,metrics}.py`;
  - `backend/src/akasha/runtime/runtime.py`;
  - `backend/tests/domain/test_execution.py`;
  - `backend/tests/test_architecture.py`;
  - `docs/architecture.md`.

  In total: 261 insertions and 27 deletions.
- **Untracked:**
  - `backend/examples/`;
  - `backend/src/akasha/sdk/`;
  - `backend/tests/integration/test_sdk_node_runtime.py`;
  - `backend/tests/test_sdk.py`;
  - `docs/sdk.md`;
  - `docs/reports/backend/phase-16-python-node-sdk.md`.
- **Unrelated changes:** none detected. The working tree was clean at the start of the phase.

---

## 24. Phase Completion Assessment

### Implemented
- the public SDK facade `akasha.sdk` with `SDK_VERSION`;
- the test harness `akasha.sdk.testing`;
- declared metrics and events with per-attempt collectors;
- `resolve_credential`;
- the `node.emitted` event and `akasha_node_metric_total`, wired through the runtime;
- the example Node `community.hmac_sign` with tests and a README;
- built-in nodes on the SDK;
- `docs/sdk.md` and the architecture updates.

### Verified
- Example tests (15), SDK tests (18), domain tests (+2) and architecture guards (3) pass.
- Integration tests against PostgreSQL (3) pass.
- Full suite: 1245 passed, 5 skipped (external).
- Ruff, ruff format and strict mypy pass.

### Not Implemented
- the artifact SDK API;
- `node package`;
- `node test` / `node validate` as commands.

### Deferred
- plugin packaging, discovery, installation and SDK-compatibility checks (Phase 17);
- the CLI (Phase 21);
- artifacts (artifact subsystem);
- live telemetry (when a consumer exists).

### Open
- the Docker probe flakiness (F5, pre-existing);
- the observer's terminal-by-exclusion logic (F1 note).

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**Phase 17 is the Plugin System.**

**It can assume:**
- A custom Node is any `akasha.sdk.Node` subclass with a valid `definition`.
- `NodeRegistry.register(node_cls, factory=None)` validates the contract and rejects duplicate ids (`NODE_ALREADY_REGISTERED`).
- `check_node_contract` is the reusable validation entry point. `run_node` can drive a plugin's nodes in plugin self-tests.

**Contracts now available:**
- `SDK_VERSION = "1.0.0"`: match `PluginMetadata.sdk_compatibility` against it using `akasha.domain.primitives.version_satisfies`.
- `NodeDefinition.metrics` and `.events`: statically inspectable declarations for plugin validation.
- `node.emitted` and `akasha_node_metric_total`.

**Must not change casually:**
- the `akasha.sdk.__all__` names and their behaviour, which need an SDK major bump;
- the closed, name-only payload of `node.emitted`;
- the rule that node tasks never touch the aggregate;
- the built-in SDK import guard.

**Known constraints:**
- One version per node type id in the registry (Phase 09 decision). Plugins cannot install side-by-side versions of the same node id.
- Custom nodes are trusted, in-process code. The loader must not claim isolation.
- `docs/sdk.md` §9 (registration) must be rewritten when plugin installation replaces manual `registry.register` calls.
- The example lives in `backend/examples/hmac_signer/`. It is a natural candidate for the Phase 17 sample plugin, but it is not packaged yet.

**Open issues to be aware of:**
- Docker probe flakiness (F5): do not trust a green integration run that reports Testcontainers skips.
