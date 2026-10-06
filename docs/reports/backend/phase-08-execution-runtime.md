# Phase 08 — Execution Runtime

## 1. Executive Summary

**What this phase was meant to deliver** (roadmap §12): the core graph
execution engine and execution persistence. The roadmap names seven
components: GraphLoader, GraphValidator, ExecutionRuntime, Scheduler,
DependencyResolver, NodeExecutor and StateManager. The engine must support
linear execution, branching, parallel independent nodes, output
propagation, node failure, execution failure and successful completion. The
session prompt added state persistence, cancellation boundaries and timeout
boundaries.

**What was implemented:** a new `akasha.runtime` package with three modules:
- `plan.py`: graph loading, re-validation against the runtime node
  catalog, and dependency resolution.
- `executor.py`: runs one node attempt and classifies its outcome.
- `runtime.py`: `ExecutionRuntime`, the scheduler loop and `StateManager`.

Supporting pieces:
- A thin application use case, `ExecutionService` (create/get).
- An `ExecutionRepository` protocol and its SQLAlchemy implementation.
- Migration `0003_executions`, which adds the tables `executions` and
  `node_executions`.
- Small additions to the Phase 07 domain: `NodeExecution.outputs`,
  `Execution.revision`, and one rehydration invariant.

**Resulting system state:**
- A published `WorkflowVersion` can be turned into a QUEUED `Execution` and
  run to a terminal state (COMPLETED, FAILED, CANCELLED or TIMEOUT).
- Every state change is persisted in PostgreSQL through CAS writes.
- All of this is proven only by tests that inject a node catalog. There is
  no production entry point yet:
  - no HTTP endpoint;
  - no composition-root wiring;
  - the production node catalog stays empty until the Node Registry
    (Phase 09).

**Test status:**
- Full suite: 559 tests pass (baseline 493).
- New tests: 34 runtime integration, 10 execution persistence integration,
  17 runtime unit and 5 domain tests.
- ruff, ruff format, mypy (strict) and `uv build` all pass.

**Acceptance criterion:** "A published Workflow Version can be executed from
start to finish" is **PASS**, with integration evidence (§14). The phase
leaves limitations that belong to other phases' scope but constrain the
current system:
- no production caller or node catalog;
- retries are refused rather than executed;
- no recovery of executions left RUNNING by a dead process.

**Phase Status:** COMPLETE WITH LIMITATIONS

(The chat summary at the end of implementation said "COMPLETE". Reviewed
strictly against the repository, "COMPLETE WITH LIMITATIONS" is more
accurate, because nothing in production can invoke the runtime yet.)

---

## 2. Phase Objective

Roadmap `/docs/roadmap-backend.md` §12 "Phase 08 — Execution Runtime":

> Goal: Implement the core graph execution engine.
> Implement: GraphLoader, GraphValidator, ExecutionRuntime, Scheduler,
> DependencyResolver, NodeExecutor, StateManager.
> Support: linear execution, branching, parallel independent nodes, output
> propagation, node failure, execution failure, successful completion.
> Implement execution persistence.
> Tests: A → B → C; A → B, A → C; A → B, A → C → D; node failure; invalid
> graph; parallel execution.
> Acceptance: A published Workflow Version can be executed from start to
> finish.

The roadmap's Phase 08 prompt (§37) adds: use the existing Node contract.
Do not implement the Node Registry, Trigger, Event or Worker systems except
for minimal interfaces that are absolutely required. Review for race
conditions, state consistency and incorrect execution ordering.

The session prompt added these requirements:
- state persistence and cancellation/timeout boundaries;
- deterministic test nodes, and no sleep-based synchronization;
- an explicit hunt for race conditions, duplicate execution, ordering
  errors, state inconsistencies, partial failures, cancellation bugs and
  resource leaks;
- "Do not implement triggers, plugins or worker queues."

**Explicitly not responsible for:**

| Area | Phase |
|---|---|
| Node Registry and built-in nodes | 09 |
| Triggers | 10 |
| Events | 11 |
| Metrics and traces | 12 |
| Secret redaction | 13 |
| Plugins | 17 |
| Workers and queues | 22 |
| Retry policies and backoff execution, crash recovery | 23 |
| Resource limits | 24 |
| Control-flow semantics (If/Switch/Loop, SKIPPED) | 09+ |

---

## 3. Scope Implemented

### Runtime (`backend/src/akasha/runtime/`)

**`plan.py`: GraphLoader, GraphValidator integration and DependencyResolver**
- `NodeCatalog = Mapping[str, type[Node]]`, keyed by node type id. This is
  the only new interface that stands in for the future registry.
- `load_plan(version, catalog) -> ExecutionPlan`:
  - calls `validate_graph` with `{type: cls.definition}` derived from the
    catalog;
  - raises `ExecutionGraphInvalidError` (code `EXECUTION_GRAPH_INVALID`,
    HTTP 422) with issue payloads;
  - precomputes `incoming` (edges in graph edge order) and `downstream`
    (sorted and de-duplicated) for every node.
- `ExecutionPlan` (frozen) provides `definition(node_id)`, `roots`,
  `ready_after(node_id, execution)` and `inputs_for(node_id, execution)`.

**`executor.py`: NodeExecutor**

`execute_node(node_cls, context, *, limit) -> NodeResult | ExecutionError`
does the following in order:
1. validates inputs;
2. instantiates the node fresh for this attempt and runs `execute()` under
   `asyncio.timeout(limit)`;
3. classifies the outcome (§8);
4. validates the outputs.

It never raises for node behaviour. Only `CancelledError` propagates.

**`runtime.py`: ExecutionRuntime, scheduler loop and StateManager**
- `StateManager`: `load(execution_id)` and `save(execution)`. Each call is
  one Unit of Work, and therefore one short transaction.
- `ExecutionRuntime(uow, catalog, *, max_concurrency=8, clock=utcnow)`:
  - `run(execution_id)` runs a QUEUED execution to a terminal state and
    returns it.
  - `cancel(execution_id, *, reason="")` requests cancellation, either
    in-process or through the database.
- `ExecutionAlreadyRunningError` (code `EXECUTION_ALREADY_RUNNING`, 409).
- Internal pieces: `_CancelRequest` (event, reason, `stopping`),
  `_verdict()` (the stop-reason precedence) and the loop methods
  `_schedule`, `_launch`, `_record`, `_stop`, `_end`, `_finish` and `_log`.

### Application (`backend/src/akasha/application/`)
- `executions.py` (new):
  - `ExecutionNotFoundError` (`EXECUTION_NOT_FOUND`, 404) and
    `load_execution(uow, id)`.
  - `ExecutionService.create_execution(workflow_id, version, *, metadata,
    timeout_policy)` loads the pinned version (or raises
    `WorkflowVersionNotFoundError`), then calls `Execution.create`, then
    `enqueue()`, saves and commits, and logs `execution_created`.
  - `ExecutionService.get_execution(id)`.
- `persistence.py`: new `ExecutionRepository` protocol (`get`, `save`
  CAS) and `UnitOfWork.executions`.

### Domain (`backend/src/akasha/domain/execution.py`)
- New field `NodeExecution.outputs: dict[str, Any] | None`. It is set by
  `complete(outputs=...)` as a private `dict` copy.
- New invariant: `outputs` may be non-None only when the node is COMPLETED.
- New field `Execution.revision: int = 0` (the CAS token), with the
  invariant `revision >= 0`.
- New invariant: a CREATED or QUEUED execution must have only PENDING
  nodes.
- `Execution.complete_node(node_id, *, outputs=None, now=None)` now accepts
  outputs.

### Persistence (`backend/src/akasha/infrastructure/`)
- `db_models.py`: new tables `executions` and `node_executions` (§6).
- `persistence.py`:
  - `SqlAlchemyExecutionRepository`:
    - `get`, which uses `FOR SHARE` on the execution row;
    - `save`, a single-statement CAS followed by a bulk upsert of the node
      rows.
  - Serialization helpers: `_error_doc/_error`, `_metadata`,
    `_retry_policy`, `_timeout_policy`, `_persistable`, `_node_row` and
    `_node_to_domain`.
  - `SqlAlchemyUnitOfWork.executions`.
- `backend/migrations/versions/0003_executions.py`: the new migration.

### Testing
- `tests/integration/test_runtime.py` (new, 34 tests).
- `tests/integration/test_execution_persistence.py` (new, 10 tests).
- `tests/runtime/test_runtime_units.py` (new directory and file, 17 tests).
- `tests/domain/test_execution.py`: 5 tests added in class
  `TestPhase08Additions`.
- `tests/integration/conftest.py`: `clean_db` also truncates
  `node_executions` and `executions`.
- `tests/integration/test_persistence.py`: `_HEAD` is now
  `0003_executions`, and the migration up/down test also checks the new
  tables.

### Documentation
- `docs/architecture.md` §11 (runtime rules) and §53 (persistence mapping).
- `backend/src/akasha/runtime/__init__.py`: component map.
- This report.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/runtime/plan.py` | Created (118 lines) | GraphLoader, validator integration, DependencyResolver, input propagation |
| `backend/src/akasha/runtime/executor.py` | Created (100 lines) | NodeExecutor: one attempt, outcome classification, node timeout |
| `backend/src/akasha/runtime/runtime.py` | Created (411 lines) | ExecutionRuntime, scheduler loop, StateManager, cancellation, execution timeout |
| `backend/src/akasha/runtime/__init__.py` | Modified | Docstring maps roadmap component names to modules |
| `backend/src/akasha/application/executions.py` | Created (79 lines) | StartExecution use case, `ExecutionNotFoundError` |
| `backend/src/akasha/application/persistence.py` | Modified (+16) | `ExecutionRepository` protocol, `UnitOfWork.executions` |
| `backend/src/akasha/domain/execution.py` | Modified (+30/−) | `outputs`, `revision`, invariants, `complete_node(outputs=)` |
| `backend/src/akasha/infrastructure/db_models.py` | Modified (+61) | `executions`, `node_executions` Core tables |
| `backend/src/akasha/infrastructure/persistence.py` | Modified (+272/−1) | Execution repository and serialization, UoW wiring |
| `backend/migrations/versions/0003_executions.py` | Created (96 lines) | Schema for execution persistence |
| `backend/tests/integration/test_runtime.py` | Created (1294 lines) | Runtime scenarios against real PostgreSQL |
| `backend/tests/integration/test_execution_persistence.py` | Created (413 lines) | Repository, constraint, lock and upgrade-path tests |
| `backend/tests/runtime/test_runtime_units.py` | Created (299 lines) | Executor and plan unit tests |
| `backend/tests/domain/test_execution.py` | Modified (+51) | Domain additions |
| `backend/tests/integration/conftest.py` | Modified | Truncate the new tables |
| `backend/tests/integration/test_persistence.py` | Modified | New migration head, new tables in up/down test |
| `docs/architecture.md` | Modified (+47) | §11 runtime rules, §53 persistence mapping |
| `docs/reports/backend/phase-08-execution-runtime.md` | Created | This report |

- **New directory:** `backend/tests/runtime/`.
- **No generated files.**
- **No configuration or dependency changes:** `pyproject.toml` and
  `uv.lock` are unchanged (verified with `git diff --quiet`).

---

## 5. Architecture Changes

### 5.1 Single-writer scheduler loop

- **Decision:** exactly one coroutine (`ExecutionRuntime._schedule`)
  mutates and persists the `Execution` aggregate. Node attempts run as
  `asyncio` tasks that only return a `NodeOutcome`.
- **Context:** parallel branches finish concurrently. If each task mutated
  the aggregate and saved it, domain transitions would interleave and saves
  would conflict with each other.
- **Chosen approach:**
  1. `_launch` creates tasks;
  2. `asyncio.wait(FIRST_COMPLETED)` waits for the first result;
  3. `_record` applies outcomes in node-id order.
  The same loop handles cancellation, deadline and failure.
- **Alternatives considered:**
  1. Tasks mutate the aggregate under an `asyncio.Lock`. This still needs
     serialized saves and spreads transition logic across tasks.
  2. One save per task under CAS with retry-on-conflict. This produces
     conflicts within a single run.
- **Reason:** in-process races are impossible by construction, and there is
  one place where every transition happens.
- **Consequences:** Phase 22/23 workers can call `run()` unchanged. Event
  emission (Phase 11) belongs in the same loop.

### 5.2 CAS persistence as the cross-process safety net

- **Decision:** `ExecutionRepository.save` is a compare-and-swap on
  `executions.revision`, the same pattern as `WorkflowRepository.save`.
- **Context:** there are no leases or ownership records yet (Phase 22).
  Two runtimes, or a runtime and an out-of-process cancel, can target the
  same execution.
- **Chosen approach:**
  - a single `UPDATE ... WHERE revision = :r`, or
    `INSERT ... ON CONFLICT DO NOTHING` for revision 0;
  - `rowcount != 1` raises `ConcurrentModificationError`;
  - node rows are upserted in the same transaction.
- **Alternatives considered:**
  - `SELECT ... FOR UPDATE` held for the whole run: long transactions
    across node execution violate backend AGENTS §16;
  - advisory locks: session-bound, and outside the current UoW model.
- **Consequences:**
  - The loser of a race fails loudly (`ConcurrentModificationError` or
    `InvalidStateTransitionError`) and runs no nodes.
  - An owner whose execution was cancelled from elsewhere stops at its next
    save.
  - Phase 22 leases can build on the same revision column.

### 5.3 Re-validation at run time

- **Decision:** `load_plan` re-runs `validate_graph` against the runtime's
  catalog before the execution starts.
- **Context:** publish-time validation used the catalog that existed at
  publish time. Node types or config schemas may have changed since.
- **Consequences:**
  - An invalid snapshot yields FAILED with `EXECUTION_GRAPH_INVALID`
    before any node starts, and `started_at` stays `None`.
  - Phase 09 must supply the same catalog to `WorkflowService` and to
    `ExecutionRuntime`.

### 5.4 Fail-fast failure strategy

- **Decision:** the first node failure stops all in-flight siblings. They
  are cancelled, awaited, and any attempts that finished anyway are
  recorded. The execution then fails with that node's `ExecutionError`.
- **Alternatives considered:** drain (let siblings finish). This is
  unbounded when a sibling hangs.
- **Consequences:** Try/Catch-style control flow (Phase 09+) must change
  the verdict logic in one place (`_verdict` / `_end`).

### 5.5 A cancelled or crashed run is not a failure

- **Decision:** if the `run()` task itself is cancelled (for example on
  process shutdown), node tasks are stopped. No terminal state is persisted
  and the execution stays RUNNING.
- **Reason:** architecture §139: "A process crash must not automatically
  be interpreted as a clean execution failure without recovery logic."
- **Consequences:** stale RUNNING executions accumulate until Phase 23
  recovery exists.

### 5.6 No `asyncio.TaskGroup`

- **Decision:** task ownership is explicit. The `finally` block of
  `_schedule` cancels and awaits every remaining node task.
- **Context:** the first draft used `TaskGroup`. Inside a TaskGroup, an
  exception raised in the body is wrapped in `BaseExceptionGroup`. That
  would have turned `ConcurrentModificationError` and other domain errors
  from `run()` into exception groups.
- **Consequences:** the guarantee that no node task outlives `run()` rests
  on the `finally` block. Tests cover it: outer cancellation and the
  out-of-process cancel.

### 5.7 Node catalog as the only registry stand-in

- **Decision:** `NodeCatalog = Mapping[str, type[Node]]` is injected.
  A fresh node instance is created per attempt.
- **Reason:** Phase 04 and Phase 06 already treat the definitions catalog
  as an injected mapping. Phase 09 will supply both mappings from the
  registry.

These decisions are recorded in `docs/architecture.md` §11 and §53. No ADR
was written: none of them contradicts the documented architecture. They
refine it.

---

## 6. Domain / Data Model Changes

### Domain (`akasha.domain.execution`)

| Entity | Change | Compatibility |
|---|---|---|
| `NodeExecution` | New field `outputs: dict[str, Any] \| None = None`; `complete(*, outputs=None, now=None)` stores `dict(outputs or {})` | Additive; defaults keep all Phase 07 callers valid |
| `NodeExecution` | Invariant: `outputs is not None` ⇒ `status is COMPLETED` | Only affects rehydration |
| `Execution` | New field `revision: int = 0`; invariant `revision >= 0` | Additive |
| `Execution` | Invariant: CREATED/QUEUED ⇒ every node PENDING | Closes Phase 07 L6 for this case |
| `Execution.complete_node` | New keyword `outputs` | Additive |

- No enum, state or transition changes.
- The Phase 07 action tables are untouched.
- All 255 Phase 07 domain tests still pass.

### Database (migration `0003_executions`, revises `0002_workflow_revision`)

**`executions`**

| Column | Type | Notes |
|---|---|---|
| `id` | String, PK | |
| `workflow_id` | String, NOT NULL | |
| `workflow_version` | Integer, NOT NULL | |
| `status` | String, NOT NULL | |
| `metadata` | JSONB | `trigger_type`, `trigger_id`, `correlation_id`, `engine_version` |
| `retry_policy` | JSONB | `max_attempts`, `initial_backoff_s`, `backoff_multiplier`, `max_backoff_s`, sorted `retryable_kinds` |
| `timeout_policy` | JSONB | `execution_timeout_s`, `node_timeout_s` (nullable floats) |
| `error` | JSONB, nullable | `code`, `message`, `kind`, `node_id`, `details` |
| `cancellation` | JSONB, nullable | `requested_at` ISO string, `reason` |
| `revision` | Integer, NOT NULL | CAS token |
| `created_at`, `started_at`, `finished_at` | timestamptz | |

Constraints and indexes:
- `fk_executions_workflow_version`: `(workflow_id, workflow_version)` →
  `workflow_versions(workflow_id, version)`, `ON DELETE RESTRICT`. It
  references the existing unique index `uq_workflow_versions_workflow_version`.
- `ck_executions_status`: `status` must be one of the nine enum values.
- `ck_executions_revision_positive`: `revision >= 1`.
- Index `ix_executions_workflow_id`.

**`node_executions`**

| Column | Type | Notes |
|---|---|---|
| `id` | String, PK | |
| `execution_id` | String | FK → `executions.id`, `ON DELETE CASCADE` |
| `node_id` | String | |
| `status` | String | |
| `attempt` | Integer | |
| `failures` | JSONB list | Of error documents |
| `outputs` | JSONB, nullable | |
| `retry_at`, `started_at`, `finished_at` | timestamptz | |

Constraints and indexes:
- `ck_node_executions_status`: `status` must be one of the seven values.
- `ck_node_executions_attempt_non_negative`.
- Unique index `uq_node_executions_execution_node (execution_id, node_id)`.

**Serialization rules:**
- Timedeltas are stored as float seconds.
- Datetimes round-trip through `timestamptz`; `_ensure_utc` handles naive
  values from asyncpg.
- Outputs go through `_persistable`. Values that JSON cannot represent
  (bytes, arbitrary objects) become `{"$unpersisted": "<type name>"}`. If
  the whole document cannot be JSON-encoded (NaN or Infinity, circular
  data, non-string keys), the result is
  `{"$unpersisted": "value not representable as JSON"}`.

**Compatibility:**
- Purely additive; no existing rows change.
- Downgrade drops both tables.
- The upgrade from 0002 with an existing published version, followed by an
  execution of that version, is tested.

---

## 7. API / Contract Changes

**HTTP, WebSocket, event, plugin and SDK:** none. No endpoint was added.

**Python-level contracts (new):**

`ExecutionService.create_execution(workflow_id, version, *, metadata=None, timeout_policy=None) -> Execution`
- Returns a QUEUED execution with all nodes PENDING and `revision == 1`.
- Errors: `WorkflowVersionNotFoundError` (404).
- Retry policy is always the default (`max_attempts = 1`).

`ExecutionService.get_execution(id) -> Execution`
- Errors: `ExecutionNotFoundError` (404).

`ExecutionRuntime.run(execution_id) -> Execution`
- Returns the terminal execution.

| Error | Code / status | When |
|---|---|---|
| `ExecutionNotFoundError` | 404 | Unknown execution id |
| `InvalidStateTransitionError` | 409, details `action=run`, `from` | Execution is not QUEUED |
| `ExecutionAlreadyRunningError` | 409 | Same runtime instance is already running it |
| `ConfigurationError` | — | `retry_policy.max_attempts > 1` |
| `ConcurrentModificationError` | 409 | Lost a CAS race or ownership |
| `asyncio.CancelledError` | — | Outer task cancelled; state is left RUNNING |

`ExecutionRuntime.cancel(execution_id, *, reason="") -> None`
- If the execution is active in this runtime: the call only signals the
  loop and returns immediately. Idempotent; the first reason wins.
- Otherwise it loads the execution, calls `cancel()` and saves.
- Errors: `InvalidStateTransitionError` (already terminal),
  `ExecutionNotFoundError`, `ConcurrentModificationError`.

**Node contract use** (`NodeExecutionContext`, unchanged shape; the fields
are populated as follows):
- `workflow_version`: `str(version.version)`.
- `config`: a deep copy.
- `inputs`: resolved and deep-copied.
- `logger`: `logging.getLogger("akasha.nodes.<node_type>")`.
- `is_cancelled`: `_CancelRequest.should_stop`.
- `runtime_metadata`: `workflow_version_id`, `engine_version`, `attempt`.
- `credentials`: `None` (Phase 13).

**New stable error codes persisted in `ExecutionError.code`:**

| Code | Meaning |
|---|---|
| `EXECUTION_GRAPH_INVALID` | Snapshot is not executable with the runtime catalog |
| `NODE_INPUT_INVALID` | Resolved inputs violate the input contract |
| `NODE_OUTPUT_INVALID` | Outputs violate the contract, or the node returned a non-`NodeResult` |
| `NODE_UNEXPECTED_ERROR` | Node raised a non-`NodeError` exception (kind `PERMANENT`) |
| `NODE_TIMEOUT` | Runtime node limit exceeded, or the node raised its own `TimeoutError` |

Existing codes still used: `NODE_EXECUTION_ERROR` (from `NodeError`) and
`EXECUTION_TIMEOUT`.

---

## 8. Implementation Details

### Run control flow (`ExecutionRuntime._run`)
1. Load the execution through `StateManager.load`.
2. Pre-start checks:
   - if the status is not QUEUED, raise `InvalidStateTransitionError`;
   - if `max_attempts > 1`, raise `ConfigurationError`;
   - if a cancel was requested before start, call `execution.cancel()`,
     save and return.
3. `load_plan`. On `ExecutionGraphInvalidError`, call
   `execution.fail(ExecutionError(code="EXECUTION_GRAPH_INVALID",
   kind=VALIDATION, details={"issue_codes": ...}))`, save and return.
4. `execution.start()`, then `mark_node_ready` for every root, save and
   log `execution_started`.
5. Enter `_schedule`.

### Scheduler loop (`_schedule`)
Each iteration:
1. **Check the stop verdict.** `_verdict(request, deadline, now, failure)`
   returns `"cancel"` if the cancel event is set, else `"time_out"` if the
   loop time has reached the deadline, else `"fail"` if a failure was
   recorded, else `None`. A verdict goes to `_end` and the loop returns.
   Checking this first means no node starts after a stop reason exists.
2. **Launch.** `_launch` takes the READY nodes sorted by id, up to
   `max_concurrency - len(running)`. It calls `start_node` for each, saves
   once, and only then creates tasks. RUNNING is therefore committed before
   any node code runs. If nothing was launched but earlier outcomes are
   unsaved (`dirty`), it saves anyway, so completions are persisted
   promptly.
3. If nothing is running, the loop exits: `execution.complete()`, save, log.
4. **Wait.** `asyncio.wait([*running, cancel_signal],
   timeout=remaining_deadline, FIRST_COMPLETED)`. `cancel_signal` is a task
   awaiting `request.event.wait()`.
5. **Record.** `_record` handles finished tasks sorted by node id.
   A `NodeResult` goes to `complete_node(outputs=...)`; an `ExecutionError`
   goes to `fail_node`, and the first failure is remembered.
6. **Resolve readiness.** For each completed node,
   `plan.ready_after(...)` gives the nodes to pass to `mark_node_ready`.
   Nodes marked ready after a failure are cancelled by the terminal cascade.

`_end(verdict)`:
1. If the verdict is cancel: `request_cancellation(reason)`.
2. `request.stopping = True`.
3. `_stop`: cancel every in-flight task, await all of them, and record any
   that finished anyway (did not end cancelled).
4. The terminal transition: `cancel()`, `time_out()` or `fail(failure)`.
5. `_finish`: save and log.

The `finally` block of `_schedule` cancels `cancel_signal`. If tasks are
still running, it sets `stopping`, cancels them and awaits them. This is
the no-leak guarantee for persistence errors and outer cancellation.

### Dependency resolution and output propagation (`ExecutionPlan`)
- **Readiness:** a node is ready when it is PENDING and the source of every
  incoming edge is COMPLETED. There is no control flow yet, so every edge
  is a hard dependency.
- **Single input:** `inputs_for` deep-copies the upstream output value.
- **`multiple` input:** collects values in graph edge order. A source port
  that is `multiple` and returns a list contributes its items (extend);
  any other source contributes one element (append).
- **Unconnected optional input:** receives a deep copy of the port default.
- **Why deep copies:** sibling branches share upstream outputs, and a
  consumer that mutates its input must not affect another consumer. Two
  tests show this, one unit and one integration.

### Node execution (`execute_node`)

| Situation | Result |
|---|---|
| `validate_inputs` fails | `NODE_INPUT_INVALID` (VALIDATION); node code is not called |
| `NodeError` | `ExecutionError.from_node_error` (keeps kind and details) |
| `TimeoutError` and the runtime limit expired | `ExecutionError.node_timeout` |
| `TimeoutError` raised by the node itself | `NODE_TIMEOUT` with message "node reported a timeout" |
| Any other `Exception` | `NODE_UNEXPECTED_ERROR` (PERMANENT), message "node raised an unexpected <Type>" |
| Returned normally but the limit expired (cancellation swallowed) | Node timeout |
| Result is not a `NodeResult` with a `Mapping`, or `validate_outputs` fails | `NODE_OUTPUT_INVALID` |

For unexpected exceptions, the executor logs `node_unexpected_error` with
`error_type` and `traceback` (from `traceback.format_tb`: frames only, no
exception message).

### Cancellation
- **In-process:** `cancel()` sets `_CancelRequest.event`. The loop's
  `cancel_signal` wakes it up. Nodes see `ctx.is_cancelled()` return True
  and receive `CancelledError` at their next `await`, so their `finally`
  blocks run.
- **Out-of-process:** another runtime instance loads, cancels and saves.
  The owner's next CAS save raises `ConcurrentModificationError`, and its
  `finally` block stops node tasks.
- **Blocking:** `_stop` awaits tasks without a grace timeout. A node that
  swallows `CancelledError` and never returns blocks the run. This is
  deliberate: a terminal state is never reported while node code runs.

### Timeouts
- **Node:** `TimeoutPolicy.node_timeout` is applied per attempt inside
  `execute_node`.
- **Execution:** a monotonic deadline, `loop.time() + execution_timeout`,
  is set at the start of `_schedule`. The remaining time bounds
  `asyncio.wait`. Expiry is decided by `_verdict` comparing `loop.time()`
  with the deadline.
- **Early timer firing:** the loop does not rely on `asyncio.wait`
  returning empty. If a timer fires slightly early (within the event loop's
  clock-resolution slack), the loop re-checks and waits again for the
  remainder.

### Persistence behaviour
- Every `StateManager` call is its own short transaction. No transaction
  is open while node code runs (backend AGENTS §16).
- Saves happen at these points:
  - start;
  - each launch batch;
  - after recording outcomes when nothing was launched;
  - the terminal state.
- `SqlAlchemyExecutionRepository.get` reads the execution row with
  `SELECT ... FOR SHARE`, then the node rows ordered by `node_id`, then the
  pinned `WorkflowVersion` through the version repository.

### Time and identity
- The runtime takes an injectable `clock` (default `utcnow`) for all domain
  timestamps.
- Tests use a strictly increasing `TickClock`, so timestamp order equals
  event order.
- `ExecutionService` still uses domain defaults (`utcnow`) for
  `created_at`.

### Logging
`akasha.runtime` logs `execution_started`, `node_started`,
`node_completed`, `node_failed`, `execution_completed`, `execution_failed`,
`execution_cancelled` and `execution_timed_out`.
- Fields: `execution_id`, `workflow_id`, `workflow_version`,
  `correlation_id`, `status`, and `node_id`, `error_code` or `error_kind`
  where applicable.
- Never logged: inputs, outputs or config values.

### Dependency injection
- The runtime receives a UoW factory and a catalog. There are no globals.
- `_active` (running executions and their cancel requests) is state of the
  runtime instance and is asyncio-only; it is not thread-safe.

---

## 9. Findings

### F1: Two-statement aggregate load can tear under READ COMMITTED
- **Impact:** HIGH. A concurrent reader could rehydrate a state that never
  existed.
- **Resolution:** FIXED (B1).
- **Notes:**
  - Found by `test_two_runtimes_racing_execute_each_node_exactly_once`.
  - The Phase 08 rehydration invariant (CREATED/QUEUED ⇒ PENDING nodes)
    turned the silent inconsistency into a loud `DomainValidationError`.
  - The same pattern would apply to any future multi-table aggregate.

### F2: `asyncio.TaskGroup` wraps body exceptions in `BaseExceptionGroup`
- **Impact:** MEDIUM. It would have changed the error contract of `run()`.
- **Resolution:** WORKAROUND. Explicit task ownership instead (§5.6).
- **Notes:** found while designing the loop, before any test ran against
  it.

### F3: Only user cancellation reached `is_cancelled()`
- **Impact:** MEDIUM. Nodes polling `is_cancelled()` in CPU loops would
  not notice timeout, failure or shutdown stops.
- **Resolution:** FIXED (B4).

### F4: The stop check must precede launching
- **Impact:** MEDIUM. A cancel arriving during the start save, or a
  deadline passing, would persist and launch a new batch that was then
  cancelled immediately.
- **Resolution:** FIXED (B2/B3).

### F5: PostgreSQL FK can reference the existing unique *index* on `workflow_versions(workflow_id, version)`
- **Impact:** LOW.
- **Resolution:** FIXED (used as designed).
- **Notes:** no extra unique constraint was needed. Verified by the
  migration tests, `test_execution_must_reference_a_published_version` and
  `test_version_with_executions_is_protected`.

### F6: JSONB cannot store NaN/Infinity, and JSON cannot encode bytes
- **Impact:** MEDIUM. A node output such as `float("nan")` would otherwise
  make a save fail and leave the execution RUNNING.
- **Resolution:** WORKAROUND. `_persistable` stubs.
- **Notes:** this was anticipated during design, not observed as a
  failure. `test_non_finite_output_cannot_break_persistence` verifies that
  the save does not raise. Strings containing `\u0000`, which PostgreSQL
  JSONB also rejects, are not handled (OPEN, L7).

### F7: Phase 07 "aggregate bypass" risk (L1) did not materialize
- **Impact:** LOW.
- **Resolution:** FIXED (verified).
- **Notes:** the runtime calls only `Execution.*` transition methods
  (`start`, `mark_node_ready`, `start_node`, `complete_node`, `fail_node`,
  `request_cancellation`, `cancel`, `time_out`, `fail`, `complete`).
  Verified by grep; no direct `NodeExecution` mutators are used.

### F8: `NodeExecutionContext.workflow_version` is a string with ambiguous semantics
- **Impact:** LOW.
- **Resolution:** OPEN (documented).
- **Notes:** the Phase 03 example used `"1.0.0"`. The runtime passes the
  version number (`"1"`) and exposes the version id via
  `runtime_metadata["workflow_version_id"]`.

### F9: ruff `ASYNC109` rejects an async function parameter named `timeout`
- **Impact:** LOW (tooling).
- **Resolution:** FIXED. Renamed to `limit` in `execute_node`.

---

## 10. Bugs / Problems Found

| # | Problem | Origin | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|---|---|
| B1 | Losing runtime in a race raised `DomainValidationError("unstarted execution with progressed nodes")` | Introduced this phase (new repository), found by race test | Execution row and node rows read by two SELECTs; a commit in between gave a torn aggregate | `with_for_update(read=True)` on the execution row in `SqlAlchemyExecutionRepository.get` | `test_load_never_sees_a_torn_aggregate` (fails without the fix; verified by mutation), `test_two_runtimes_racing_…` | infrastructure/persistence |
| B2 | A cancel arriving while the start was persisted still launched and persisted RUNNING for the first batch | Introduced this phase, found in review | Stop reasons were evaluated only after `asyncio.wait` | `_verdict` evaluated at the top of each iteration | `test_cancel_while_start_is_persisted_starts_no_node` | runtime |
| B3 | A deadline passing between steps could launch new nodes | Introduced this phase, found in review | Same as B2; timeout was detected only as "`wait` returned nothing" | Deadline check in `_verdict` | `test_execution_timeout_stops_the_whole_run` plus mutant "deadline check removed" (killed) | runtime |
| B4 | `is_cancelled()` was False for nodes stopped by timeout, sibling failure or shutdown | Introduced this phase, found in review | Context used `request.event.is_set` | `_CancelRequest.stopping` plus `should_stop()` | `test_failure_stops_in_flight_sibling`, `test_execution_timeout_…`, `test_outer_cancellation_…` assert `:True` | runtime |
| B5 | After the B4 fix, `is_cancelled()` returned True after a *successful* run | Introduced by the B4 fix | `finally` set `stopping` unconditionally | Set `stopping` only when tasks are still running | `test_node_context_is_scoped_and_isolated` | runtime |
| T1 | Merge test assumed B's recorded finish is after C's | Test bug | B and C finished in one `wait` batch and were recorded in node-id order | Assert `max(B, C).finished_at < D.started_at` | the test itself | tests |
| T2 | mypy: `finished` list typed with the `cancel_signal` task type (`Task[Literal[True] \| ...]`) | Type error in new code | Built from `done`, which contains `cancel_signal` | Build `finished` from `running` keys | mypy gate | runtime |
| T3 | ruff `S105` on a test variable named `secret` | Lint | Rule matches the name | Renamed to `canary` | ruff gate | tests |
| D1 | First draft used a placeholder `_node_of` raising `NotImplementedError` to map finished tasks to node ids | Drafting error, fixed before the first test run | `_record` pops tasks from `running`, losing the mapping | `_record` returns completed node ids | all runtime tests | runtime |
| D2 | First draft used `TaskGroup` | Design error (F2) | Exception-group wrapping | Explicit ownership | outer-cancel and out-of-process-cancel tests | runtime |

No pre-existing (earlier-phase) bugs were found. No environmental problems
occurred: Docker 29.1.2 was available throughout.

---

## 11. Fixes Applied

1. **B1 (torn read)**
   - Symptom: race test failure with `DomainValidationError`.
   - Implementation: `FOR SHARE` on the execution row, with a docstring
     comment explaining why.
   - Verification:
     - the deterministic lock-wait regression test (bounded polling of
       `pg_stat_activity` until the reader waits, then commit the writer,
       then the reader sees RUNNING with READY nodes);
     - removing the lock makes that test fail;
     - 5 repeated runs of the race-sensitive subset passed.
2. **B2/B3 (launch after a stop reason)**
   - Implementation: loop restructured around `_verdict` and `_end`;
     `_launch` returns whether it saved; a `dirty` flag ensures completions
     are saved when nothing launches.
   - Verification: new test with a `GatedUow` that pauses the 2nd UoW use
     (the start save); `test_completed_node_is_persisted_while_siblings_still_run`.
3. **B4/B5 (`is_cancelled` semantics)**
   - Implementation: `stopping` is set in `_end` and in `finally` (only
     when tasks remain).
   - Verification: 4 tests assert the observed flag inside node cleanup or
     after the run.
4. **Redundant guard removed**
   - Symptom: an ad-hoc mutation run showed that deleting
     `if failure is None:` before readiness marking changed no observable
     behaviour.
   - Implementation: the guard was removed as dead logic, because the
     terminal cascade cancels such READY nodes anyway.
5. **Test gap closed**
   - Symptom: a mutation removing truthful recording in `_stop` survived.
   - Implementation: `test_node_finishing_while_being_stopped_is_recorded_truthfully`.
   - Verification: the mutant is now killed.
6. **T1–T3:** see §10. Verified by pytest, mypy and ruff.

---

## 12. Testing & Verification

### Unit Tests
**`tests/runtime/test_runtime_units.py` (17): PASS**
- Executor (12 including parametrized cases):
  - success;
  - `NodeError` kind and node id preserved;
  - invalid inputs never reach node code;
  - 3 output-contract violations (wrong type, missing port, extra port);
  - a non-`NodeResult` return;
  - an unexpected exception whose message is dropped;
  - runtime timeout, with the exact message;
  - a timeout the node swallowed;
  - the node's own `TimeoutError`;
  - `CancelledError` propagates.
- Plan (4):
  - roots and downstream;
  - readiness only after every upstream node, with no re-marking;
  - edge-order collection with list flattening, and inputs and defaults
    returned as private copies;
  - `ExecutionGraphInvalidError` carries the structured issues.
- `max_concurrency=0` is rejected (1).

**`tests/domain/test_execution.py` (5 new; 260 total): PASS**
- outputs are stored as a private copy;
- outputs on a non-COMPLETED node are rejected;
- CREATED/QUEUED with progressed nodes is rejected (2 parametrized cases);
- a negative revision is rejected.

### Integration Tests (PostgreSQL 17.2 via Testcontainers)

**`tests/integration/test_runtime.py` (34): PASS**

Roadmap scenarios:

| Roadmap scenario | Test |
|---|---|
| A→B→C | `test_linear_graph_propagates_outputs_in_dependency_order` |
| A→B, A→C | `test_branching_feeds_both_branches_from_one_output` |
| A→B, A→C→D | `test_roadmap_branch_then_chain_a_b_c_d` |
| Node failure | `test_node_failure_fails_execution_and_cancels_downstream` |
| Invalid graph | `test_graph_invalid_at_runtime_fails_before_any_node_runs` |
| Parallel execution | `test_parallel_branches_really_run_concurrently` (2-party `asyncio.Barrier`; sequential execution deadlocks) |

Other tests, by area:
- **Ordering and merge:**
  - `test_merge_waits_for_every_dependency_and_collects_in_edge_order`
  - `test_concurrency_limit_bounds_in_flight_nodes` (asserts persisted
    RUNNING, RUNNING, READY)
- **Failures:**
  - `test_failure_stops_in_flight_sibling`
  - `test_unexpected_exception_is_contained_and_never_leaks_its_message`
  - `test_invalid_node_output_is_a_structured_node_failure`
- **Timeouts:**
  - `test_node_timeout_fails_node_with_timeout_error`
  - `test_execution_timeout_stops_the_whole_run`
- **Cancellation:**
  - `test_cancel_running_execution_stops_nodes_cooperatively` (idempotent,
    first reason wins)
  - `test_cancel_keeps_truthful_outcome_of_finished_branch`
  - `test_node_finishing_while_being_stopped_is_recorded_truthfully`
  - `test_cancel_queued_execution_before_it_runs`
  - `test_cancel_while_start_is_persisted_starts_no_node`
  - `test_cancel_after_run_registered_but_before_start`
  - `test_cancel_finished_execution_is_rejected`
  - `test_out_of_process_cancel_makes_the_owner_stop`
- **Duplicate execution and ownership:**
  - `test_same_runtime_refuses_to_run_an_execution_twice`
  - `test_two_runtimes_racing_execute_each_node_exactly_once`
  - `test_outer_cancellation_stops_node_tasks_and_keeps_running_state`
    (also asserts RUNNING was persisted before node code ran)
- **Persistence timing:**
  - `test_completed_node_is_persisted_while_siblings_still_run`
- **Load scenarios:**
  - `test_wide_fan_out_fan_in_and_deep_chain`
  - `test_many_concurrent_executions_on_one_runtime_stay_isolated`
- **Snapshot and isolation:**
  - `test_execution_runs_its_pinned_version_not_later_drafts`
  - `test_node_context_is_scoped_and_isolated`
  - `test_object_outputs_are_isolated_between_branches`
  - `test_full_state_round_trips_through_the_database`
- **Guards:**
  - `test_run_unknown_execution`
  - `test_create_execution_for_missing_version`
  - `test_configured_retry_policy_is_refused_not_ignored`

**`tests/integration/test_execution_persistence.py` (10): PASS**
- **Round-trips:**
  - full aggregate (retry history, bytes stub, cancellation, policies,
    metadata, timestamps);
  - failed-execution error;
  - NaN output does not break saving.
- **Concurrency:**
  - CAS on revision (the stale writer's cancellation never lands);
  - a duplicate insert is a conflict.
- **Constraints:**
  - FK to a published version;
  - `RESTRICT` protects a version that has executions;
  - status CHECK constraint.
- **Lock regression:** `test_load_never_sees_a_torn_aggregate`.
- **Upgrade path:** `test_upgrade_from_0002_then_execute_existing_version`
  (a scratch database is migrated to 0002, a published version is inserted
  as raw rows, the database is upgraded to head, then the version is
  created and run as an execution to COMPLETED).

**Existing integration suite:**
- `test_persistence.py`: the migration up/down/up test now expects `0003`
  and the new tables.
- `test_workflow_service.py` and `test_workflows_api.py`: unchanged.
- Total: 121 integration tests pass.

### Infrastructure Tests
- **Migrations:**
  - empty DB up/down/up to `0003_executions`;
  - 0001 → head with backfill (existing test, now passes through 0003);
  - 0002 → head followed by an execution.
- **Real PostgreSQL behaviour exercised:**
  - FK and CHECK constraints;
  - row locks (`FOR SHARE` waiting on an uncommitted `UPDATE`);
  - CAS.
- Result: PASS.

### Security Tests
- `test_unexpected_exception_is_contained_and_never_leaks_its_message`: a
  credential-like canary in an exception message is absent from:
  - the `executions.error` and `node_executions.failures` JSON text,
    queried as raw SQL;
  - every attribute of every captured log record.
- The unit counterpart checks the same for the `ExecutionError` message.
- Config tampering by a node does not reach the snapshot, and inputs are
  isolated between branches.
- Result: PASS.

### Performance Tests
- Two load scenario tests (§16). They are functional tests with
  measurements, not benchmarks.

### Additional verification
- **Mutation check:** an ad-hoc script (not committed; deleted afterwards)
  applied 14 single-line mutants to `plan.py`, `runtime.py`, `executor.py`
  and the repository lock. All 14 were killed. One equivalent mutant led to
  the guard removal described in §11.
- **Flakiness:**
  - the race-sensitive subset ran 5×;
  - `tests/integration` plus `tests/runtime` ran 3×;
  - the full suite ran repeatedly;
  - no failures after the fixes.
- **Synchronization:**
  - asyncio `Event`, `Barrier` and `GatedUow`;
  - bounded polling only for database commits and locks
    (`pg_stat_activity`, persisted status);
  - timeout tests block forever, so the configured limit decides the
    outcome;
  - `GUARD = 10 s` is only a deadlock failsafe.

### Not performed
- Component tests, API tests, E2E tests and accessibility tests do not
  apply: no API or UI changed.
- No dependency audit tool is configured.

---

## 13. Quality Gates

Final run on the working tree: Windows, Python 3.14.0, uv, Docker 29.1.2.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 69 files already formatted |
| Linter (`uv run ruff check .`) | PASS | Intermediate failures, since fixed: ASYNC109 (F9), S105 (T3) |
| Type Checker (`uv run mypy .`, strict) | PASS | 67 source files. Intermediate failure T2 fixed |
| Unit Tests | PASS | runtime units 17, domain 260 |
| Integration Tests (`tests/integration`) | PASS | 121 passed (PostgreSQL Testcontainers). Intermediate failures B1 and T1 fixed |
| API Tests | PASS | Existing API tests (in the integration suite) are unchanged and pass; no new API |
| Full suite (`uv run pytest`) | PASS | 559 passed, 1 warning |
| Architecture test (`tests/test_architecture.py`) | PASS | The runtime imports no FastAPI/Starlette/Uvicorn |
| E2E Tests | N/A | Not in scope |
| Build (`uv build`) | PASS | sdist and wheel built into a temp directory, then deleted |
| Security Checks | N/A | No audit tool configured; the security tests above pass |
| Accessibility | N/A | Backend |
| Other: warning | Note | `StarletteDeprecationWarning` from `fastapi/testclient.py` (httpx → httpx2); pre-existing, unrelated |

No `noqa` and no `type: ignore` were added in any new or changed file.

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| A published Workflow Version can be executed from start to finish | PASS | Every runtime integration test publishes via `WorkflowService.publish_workflow`, creates via `ExecutionService.create_execution`, runs via `ExecutionRuntime.run` and asserts COMPLETED from a fresh DB read. Also `test_upgrade_from_0002_…` for a pre-existing version. Caveat: only with an injected node catalog; production has no catalog or caller (L8), which is Phase 09/22 scope and does not block this criterion |
| Implement GraphLoader | PASS | `runtime/plan.py::load_plan` |
| Implement GraphValidator (integration) | PASS | `load_plan` calls `validate_graph`; `test_graph_invalid_at_runtime_…`, `test_invalid_graph_is_rejected_…` |
| Implement ExecutionRuntime | PASS | `runtime/runtime.py::ExecutionRuntime` |
| Implement Scheduler | PASS | `_schedule` / `_launch`: id-order, concurrency limit; `test_concurrency_limit_bounds_in_flight_nodes` |
| Implement DependencyResolver | PASS | `ExecutionPlan.ready_after` / `inputs_for`; merge and unit readiness tests |
| Implement NodeExecutor | PASS | `runtime/executor.py::execute_node`; 12 unit tests |
| Implement StateManager | PASS | `runtime/runtime.py::StateManager` |
| Support linear execution | PASS | `test_linear_graph_…` |
| Support branching | PASS | `test_branching_…`, `test_roadmap_branch_then_chain_a_b_c_d` |
| Support parallel independent nodes | PASS | Barrier test; concurrency limit test |
| Support output propagation | PASS | Linear/diamond outputs `"abc"`, `"a-b+a-c"`; 72-node load test; isolation tests |
| Support node failure | PASS | `test_node_failure_…`, `test_failure_stops_in_flight_sibling`, unexpected-exception and invalid-output tests |
| Support execution failure | PASS | `test_graph_invalid_at_runtime_…` (execution-level failure without a node failure); node failure propagates to execution FAILED |
| Support successful completion | PASS | All happy-path tests |
| Implement execution persistence | PASS | Migration 0003, repository, 10 persistence tests; every runtime test asserts persisted state |
| Roadmap test "A → B → C" | PASS | see above |
| Roadmap test "A → B, A → C" | PASS | see above |
| Roadmap test "A → B, A → C → D" | PASS | see above |
| Roadmap test "Node failure" | PASS | see above |
| Roadmap test "Invalid graph" | PASS | see above |
| Roadmap test "Parallel execution" | PASS | see above |
| Session: cancellation boundaries | PASS | 8 cancellation tests (§12) |
| Session: timeout boundaries | PASS | Node and execution timeout tests; 3 executor timeout units |
| Session: no arbitrary sleeps | PASS | Only bounded polling of DB state, as permitted by backend AGENTS §62 |
| Session: review for races, duplicates, ordering, inconsistencies, partial failures, cancellation bugs, leaks | PASS | B1–B5 found and fixed; race, duplicate-run, ordering, torn-read, outer-cancel (leak) tests |
| Session/prompt: no triggers, plugins or worker queues | PASS | None added; `NodeCatalog` mapping is the only registry stand-in |

No criterion is PARTIAL or FAIL.

The overall status "COMPLETE WITH LIMITATIONS" reflects §20 (no production
entry point, retries refused, no recovery). These are not missing Phase 08
scope, but they constrain the resulting system.

---

## 15. Security Considerations

- **Secrets and error disclosure:**
  - Unexpected node exceptions persist and log only the exception type and
    the traceback frames, never `str(exc)`.
  - The frame text contains source lines, so hardcoded literals in node
    source would appear in logs. That is acceptable for trusted code, but
    it is not redaction.
- **Still unredacted (OPEN, Phase 13):**
  - `NodeError` messages and details from nodes are persisted verbatim.
    This is the Phase 07 open issue, now actually persisted.
  - Node outputs are persisted unredacted as JSONB. They may contain
    sensitive business data or secrets returned by nodes.
- **Logging:** runtime logs contain ids, states and error codes only.
  Inputs, outputs and config values are never logged (AGENTS §76/§77).
  Node loggers are named by node type, which is bounded (validated
  catalog keys).
- **Isolation:**
  - Nodes receive a deep copy of their config and deep-copied inputs.
  - Nodes cannot access the aggregate, the database or other nodes' state
    (architecture §103).
  - Nodes are still trusted, in-process code: no sandbox (backend AGENTS
    §24).
- **Snapshot integrity:**
  - Executions pin `(workflow_id, version)` with a `RESTRICT` FK.
  - Draft edits and later publishes cannot affect a queued or running
    execution (tested).
- **Input validation:** node inputs and outputs are validated against the
  contract at runtime. Invalid values become structured errors, not
  crashes.
- **Resource exhaustion:**
  - The concurrency limit is enforced (default 8).
  - Outputs are unbounded in size and there is no graph-size cap
    (Phase 24).
- **Not applicable:** authentication and authorization (no endpoints),
  SSRF, path traversal, expressions and plugins.

---

## 16. Performance Considerations

- **Saves per run:** each save is one CAS UPDATE plus one multi-row upsert
  of *all* node rows. Saves happen at start, per launch batch, per recorded
  batch that launched nothing, and at the terminal state. Cost per save is
  O(nodes); a run costs about O(nodes²) row writes in the worst case. There
  is a `ponytail:` comment in `SqlAlchemyExecutionRepository.save`.
- **Scheduler:** the READY scan and sort each iteration is O(nodes log
  nodes); `ready_after` is O(out-degree × in-degree).
- **Measurements** (single run, Windows laptop, Testcontainers PostgreSQL,
  test phase "call" only):
  - `test_wide_fan_out_fan_in_and_deep_chain` (72 nodes, 40-wide,
    `max_concurrency=16`): 2.39 s in the final run (2.77 s in an earlier
    run).
  - `test_many_concurrent_executions_on_one_runtime_stay_isolated`
    (10 × 2-node executions in parallel): 1.08–1.30 s.
  - The time is dominated by database round-trips. No profiling was done.
- **Memory:**
  - node outputs are held in the aggregate for the whole run;
  - inputs are deep-copied per consumer, which costs CPU and memory
    proportional to payload size.
- **Connection pool:** the existing `Database` pool (5 + 5 overflow) was
  sufficient for 10 concurrent executions in tests.
- **Loading:** `get` issues 3 queries (execution with `FOR SHARE`, node
  rows, version). No N+1.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §11 "Runtime Architecture" | Authoritative runtime semantics | Module map; QUEUED-only; run-time re-validation; single writer; readiness; id-order launch and concurrency limit; RUNNING persisted first; input deep copy and edge-order collection; fail-fast and precedence; stop semantics; CAS; outer cancellation leaves RUNNING; retries refused |
| `docs/architecture.md` §53 "Execution Persistence" | Storage mapping promised in the Phase 07 handoff | Tables, FKs, JSONB documents, output stubs, inputs not stored, share-locked loading |
| `backend/src/akasha/runtime/__init__.py` | Discoverability | Roadmap component → module map |
| `docs/reports/backend/phase-08-execution-runtime.md` | Phase report | This document |

Documentation to update in future phases:
- `backend/README.md`: does not describe running executions. Update when
  a CLI (Phase 21) or API entry point exists.
- Architecture §11: when retries (Phase 23), events (Phase 11) or control
  flow (Phase 09+) change the loop.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or version-changed (`pyproject.toml`
  and `uv.lock` unchanged).
- **Database:** new migration `0003_executions` (two tables, constraints,
  indexes).
- **Test infrastructure:**
  - `clean_db` truncates the two new tables.
  - The new `GatedUow` test helper, local to `test_runtime.py`, wraps the
    real `SqlAlchemyUnitOfWork`.
  - No new containers.
- **Unchanged:** configuration, environment variables, Docker and Redis.
  `max_concurrency` is a constructor argument, not a setting (L8).

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Dependency/blocker | Affects Phase 08 completion |
|---|---|---|---|---|
| Node Registry supplying `NodeCatalog` and definitions in production | Registry scope | 09 | — | No (criterion proven with an injected catalog) |
| Composition-root wiring of `ExecutionService`/`ExecutionRuntime`; `max_concurrency` setting | No caller exists until queue/worker or CLI | 21/22 | Worker/queue design | No |
| Execution HTTP endpoints (start, get, cancel, list with pagination) | HTTP must not block on runs (backend AGENTS §42); needs a background owner | after 22 | Worker/queue | No |
| Executing retries (`retry_node`, waiting for backoff) | Retry is roadmap Phase 23 scope; refused explicitly now | 23 | — | No |
| Recovery of RUNNING executions left by dead processes; heartbeats and leases | Recovery and worker scope | 22/23 | — | No |
| Grace period for nodes that ignore cancellation | Needs a policy decision (abandon vs. block) | 23 | — | No |
| Events (`ExecutionStarted`, …) from the loop | Event system | 11 | — | No |
| Metrics (durations, counts) | Observability | 12 | — | No |
| Redaction of persisted error text and outputs | Credentials/secrets | 13 | — | No |
| Resolved node versions in `ExecutionMetadata` | Needs registry | 09 | Registry | No |
| Output size limits, graph size limits, dirty-row persistence | Resource limits and performance | 24 | — | No |
| Control flow (conditional readiness, SKIPPED, loops) | Control-flow nodes | 09+ | — | No |

---

## 20. Known Limitations

- **L1 (stale runs):** a process that dies leaves its executions RUNNING,
  with node rows possibly RUNNING. There is no heartbeat, lease or
  recovery. Another runtime can still cancel such an execution, because
  that is a CAS write.
- **L2 (retries):** retries are not executed. `run()` raises
  `ConfigurationError` if `retry_policy.max_attempts > 1`.
  `ExecutionService` never sets a retry policy.
- **L3 (non-cooperative nodes):** a node that swallows `CancelledError`
  and never returns blocks its run indefinitely during a stop.
- **L4 (per-node timeout visibility):** a per-node timeout reaches that
  node only as `CancelledError`; `is_cancelled()` stays False for it.
- **L5 (cancel racing completion):** an in-process `cancel()` that arrives
  after the loop has decided to complete (for example, during the final
  save) is silently a no-op, and the run ends COMPLETED. `cancel()` does
  not report whether it took effect.
- **L6 (persistence cost):** every save rewrites every node row (§16).
- **L7 (persisted outputs):**
  - Unbounded in size.
  - Non-JSON values are lossy stubs, so a future recovery cannot resume
    from binary outputs.
  - Strings containing `\u0000` will make the JSONB write fail, and that
    run is then left RUNNING.
- **L8 (no production entry point):**
  - No API endpoint and no bootstrap wiring.
  - The production catalog is empty, so executions can only be run by code
    that constructs `ExecutionRuntime` itself (tests today).
- **L9 (context field):** `NodeExecutionContext.workflow_version` carries
  the version number as a string (F8).
- **L10 (not thread-safe):** runtime instance state (`_active`) supports a
  single event loop only.
- **L11 (created_at clock):** `created_at` is set by the domain default
  clock, not the runtime's injected clock. Test timestamps therefore mix a
  real `created_at` with fake runtime times; the domain does not check
  timestamp monotonicity (Phase 07 L3).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Persisted `ExecutionError` text and node outputs are unredacted | Secrets or sensitive data returned or raised by nodes are stored in PostgreSQL | MEDIUM | infrastructure/persistence, runtime | Redact before persisting | 13 |
| Stale RUNNING executions after a crash (L1) | Executions stay RUNNING indefinitely | MEDIUM | runtime, recovery | Leases/heartbeats plus stale detection | 22/23 |
| `\u0000` in output strings breaks the save (L7) | Run left RUNNING; `run()` raises | LOW | persistence | Strip or escape NUL in `_persistable`, or store outputs as artifacts | 23/24 |
| Node ignoring cancellation blocks the run (L3) | Hung execution | LOW | runtime | Decide a grace-period policy | 23 |
| `cancel()` gives no feedback when racing completion (L5) | Caller may assume the cancel took effect | LOW | runtime | Return or raise based on outcome when an API exposes cancel | 22 (API) |
| Empty production catalog, no wiring (L8) | Runtime unusable outside tests | MEDIUM | bootstrap | Registry, then wiring | 09, 21/22 |

---

## 22. Architectural / Product Decisions

- **Only QUEUED executions are runnable.** Resuming RUNNING executions is
  recovery (Phase 23), not `run()`.
- **The first failure in node-id order becomes the execution error.**
  "First" is deterministic, not temporal, when several nodes fail in one
  batch.
- **Stop precedence is cancel > execution timeout > node failure.** A user
  who cancelled never sees FAILED or TIMEOUT; this is consistent with the
  Phase 07 "only cancel after request" rule.
- **Truthful records beat tidy records.** An attempt that finishes during
  a stop is recorded COMPLETED or FAILED, even inside a CANCELLED
  execution.
- **RUNNING is committed before node code runs.** A persisted RUNNING node
  therefore means "may have produced side effects". Phase 23 recovery must
  treat it that way.
- **Retry policies are refused, not ignored.** A silently ignored policy
  would be a correctness lie.
- **Execution errors for invalid graphs carry only issue codes**
  (`details.issue_codes`). The full issue list goes to the error raised by
  `load_plan`, not into the persisted error. This keeps it small and
  payload-free.
- **Node executions are rows; value objects are JSONB.** Per-node
  queryability is kept for the inspector and recovery.
- **Inputs are not persisted.** They can be reconstructed from upstream
  outputs and the pinned graph (architecture §53 now says so).
- **`FOR SHARE` on load** is the chosen consistency mechanism for the
  two-table aggregate. REPEATABLE READ per load would also work but
  changes the UoW's transaction configuration.
- **A fresh node instance per attempt.** No node state is shared across
  executions or attempts.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **HEAD:** `3f79592 backend phase 07 - execution domain and state machine`.
- **Working tree:** uncommitted; nothing staged; no commit made (none was
  requested).
- **Modified:**
  - `backend/src/akasha/application/persistence.py`
  - `backend/src/akasha/domain/execution.py`
  - `backend/src/akasha/infrastructure/db_models.py`
  - `backend/src/akasha/infrastructure/persistence.py`
  - `backend/src/akasha/runtime/__init__.py`
  - `backend/tests/domain/test_execution.py`
  - `backend/tests/integration/conftest.py`
  - `backend/tests/integration/test_persistence.py`
  - `docs/architecture.md`
- **Untracked:**
  - `backend/migrations/versions/0003_executions.py`
  - `backend/src/akasha/application/executions.py`
  - `backend/src/akasha/runtime/executor.py`
  - `backend/src/akasha/runtime/plan.py`
  - `backend/src/akasha/runtime/runtime.py`
  - `backend/tests/integration/test_execution_persistence.py`
  - `backend/tests/integration/test_runtime.py`
  - `backend/tests/runtime/`
  - `docs/reports/backend/phase-08-execution-runtime.md`
- **Diff of tracked files:** 9 files, +494/−11.
- **Line endings:** all changed and new files use LF (checked; no CRLF
  found).
- **Unrelated changes:** none detected.
- **Temporary artifacts:** the mutation script and build outputs lived in
  `%TEMP%\opencode` and have been deleted. The existing
  `backend/.hypothesis/` directory is git-ignored and was not touched
  intentionally.

---

## 24. Phase Completion Assessment

### Implemented
- GraphLoader, validator integration and DependencyResolver
  (`runtime/plan.py`).
- NodeExecutor (`runtime/executor.py`).
- ExecutionRuntime, Scheduler and StateManager (`runtime/runtime.py`), with
  cancellation (in-process and out-of-process) and node and execution
  timeouts.
- `ExecutionService`.
- Execution persistence: repository, tables and migration 0003.
- Domain additions: outputs, revision, invariant.
- Architecture documentation.

### Verified
- 559 tests pass, including 121 PostgreSQL integration tests.
- All six roadmap scenarios pass.
- 14 of 14 mutants were killed.
- The race-sensitive tests were repeated without failures.
- ruff, ruff format, mypy (strict) and build all pass.

### Not Implemented
- Nothing within the Phase 08 roadmap scope.

### Deferred
- See §19: registry, wiring, API, retries, recovery, events, metrics,
  redaction, limits, control flow.

### Open
- See §21: redaction of persisted text and outputs; stale RUNNING
  executions; NUL characters in outputs; non-cooperative nodes; cancel
  feedback; no production catalog or wiring.

### Blocking Issues
- None for this phase.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**Phase 09 (Node Registry + Built-in Nodes) can assume:**
- **Catalog:** `ExecutionRuntime(uow, catalog)` expects
  `catalog: Mapping[node_type_id, type[Node]]`. `WorkflowService` expects
  `Mapping[node_type_id, NodeDefinition]`. The registry should produce both
  from one source so that publish-time and run-time validation agree. The
  runtime re-validates every run.
- **Node lifecycle:**
  - Nodes are instantiated with no arguments, once per attempt.
  - They get `NodeExecutionContext` with deep-copied `config` and
    `inputs`.
  - They must raise `NodeError` for expected failures.
  - They must let `CancelledError` propagate; cleanup belongs in
    `finally`.
- **Node timeouts and cancellation:** `TimeoutPolicy.node_timeout` wraps
  `execute()`. Long-running nodes (for example `core.delay`) should await
  cancellable primitives. CPU loops should poll `ctx.is_cancelled()`.
- **Output handling:**
  - Outputs are validated against the declared ports, so every declared
    output must be returned.
  - Outputs are persisted as JSONB; binary values become stubs.
- **Side effects:** a node's side effects may already have happened when
  its row says RUNNING. HTTP and SMTP nodes should consider idempotency
  keys (`ctx.execution_id`, `ctx.node_execution_id`).
- **Control flow:** If/Switch and SKIPPED require changing readiness
  (`ExecutionPlan.ready_after`) and the verdict. Today every edge is a hard
  dependency, and cycles are rejected by validation.

**Must not be changed casually:**
- the single-writer loop, and the rule that RUNNING is persisted before
  node code runs;
- CAS on `executions.revision`;
- `FOR SHARE` in `SqlAlchemyExecutionRepository.get`;
- stop precedence (`_verdict`);
- truthful recording in `_stop`;
- "outer cancellation leaves RUNNING";
- the refusal of `max_attempts > 1` until Phase 23 implements retries.

**Prerequisites and constraints:**
- Redaction (Phase 13) must hook into the persistence of
  `ExecutionError.message`/`details` and node outputs.
- Workers (Phase 22) must add ownership (leases) on top of the revision
  CAS and provide the background caller for `run()`.
- Recovery (Phase 23) must decide what to do with RUNNING nodes, which may
  have produced side effects.
