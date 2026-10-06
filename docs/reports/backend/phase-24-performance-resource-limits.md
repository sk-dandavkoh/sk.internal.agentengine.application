# Phase 24 — Performance + Resource Limits

## 1. Executive Summary

**Supposed to achieve.** Roadmap §28 (Phase 24) has the goal "Protect the
Engine from uncontrolled workloads." It asks for configurable limits on
execution duration, node duration, loop iterations, concurrency, output
size, artifact size, HTTP response size, LLM token usage, retry count and
execution depth, plus concurrency controls. The single acceptance
criterion is: "A malformed or badly configured Workflow cannot trivially
consume unlimited resources."

**State found before implementation.** Inspected in
`runtime/runtime.py`, `application/executions.py`, `domain/execution.py`,
`bootstrap.py`, `config.py`, `nodes/http.py`, `nodes/llm.py` and
`application/ai.py`:

- Unlimited by default:
  - Execution and node timeouts existed (`TimeoutPolicy`), but `None`
    meant unlimited. No production caller (API, trigger service, CLI)
    passed a `TimeoutPolicy`, so every real execution ran without a time
    limit.
- No upper bound:
  - `RetryPolicy.max_attempts` was unbounded.
  - Node outputs reached memory, PostgreSQL (JSONB) and the API at any
    size. The architecture said explicitly: "Size limits (Phase 24) … are
    not applied yet."
  - Executions per workflow were unbounded.
  - Graph size and depth had no limit.
- Hard-coded or partial:
  - `ExecutionRuntime(max_concurrency=8)` (nodes in flight per execution)
    was a hard-coded default; bootstrap did not configure it.
  - LLM `max_tokens` was bounded only by the `core.llm` config schema
    (`MAX_OUTPUT_TOKENS = 100_000`); unset meant the provider's default.
- Already bounded:
  - The HTTP response size (`AKASHA_HTTP_MAX_RESPONSE_BYTES`, Phase 09).
  - Executions per worker (`AKASHA_WORKER_CONCURRENCY`, Phase 22).

**Actually implemented.**

- **`ResourceLimits`:** a domain value object
  (`backend/src/akasha/domain/limits.py`), built from new `AKASHA_LIMIT_*`
  settings and validated at startup.
- **Admission control in `ExecutionService.create_execution`:**
  - **Durations:** a missing timeout gets the cap, and one above the cap
    is rejected.
  - **Attempts:** a retry policy above the cap is rejected.
  - **Graph:** too many nodes, or a graph too deep, is rejected.
  - **Capacity:** unfinished executions per workflow are capped (HTTP 429),
    made atomic by a workflow-row lock.
- **Runtime enforcement of output size:** per node and per execution. The
  execution budget is recounted on resume.
- **Configuration wiring:** the per-execution node concurrency is
  configurable.
- **`AIService`:** caps output tokens per request.
- **Observability:** a new metric
  `akasha_resource_limit_exceeded_total{limit}` and two structured log
  events.

**Resulting state.**

- Every execution persisted through `ExecutionService` carries finite
  duration limits.
- Per execution, these are bounded: run time, attempts per node, number
  and depth of nodes, stored output, and nodes in flight.
- Unfinished executions per workflow are bounded.
- No-op items (with justification, §9):
  - Loop iterations and execution nesting depth: no loop, subgraph or
    sub-execution construct exists.
  - Artifact size: no artifact store exists.

**Acceptance.** The criterion is satisfied with evidence (§14). The
limitations in §20 remain, the most relevant being sequential trigger
recursion through the engine's own webhook.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap `docs/roadmap-backend.md` §28:

- Goal: "Protect the Engine from uncontrolled workloads."
- Scope: "Implement configurable limits for: execution duration, node
  duration, loop iterations, concurrency, output size, artifact size, HTTP
  response size, LLM token usage, retry count, execution depth.
  Implement concurrency controls."
- Tests: "Test that every relevant limit is enforced."
- Acceptance: "A malformed or badly configured Workflow cannot trivially
  consume unlimited resources."
- Phase prompt (roadmap): "Do not perform unrelated optimization. Stop
  after Phase 24."

Session request (additional):

- Limits must be configurable, validated, observable and enforced by the
  correct layer, with no scattered hard-coded limits.
- Test every limit exactly at its limit and one beyond it.
- Also test nested/combined limits, cancellation after a limit, and the
  persisted failure state.
- Run controlled performance/concurrency tests where useful.

Relevant rules:

- AGENTS §43: limits enforced server-side; test within and over the
  limit.
- AGENTS §41: bounded metric labels.
- AGENTS §52: configuration through startup injection.
- AGENTS §114: run the quality gates.
- backend/AGENTS §2: resource limits are backend-authoritative.
- Architecture §74 (concurrency levels), §75 (resource limits), §107 (API
  payload limits), §130 (infinite-loop protection) and §131 (execution
  depth).

**Not responsible for:**

- API payload limits and rate limiting (§107/§108). These are in the
  Phase 25 Security Hardening scope ("API limits").
- Unrelated performance optimization.
- New loop, subgraph or artifact constructs.

---

## 3. Scope Implemented

### Domain (`backend/src/akasha/domain/limits.py`, new)

- `Limit` (StrEnum): `execution_duration`, `node_duration`,
  `node_attempts`, `graph_nodes`, `graph_depth`, `active_executions`,
  `node_output`, `execution_output`. It is the metric label set.
- `ResourceLimitError(AkashaError)`: code `RESOURCE_LIMIT_EXCEEDED`, HTTP
  422, `details = {limit, maximum, requested}`, attribute `limit`.
- `ExecutionCapacityError(ResourceLimitError)`: code
  `EXECUTION_CAPACITY_EXCEEDED`, HTTP 429.
- `ResourceLimits` (frozen dataclass): fields and defaults are in the
  §3 table below.
  - `__post_init__` validates every field.
  - `policies(timeout, retry) -> (TimeoutPolicy, RetryPolicy)`
  - `check_graph(graph)`
  - `check_capacity(active)`
  - `check_output(node_id, size, total) -> tuple[Limit, ExecutionError] | None`
- `json_size(value) -> int`: compact UTF-8 JSON byte count.
- `graph_depth(graph) -> int`: the longest path in nodes.

### Configuration (`backend/src/akasha/config.py`)

- Ten new settings: `limit_execution_timeout_seconds`,
  `limit_node_timeout_seconds`, `limit_node_attempts`,
  `limit_graph_nodes`, `limit_graph_depth`,
  `limit_active_executions_per_workflow`, `limit_node_output_bytes`,
  `limit_execution_output_bytes`, `limit_execution_concurrency` and
  `limit_llm_output_tokens`. Each has pydantic range constraints.
- `Settings.resource_limits() -> ResourceLimits`. It is also called from
  the existing `model_validator`, so contradictory limits fail at startup.

### Application

- `ExecutionService` (`application/executions.py`):
  - **Constructor:** new parameter `limits: ResourceLimits | None`; the
    default is `ResourceLimits()`.
  - **`create_execution`:** now delegates to `_create`. On
    `ResourceLimitError` it:
    - increments the metric
    - logs `execution_rejected`
    - re-raises the error
  - **`_create`:**
    - applies `limits.policies` before opening the transaction
    - inside the transaction, calls `check_graph(snapshot.graph)` and
      `check_capacity(await uow.executions.count_unfinished(workflow_id))`
      before `Execution.create`
- `ExecutionRepository` protocol (`application/persistence.py`): new
  method `count_unfinished(workflow_id) -> int`.
- `AIService` (`application/ai.py`): new keyword
  `max_output_tokens: int = 16_384`, validated `>= 1`. In `complete`:
  - `max_tokens is None` → the request is `replace`d with the limit
  - `max_tokens > limit` → `ProviderError(kind=CONFIGURATION)`, logged
    via `_log_failure`, raised before credential resolution or any
    adapter call

### Persistence (`infrastructure/persistence.py`)

- `SqlAlchemyExecutionRepository.count_unfinished`:
  - first runs `SELECT workflows.id … FOR NO KEY UPDATE`
    (`with_for_update(key_share=True)`)
  - then counts executions of the workflow whose `status` is not in
    `TERMINAL_EXECUTION_STATUSES`

### Runtime (`runtime/runtime.py`)

- `ExecutionRuntime.__init__`: new parameter
  `limits: ResourceLimits | None`.
- `_CancelRequest` (per-run state): new field `output_bytes: int = 0`.
- `_run`: on resume, `output_bytes` is recomputed from the persisted
  `COMPLETED` node outputs.
- New method `_limit_output(outcome, node_id, attempt, request)`. It turns
  a `NodeResult` that breaks a cap into the `ExecutionError` from
  `check_output`, increments the metric and logs
  `node_output_limit_exceeded`.
- `_record` and `_stop` take `request` and route every outcome through
  `_limit_output`.
- The module docstring documents the split between admission and runtime
  enforcement.

### Observability (`observability/metrics.py`)

- New counter `akasha_resource_limit_exceeded` (exposed as `_total`),
  label `limit`.

### Composition (`bootstrap.py`)

- `limits = settings.resource_limits()` is passed to:
  - `ExecutionService(limits=...)`
  - `ExecutionRuntime(limits=..., max_concurrency=settings.limit_execution_concurrency)`
- `create_ai_service` passes
  `max_output_tokens=settings.limit_llm_output_tokens`.

### Limit → enforcement map

| Roadmap limit | Setting (default) | Enforced by | Over the limit |
|---|---|---|---|
| execution duration | `AKASHA_LIMIT_EXECUTION_TIMEOUT_SECONDS` (3600) | admission; existing runtime deadline | 422; at run time `TIMEOUT` |
| node duration | `AKASHA_LIMIT_NODE_TIMEOUT_SECONDS` (900) | admission; existing `execute_node` timeout | 422; at run time `NODE_TIMEOUT` |
| retry count | `AKASHA_LIMIT_NODE_ATTEMPTS` (5 = 4 retries) | admission | 422 |
| execution depth | `AKASHA_LIMIT_GRAPH_DEPTH` (100) | admission | 422 |
| loop iterations (bounded via graph size × attempts) | `AKASHA_LIMIT_GRAPH_NODES` (500) | admission | 422 |
| concurrency, per workflow | `AKASHA_LIMIT_ACTIVE_EXECUTIONS_PER_WORKFLOW` (100) | admission (row lock) | 429 |
| concurrency, per execution | `AKASHA_LIMIT_EXECUTION_CONCURRENCY` (8) | runtime scheduler `_launch` | waits for a slot |
| concurrency, per worker | `AKASHA_WORKER_CONCURRENCY` (4, existing) | `Worker` | job waits in the queue |
| output size, per node | `AKASHA_LIMIT_NODE_OUTPUT_BYTES` (1 MiB) | runtime | node `NODE_OUTPUT_TOO_LARGE` |
| output size, per execution | `AKASHA_LIMIT_EXECUTION_OUTPUT_BYTES` (16 MiB) | runtime | node `EXECUTION_OUTPUT_TOO_LARGE` |
| artifact size | n/a, no artifact store (§9) | — | — |
| HTTP response size | `AKASHA_HTTP_MAX_RESPONSE_BYTES` (5 MiB, existing) | `core.http` / `core.web_search` | node `HTTP_RESPONSE_TOO_LARGE` |
| LLM token usage | `AKASHA_LIMIT_LLM_OUTPUT_TOKENS` (16384) | `AIService` | `PROVIDER_CONFIGURATION` → node kind `CONFIGURATION` |

The two output-limit failures have kind `PERMANENT`, so a retry policy
never repeats them.

### Testing

- New suites:
  - `tests/domain/test_limits.py`: 16 test functions, 25 cases.
  - `tests/integration/test_resource_limits.py`: 18 functions, 21 cases.
- Extended suites (§12):
  - `tests/test_config.py`
  - `tests/application/test_ai_service.py`
  - `tests/nodes/test_http_node.py`
  - `tests/integration/test_triggers_api.py`
  - `tests/application/test_trigger_service.py` (fake repository method)

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/limits.py` | Created (216 lines) | `ResourceLimits`, `Limit`, limit errors, `json_size`, `graph_depth` |
| `backend/src/akasha/config.py` | Modified (+32) | `AKASHA_LIMIT_*` settings, `resource_limits()`, fail-fast validation |
| `backend/src/akasha/application/executions.py` | Modified (+50) | Admission control, metric, `execution_rejected` log |
| `backend/src/akasha/application/persistence.py` | Modified (+6) | `ExecutionRepository.count_unfinished` contract |
| `backend/src/akasha/infrastructure/persistence.py` | Modified (+21) | `count_unfinished` with workflow-row lock |
| `backend/src/akasha/runtime/runtime.py` | Modified (+54/−4) | Output limits, resume recount, `limits` injection |
| `backend/src/akasha/application/ai.py` | Modified (+22/−3) | Output-token limit |
| `backend/src/akasha/observability/metrics.py` | Modified (+8) | `akasha_resource_limit_exceeded_total{limit}` |
| `backend/src/akasha/bootstrap.py` | Modified (+5/−2) | Wiring of limits, node concurrency and token limit |
| `backend/src/akasha/domain/execution.py` | Modified (+3/−1) | `TimeoutPolicy` docstring: `None` is replaced at admission |
| `backend/tests/domain/test_limits.py` | Created | Unit boundary tests |
| `backend/tests/integration/test_resource_limits.py` | Created | PostgreSQL boundary, combined, cancellation, resume and stress tests |
| `backend/tests/test_config.py` | Modified (+36) | Env loading and fail-fast of limits |
| `backend/tests/application/test_ai_service.py` | Modified (+39) | Token limit at/beyond/unset, settings wiring |
| `backend/tests/nodes/test_http_node.py` | Modified (+16/−8) | HTTP response size exactly at and one byte beyond the limit |
| `backend/tests/integration/test_triggers_api.py` | Modified (+23/−2) | Test container derives limits from `Settings`; 429 API test |
| `backend/tests/application/test_trigger_service.py` | Modified (+7) | `FakeExecutions.count_unfinished` (protocol conformance) |
| `backend/README.md` | Modified (+16) | Resource-limit settings table |
| `docs/architecture.md` | Modified (+42/−2) | §75 implementation, output-storage note, metrics list |
| `docs/reports/backend/phase-24-performance-resource-limits.md` | Created | This report |

There are no new directories, migrations, dependencies or generated
files.

---

## 5. Architecture Changes

The changes are clarifications within the existing architecture (§74,
§75). The dependency direction is unchanged:
`tests/test_architecture.py` passes, and `domain/limits.py` imports only
domain modules and `akasha.errors`.

### 5.1 One limits value object, injected

- **Decision:** All engine-wide caps live in
  `akasha.domain.limits.ResourceLimits`, built only by
  `Settings.resource_limits()` and injected into `ExecutionService` and
  `ExecutionRuntime`.
- **Context:** The session required "no scattered hard-coded limits".
  Before this phase, limits were absent or were defaults in constructors.
- **Chosen approach:** A frozen dataclass with its own invariants. The
  settings mirror its fields.
- **Alternatives considered:**
  - Passing individual numbers to each service (scattered and
    unvalidated as a set).
  - Pydantic settings used directly by the domain (the domain must not
    depend on `pydantic_settings`; `tests/test_architecture.py` forbids
    it).
- **Reason:** One place knows the numbers. Cross-field invariants
  (node ≤ execution) are enforced once. The domain stays
  framework-free.
- **Consequences:** Future limits (iterations, nesting, token budgets)
  extend this object and the `Limit` enum.

### 5.2 Enforcement at admission for anything decidable before running

- **Decision:** Durations, attempts, graph size and depth, and workflow
  capacity are enforced in `ExecutionService.create_execution`.
- **Context:** Every trigger path converges on `create_execution`
  (documented in `application/executions.py`).
- **Chosen approach:** `policies()` before the transaction;
  `check_graph` and `check_capacity` inside it, before the aggregate is
  created. Missing timeouts are replaced by the cap and persisted.
- **Alternatives considered:**
  - Enforcing in the runtime's `load_plan` (too late: rows are already
    stored and queued).
  - Enforcing at publish (the limits are operator configuration, not
    workflow validity, and can change after publish).
- **Reason:** A rejected request stores nothing and consumes no queue or
  worker capacity. Persisted policies make each execution's limits
  explicit and reproducible (AGENTS §73).
- **Consequences:** A resumed execution keeps the limits it was admitted
  with. Any new entry point that creates executions must go through
  `ExecutionService`.

### 5.3 Output limits in the runtime's recording step

- **Decision:** Output size is checked in `ExecutionRuntime._record` (via
  `_limit_output`), not in nodes, the executor or the repository.
- **Context:** The runtime's single scheduler coroutine is the only place
  where a node outcome becomes aggregate state (module docstring).
- **Alternatives considered:**
  - In `execute_node`: possible for the per-node limit, but the execution
    total needs per-run state the executor does not have.
  - In the repository: too late, because events and downstream inputs
    already see the value.
  - In the domain aggregate: would require persisting output limits per
    execution.
- **Reason:** One check covers both limits and every node type, including
  plugins.
- **Consequences:** Output limits are the running engine's values, not
  persisted per execution (§20).

### 5.4 Capacity race prevention through a workflow-row lock

- **Decision:** `count_unfinished` locks the workflow row with
  `FOR NO KEY UPDATE` before counting.
- **Context:** A plain count lets concurrent admissions all pass. The
  stress test proves it: with the lock removed, it failed 3 out of 3
  runs.
- **Alternatives considered:**
  - `pg_advisory_xact_lock(hashtext(workflow_id))` (hash collisions; less
    explicit).
  - Plain `FOR UPDATE` (would also block foreign-key key-share locks).
  - An approximate cap without a lock.
- **Reason:** An exact cap with the smallest lock scope that serializes
  only admissions to the same workflow.
- **Consequences:** Admissions to one workflow serialize for one short
  transaction. Draft updates (CAS `UPDATE` on the workflow row) can
  briefly wait for an admission.

---

## 6. Domain / Data Model Changes

- **New value object:** `ResourceLimits`.
- **New enum:** `Limit`.
- **New errors:** `ResourceLimitError` (422) and
  `ExecutionCapacityError` (429).
- **New pure functions:** `json_size` and `graph_depth`.
- **New repository contract method:**
  `ExecutionRepository.count_unfinished`.
- **Changed semantics, no code change:** `TimeoutPolicy` still allows
  `None`. However, executions created through `ExecutionService` are now
  persisted with the effective (capped) values instead of `None`.
  - This uses the existing JSONB `timeout_policy` document; the schema is
    unchanged.
  - Rows from before this phase keep `null` (unlimited) on resume. They
    are not reinterpreted.
- **New persisted error codes** (in the existing `ExecutionError`
  document): `NODE_OUTPUT_TOO_LARGE` and `EXECUTION_OUTPUT_TOO_LARGE`,
  kind `permanent`, details `{size, limit}` as strings.
- **No tables, columns, indexes, constraints or migrations.** The
  existing `ix_executions_workflow_id` serves the count.
- **No state transitions added.**

---

## 7. API / Contract Changes

**No endpoints added, changed or removed.**

**New error responses** on every endpoint that creates executions:

- `POST /api/v1/workflows/{id}/versions/{v}/execute`
- trigger invoke
- webhooks

All use the existing error schema (`code`, `message`, `details`,
`request_id`):

| Code | HTTP | When | `details` |
|---|---|---|---|
| `RESOURCE_LIMIT_EXCEEDED` | 422 | policy, graph size or graph depth above a limit | `{limit, maximum, requested}` |
| `EXECUTION_CAPACITY_EXCEEDED` | 429 | workflow at its unfinished-execution cap | `{"limit": "active_executions", "maximum": N, "requested": N+1}` |

Verified by `test_manual_execute_over_workflow_capacity_is_429`.

**Execution state contract.** Executions now report finite
`timeout_policy` values when the caller set none.
- Compatibility: this is an intentional behaviour change. Deployments
  that relied on unlimited runs must raise
  `AKASHA_LIMIT_EXECUTION_TIMEOUT_SECONDS` and
  `AKASHA_LIMIT_NODE_TIMEOUT_SECONDS`.

**Node-facing contract.** A `core.llm` request without `max_tokens` is
now sent with the engine limit. A request above the limit fails with
code `PROVIDER_CONFIGURATION` (`ProviderError` kind `configuration` →
`NodeErrorKind.CONFIGURATION`).

**Metrics contract.** New series
`akasha_resource_limit_exceeded_total{limit="<Limit value>"}`.

**No WebSocket, event-payload, plugin or SDK contract changes.**

---

## 8. Implementation Details

- **Order of checks in `create_execution`.** The checks run in this
  order:
  1. `policies` (pure, no transaction)
  2. version lookup (404 `WORKFLOW_VERSION_NOT_FOUND`)
  3. `check_graph`
  4. `count_unfinished` + `check_capacity`
  5. `Execution.create`
  6. `enqueue`
  7. save, commit, publish events, dispatch

  Any `ResourceLimitError` aborts the unit of work before a save, so
  nothing is stored. This is asserted via `stored_executions(h) == 0`.
- **Missing values take the limit.** `_capped(limit, value, maximum)`
  returns `maximum` for `None`, raises for `value > maximum`, and keeps
  `value` otherwise. Exactly at the limit is allowed; the limit + 1 µs is
  rejected.
- **Output measurement.** `json_size` runs
  `json.dumps(..., ensure_ascii=False, default=str, separators=(",", ":"))`,
  then encodes as UTF-8 with `surrogatepass`. Bytes, NaN and unpaired
  surrogates are measured, never raise. This approximates the stored
  JSONB size; it is not the exact PostgreSQL storage size.
- **Output accounting.**
  - `_record` processes finished attempts in node-id order (existing
    behaviour).
  - `_limit_output` adds a successful node's size to
    `request.output_bytes` only when it fits.
  - A node that breaks a limit becomes an `ExecutionError` and goes
    through the existing `fail_node` path.
  - `can_retry_node` is false for `PERMANENT`, so the execution then
    fails with that error, cancels the remaining nodes and persists
    `FAILED`.
  - The oversized output is never put on the aggregate, so it is never
    persisted, published or propagated.
  - During `_stop` (cancel or timeout) the same check applies. The
    terminal verdict comes from the stop reason.
- **Resume.** `_run` recomputes `request.output_bytes` from persisted
  `COMPLETED` outputs when `execution.is_resumed`. The budget does not
  reset after a crash and recovery.
- **Graph depth.** Kahn's algorithm over `graph.edges`. Edges to unknown
  nodes are ignored, and nodes on cycles are never reached (bounded
  time). Cycles remain a validation error at publish.
- **Concurrency.**
  - Per-execution node concurrency uses the existing `_launch` slot
    logic, now fed from settings.
  - Per-workflow capacity is serialized by the row lock (§5.4).
  - Worker-level concurrency is unchanged.
- **Logging.** Both log events carry no payload content:
  - `execution_rejected` fields: `workflow_id`, `workflow_version`,
    `reason` (code), `limit`.
  - `node_output_limit_exceeded` fields: `node_id`, `node_type`, `limit`,
    `size`. It inherits the execution and correlation ids from the
    runtime's log context.
- **Configuration.**
  - Every limit has pydantic range constraints.
  - `Settings._stale_after_heartbeats` (the existing `model_validator`)
    also calls `resource_limits()`, so a `DomainValidationError` (a
    `ValueError`) surfaces as a pydantic `ValidationError` at startup.

---

## 9. Findings

### 9.1 Executions had no effective time limit
- **Finding:** `TimeoutPolicy` defaulted to `None`/`None`, and no
  production code path set one, so every execution was unlimited in time.
- **Impact:** HIGH
- **Resolution:** FIXED. Admission replaces missing values with the
  limits.
- **Notes:** This is a behaviour change for long-running deployments
  (§7).

### 9.2 Retry count was unbounded
- **Finding:** `RetryPolicy.max_attempts` had no upper bound.
- **Impact:** MEDIUM. Today it is reachable only through
  `ExecutionService`'s parameter: there is no API surface for retry
  policies (Phase 23 limitation).
- **Resolution:** FIXED (attempt cap at admission).

### 9.3 Node outputs had no size bound
- **Finding:** The architecture said output size limits were "not applied
  yet". Any output size reached memory, JSONB rows, events and API
  responses.
- **Impact:** HIGH
- **Resolution:** FIXED (per-node and per-execution limits).

### 9.4 Unbounded executions per workflow
- **Finding:** An interval trigger faster than its runs, or a webhook
  flood, could accumulate unlimited `QUEUED` executions.
- **Impact:** MEDIUM
- **Resolution:** FIXED (per-workflow capacity, HTTP 429). The scheduler
  logs and skips a rejected fire (existing `except` in
  `runtime/scheduler.py`).

### 9.5 No loop, sub-execution or artifact constructs exist
- **Finding:** Graph validation rejects cycles (`CYCLE_DETECTED`), there
  is no subgraph or sub-execution node, and there is no artifact store.
- **Impact:** LOW (for this phase)
- **Resolution:** WORKAROUND / DEFERRED.
  - Iterations are bounded by graph nodes × attempts, both now capped.
  - "Execution depth" is implemented as graph depth (the longest
    dependency chain).
  - Artifact-size limits wait for an artifact store. Workflow files are
    already capped (`MAX_WORKFLOW_FILE_BYTES` etc., Phase 18), and node
    outputs (the stored results) are capped here.
- **Notes:** This is a roadmap assumption that does not match the current
  system: the roadmap lists loop-iteration and artifact limits, but the
  constructs do not exist yet.

### 9.6 Trigger recursion through the engine's own webhook
- **Finding:** An execution's `core.http` node can call the engine's own
  webhook, which creates a new execution, which can call it again.
- **Impact:** MEDIUM
- **Resolution:** OPEN.
  - SSRF protection blocks loopback and private destinations by default.
  - Not blocked when the engine is reachable under a public name, or
    when `AKASHA_NETWORK_ALLOW_PRIVATE` / `AKASHA_NETWORK_ALLOWED_HOSTS`
    allow it.
  - Per-workflow capacity bounds parallel fan-out, not a sequential chain.
- **Notes:** Architecture §130 lists "event-trigger recursion". A fix
  needs a recursion depth propagated across triggers (e.g. a signed
  header or execution lineage).

### 9.7 LLM `max_tokens` above the limit is caught only at run time
- **Finding:** The `core.llm` config schema allows up to 100000, while
  the engine limit is configurable (default 16384).
- **Impact:** LOW
- **Resolution:** OPEN (documented).
- **Notes:** Publish-time validation cannot know operator limits without
  coupling workflow validation to configuration.

### 9.8 Pre-existing Windows-only test failures
- **Finding:** 6 tests fail and 4 error in this Windows environment, both
  with and without this phase's changes (§13).
- **Impact:** LOW (for this phase)
- **Resolution:** OPEN. Environment-specific; not caused by this phase.

---

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Area | Category |
|---|---|---|---|---|---|---|
| 1 | Production executions ran without time limits | Policies defaulted to `None`; no caller set them | Admission fills limits (§3) | `test_unset_policies_are_persisted_as_the_caps`, `test_execution_duration_cap_applies_without_a_policy`, `test_node_duration_cap_applies_without_a_policy` | application | pre-existing, discovered this phase |
| 2 | New HTTP boundary test failed for `/stream-big` | The test assumed a 2048-byte body; the local server sends 4096 | Test parametrized with the per-endpoint body size (`_BIG`) | itself | tests | introduced and fixed this phase (test bug) |
| 3 | New integration tests failed with `WorkflowValidationFailedError` | Test graphs had edge-less nodes; validation rejects `DISCONNECTED_NODE` | Test graphs reshaped (root → fan-out, A → B) | itself | tests | introduced and fixed this phase (test bug) |
| 4 | `mypy` error `Need type annotation for "fan"` | An empty-dict config literal inside a comprehension | Explicit annotation | mypy gate | tests | introduced and fixed this phase |
| 5 | Edited files were written with CRLF line endings | Python text-mode writes on Windows | All changed files normalized back to LF | `git diff` shows no line-ending noise | tooling | environment |
| 6 | Windows-only failures (symlinks, POSIX modes, SIGTERM, backslash paths, long test ids) | Test environment | Not fixed | — | tests/CLI/plugins | pre-existing environment problem |

---

## 11. Fixes Applied

1. **Unlimited execution and node duration.**
   - Symptom: no time limit on real executions.
   - Root cause: §9.1.
   - Implementation: `ResourceLimits.policies` in `_create`.
   - Verification: integration tests show `TIMEOUT` and `NODE_TIMEOUT`
     with no requested policy, and the persisted policy equals the
     limits.
2. **Unbounded retries.**
   - Implementation: attempt cap in `policies`.
   - Verification: unit and integration tests at 3 and 4 attempts with a
     cap of 3.
3. **Unbounded output size.**
   - Implementation: `_limit_output` in the runtime.
   - Verification: node and execution limits tested exactly at the limit
     and one byte over, with the persisted `FAILED` state and no stored
     oversized output.
4. **Unbounded executions per workflow.**
   - Implementation: `count_unfinished` with a lock, and
     `check_capacity`.
   - Verification: boundary test, concurrent-admission stress test
     (20 → exactly 5), API 429 test.
5. **Hard-coded per-execution node concurrency.**
   - Implementation: `AKASHA_LIMIT_EXECUTION_CONCURRENCY` →
     `ExecutionRuntime(max_concurrency=...)`.
   - Verification: in-flight peak test (24 nodes, limit 3 → peak 3) and
     config validation (`0` rejected). The bootstrap wiring itself is a
     one-line assignment and has no dedicated test.
6. **Unbounded LLM output tokens per request.**
   - Implementation: `AIService` limit.
   - Verification: unset → limit; exactly the limit is accepted; the
     limit + 1 is rejected before any adapter call.
7. **Test fixes:** items 2–5 in §10.

---

## 12. Testing & Verification

### Unit Tests
- **`tests/domain/test_limits.py`** (25 cases): PASS.
  - Invariants: 9 invalid constructions; finite defaults.
  - Durations: missing → limit, lower kept, exactly at the limit
    allowed, limit + 1 µs rejected (execution and node).
  - Attempts: 3 allowed and 4 rejected at limit 3, with exact `details`.
  - Graph: 4 nodes ok and 5 rejected; depth 3 ok and 4 rejected.
    Longest path, including a shortcut edge. Cycle-safe measurement.
  - Capacity: 1 active admitted and 2 rejected (limit 2), code
    `EXECUTION_CAPACITY_EXCEEDED`, status 429, subclass of
    `ResourceLimitError`.
  - Node output: 100 ok and 101 rejected. Execution output: total 250 ok
    and 251 rejected. Node limit reported first when both are broken.
  - `json_size`: 2-byte and 4-byte characters, unpaired surrogate,
    bytes, NaN.
- **`tests/test_config.py`** (+7): limits loaded from env; contradictory
  pairs and out-of-range values raise `ValidationError`. PASS.
- **`tests/application/test_ai_service.py`** (+3, fake adapter): unset
  `max_tokens` → limit; exactly the limit sent; the limit + 1 →
  `ProviderError(CONFIGURATION)` with no adapter call; settings wiring
  (`limit_llm_output_tokens=123`) rejects 124 before credentials;
  `max_output_tokens=0` → `ConfigurationError`. PASS.

### Component Tests
- **`tests/nodes/test_http_node.py`** (local HTTP server, no internet):
  - `/big` (Content-Length, 2048 B) and `/stream-big` (close-delimited,
    4096 B): limit = body − 1024 and body − 1 → `HTTP_RESPONSE_TOO_LARGE`
    (PERMANENT).
  - limit = body → accepted with the full body.
  - Result: PASS.

### Integration Tests (real PostgreSQL 17.2, Testcontainers)

`tests/integration/test_resource_limits.py` (21 cases): PASS.

- **Admission:**
  - unset policies persisted as the limits
  - each policy beyond a limit (execution, node, attempts) rejected,
    0 rows stored, metric = 1
  - policies exactly at the limits admitted and persisted
- **Graph:** limits at 4 run to `COMPLETED`; 5 rejected (nodes and depth
  variants).
- **Capacity:** at limit 2 the third request is refused; another workflow
  is unaffected; a cancelled execution frees its slot; the metric
  counts rejections.
- **Node output:**
  - exactly 100 B completes
  - 101 B: execution `FAILED` with `NODE_OUTPUT_TOO_LARGE`, details
    `{size: 101, limit: 100}`, node `FAILED` with `outputs is None`,
    downstream `CANCELLED` and never started, metric = 1
- **Combined (output limit + `RetryPolicy(max_attempts=3)`):** the node
  ran once, `attempt == 1`.
- **Execution output:**
  - 120 B total at limit 120 completes
  - at limit 119: B fails with `EXECUTION_OUTPUT_TOO_LARGE` (size 120);
    A stays `COMPLETED`; metric = 1
- **Duration limits without a policy:**
  - execution limit 0.3 s → `TIMEOUT`, the hanging node `CANCELLED`, and
    its cleanup observed (`probe.cleaned`)
  - node limit 0.2 s → node `FAILED` `NODE_TIMEOUT`, execution `FAILED`
- **Cancellation after a limit:** cancelling after an output-limit
  `FAILED`, or after a `TIMEOUT`, raises `InvalidStateTransitionError`;
  the persisted status, `cancellation` and `revision` are unchanged.
- **Resume:**
  - setup: crash after A (60 B) completed, stale recovery, rerun; B adds
    61 B against a limit of 119
  - result: `EXECUTION_OUTPUT_TOO_LARGE`; A not re-executed

### API Tests
- `test_manual_execute_over_workflow_capacity_is_429`: real ASGI app, auth
  and PostgreSQL. With `limit_active_executions_per_workflow=1`, the first
  execute returns 201 and the second returns 429
  `EXECUTION_CAPACITY_EXCEEDED` in the standard error shape (no
  traceback). PASS.
- The existing manual-execute API tests still pass, with the test
  container now deriving limits from `Settings`.

### Infrastructure Tests
- PostgreSQL via Testcontainers (`postgres:17.2`). Docker 29.1.2 was
  available.
- The new tests use the existing `clean_db` fixture.
- No new infrastructure.

### Performance / Concurrency Tests
- **Concurrent admissions:** 20 simultaneous `create_execution` calls
  against limit 5 → exactly 5 admitted, 15 `ExecutionCapacityError`, 5
  rows stored.
- **Parallel output budget:** 10 fan-out nodes × 50 B at concurrency 4,
  with room for 6 → exactly 7 `COMPLETED` (root + 6), stored total equal
  to the budget.
- **Node concurrency:** 24 fan-out nodes, limit 3 → measured peak
  in-flight count exactly 3.
- **Mutation checks** (manual, reverted afterwards):
  - Zeroing the resume recount → the resume test fails.
  - Disabling the workflow-row lock (`.where(False)` on the lock select)
    → the concurrent-admission test fails 3 out of 3 runs.
- **Repeatability:** the limit suites (46 cases) passed 5 consecutive
  runs.
- No latency or throughput benchmarks were performed.

### Security Tests
- No dedicated security test was added. Security-relevant assertions
  inside the above:
  - error responses carry no traceback (API test)
  - limit errors expose only numbers and enum names

### E2E / Accessibility
- Not applicable (backend phase, no UI).

---

## 13. Quality Gates

Run in `backend/` on Windows 11 Pro 10.0.26200 with Python 3.14 (uv),
after the final change:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 185 files already formatted |
| Linter (`uv run ruff check .`) | PASS | Two E501 hits mid-phase were fixed by formatting and edits |
| Type Checker (`uv run mypy .`) | PASS | 181 source files. One test annotation error mid-phase, fixed (§10 #4) |
| Unit Tests | PASS | All new and extended unit tests pass |
| Integration Tests | PASS (phase scope) | 21/21 new; full suite below |
| API Tests | PASS | Including the new 429 test |
| Full `uv run pytest` | FAIL (pre-existing) | 1789 passed, 5 skipped, **6 failed, 4 errors** (253.7 s). All 10 are environment failures, listed below |
| E2E Tests | N/A | Backend phase |
| Build | N/A | No container or build change in this phase |
| Security Checks | N/A | No dependency audit or security scanner run in this phase |
| Accessibility | N/A | No UI |
| Architecture guard (`tests/test_architecture.py`) | PASS | Part of the full run |

**Full-suite failures and errors.** Each one fails the same way without
this phase's changes. I verified this by stashing the changes and
re-running the affected files on clean `main` (`test_workflow_package.py`,
`integration/test_cli.py`, `test_cli.py`, `test_plugins.py`: 6 failed,
2 errors; `test_credentials.py`: 2 errors).

- `test_plugins.py::test_symlinks_in_packages_are_rejected`: WinError
  1314, no symlink privilege.
- `test_cli.py::test_backup_file_mode_helper`: POSIX file mode bits.
- `integration/test_cli.py::test_backup_destroy_restore_run`: the same
  file-mode assertion.
- `integration/test_cli.py::test_ctrl_c_cancels_the_run_cooperatively`
  and `…::test_worker_process_registers_and_stops_gracefully_on_sigterm`:
  POSIX signal handling.
- `test_workflow_package.py::test_unexpected_or_traversal_entries_rejected[docs\\..\\evil.txt]`:
  backslash path semantics on Windows.
- ERRORs in `test_workflow_package.py::test_manifest_not_a_json_object[...]`
  and `test_credentials.py::test_secret_bounds[...]`: the parametrized
  test ids are too long for Windows temp paths.

These should be confirmed on Linux CI.

---

## 14. Acceptance Criteria Verification

The roadmap has one acceptance criterion. Its scope items and the
session's testing requirements are evaluated individually as supporting
evidence.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| **A malformed or badly configured Workflow cannot trivially consume unlimited resources** | PASS | Every per-execution resource dimension has a server-side limit with boundary tests (rows below); per-workflow volume is capped. Residual: sequential self-triggering via webhook (§9.6), not "trivial" under default SSRF policy |
| Scope: execution duration | PASS | Admission limit and runtime deadline; `test_execution_duration_cap_applies_without_a_policy`, unit boundary tests |
| Scope: node duration | PASS | `test_node_duration_cap_applies_without_a_policy`, unit boundary tests |
| Scope: loop iterations | PARTIAL | No loop construct exists. Graph DAG + `max_graph_nodes` × `max_node_attempts` bound node attempts (tested). A dedicated iteration limit waits for a loop construct. Not blocking |
| Scope: concurrency | PASS | Per execution (peak test), per workflow (boundary + stress + API 429), per worker (existing) |
| Scope: output size | PASS | Node and execution limits, at/beyond, persisted state, resume |
| Scope: artifact size | PARTIAL | No artifact store exists. Node outputs (stored results) capped; workflow files capped since Phase 18. Not blocking |
| Scope: HTTP response size | PASS | Existing limit; new exact-boundary tests for both body framings |
| Scope: LLM token usage | PASS (per request) | `AIService` limit tests. A per-execution token budget is deferred (§19) |
| Scope: retry count | PASS | Attempt cap at/beyond, unit and integration |
| Scope: execution depth | PARTIAL | Graph depth implemented and tested. Nested-execution depth has no construct; trigger-recursion depth is open (§9.6) |
| Scope: concurrency controls | PASS | Same evidence as concurrency |
| Session: configurable / validated / observable / correct layer | PASS | `AKASHA_LIMIT_*`, fail-fast tests, metric + logs asserted, layer map §3 |
| Session: exactly at limit / one beyond | PASS | Every implemented limit (§12) |
| Session: nested/combined limits | PASS | Output limit + retry policy; node limit vs execution limit precedence; execution vs node timeout |
| Session: cancellation after limit | PASS | Two integration tests |
| Session: persisted failure state | PASS | All runtime-limit tests assert on reloaded state |

The PARTIAL rows concern constructs that do not exist. They do not block
completion; they are the reason for "WITH LIMITATIONS".

---

## 15. Security Considerations

- **Denial of service:**
  - per-workflow execution flooding is now rejected before storage (429)
  - oversized node outputs no longer reach the database or API
  - unbounded LLM output requests are capped
- **Error disclosure:** limit errors carry only numeric limits, sizes and
  enum names. No payload or stack trace (asserted in the API test).
- **Logging:** the new log events carry ids, sizes and limit names only,
  never output content or prompts (AGENTS §76).
- **Metric labels:** only `Limit` enum values (bounded cardinality,
  AGENTS §41).
- **SSRF / trigger recursion:** unchanged SSRF policy. Recursion through
  the engine's own webhook remains open when private destinations are
  allowed (§9.6).
- **Authentication and authorization:** unchanged. Limits apply after
  authorization, in the application service.
- **Not applicable:** credentials, XSS/CSRF, path traversal, plugin
  trust, expressions.

---

## 16. Performance Considerations

- **`json_size`:** serializes each successful node output once, in the
  scheduler coroutine. The cost is linear in output size, which is now
  capped at 1 MiB by default. Not measured in milliseconds; there are no
  benchmarks in this phase.
- **Memory:** an oversized output exists in memory until it is measured
  and dropped (§20).
- **Admission:** one indexed `count(*)` over the workflow's executions
  (`ix_executions_workflow_id`) plus one row lock. Admissions to the same
  workflow serialize; different workflows are unaffected. The count covers
  all of the workflow's executions, terminal ones included, before
  filtering by status.
  - Possible bottleneck: workflows with very many historic executions.
  - Upgrade path: a partial index on non-terminal statuses, if
    measurement shows it is needed.
- **Resume:** the output budget is recomputed by serializing completed
  outputs once per resume.
- **Concurrency behaviour:** verified by the stress tests (§12). No
  throughput claims are made.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §75 | Implementation of the resource-limits section | Limit/default/layer/behaviour table; admission semantics; observability; loops, nesting and artifacts rationale |
| `docs/architecture.md` (execution persistence note) | Statement "Size limits (Phase 24) … are not applied yet" became false | Now refers to the §75 output limits |
| `docs/architecture.md` (metrics list) | New metric | `akasha_resource_limit_exceeded_total{limit}` |
| `backend/README.md` | Operators need the new settings | `AKASHA_LIMIT_*` table |
| `backend/src/akasha/domain/execution.py` | `TimeoutPolicy` docstring referenced "Phase 24" as future | Now states that `None` is replaced at admission |
| `docs/reports/backend/phase-24-performance-resource-limits.md` | Phase report | This document |

**Future documentation:**

- §130/§131 (loop and execution depth) once loop or sub-execution
  constructs exist.
- An ADR is not required: no decision contradicts the architecture.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded.
- **New environment variables:** `AKASHA_LIMIT_EXECUTION_TIMEOUT_SECONDS`,
  `AKASHA_LIMIT_NODE_TIMEOUT_SECONDS`, `AKASHA_LIMIT_NODE_ATTEMPTS`,
  `AKASHA_LIMIT_GRAPH_NODES`, `AKASHA_LIMIT_GRAPH_DEPTH`,
  `AKASHA_LIMIT_ACTIVE_EXECUTIONS_PER_WORKFLOW`,
  `AKASHA_LIMIT_NODE_OUTPUT_BYTES`, `AKASHA_LIMIT_EXECUTION_OUTPUT_BYTES`,
  `AKASHA_LIMIT_EXECUTION_CONCURRENCY` and
  `AKASHA_LIMIT_LLM_OUTPUT_TOKENS`.
- **Database:** no migration, no schema change.
- **Redis, Docker, external services:** none.
- **Test infrastructure:**
  - The `trigger_client` helper in `test_triggers_api.py` now builds a
    single `ExecutionService` with `settings.resource_limits()`, matching
    bootstrap.
  - `FakeExecutions` gained `count_unfinished`.

---

## 19. Deferred Work

| Item | Why deferred | Intended phase | Blocker | Affects completion |
|---|---|---|---|---|
| Trigger-recursion depth (self-webhook chains) | Needs lineage/depth propagation across triggers; security-shaped | Phase 25 (Security Hardening) or later | Design of a trusted depth carrier | No (documented limitation) |
| Loop-iteration limit | No loop construct exists | When loops are introduced | Loop construct | No |
| Execution/subgraph nesting depth | No sub-execution construct exists | When introduced | Construct | No |
| Artifact size limit | No artifact store exists | When artifact storage is introduced | Artifact store | No |
| Per-execution LLM token budget | Per-request cap × nodes × attempts already bounds usage; a budget needs cross-node accounting | Unassigned | None | No |
| Per-provider / per-node-type concurrency (architecture §74) | No provider rate data to size them; not required for the acceptance criterion | Unassigned | None | No |
| API payload limits for execution input, rate limiting (§107/§108) | Roadmap places "API limits" in Phase 25 | Phase 25 | None | No |

---

## 20. Known Limitations

- **Sequential self-triggering** through the engine's own webhook is not
  bounded when SSRF settings allow the destination (§9.6).
- **Oversized outputs exist in memory briefly.** They are fully produced
  by the node before they are measured and rejected. Bounding them during
  production would require streaming node outputs.
- **Output limits are not persisted per execution.** They are the
  running engine's values. Lowering them affects in-flight executions
  (including resumed ones), unlike durations and attempts, which are
  fixed at admission.
- **Executions created before this phase** keep `null` (unlimited)
  timeouts when resumed. They are not reinterpreted.
- **`core.llm` `max_tokens` above the engine limit** passes publish
  validation and fails at run time with `PROVIDER_CONFIGURATION`.
- **Noisy scheduler logs at capacity.** A scheduled trigger fire rejected
  at capacity is logged by the scheduler with a full exception
  (`scheduled_trigger_failed`) on each fire while the workflow stays at
  its cap.
- **`json_size` approximates the stored size.** It is compact JSON, not
  exact JSONB storage.
- **Untested wiring.** The bootstrap wiring of
  `limit_execution_concurrency` has no dedicated test; the runtime
  parameter itself is tested.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Trigger recursion via own webhook | Unbounded sequential execution chain under permissive network settings | MEDIUM | triggers / `core.http` | Propagate a trigger depth (signed header or lineage) and cap it in `ResourceLimits` | Phase 25 |
| Windows-only test failures (6 failed, 4 errors) | Local full-suite run is red on Windows | LOW | tests (CLI, plugins, packages, credentials) | Confirm green on Linux CI; mark platform-specific tests with `skipif` where appropriate | Unassigned |
| Scheduler logs full exceptions for capacity rejections | Log noise | LOW | `runtime/scheduler.py` | Log `ResourceLimitError` as a warning without a traceback | Unassigned |
| Run-time-only check of `core.llm` `max_tokens` against the engine limit | Late failure | LOW | `nodes/llm.py` / `AIService` | Optionally surface engine limits in publish validation | Unassigned |

---

## 22. Architectural / Product Decisions

- **One number per limit.** It is both the default and the maximum, with
  no separate "default timeout" setting. A caller may lower a limit,
  never raise it. This matches the existing `AIService` timeout
  semantics ("may shorten, never extend").
- **Effective limits are persisted on the execution** for durations and
  attempts. They are reproducible and stable across resume. Output
  limits are not, because they are checked by the runtime and need no
  schema change.
- **Status codes:** 429 for capacity, because retrying later can succeed.
  422 for every other limit, because the same request can never succeed.
- **Output-limit failures are `PERMANENT`**, because the same node with
  the same input produces the same size. Retrying wastes resources.
- **When both output limits are broken**, the node limit is reported
  first: it names the actual culprit.
- **"Execution depth" means graph depth** (nodes on the longest
  dependency chain), as long as there is no nesting construct.
- **The capacity check uses a workflow-row `FOR NO KEY UPDATE`**, not an
  advisory lock or plain `FOR UPDATE` (§5.4).
- **Output limits are counted on outputs after secret masking**, i.e.
  exactly what would be stored.

---

## 23. Repository / Git State

- **Branch:** `main`. Last commit `92b8bc9 backend phase 23 - reliability
  and recovery`.
- **Working tree:** uncommitted changes from this phase only.
  - Modified (16): `backend/README.md`; `backend/src/akasha/` files
    `application/ai.py`, `application/executions.py`,
    `application/persistence.py`, `bootstrap.py`, `config.py`,
    `domain/execution.py`, `infrastructure/persistence.py`,
    `observability/metrics.py`, `runtime/runtime.py`; `backend/tests/`
    files `application/test_ai_service.py`,
    `application/test_trigger_service.py`,
    `integration/test_triggers_api.py`, `nodes/test_http_node.py`,
    `test_config.py`; `docs/architecture.md`.
  - Untracked (4): `backend/src/akasha/domain/limits.py`,
    `backend/tests/domain/test_limits.py`,
    `backend/tests/integration/test_resource_limits.py`, and this report.
- **Diff size:** 380 insertions and 22 deletions in tracked files, plus
  the new files.
- **No unrelated changes detected.**
- **Temporary stashes:** used only to verify pre-existing failures, and
  restored immediately. `git stash list` is empty.

---

## 24. Phase Completion Assessment

### Implemented
- `ResourceLimits` with settings and fail-fast validation.
- Admission control: durations, attempts, graph size and depth, workflow
  capacity with a lock.
- Runtime output limits (node and execution) with resume recount.
- Configurable per-execution node concurrency.
- Per-request LLM output-token limit.
- Limit metric and log events.
- Documentation.

### Verified
- Every implemented limit exactly at its value and one beyond, in unit
  and/or PostgreSQL integration tests.
- Combined limits, cancellation after a limit, persisted failure state,
  resume behaviour.
- Three stress tests and two mutation checks.
- Format, lint and type-check gates pass.
- Full suite: 1789 passed; the 6 failed and 4 errors are pre-existing
  Windows environment issues.

### Not Implemented
- Dedicated loop-iteration, nesting-depth and artifact-size limits (no
  constructs exist).

### Deferred
- See §19: trigger-recursion depth, per-execution token budget,
  provider/node-type concurrency, API payload and rate limits.

### Open
- See §21.

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**The next phase can assume:**

- Every execution created through `ExecutionService.create_execution`
  has finite `timeout_policy` values and `retry_policy.max_attempts ≤`
  the engine limit.
- No graph larger or deeper than the limits is executed.
- Per-workflow unfinished executions are capped.
- Stored node outputs never exceed the node or execution limits.

**APIs and contracts now available:**

- `akasha.domain.limits.ResourceLimits`, `Limit`, `ResourceLimitError`
  (422) and `ExecutionCapacityError` (429).
- `Settings.resource_limits()`.
- `ExecutionRepository.count_unfinished`.
- `ExecutionRuntime(limits=...)`.
- `AIService(max_output_tokens=...)`.
- Metric `akasha_resource_limit_exceeded_total{limit}`.

**Must not be changed casually:**

- **The capacity lock** in `count_unfinished`. Removing it reintroduces
  over-admission; `test_concurrent_admissions_never_exceed_the_capacity`
  guards it.
- **The single entry point.** All execution creation must stay behind
  `ExecutionService.create_execution`; bypassing it bypasses admission.
- **The output check placement** in `_record` / `_limit_output`, before
  any aggregate mutation.
- **Metric labels.** The `limit` label must stay restricted to `Limit`
  values.

**Known constraints:**

- Output limits are engine-global, not per execution.
- `core.llm` schema maximum (100000) and the engine token limit differ.

**Phase 25 (Security Hardening) should consider:**

- trigger-recursion depth (§9.6)
- API payload limits for execution input and webhooks
- webhook and API rate limiting
- whether a self-call guard is needed when private networks are allowed

**Prerequisites:** none beyond the uncommitted changes listed in §23
being committed.
