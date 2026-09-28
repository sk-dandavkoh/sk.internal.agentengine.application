# Phase 07 — Execution Domain + State Machine

## 1. Executive Summary

**Supposed to achieve (roadmap §11):** a formal execution model: `Execution`,
`NodeExecution`, execution/node states, `ExecutionError`, `RetryPolicy`,
`TimeoutPolicy`, `CancellationState`, with explicit transition validation and
tests for every valid and invalid transition. No runtime.

**Actually implemented:** Phase 02 already had both status enums and
target-keyed transition tables in `backend/src/akasha/domain/execution.py`.
This phase replaced those tables with **action-keyed** tables
(`EXECUTION_ACTIONS`, `NODE_ACTIONS`), derived the state→targets view from
them, made `Execution` the aggregate root that guards every node transition,
and added the value objects `ExecutionError`, `RetryPolicy`, `TimeoutPolicy`,
`CancellationState` and `ExecutionMetadata`. Retry, timeout and cancellation
semantics are enforced by the domain. The module still uses only the standard
library plus `akasha.errors`, `akasha.__version__` and other domain modules.

**Resulting system state:** the execution lifecycle is a pure, in-memory
domain model. It is not persisted, not exposed through the API and not driven
by a runtime (all Phase 08+). `tests/domain/test_execution.py` has 255 tests:
a hand-written specification, full state × action matrices, scenario tests, a
Hypothesis stateful fuzzer and BREAK regressions. An ad-hoc mutation run
killed 21 of 21 mutants. The full backend suite passes (493 tests) and ruff,
ruff format and mypy (strict) are clean.

Deliberately attacking the model found one bug from Phase 02 and three holes
in the new model; all are fixed and covered by regression tests.

**Acceptance criteria:** both roadmap criteria are satisfied (§14).

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap `/docs/roadmap-backend.md` §11 "Phase 07 — Execution Domain + State
Machine":

> Goal: Create the formal execution model.
> Implement: Execution, NodeExecution, ExecutionState, NodeExecutionState,
> ExecutionError, RetryPolicy, TimeoutPolicy, CancellationState.
> Execution states: CREATED, QUEUED, RUNNING, PAUSED, WAITING, COMPLETED,
> FAILED, CANCELLED, TIMEOUT. Node states: PENDING, READY, RUNNING,
> COMPLETED, FAILED, SKIPPED, CANCELLED.
> Implement explicit state transition validation.
> Tests: Test every valid and invalid state transition.
> Acceptance: Execution lifecycle is deterministic and invalid transitions
> are rejected.

The session prompt also required retry, timeout and cancellation semantics,
node execution state, execution metadata, structured execution errors, and
tests for the normal lifecycle, failure, retry, timeout, cancellation,
invalid transitions and terminal behaviour. It also required a
model → implement → test → break → fix loop.

**Explicitly not responsible for** (roadmap dependency graph and
backend AGENTS §78):
- Execution runtime, scheduler, dependency resolution and output propagation
  (Phase 08).
- Execution persistence and migrations (Phase 08; Phase 05 deferred it).
- Node Registry and resolved node versions (Phase 09).
- Events (Phase 11) and observability/metrics (Phase 12).
- Secret redaction (Phase 13).
- Workers, queues and queue timeouts (Phase 22).
- Recovery (Phase 23) and resource-limit caps (Phase 24).
- Any API endpoint.

---

## 3. Scope Implemented

### Domain: state machines (`backend/src/akasha/domain/execution.py`)
- `ExecutionStatus` and `NodeExecutionStatus` (`StrEnum`, values unchanged
  from Phase 02).
- `EXECUTION_ACTIONS: Mapping[str, tuple[frozenset[ExecutionStatus], ExecutionStatus]]`.
  The named actions are `enqueue, start, pause, wait, resume, complete, fail,
  cancel, time_out`.
- `NODE_ACTIONS`, with the named actions `mark_ready, start, retry, complete,
  fail, skip, cancel`.
- Derived via `_derive()`: `EXECUTION_TRANSITIONS`, `NODE_TRANSITIONS`
  (state → reachable targets), `TERMINAL_EXECUTION_STATUSES`,
  `TERMINAL_NODE_STATUSES` (states with no outgoing transitions).
- `_apply(actions, action, current) -> target` is the single transition
  check. It raises `InvalidStateTransitionError` with the details `action`,
  `from` and `to`.

### Domain: value objects
- `ExecutionError` (frozen): `code` (must match `^[A-Z][A-Z0-9_]*$`),
  `message` (non-blank), `kind: NodeErrorKind`, `node_id: str | None`
  (validated id), `details: Mapping[str, str]` (defensive copy). It has a
  `retryable` property and the factories `from_node_error(NodeError,
  node_id=)` and `node_timeout(node_id=, timeout=)`.
- `RetryPolicy` (frozen):
  - Fields: `max_attempts=1`, `initial_backoff=1s`, `backoff_multiplier=2.0`,
    `max_backoff=5min`, `retryable_kinds=RETRYABLE_ERROR_KINDS`.
  - Validation: `max_attempts ≥ 1`, backoff ≥ 0, multiplier ≥ 1, max ≥
    initial, and `CANCELLATION` is never allowed as a retryable kind.
  - Methods: `allows_retry(error, attempt=)` and `backoff(attempt)`
    (exponential, capped, falls back to the cap on `OverflowError`).
- `TimeoutPolicy` (frozen): `execution_timeout`, `node_timeout`
  (`timedelta | None`, must be positive), plus `execution_deadline(started_at)`
  and `node_deadline(started_at)`.
- `CancellationState` (frozen): `requested_at`, `reason`.
- `ExecutionMetadata` (frozen): `trigger_type`, `trigger_id` (validated id),
  `correlation_id` (1–128 printable characters), `engine_version` (defaults
  to `akasha.__version__`, must be non-blank).

### Domain: entities
- `NodeExecution`:
  - Fields: `attempt`, `failures: list[ExecutionError]`, `retry_at`,
    `started_at` (current attempt), `finished_at`.
  - Properties: `is_terminal`, and `error` (the last failure, only while the
    node is `FAILED`).
  - Mutators: `mark_ready, start, complete, fail, retry, skip, cancel`.
- `Execution` (aggregate root):
  - Fields: `metadata`, `retry_policy`, `timeout_policy`,
    `node_executions: dict[node_id, NodeExecution]`,
    `error: ExecutionError | None`, `cancellation: CancellationState | None`.
  - `Execution.create()` builds one `PENDING` NodeExecution per node of the
    pinned `ProjectVersion.graph`.
  - Execution transitions: `enqueue, start, pause, wait, resume, complete,
    fail, request_cancellation, cancel, time_out`.
  - Guarded node operations: `mark_node_ready, start_node, skip_node,
    complete_node, fail_node, retry_node` (the last returns the backoff
    delay).
  - Queries: `is_terminal`, `is_cancellation_requested`, `is_timed_out(now)`,
    `node(node_id)`.
- Constructor invariants (`__post_init__`) reject states that no sequence of
  transitions can reach (see §6).

### Domain: related change (`backend/src/akasha/domain/node.py`)
- The private `_RETRYABLE` was renamed to the public
  `RETRYABLE_ERROR_KINDS`, so `RetryPolicy` reuses the Phase 03
  classification instead of duplicating it. `NodeError.retryable` behaves
  exactly as before.

### Testing (`backend/tests/domain/test_execution.py`, rewritten)
255 collected tests (see §12).

### Documentation
- `docs/architecture.md` §3.6: authoritative execution rules.
- This report.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/execution.py` | Modified (+560/−131; now 676 lines) | Action-keyed state machines, aggregate guards, policies, error/metadata value objects |
| `backend/src/akasha/domain/node.py` | Modified (2 lines) | `_RETRYABLE` → public `RETRYABLE_ERROR_KINDS` for reuse by `RetryPolicy` |
| `backend/tests/domain/test_execution.py` | Modified / rewritten (now 1136 lines) | Old tests imported removed private names and target-keyed semantics; replaced by spec, matrix, scenario, fuzz and regression tests |
| `docs/architecture.md` | Modified (+23 lines, §3.6) | Document the authoritative transition rules for frontend mirroring and later phases |
| `docs/reports/backend/phase-07-execution-state-machine.md` | Created | This report |

- No new directories.
- No migrations.
- No configuration or dependency changes (`pyproject.toml` and `uv.lock`
  are untouched).
- No generated files.

---

## 5. Architecture Changes

### 5.1 Action-keyed transition tables

**Decision:** Transitions are defined per named action as `(legal sources,
target)`. The target-keyed view is derived from these tables and is never
maintained by hand.

**Context:** Phase 02 checked `target in table[current]`. Two actions share
a target but have different legal sources:
- `start`/`resume` → `RUNNING`
- `mark_ready`/`retry` → `READY`

A target-keyed check therefore accepted `start()` from `PAUSED` (bug B1, §10).

**Chosen Approach:** `EXECUTION_ACTIONS` / `NODE_ACTIONS` plus `_apply()`.
`_derive()` computes `EXECUTION_TRANSITIONS` / `NODE_TRANSITIONS` and the
terminal sets.

**Alternatives Considered:**
1. Keep target-keyed tables and add ad-hoc `if` guards in `start()` and
   `mark_ready()`. This scatters the rules again, which violates the
   "state machines are data" decision from Phase 02.
2. Introduce an `Enum` of actions. This adds a type without adding safety;
   the matrix tests already catch any typo in an action name.

**Reason:** A single source of truth where each method's legal sources are
explicit.

**Consequences:**
- The Phase 02 report asked Phase 07 to "extend (not rewrite) the transition
  tables". The table *shape* changed, but the states and the Phase 02
  transitions were all kept; only additions were made (§6).
- `EXECUTION_TRANSITIONS` and `NODE_TRANSITIONS` are now public, so a future
  contract endpoint or the frontend can mirror them.

### 5.2 `Execution` is the aggregate root for `NodeExecution` state

**Decision:** Node transitions are performed through `Execution` methods,
which check execution-level preconditions before delegating to
`NodeExecution`.

**Context:** Several rules span both entities:
- no node starts while the execution is paused or cancelling
- no completion with open nodes
- a terminal execution has only terminal nodes
- no retry after a cancellation request

**Chosen Approach:** `_node_for(node_id, action, allowed_execution_states,
blocked_by_cancellation=)` plus six wrapper methods.

**Alternatives Considered:**
1. A single generic `node_transition(node_id, target)`. It is less explicit,
   and backend AGENTS §18 prefers named transitions.
2. Leaving the cross-entity rules to the Phase 08 runtime. The backend
   AGENTS state that transitions must be enforced by the domain, not by
   callers.

**Reason:** These invariants are domain rules, and they are testable without
a runtime.

**Consequences:**
- The runtime must call `Execution.*_node` and not `NodeExecution` methods
  directly. Python cannot enforce this (limitation L1, §20).

### 5.3 Retry reuses the NodeExecution

**Decision:** A retry is the transition `RUNNING → READY` on the same
NodeExecution. `attempt` counts starts, and `failures` records every failed
attempt.

**Context:** The node state set has no `RETRYING` state, and `FAILED` must
stay terminal.

**Alternatives Considered:** A new NodeExecution per attempt. This would
multiply rows and complicate the rule "one node execution per node", and it
would require an attempt relation that nothing consumes yet.

**Reason:** This is the smallest model that keeps `FAILED` terminal and
preserves the retry history (architecture §53 "retry information").

**Consequences:**
- Persistence (Phase 08) stores `attempt`, `failures` and `retry_at` per
  node.
- Loop iterations (a later phase) may need multiple NodeExecutions per node.
  That is not solved here.

### 5.4 Only `cancel` after a cancellation request

**Decision:** Once `request_cancellation()` has recorded a
`CancellationState`, every execution action except `cancel` is rejected.

**Context:** AGENTS §97: "A cancelled execution must not later report itself
as successfully completed." Cancellation is cooperative, so there is a
window between the request and the final `CANCELLED` state.

**Alternatives Considered:**
1. Allow `FAILED`/`TIMEOUT` during that window. This creates ambiguous final
   states for the same user intent.
2. Allow completion if every node finished anyway. This contradicts the user
   having explicitly cancelled.

**Reason:** This is the simplest rule under which the guarantee is
unconditional.

**Consequences:**
- The runtime must finalize with `cancel()` even if the nodes finished
  successfully in the meantime.
- In-flight nodes may still record `COMPLETED`/`FAILED` truthfully.

### 5.5 Node timeout is an error, not a state

**Decision:** A node timeout produces
`ExecutionError(kind=TIMEOUT, code="NODE_TIMEOUT")`, which goes through
`retry_node` or `fail_node`.

**Context:** The node state list has no `TIMEOUT`. `NodeErrorKind.TIMEOUT`
is retryable per Phase 03.

**Reason:** No new state is needed, and timeouts follow the same retry
policy as other retryable errors.

**Consequences:** The execution-level `TIMEOUT` state remains distinct and is
terminal.

### 5.6 Domain reads `akasha.__version__`

**Decision:** `ExecutionMetadata.engine_version` defaults to
`akasha.__version__`.

**Context:** The domain may only depend on the domain layer, the stdlib and
`akasha.errors`. `akasha/__init__.py` holds only the version constant.

**Reason:** This avoids forcing every caller to pass the version.
`tests/test_architecture.py` still passes, because it checks only `akasha.<layer>`
imports.

**Consequences:** If `akasha/__init__.py` ever gains imports, this import
must be revisited.

---

## 6. Domain / Data Model Changes

### Transition changes relative to Phase 02
Execution — **added** transitions (all Phase 02 transitions kept):
- `CREATED → FAILED`, `QUEUED → FAILED`: failure before start, e.g. the
  snapshot cannot be loaded or queue submission fails.
- `PAUSED → FAILED`, `WAITING → FAILED`: a node still running during the
  pause fails permanently.
- `PAUSED → TIMEOUT`, `WAITING → TIMEOUT`: the deadline includes paused and
  waiting time.

Execution — **tightened:** `start` is legal only from `QUEUED`. Before this
phase it was effectively legal from `PAUSED`/`WAITING` (bug B1).

Node — **added:** `RUNNING → READY` (action `retry`). **Tightened:**
`mark_ready` is legal only from `PENDING`, and `skip` is rejected once
`attempt > 0`.

The final legal sets are written out by hand in `EXPECTED_EXECUTION` /
`EXPECTED_NODE` in the test file and in architecture §3.6.

### Entity field changes
| Entity | Before (Phase 02) | After |
|---|---|---|
| `Execution` | `trigger_type: TriggerType` | moved to `metadata.trigger_type` |
| `Execution` | `node_executions: list[NodeExecution]` | `dict[str, NodeExecution]` keyed by node instance id |
| `Execution` | `error: str \| None` | `error: ExecutionError \| None` |
| `Execution` | — | `metadata`, `retry_policy`, `timeout_policy`, `cancellation` |
| `NodeExecution` | `error: str \| None` field | `failures: list[ExecutionError]` + derived `error` property |
| `NodeExecution` | — | `attempt`, `retry_at` |

### New value objects
`ExecutionError`, `RetryPolicy`, `TimeoutPolicy`, `CancellationState`,
`ExecutionMetadata` (§3).

### Constructor invariants (new)
- `Execution`:
  - every `node_executions` key equals its `NodeExecution.node_id` and exists
    in the pinned graph
  - a terminal execution has only terminal nodes
  - `FAILED`/`TIMEOUT` require an `error`
  - `CANCELLED` requires a `cancellation`
- `NodeExecution`:
  - `attempt ≥ len(failures)` (this also rejects a negative `attempt`)
  - `FAILED` requires at least one failure

### Naming
The roadmap names `ExecutionState` / `NodeExecutionState`; the Phase 02 names
`ExecutionStatus` / `NodeExecutionStatus` were kept (see §22).

### Compatibility
Execution was never persisted, serialized or exposed. Before this phase,
only `tests/domain/test_execution.py` used the changed fields. There are no
database tables, DTOs or serialization formats to migrate.

---

## 7. API / Contract Changes

No HTTP, WebSocket, event, plugin or SDK contract changes.

Python-level domain contract changes:
- New public constants in `akasha.domain.execution`: `EXECUTION_ACTIONS`,
  `NODE_ACTIONS`, `EXECUTION_TRANSITIONS`, `NODE_TRANSITIONS`,
  `TERMINAL_EXECUTION_STATUSES`, `TERMINAL_NODE_STATUSES`.
- The private `_EXECUTION_TRANSITIONS` / `_NODE_TRANSITIONS` were removed.
  Only the old test file used them.
- `akasha.domain.node.RETRYABLE_ERROR_KINDS` is public (it replaces
  `_RETRYABLE`).
- The signatures of `Execution.create()`, `Execution.fail()` and
  `NodeExecution.fail()` changed (the error is now an `ExecutionError`, and
  metadata and policies are keyword arguments).
- Errors: every rejection raises the existing `InvalidStateTransitionError`
  (`code="INVALID_STATE_TRANSITION"`, `http_status=409`) with structured
  details (`action`, `from`, and where relevant `to`, `node_id`, `attempt`,
  `max_attempts`, `kind`, `retry_at`, `node_ids`). Unknown nodes and
  malformed values raise `DomainValidationError` (422). No new error classes
  were added.

---

## 8. Implementation Details

- **Validate before mutating.** Every mutator computes and validates its
  target before changing any field, so a rejected call leaves the aggregate
  unchanged. The fuzzer verifies this by comparing a full snapshot around
  every rejected operation.
  - `Execution._target(action)` validates without side effects (table check
    plus the cancellation guard).
  - `Execution._finish(action, now)` validates, cancels every non-terminal
    node, then sets `status` and `finished_at`. Node cancellation cannot
    raise here, because every non-terminal node state is a legal source of
    `cancel`.
- **Guards on node operations** (`_node_for`):

  | Operation | Allowed execution states | Blocked by cancellation request |
  |---|---|---|
  | `start_node` | RUNNING | yes |
  | `mark_node_ready`, `skip_node` | RUNNING, PAUSED, WAITING | yes |
  | `retry_node` | RUNNING, PAUSED, WAITING | yes |
  | `complete_node`, `fail_node` | RUNNING, PAUSED, WAITING | no (the in-flight work really happened) |

- **Retry flow:** `retry_node(node_id, error, now)` goes through these steps:
  1. aggregate guard
  2. node table check (the node must be `RUNNING`)
  3. `RetryPolicy.allows_retry(error, attempt=ne.attempt)`
  4. `delay = backoff(ne.attempt)`
  5. `ne.retry(error, retry_at=now + delay)`

  The next `start` is rejected while `now < retry_at`. `start` increments
  `attempt`, resets `started_at` (per-attempt node timeout) and clears
  `retry_at`. If the retry is refused, the caller must call `fail_node`
  explicitly; the domain never converts it automatically.
- **Backoff:** `initial × multiplier^(attempt−1)`, capped at `max_backoff`.
  Float overflow for huge attempt numbers falls back to the cap (tested with
  `attempt=10**9`). There is no jitter; the `ponytail:` comment at
  `execution.py:229` names the upgrade path.
- **Timeout:** `Execution.is_timed_out(now)` is `now ≥ started_at +
  execution_timeout`. `time_out()` does not recheck the clock (§20 L2). It
  records `ExecutionError(code="EXECUTION_TIMEOUT", kind=TIMEOUT)`.
- **Cancellation:** `request_cancellation()` is idempotent (the first
  request wins) and rejected once the execution is terminal. `cancel()` is
  legal from every non-terminal state and records a `CancellationState` if
  none was requested. `is_cancellation_requested` is the value intended for
  `NodeExecutionContext.is_cancelled` in Phase 08. That wiring is not done.
- **Time:** follows the existing `now: datetime | None = None` → `now or
  utcnow()` seam from Phase 02. No `Clock` protocol was introduced.
- **Concurrency:** none. The aggregate is a plain in-memory object;
  concurrent-update protection (optimistic locking) is a persistence concern
  for Phase 08.

---

## 9. Findings

### F1 — Target-keyed transition tables accepted `start()` from PAUSED/WAITING
- **Impact:** MEDIUM. No runtime consumed it yet, but `started_at` semantics
  and the `start` vs `resume` meaning were violated.
- **Resolution:** FIXED (§5.1, bug B1).
- **Notes:** This was pre-existing since Phase 02. The old
  `TestExecutionLifecycle._attempt` helper picked `resume()` whenever the
  state was PAUSED/WAITING, so the 9×9 matrix never exercised `start()` from
  those states.

### F2 — The Phase 03 error taxonomy is sufficient for execution errors
- **Impact:** LOW
- **Resolution:** FIXED (reused)
- **Notes:** `NodeErrorKind` serves as `ExecutionError.kind`, which avoids a
  second classification. The only change was making the retryable set
  public.

### F3 — Python cannot enforce aggregate-only mutation
- **Impact:** MEDIUM
- **Resolution:** OPEN (documented, L1)
- **Notes:** `e.node(id).start()` bypasses the execution-level guards; the
  node-level table still applies. This was confirmed by a probe during the
  BREAK step.

### F4 — Wall-clock deadline checks conflict with runtime timers
- **Impact:** LOW
- **Resolution:** DEFERRED (design choice, L2)
- **Notes:** Requiring `time_out(now)` to prove `now ≥ deadline` could
  reject legitimate timeouts under clock skew or monotonic-vs-wall-clock
  drift and strand executions. Kept as a separate query.

### F5 — The mutation script converted line endings to CRLF on Windows
- **Impact:** LOW (tooling/environment)
- **Resolution:** FIXED
- **Notes:** `Path.write_text` on Windows wrote `\r\n` when the script
  restored the originals. Detected via `git ls-files --eol` (`i/lf w/crlf`)
  and normalized back to LF. Both files show `w/lf` afterwards.

---

## 10. Bugs / Problems Found

| # | Problem | Origin | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|---|---|
| B1 | `start()` accepted from PAUSED/WAITING | Pre-existing (Phase 02) | Transitions keyed by target; `start`/`resume` share `RUNNING` | Action-keyed tables (`start` sources = `{QUEUED}`) | `TestBreakRegressions::test_start_cannot_masquerade_as_resume`; matrix cases `(PAUSED/WAITING, start)` | `execution.py` |
| B2 | A node in READY after a retry (already ran) could be SKIPPED | Introduced this phase (by the new retry transition), found in BREAK | `skip` sources include `READY`, which a retried node re-enters | `NodeExecution.skip` rejects `attempt > 0` | `TestBreakRegressions::test_node_awaiting_retry_cannot_be_skipped` | `execution.py` |
| B3 | Constructor built `COMPLETED` with a `PENDING` node (also FAILED/TIMEOUT without error, CANCELLED without cancellation) | Pre-existing gap; the new fields made it more relevant | `__post_init__` checked only ids and status coercion | Constructor invariants | `TestBreakRegressions::test_constructor_rejects_unreachable_states` (4 cases) | `execution.py` |
| B4 | `FAILED` NodeExecution constructible without an error | Introduced this phase (with `failures` list), found in BREAK | No constructor invariant | `FAILED ⇒ failures`, `attempt ≥ len(failures)` | `test_constructor_rejects_failed_node_without_error`, `test_constructor_rejects_more_failures_than_attempts` | `execution.py` |
| T1 | First fuzz invariant failed | Test bug, this phase | The invariant assumed a cancellation request implies a started execution; CREATED/QUEUED are legal | The invariant now asserts "never COMPLETED/FAILED/TIMEOUT after a request" | the fuzzer itself | tests |
| T2 | mypy errors in the new tests (narrowing across method calls, `**kwargs` dicts, `Optional` access) | Test code, this phase | mypy narrows `e.status` after an assert; untyped kwargs dicts | Restored the `_expect()` helper, replaced kwargs dicts with lambdas, explicit `None` check | mypy gate | tests |
| E1 | CRLF line endings after the mutation run | Tooling | Windows `write_text` | Normalized to LF | `git ls-files --eol` | environment |

---

## 11. Fixes Applied

1. **B1**
   - Symptom: `Execution.start()` succeeded while paused.
   - Root cause: the target-keyed table.
   - Implementation: `EXECUTION_ACTIONS`/`NODE_ACTIONS` + `_apply()`.
   - Verification: 81 execution and 49 node matrix cases; the regression
     test.
2. **B2**
   - Symptom: `skip_node` succeeded on a READY node with `attempt == 1`.
   - Root cause: `skip` did not distinguish "never ran" from "ready again
     after a retry".
   - Implementation: guard in `NodeExecution.skip`.
   - Verification: a BREAK probe script, then the regression test.
3. **B3/B4**
   - Symptom: impossible states were constructible.
   - Root cause: no rehydration invariants.
   - Implementation: checks in `Execution.__post_init__` and
     `NodeExecution.__post_init__`.
   - Verification: probe script, 6 regression tests.
4. **T1/T2**
   - Symptom: failing fuzz invariant; 7 mypy errors in tests.
   - Implementation: corrected invariant; typing fixes.
   - Verification: pytest and mypy green.
5. **E1**
   - Symptom: `w/crlf` on two files.
   - Implementation: byte-level `\r\n → \n`.
   - Verification: `git ls-files --eol` shows `w/lf`.

---

## 12. Testing & Verification

### Unit Tests (`backend/tests/domain/test_execution.py`, 255 tests, all pass)
| Group | Count | Content |
|---|---|---|
| `test_execution_transition_spec`, `test_node_transition_spec` | 2 | Derived tables equal the hand-written `EXPECTED_EXECUTION` / `EXPECTED_NODE` |
| `test_terminal_sets`, `test_no_state_is_unreachable`, `test_created_and_pending_are_never_reentered` | 3 | Table sanity |
| `test_execution_action_matrix` | 81 | 9 states × 9 actions; legal → target reached; illegal → `InvalidStateTransitionError`, unchanged state, details `action`/`from`, code |
| `test_node_action_matrix` | 49 | 7 states × 7 actions, same assertions |
| `test_important_illegal_execution_transitions` | 12 | Named attacks (start from PAUSED, resume from QUEUED, time_out from QUEUED, …) |
| `test_important_illegal_node_transitions` | 10 | Named attacks (start from PENDING, mark_ready from RUNNING, retry from FAILED, …) |
| `TestLifecycle` | 17 | Normal lifecycle with nodes and timestamps; graph coverage; unknown/foreign nodes; pause/wait/resume; completion guards; node start guards; in-flight completion while paused; metadata valid/invalid; snapshot pinning |
| `TestFailure` | 12 | Node failure → execution failure + cascade; sibling cancellation; failure before start; error/node consistency; `from_node_error`; invalid `ExecutionError`; defensive copy |
| `TestRetry` | 18 | Transient → retry → success; backoff enforcement; exhaustion; 4 non-retryable kinds; default policy; retry blocked by a cancellation request; retry of a non-running node; backoff curve and cap; overflow; invalid policies |
| `TestTimeout` | 11 | Deadline; not started / unlimited; TIMEOUT state + error + cascade; paused/waiting timeout; node timeout retry and final failure; execution timeout during backoff; invalid policies |
| `TestCancellation` | 17 | Cancel from CREATED/QUEUED; cooperative cancel while running; idempotent request; only-cancel-after-request (5 actions); no resume after request; never completes; no new node work (3 ops); cancel during node execution; cancel during retry backoff; cancellation error not retryable |
| `TestTerminal` | 12 | For each of the 4 terminal states: every action rejected with unchanged state/`finished_at`; `request_cancellation` rejected; all 6 node ops rejected |
| `test_execution_state_machine_fuzz` | 1 | Hypothesis `RuleBasedStateMachine` (see below) |
| `TestBreakRegressions` | 9 | B1–B4 plus a rejected unknown status string |
| `test_graph_without_nodes_can_complete` | 1 | Degenerate empty snapshot |

**Stateful fuzz** (`ExecutionMachine`, Hypothesis 6.168.2): 300 examples ×
40 steps per CI run.
- Rules: time ticks, all 9 execution actions, cancellation requests, and 6
  node operations on 3 nodes with random errors.
- The policy allows 3 attempts.
- Invariants checked after every step:
  1. a rejected operation changes nothing (full snapshot comparison)
  2. terminal ⇒ all nodes terminal and `finished_at` set
  3. COMPLETED ⇒ all nodes COMPLETED/SKIPPED
  4. a cancellation request is sticky and never ends COMPLETED/FAILED/TIMEOUT;
     CANCELLED ⇒ a `cancellation` is recorded
  5. RUNNING nodes exist only in started executions
  6. `attempt ≤ max_attempts` and `len(failures) ≤ attempt`
  7. FAILED/TIMEOUT executions carry an error; a node has `error` iff it is
     FAILED
- Result: pass. One additional local run with 3000 examples × 60 steps
  (not part of CI) also passed.

**Mutation check** (ad-hoc script, run once, not committed): 21 source
mutants covering:
- transition sources
- the cancellation guard
- the terminal cascade
- `retry_at` enforcement
- the `<`/`<=` attempt bound
- retryable-kind check
- node start guard
- completion guard
- request idempotency
- the skip-after-attempt guard
- the backoff cap and exponent
- attempt increment
- failure recording
- the deadline comparison
- the cancellation-kind validation
- the terminal request guard

Result: 21/21 killed, so no survivors.

### Integration Tests
Existing `tests/integration/` suite, 77 tests against a real PostgreSQL via
Testcontainers: pass. This phase added no integration tests, because the
execution model has no persistence yet.

### API Tests
No new API tests (no API changes). The existing API tests inside the
integration suite pass.

### Architecture Tests
`tests/test_architecture.py` (2 tests) passes. The domain still imports no
forbidden layer or framework.

### Security Tests
Unit-level only:
- log-injection-style correlation ids (newline, >128 characters, empty) are
  rejected
- `CANCELLATION` cannot be configured as retryable
- a retry after a cancellation request is rejected
- a malformed error code or node id is rejected

No separate security suite was run.

### Not performed
Component, E2E, accessibility and performance tests do not apply to a pure
domain phase and were not run.

---

## 13. Quality Gates

Final run on the working tree (Windows, Python 3.14.0, uv):

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 61 files already formatted. The first run of `ruff check` flagged 6 E501 lines, fixed by `ruff format` |
| Linter (`uv run ruff check .`) | PASS | All checks passed |
| Type Checker (`uv run mypy .`, strict) | PASS | 59 source files. An intermediate run had 7 test-typing errors (T2), fixed |
| Unit Tests | PASS | 255/255 in `test_execution.py`. An intermediate run had 1 failing fuzz invariant (T1), fixed |
| Integration Tests (`tests/integration`) | PASS | 77 passed (PostgreSQL Testcontainers) |
| API Tests | PASS | Included in the integration suite; unchanged |
| Full suite (`uv run pytest`) | PASS | 493 passed (baseline before the phase: 334), 1 warning |
| E2E Tests | N/A | Not in scope |
| Build (`uv build`) | PASS | sdist and wheel `akasha_engine-0.1.0` built into a temp directory |
| Security Checks | N/A | No dependency audit tool configured; unit-level security cases pass |
| Accessibility | N/A | Backend domain |
| Other: warning | Note | `StarletteDeprecationWarning` from `fastapi/testclient.py` (httpx → httpx2). Pre-existing and unrelated to this phase |

The only `type: ignore` added is at `test_execution.py:1135`. It is
intentional: that test passes an invalid status string on purpose and
documents it inline. No `noqa` was added.

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Implement Execution, NodeExecution, ExecutionState, NodeExecutionState, ExecutionError, RetryPolicy, TimeoutPolicy, CancellationState (scope) | PASS | All in `domain/execution.py`. States are named `ExecutionStatus`/`NodeExecutionStatus` (the Phase 02 names; §22) |
| All 9 execution states and 7 node states present | PASS | Enums; `test_execution_transition_spec` / `test_node_transition_spec` enumerate all of them |
| Explicit state transition validation | PASS | `EXECUTION_ACTIONS`/`NODE_ACTIONS` + `_apply`. In `execution.py`, `status` is assigned only by transition methods and by enum coercion in `__post_init__`, which is itself guarded by the rehydration invariants |
| Test every valid and invalid state transition | PASS | 81 + 49 state × action matrix cases covering every pair; derived tables equal hand-written specs |
| Execution lifecycle is deterministic | PASS | Pure functions of state and injected `now`; no randomness (no jitter); tables derived; fuzz invariants |
| Invalid transitions are rejected | PASS | Matrix, `TestTerminal`, named illegal cases, the fuzz no-mutation invariant, 21/21 mutants killed |
| Session requirement: retry / timeout / cancellation semantics | PASS | `TestRetry`, `TestTimeout`, `TestCancellation` |
| Session requirement: execution metadata, structured execution errors | PASS | `ExecutionMetadata`, `ExecutionError` + tests |
| Session requirement: do not implement the full runtime | PASS | No scheduler, persistence or runtime code; `akasha/runtime/` untouched |

---

## 15. Security Considerations

- **Input validation / log safety:** `ExecutionMetadata.correlation_id` must
  be 1–128 printable characters, because correlation ids will come from
  request headers and end up in logs. `ExecutionError.code` is restricted to
  `^[A-Z][A-Z0-9_]*$`, and ids are validated.
- **Secrets:** `ExecutionError.message` and `details` may carry text from
  node code or providers. No redaction exists yet (Phase 13). Before
  execution errors are persisted, logged or returned via API, they must pass
  through redaction. This is an open item for Phase 08/13.
- **Error disclosure:** `InvalidStateTransitionError` details contain only
  state names, action names, ids, counts and timestamps; no payloads.
- **Cancellation integrity:** a retry cannot revive cancelled work. This is
  enforced by the policy validation and by the aggregate guard.
- Authentication, authorization, SSRF, file access, expressions and plugins:
  not affected by this phase.

---

## 16. Performance Considerations

- `complete()` (the unfinished-node scan) and the terminal cascade are O(n)
  in the node count.
- `Execution.create()` is O(n).
- No measurements were taken, and none were needed for in-memory O(n)
  operations.
- `failures` per node is bounded by `max_attempts`, but `max_attempts` has
  no upper cap yet (resource limits, Phase 24).
- The unit suite for this module runs in about 4 s, dominated by the fuzzer
  (300 × 40 steps).

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §3.6 "Explicit State" | The backend is authoritative and the frontend must mirror its rules | Location of the tables, action-keyed rationale, terminal states, aggregate-root rule, completion/cascade rule, retry, timeout and cancellation semantics |
| `docs/reports/backend/phase-07-execution-state-machine.md` | Phase report | This document |

Should be updated in future phases:
- `docs/architecture.md` §53 "Execution Persistence": map the new fields
  (`attempt`, `failures`, `retry_at`, `cancellation`, policies, metadata) to
  storage in Phase 08.
- `backend/README.md`: not changed. It does not currently describe the
  execution model.

---

## 18. Dependencies / Infrastructure Changes

None.
- No dependency was added, removed or version-changed. Hypothesis 6.168.2
  was already a dev dependency and is now also used for stateful testing.
- No configuration, environment variable, Docker, database or Redis
  changes.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects Phase 07 completion |
|---|---|---|---|
| Persistence of Execution/NodeExecution (+ migration, repository, optimistic locking) | Roadmap puts persistence with the runtime | 08 | No |
| Runtime enforcement: scheduling, sleeping for backoff, watching deadlines, wiring `is_cancellation_requested` into `NodeExecutionContext.is_cancelled` | Runtime scope | 08 | No |
| Backoff jitter (`ponytail:` at `execution.py:229`) | Determinism now; needed only when workers retry in lock-step | 22/23 | No |
| Resolved node/plugin versions in `ExecutionMetadata` | No registry exists | 09 / 17 | No |
| Per-node retry/timeout overrides from node config | Needs runtime/config resolution | 08 / 24 | No |
| Queue timeout (`QUEUED → TIMEOUT`) | Worker/queue scope | 22 | No |
| Upper caps on `max_attempts` and timeouts | Resource limits | 24 | No |
| Execution-level retry ("RetryExecution" = a new Execution, not a transition) | Application use case | later | No |
| Secret redaction of `ExecutionError` text | Credentials/secrets phase | 13 | No |
| Execution events (`ExecutionStarted`, …) | Event system | 11 | No |

---

## 20. Known Limitations

- **L1:** Aggregate guards can be bypassed by calling `NodeExecution`
  mutators directly (`e.node(id).start()`). Only the node-level table
  applies in that case. The docstring states "Mutate only through
  `Execution`".
- **L2:** `time_out()` does not verify that the deadline has passed;
  `is_timed_out(now)` is a separate query.
- **L3:** Timestamps are not checked for monotonicity. For example,
  `complete(now=...)` earlier than `started_at` is accepted.
- **L4:** One NodeExecution per node per execution. Loops or for-each
  constructs that run a node multiple times are not modelled.
- **L5:** `request_cancellation(reason=...)` does not bound the length of
  `reason`. This is expected to be validated at the API boundary.
- **L6:** The rehydration invariants cover only the rules listed in §6. For
  example, a `RUNNING` node inside a `QUEUED` execution would still be
  constructible.
- **L7:** The enum names deviate from the roadmap (`*Status` instead of
  `*State`).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended action | Target |
|---|---|---|---|---|---|
| `ExecutionError` text is not redacted | Secrets from node errors could be persisted, logged or returned once those paths exist | MEDIUM | domain/execution, observability | Redact at persistence/log/API boundaries | 08 (persistence) / 13 |
| Aggregate bypass via direct `NodeExecution` calls (L1) | Runtime bugs could violate cross-entity rules | LOW | runtime | Runtime code review + runtime tests use only `Execution.*` methods | 08 |
| Incomplete rehydration invariants (L6) | Corrupt persisted rows could load | LOW | persistence | Extend the constructor checks when the persistence mapping is written | 08 |

---

## 22. Architectural / Product Decisions

- **Transitions are keyed by action; the target view is derived.** Never
  edit a target-keyed table by hand. See §5.1.
- **`Execution` owns node state changes** (§5.2).
- **Retry reuses the NodeExecution** (`RUNNING → READY`). `failures` is the
  retry history, and `error` is non-null only for `FAILED` nodes (§5.3).
- **Default `RetryPolicy.max_attempts = 1` (no retry).** Retrying side
  effects is not harmless (AGENTS §47), so retries must be opted into.
- **`max_attempts` counts all attempts, including the first.**
- **Backoff is a domain invariant.** `retry_at` is persisted state, and an
  early `start` is rejected, which makes recovery after a restart
  deterministic.
- **After a cancellation request only `cancel` is legal** (§5.4). In-flight
  nodes may still record their true outcome.
- **The execution deadline includes paused and waiting time**, so there are
  no immortal paused runs. `TIMEOUT` is reachable only after start.
- **`fail` is legal from any non-terminal state**, because failures can
  occur before start (snapshot or submission errors).
- **Node timeout is an error kind, not a node state** (§5.5).
- **The Phase 02 enum names were kept.** Renaming them to `*State` would
  either create duplicate concepts (AGENTS §6) or cause churn with no
  semantic gain.
- **`ExecutionError` is a value object, not an exception.** It is persisted
  state; exceptions remain `NodeError` / `InvalidStateTransitionError`.

---

## 23. Repository / Git State

- Branch: `main`. HEAD: `a4184bf backend phase 06 - project application services`.
- Working tree (uncommitted, nothing staged except the intent-to-add
  marker):
  - `M backend/src/akasha/domain/execution.py`
  - `M backend/src/akasha/domain/node.py`
  - `M backend/tests/domain/test_execution.py`
  - `M docs/architecture.md`
  - `A docs/reports/backend/phase-07-execution-state-machine.md` (added
    with `git add -N`, intent-to-add only)
- Line endings of the changed files are LF (verified with
  `git ls-files --eol`).
- No unrelated changes detected. No commit was made, because none was
  requested.
- Temporary artifacts (the mutation script, the build output and the
  collection output in `%TEMP%\opencode`) are outside the repository. The
  mutation script was deleted.

---

## 24. Phase Completion Assessment

### Implemented
Action-keyed execution and node state machines, the `Execution` aggregate
with guarded node operations, `ExecutionError`, `RetryPolicy`,
`TimeoutPolicy`, `CancellationState`, `ExecutionMetadata`, constructor
invariants, and architecture documentation.

### Verified
- 255 unit tests, including exhaustive state × action matrices and the
  stateful fuzzer.
- 21/21 mutants killed.
- Full suite: 493 passed.
- ruff, ruff format, mypy (strict) and `uv build`: all pass.

### Not Implemented
Nothing in the Phase 07 scope. Runtime and persistence were out of scope by
design.

### Deferred
See §19 (persistence, runtime enforcement, jitter, resolved versions, queue
timeout, caps, redaction, events).

### Open
See §21 (redaction of error text, aggregate bypass, incomplete rehydration
invariants). None of these block this phase.

### Blocking Issues
None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase

**Phase 08 — Execution Runtime** can assume:
- `Execution.create(project_id, project_version, metadata=, retry_policy=,
  timeout_policy=, now=)` pins the immutable version and pre-creates one
  PENDING NodeExecution per graph node.
- The legal operation sequence for the runtime:
  1. `enqueue` → `start(now)`
  2. per node: `mark_node_ready` → `start_node(now)` → `complete_node` or
     (`retry_policy.allows_retry(err, attempt=)` ? `retry_node` : `fail_node`)
  3. `complete` or `fail(error)`
  4. `pause`/`wait`/`resume`
  5. `request_cancellation` → cooperative stop → `cancel`
  6. `is_timed_out(now)` → `time_out`
- Map node failures with `ExecutionError.from_node_error(NodeError,
  node_id=)`, and node timeouts with `ExecutionError.node_timeout(...)`.
- Wait until `node.retry_at` before calling `start_node`; an early call
  raises.
- Use `timeout_policy.node_deadline(node.started_at)`; it is per attempt.
- Feed `execution.is_cancellation_requested` into
  `NodeExecutionContext.is_cancelled`.
- Every rejection is an `InvalidStateTransitionError` (409) with structured
  details. The runtime should treat it as a programming or race error, not
  as a node failure.

**Must not be changed casually:**
- the action tables and the hand-written spec in the tests (they must change
  together)
- the "only cancel after request" rule
- the default `max_attempts = 1`
- `RETRYABLE_ERROR_KINDS`
- the terminal cascade
- the constructor invariants

**Prerequisites / constraints for Phase 08:**
- Persistence must store and restore `attempt`, `failures`, `retry_at`,
  `cancellation`, `metadata` and both policies.
- Rehydration goes through the constructors, which reject unreachable
  states; extend the invariants there if needed (L6).
- Error text must be redacted before logging or persistence once Phase 13
  exists; until then, keep it out of logs.
- Loop semantics will need a decision about multiple NodeExecutions per node
  (L4).
