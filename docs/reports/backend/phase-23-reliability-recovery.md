# Phase 23 — Reliability + Recovery

## 1. Executive Summary

**Supposed to achieve.** Roadmap §27 (Phase 23): "Make execution resilient
to failures." Scope: retry policies, exponential backoff, timeouts,
cancellation, stale execution detection, worker crash recovery, execution
recovery. Error categories: transient, permanent, validation, configuration,
provider, timeout, cancellation. Acceptance criterion: "The system never
silently loses execution state." The session request added idempotency
boundaries, recovery state transitions, and explicit tests for nine failure
scenarios: transient failure, permanent failure, worker crash, process
restart, timeout, cancellation, duplicate execution request, stale
execution, partially persisted state.

**State before this phase.** Discovered while inspecting the code:

- The domain already modelled `RetryPolicy`, `TimeoutPolicy`,
  `CancellationState`, the node `retry` transition (`RUNNING → READY`) and
  `NodeErrorKind`. Much of the phase's vocabulary existed.
- The runtime enforced node and execution timeouts and cooperative
  cancellation. However, `ExecutionRuntime._run` **refused** any execution
  with `retry_policy.max_attempts > 1` (`ConfigurationError`). Retries were
  modelled but never executed.
- An execution whose owning process died stayed `RUNNING` forever. Nothing
  detected it or recovered it. Phase 22 documented this as deferred to
  Phase 23.

**Actually implemented.**

- **Retries:** the runtime now enforces retries with backoff. The backoff is
  persisted before waiting, and cancellation or the deadline can interrupt
  the wait.
- **Owner heartbeat:** a heartbeat on the execution row (migration `0010`,
  database clock). It is a compare-and-swap on the revision, so a running
  owner learns within one heartbeat interval that another writer took its
  run.
- **Stale recovery:** `ExecutionRuntime.recover_stale` detects stale
  executions and claims each one exactly once. It is called from
  `WorkerManager.recover`.
- **New transition:** `Execution.recover` (`RUNNING/PAUSED/WAITING →
  QUEUED`) settles in-flight attempts deterministically.
- **Resume:** a recovered execution is resumed, not restarted.
- **Idempotency boundary:** based on the existing
  `NodeCapabilities.has_side_effects` flag and the retry-policy opt-in.
- **Events and metrics:** three new event types and two new metrics.
- **Settings:** one new setting.
- **Tests:** 25 new integration tests, 6 repository/migration tests,
  1 real-Redis worker end-to-end test, and 18 new domain tests. The spec,
  matrix and fuzz layers were extended.

**Resulting state.**

- For every documented failure scenario, the persisted execution ends in an
  explicit state: `COMPLETED`, `FAILED` (with a `NODE_INTERRUPTED` or
  `NODE_VERSIONS_CHANGED` code), `CANCELLED`, `TIMEOUT`, or `QUEUED` to be
  resumed.
- No path leaves a started execution `RUNNING` without an owner beyond the
  stale threshold, as long as a worker manager's recovery pass is running.

**Acceptance criteria.** The single roadmap criterion is satisfied, with
evidence in §14. Remaining limitations are listed in §20. The most
relevant: there is no configuration surface (API, workflow or engine
default) for retry and timeout policies.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap `docs/roadmap-backend.md` §27:

- Goal: "Make execution resilient to failures."
- Scope: `retry policies`, `exponential backoff`, `timeouts`,
  `cancellation`, `stale execution detection`, `worker crash recovery`,
  `execution recovery`.
- Error categories: `transient`, `permanent`, `validation`,
  `configuration`, `provider`, `timeout`, `cancellation`.
- Tests: simulate `Node failure`, `provider timeout`, `worker crash`,
  `process restart`, `retry exhaustion`, `cancellation`.
- Acceptance: "The system never silently loses execution state."

Relevant rules:

- backend/AGENTS.md §39 (retry needs max attempts, backoff, clear
  transitions, observability; cancellation is never revived).
- §40 (timeouts).
- §41 (cancellation must not be a database flag unknown to the runtime).
- §43 (recovery must not be "restart everything"; it must be clear which
  node ran, which may be re-run, and which side effects may have happened).
- §44 (idempotency).
- Architecture §47, §48, §49, §138, §139, §140.

**Not responsible for:**

- Resource limits and engine-wide default timeouts (Phase 24,
  Performance + Resource Limits).
- API idempotency keys for execution creation (architecture §148; not in
  the Phase 23 roadmap scope).
- A frontend or API surface for configuring retry policies (no roadmap
  item).
- Driving `PAUSED`/`WAITING` from the runtime (no roadmap item).

---

## 3. Scope Implemented

### Domain (`backend/src/akasha/domain/execution.py`, `domain/event.py`)

- **New action `recover`.** Added to `EXECUTION_ACTIONS` as
  `(_E_STARTED, QUEUED)`.
- **New method `Execution.recover(*, repeatable, now)`.** It settles an
  execution whose owner is gone, in this precedence:
  1. A requested cancellation ends it with `cancel()`.
  2. A passed deadline ends it with `time_out()`.
  3. Each node left `RUNNING` gets an `ExecutionError.node_interrupted`
     (`NODE_INTERRUPTED`, kind `TRANSIENT`). The attempt is repeated
     (`READY`, `retry_at = now`) if `retry_policy.allows_retry` holds, or
     if the node id is in `repeatable` and
     `attempt < INTERRUPTED_ATTEMPTS_LIMIT` (3). Otherwise the node is
     failed with `fail_node`.
  4. If any node failed, the execution fails with the first error.
     Otherwise the status becomes `QUEUED` and `execution.recovered` is
     emitted.
- **New constants.** `NODE_INTERRUPTED`, `INTERRUPTED_ATTEMPTS_LIMIT = 3`,
  and `_N_SETTLED`.
- **New property `Execution.is_resumed`.** It is `QUEUED` with
  `started_at` set.
- **Changed `Execution.start`.** A resumed execution keeps `started_at`
  and the recorded `metadata.node_versions`, and emits `execution.resumed`
  instead of `execution.started`.
- **New `Execution.can_retry_node(node_id, error) -> bool`.** A
  non-raising query mirroring `retry_node`'s acceptance rules.
- **Retry scheduling now emits an event.** `retry_node` and recovery share
  `_schedule_retry`, which emits `node.retry_scheduled`. Before this phase
  a retry emitted no event.
- **Changed rehydration invariant.** A `CREATED`/`QUEUED` execution with
  `started_at` set (i.e. recovered) may carry any non-`RUNNING` node
  status. Before, all nodes had to be `PENDING`.
- **New event types.** `EventType.NODE_RETRY_SCHEDULED`,
  `EXECUTION_RECOVERED` and `EXECUTION_RESUMED`, each with a closed
  payload key set in `PAYLOAD_KEYS`.

### Runtime (`backend/src/akasha/runtime/runtime.py`)

- **Retries.** Removed the `max_attempts > 1` refusal. `_record(..., retry:
  bool)` calls `retry_node` when `retry` is true and `can_retry_node`
  agrees; otherwise it calls `fail_node`. `_stop` passes `retry=False`, so
  nothing retries while the run is ending.
- **Backoff.** `_launch` starts only `READY` nodes whose `retry_at` is
  `None` or already due. The new `_retry_wait` returns the seconds until
  the earliest future `retry_at`. `_schedule` keeps looping while nodes
  wait for a backoff, and its `asyncio.wait` timeout is
  `min(next heartbeat, backoff, deadline)`.
- **Heartbeat.** New constructor parameter `heartbeat_seconds` (default
  5.0, validated > 0). `_schedule` calls `StateManager.heartbeat` when an
  interval is due. A failed CAS raises `ExecutionOwnershipLostError`, a
  subclass of `ConcurrentModificationError`; the existing `finally` then
  cancels and awaits the node tasks.
- **Resume in `_run`.**
  - If `execution.is_resumed` and `plan.node_versions` differs from the
    recorded versions, the execution fails with `NODE_VERSIONS_CHANGED`
    (kind `CONFIGURATION`).
  - Otherwise `elapsed = now - started_at` is passed to `_schedule`, and
    the deadline is `loop.time() + limit - elapsed`.
  - Readiness is re-derived: `PENDING` roots are marked `READY`, and
    `_advance` runs from every `COMPLETED`/`SKIPPED` node.
- **Stale recovery.** `recover_stale(*, stale_after, limit=100) ->
  list[Execution]` lists stale ids and skips ids in this runtime's
  `_active`. For each remaining id it calls `StateManager.recover`, which
  runs claim, load, `_settle`, save and commit in one transaction. An
  `AkashaError` on one execution is logged
  (`execution_recovery_failed`) and the pass continues.
- **`_settle`.** Builds `repeatable` from the catalog: node instances whose
  node type is known and has `capabilities.has_side_effects == False`.
- **`StateManager`.** New methods `heartbeat`, `list_stale` and `recover`.
  Post-commit observe/publish moved into `_committed`, which is shared by
  `save` and `recover`.

### Persistence

- `application/persistence.py`: `ExecutionRepository` gained `heartbeat`,
  `list_stale` and `claim_stale`.
- `infrastructure/persistence.py` (`SqlAlchemyExecutionRepository`):
  - `heartbeat`: `UPDATE … SET heartbeat_at = now() WHERE id AND
    revision`; returns whether one row matched.
  - `list_stale`: status in running/paused/waiting and `heartbeat_at <
    now() - stale_after`, ordered by `heartbeat_at, id`, with a limit.
  - `claim_stale`: the same predicate for one id, `FOR UPDATE SKIP LOCKED`.
  - `save`: now sets `heartbeat_at = now()` on every insert and update.
- `infrastructure/db_models.py`: column `heartbeat_at` and index
  `ix_executions_started_heartbeat_at`.
- Migration `backend/migrations/versions/0010_execution_heartbeat.py`.

### Application / Workers / Config

- `application/executions.py`: `ExecutionService.create_execution` takes
  `retry_policy: RetryPolicy | None`.
- `workers.py`:
  - `Worker._runtime` renamed to the public attribute `Worker.runtime`.
  - `WorkerManager` takes `stale_seconds` (default 60.0).
  - `WorkerManager.recover()` now runs `runtime.recover_stale` first and
    enqueues each recovered execution that is `QUEUED`
    (`QueueUnavailableError` is suppressed; see §8). It returns
    `(redelivered, redispatched, stale_recovered)` instead of a 2-tuple.
- `config.py`: `execution_stale_seconds` (default 60.0, `gt=0`), plus a
  `model_validator` requiring `>= 3 × worker_heartbeat_seconds`.
- `bootstrap.py`: passes `heartbeat_seconds=settings.worker_heartbeat_seconds`
  to `ExecutionRuntime` and `stale_seconds=settings.execution_stale_seconds`
  to `WorkerManager`.

### Observability

- `observability/metrics.py`: new counters `akasha_node_retries_total`
  (`node_type`, `error_kind`) and `akasha_executions_recovered_total`.
- `observability/execution.py`:
  - `node.retry_scheduled` increments `node_retries`, logs the error
    fields, and is **not** counted as a finished node execution (new set
    `_NOT_FINISHED`).
  - `execution.recovered` increments `executions_recovered`.
  - Both are logged at WARNING.

### Testing

See §12.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/execution.py` | Modified (+145/−) | `recover` action and method; resume in `start`; `can_retry_node`; `_schedule_retry` with event; `node_interrupted`; rehydration invariant |
| `backend/src/akasha/domain/event.py` | Modified | 3 event types and their payload keys |
| `backend/src/akasha/runtime/runtime.py` | Modified (+239/−) | Retry enforcement, backoff wait, heartbeat, resume, `recover_stale`, `ExecutionOwnershipLostError` |
| `backend/src/akasha/application/persistence.py` | Modified | Repository protocol: `heartbeat`, `list_stale`, `claim_stale` |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | SQL implementations; `save` refreshes the heartbeat |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | `heartbeat_at` column and partial index (mirror of the migration) |
| `backend/migrations/versions/0010_execution_heartbeat.py` | Created | Schema change |
| `backend/src/akasha/application/executions.py` | Modified | `retry_policy` parameter; docstring |
| `backend/src/akasha/workers.py` | Modified | Stale recovery in the recovery pass; public `Worker.runtime`; docstrings |
| `backend/src/akasha/config.py` | Modified | `execution_stale_seconds` and validator |
| `backend/src/akasha/bootstrap.py` | Modified | Wiring of heartbeat and stale threshold |
| `backend/src/akasha/observability/metrics.py` | Modified | 2 counters |
| `backend/src/akasha/observability/execution.py` | Modified | Observer handling of the new events |
| `backend/tests/integration/test_reliability.py` | Created (760 lines) | 25 failure-injection scenarios on real PostgreSQL |
| `backend/tests/integration/test_execution_persistence.py` | Modified (+246) | 6 heartbeat/stale repository tests and the 0009→0010 upgrade test |
| `backend/tests/integration/test_workers.py` | Modified | Real-Redis worker-crash end-to-end test; 3-tuple `recover()` assertions |
| `backend/tests/integration/test_runtime.py` | Modified | Removed `test_configured_retry_policy_is_refused_not_ignored` and an unused import |
| `backend/tests/integration/test_persistence.py` | Modified | Migration head `_HEAD = "0010_execution_heartbeat"` |
| `backend/tests/domain/test_execution.py` | Modified (+218) | Spec, matrix and fuzz extended with `recover`; `TestRecovery`; rehydration invariant in the fuzz |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeExecutions` implements the three new protocol methods (mypy) |
| `backend/tests/test_config.py` | Modified | Stale-threshold validator test |
| `docs/architecture.md` | Modified (+76) | §3.6, §11, §19 (events), §72, metrics, §138 |
| `docs/sdk.md` | Modified | Retries, recovery and idempotency for node authors |
| `backend/README.md` | Modified | `AKASHA_EXECUTION_STALE_SECONDS` |
| `docs/reports/backend/phase-23-reliability-recovery.md` | Created | This report |

No new directories. No generated files. No dependency or lockfile changes
(`pyproject.toml` and `uv.lock` are unchanged).

---

## 5. Architecture Changes

### 5.1 Recovery requeues instead of resuming in place

**Decision.** A stale execution is settled and moved `RUNNING → QUEUED`.
The normal pipeline (dispatch, then a worker's `runtime.run`) then resumes
it.

**Context.** Something must take ownership of an orphaned execution and
continue it. Several workers may notice it at the same time.

**Chosen Approach.** The new `recover` transition. `run` accepts QUEUED
executions that started before (`is_resumed`) and resumes them.

**Alternatives Considered.**

- The recovering process runs the execution directly.
- A new status such as `RECOVERING`.

**Reason.** Requeueing reuses Phase 22's machinery unchanged: the job
duplicate guard, "run only QUEUED", CAS saves, lost-job re-dispatch
(`dispatch_queued`), and worker concurrency limits. Duplicate resume
requests are therefore refused by the same mechanism as duplicate
deliveries. No new status is needed.

**Consequences.**

- `QUEUED` now covers two cases: never started, and recovered. They are
  distinguished by `started_at`.
- The `execution.resumed` event marks the second start.
- Consumers must not assume a QUEUED execution has no progressed nodes.

### 5.2 Liveness is an execution-row heartbeat on the database clock

**Decision.** `executions.heartbeat_at` is written with `now()` by every
save and by the owner's periodic heartbeat. Staleness is judged with
`now()` as well.

**Context.** A stale execution has to be detected. Workers already have
Redis registrations with a TTL (Phase 22), but CLI in-process runs
(`akasha workflow run`) have no worker registration, and Redis is optional.

**Chosen Approach.** A database column, a CAS heartbeat, and a stale query
that compares against the database clock.

**Alternatives Considered.**

- Derive liveness from `WorkerInfo.active_executions` in Redis. Rejected:
  it does not cover CLI runs, it races between a job starting and the next
  heartbeat, and it depends on Redis.
- Application clocks, following the `list_queued` pattern. Rejected: stale
  detection across hosts would depend on their clocks agreeing.

**Reason.** The database is the single source of truth for execution
state, and one clock avoids skew between hosts.

**Consequences.**

- Tests simulate time passing by moving `heartbeat_at` back in SQL.
- The injected `Clock` does not govern staleness; it still governs the
  domain timestamps and deadlines.

### 5.3 The heartbeat is a CAS; losing it stops the owner

**Decision.** The heartbeat succeeds only if the row's revision still
matches the owner's in-memory revision. It does not bump the revision.

**Context.** Two problems needed solving. A recoverer taking over a
live-but-slow owner must not leave two runs executing nodes. And an
out-of-process cancel previously reached a busy owner only at its next
save, which could be never for a blocked node.

**Chosen Approach.**

- `ExecutionOwnershipLostError` is raised from the scheduler loop, which
  is the only coroutine that owns the aggregate.
- Recovery claims with `FOR UPDATE SKIP LOCKED` and re-checks staleness, so
  a racing owner heartbeat blocks on the row lock and then fails its WHERE
  on the new revision (tested).

**Alternatives Considered.**

- A separate heartbeat task. Rejected: a second coroutine touching
  ownership state.
- Merging a cancel request into the owner's aggregate. Rejected: unsafe
  when the owner already applied a terminal transition in memory.

**Reason.** A single writer per aggregate is preserved (see the module
docstring in `runtime.py`).

**Consequences.** Node code can outlive an out-of-process `CANCELLED` or a
takeover by up to one heartbeat interval (§20).

### 5.4 Idempotency boundary for interrupted attempts

**Decision.** An interrupted attempt is repeated only in two cases:

- The execution's retry policy allows it. The user opted into repeats,
  including side effects.
- The node type declares `has_side_effects=False` (the default in
  `NodeCapabilities`), bounded to 3 attempts in total.

Otherwise the node and the execution fail with `NODE_INTERRUPTED`.

**Context.** backend/AGENTS.md §43 and §47: retrying side effects is not
harmless, and a crash is not a clean failure without recovery logic.

**Alternatives Considered.**

- Always fail interrupted executions. Rejected: too harsh for pure nodes.
- Always re-run. Rejected: duplicates emails and HTTP POSTs.
- Only the retry policy decides. Rejected: it would leave the existing
  `has_side_effects` capability meaningless.

**Consequences.**

- Built-in and plugin nodes with external effects must declare
  `has_side_effects=True`. §9 F7 checks which built-in nodes already do.
- `node_execution_id` is stable across all attempts and is documented as
  the idempotency key.

### 5.5 Resume refuses node-version drift

**Decision.** Resuming with different resolved node versions fails the
execution (`NODE_VERSIONS_CHANGED`).

**Reason.** Finished nodes ran on the recorded versions. Mixing
implementations would break reproducibility (AGENTS §73).

---

## 6. Domain / Data Model Changes

- **Execution state machine.** New action `recover`
  (`RUNNING|PAUSED|WAITING → QUEUED`). The derived
  `EXECUTION_TRANSITIONS` gains `QUEUED` as a target from the three started
  states. The node state machine is unchanged.
- **Rehydration invariant (`Execution.__post_init__`).**
  - `CREATED`/`QUEUED` with `started_at is None`: all nodes `PENDING`
    (unchanged).
  - `CREATED`/`QUEUED` with `started_at` set: no node may be `RUNNING`
    (new).
  - The error message changed from "unstarted execution with progressed
    nodes" to "queued execution with progressed nodes".
- **Value objects.** `ExecutionError.node_interrupted(node_id=...)`
  factory. Constants `NODE_INTERRUPTED` and `INTERRUPTED_ATTEMPTS_LIMIT`.
- **Database.**
  - `executions.heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
  - Partial index `ix_executions_started_heartbeat_at (heartbeat_at) WHERE
    status IN ('running','paused','waiting')`.
  - The migration fills existing rows with the migration time, using
    PostgreSQL's non-rewriting default path for a stable default.
  - Downgrade drops the index and the column.
- **Compatibility.**
  - Rows written before 0010 are loadable and recoverable (tested by
    `test_upgrade_from_0009_makes_orphaned_running_rows_recoverable`).
  - An older engine process still running against the 0010 schema would
    not heartbeat. Its runs would be treated as stale after the threshold
    and taken over, and the old process's next save would lose its CAS.
  - The `retry_policy` JSON document format is unchanged.

---

## 7. API / Contract Changes

- **HTTP endpoints.** None added, changed or removed. `ExecutionDetail` is
  unchanged. A recovered execution is simply reported as `queued` again.
- **Event contract (persisted events, `EventPublisher`, events API).**
  Three new event types. `EVENT_SCHEMA_VERSION` stays 1, because adding a
  type does not require a bump (`domain/event.py`).

  | Event | Scope | Payload | Emitted by |
  |---|---|---|---|
  | `node.retry_scheduled` | node | `attempt` (the failed one), `error_code`, `error_kind`, `backoff_ms` | `Execution.retry_node`, `Execution.recover` |
  | `execution.recovered` | execution | `interrupted_nodes` (int) | `Execution.recover` when requeued |
  | `execution.resumed` | execution | none | `Execution.start` on a recovered execution |

  Compatibility: consumers that switch over event types must tolerate
  unknown types (AGENTS §81). A resumed execution emits no second
  `execution.started`.
- **Application contracts.**
  - `ExecutionService.create_execution(..., retry_policy=None)` is
    additive.
  - `ExecutionRepository` has 3 new required methods, a breaking change
    for any implementation outside the repo; the only one inside the repo
    was the test fake.
  - `ExecutionRuntime(..., heartbeat_seconds=5.0)` is additive.
  - `ExecutionRuntime.recover_stale` is new.
  - `ExecutionOwnershipLostError` (code `EXECUTION_OWNERSHIP_LOST`,
    subclass of `ConcurrentModificationError`) is new.
  - `WorkerManager(..., stale_seconds=60.0)` is additive.
  - **Breaking:** `WorkerManager.recover()` now returns a 3-tuple.
  - `Worker._runtime` became `Worker.runtime`.
- **Behavioral contract change.** An execution with `max_attempts > 1` is
  now executed with retries. Before, it was refused with
  `ConfigurationError`.
- **SDK contract.** No code change. Documented semantics:
  - `has_side_effects` now affects recovery.
  - `node_execution_id` is stable across attempts.
  - `runtime_metadata["attempt"]` is 1-based.
- **Metrics.** Two new series (§3).

---

## 8. Implementation Details

- **Single owner loop (unchanged principle).** `_schedule` remains the
  only coroutine that mutates the aggregate. Heartbeats run inside it, not
  in a separate task, so the heartbeat reads a revision that nothing can
  change concurrently.
- **Loop order per iteration.**
  1. Verdict: cancel, then deadline, then failure.
  2. Heartbeat, if due.
  3. `_launch` due `READY` nodes; if nothing was launched and there are
     unsaved outcomes, save.
  4. Compute the backoff wait.
  5. Exit if nothing is running and no backoff is pending.
  6. `asyncio.wait` for node tasks or the cancel signal, with a timeout of
     `min(next heartbeat, backoff, deadline)`.
  7. Record the outcomes (with retry).

  A retry decision is therefore saved (by the "dirty" save) before the
  backoff wait starts (tested:
  `test_retry_state_is_persisted_before_the_backoff_wait`).
- **Slot-blocked vs. backoff-blocked READY nodes.** `_retry_wait` counts
  only nodes whose `retry_at` is in the future. Counting slot-blocked nodes
  would produce a zero timeout and a busy loop.
- **Backoff timing** uses the injected clock (`retry_at - clock()`) for the
  wait, and `loop.time()` for the deadline and heartbeat. Consequence: with
  a fake clock that does not advance, a non-zero backoff still waits in
  real time for `retry_at - clock()`. Tests use a zero backoff with the
  tick clock, and a real clock where backoff timing is asserted.
- **Resume deadline.** `elapsed` is computed once from the injected clock
  (`now - started_at`) and subtracted from the loop-time deadline. It
  applies only to resumed runs. Fresh runs are unchanged, which keeps the
  existing tick-clock timeout tests valid.
- **Recovery transaction.** `StateManager.recover` runs `claim_stale`
  (`FOR UPDATE SKIP LOCKED` + stale re-check), then
  `load_execution` (`FOR SHARE` on the already-locked row), then
  `Execution.recover`, then `save_execution` (CAS + events), then
  `commit`. Events are published after the commit.
- **Recovery pass order (`WorkerManager.recover`).**
  1. Stale recovery. Enqueuing a recovered execution may be refused as a
     duplicate while the dead worker still holds the job.
  2. `queue.recover()`, which redelivers that held job.
  3. `dispatch_queued`.

  A job lost at any point is re-dispatched by step 3 once the execution is
  older than `recovery_seconds`. It is always older, because `created_at`
  predates the crash. `QueueUnavailableError` on the enqueue in step 1 is
  suppressed for exactly that reason (commented in the code).
- **Own runs are never recovered.** `recover_stale` skips ids in the
  runtime's `_active` map, which covers a late heartbeat of a live local
  run.
- **Error handling.** A heartbeat failure caused by an infrastructure
  error (database down) propagates out of `run`, like a failed save. The
  worker logs `job_failed`, and the execution goes stale and is recovered
  later.
- **Configuration.** The heartbeat interval shares
  `AKASHA_WORKER_HEARTBEAT_SECONDS` with the Redis worker registration.
  The stale threshold `AKASHA_EXECUTION_STALE_SECONDS` is validated at
  startup.
- **Security-sensitive behavior.** The `NODE_INTERRUPTED` message is fixed
  text. The new events carry scalar payloads only. Recovery logs ids and
  error codes, never payloads.

---

## 9. Findings

### F1 — Retries were modelled but never executed
**Impact:** HIGH. **Resolution:** FIXED.
`ExecutionRuntime._run` rejected `max_attempts > 1`, and a test
(`test_configured_retry_policy_is_refused_not_ignored`) pinned that
behavior. The domain retry transition and `RetryPolicy.backoff` were
already unit-tested. This phase replaced the refusal with enforcement and
removed that test.

### F2 — No configuration surface exists for retry or timeout policies
**Impact:** MEDIUM. **Resolution:** DEFERRED / OPEN.
No production caller passes `timeout_policy` (a pre-existing gap), and
there was no `retry_policy` parameter at all. This phase added the
parameter to `ExecutionService.create_execution`. However, API, trigger,
workflow and engine-default configuration still do not exist. In a running
engine, every execution therefore uses the defaults: no retries and no
timeouts.

### F3 — "provider" is not a distinct error kind
**Impact:** LOW. **Resolution:** No change (documented).
The roadmap lists `provider` as an error category. `NodeErrorKind` has no
`PROVIDER` member. Provider errors are mapped to existing kinds in
`nodes/llm.py` (`_ERROR_KINDS`), a Phase 14 decision:

- `RATE_LIMIT`/`UNAVAILABLE` → `TRANSIENT`
- `TIMEOUT` → `TIMEOUT`
- `CONFIGURATION`/`AUTHENTICATION` → `CONFIGURATION`
- other → `PERMANENT`

The provider classification is preserved in `ProviderErrorKind` and the
error code. Retry decisions derive from the mapped kind, which satisfies
the intent of "do not retry permanent provider errors".

### F4 — Out-of-process cancel did not reach a blocked owner
**Impact:** MEDIUM. **Resolution:** FIXED (bounded).
Before this phase, `runtime.cancel` for an execution owned by another
process wrote `CANCELLED` directly. The owner noticed only at its next CAS
save, so a node blocked forever kept running indefinitely. The heartbeat
CAS now stops the owner within one heartbeat interval (tested:
`test_out_of_process_cancel_reaches_an_idle_owner_via_heartbeat`). The
window of up to one interval remains (§20).

### F5 — Observer and metrics side effects of the new events
**Impact:** LOW. **Resolution:** FIXED before the events shipped.
`observe_execution` treated every node event except `started`/`emitted`
as a terminal node event. `node.retry_scheduled` would have been counted
in `akasha_node_executions_finished`. Re-emitting `execution.started` on
resume would have skewed `akasha_execution_queue_wait_seconds`. Handled by
the `_NOT_FINISHED` set and a separate `execution.resumed` event.

### F6 — The resume deadline would have restarted
**Impact:** MEDIUM. **Resolution:** FIXED (by design).
`_schedule` computed the deadline from `loop.time()` at loop start, which
would give a resumed run a fresh full budget. Fixed by subtracting
`elapsed`. Regression test:
`test_resumed_run_keeps_the_original_deadline`.

### F7 — `has_side_effects` accuracy of built-in nodes not audited
**Impact:** MEDIUM. **Resolution:** OPEN.
Recovery now relies on `NodeCapabilities.has_side_effects`. Inspection
while writing this report found:

- `core.http` (`nodes/http.py:266`) and `core.smtp` (`nodes/smtp.py:87`)
  declare `has_side_effects=True`.
- `core.llm` and `core.web_search` keep the default `False`, so an
  interrupted attempt is re-run. For `core.llm` that means a repeated,
  billable provider call that sends the prompt again.
- Plugin/example nodes were not reviewed.

Whether the LLM behavior is acceptable is a product decision that has not
been made. There is no contract test pinning the flag for effectful
built-ins.

---

## 10. Bugs / Problems Found

### Introduced during this phase and fixed before completion

| Problem | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|
| mypy: `FakeExecutions` could not be instantiated | New required methods on the `ExecutionRepository` protocol | Implemented `heartbeat`/`list_stale`/`claim_stale` in the fake | `mypy .` passes | tests |
| 2 reliability tests failed with `WorkflowValidationFailedError` | Test fixtures used single-node graphs, which publish validation rejects | Changed them to two-node graphs | the tests themselves | tests |
| Reliability tests: "fixture 'h' not found" | The fixture was imported from the wrong place (it lives in each test module) | Local `h` fixture | the tests themselves | tests |
| Lint: E501, RUF059, ASYNC110 (a polling `while` with a condition), I001 | Authoring | Reformatted; the polling loop was restructured with a bounded timeout | `ruff check .` | src/tests |
| mypy: `Execution(**dict[str, object])` in a domain test | Untyped kwargs | Typed helper `rehydrate()` | `mypy .` | tests |
| Weak assertion in `test_owner_heartbeat_waits_for_a_recoverer_and_then_loses` (`sleep(0)` + `not beat.done()`) | Did not prove lock ordering; sleep-based | Removed; the test asserts the outcome (the owner loses) | the test itself | tests |

### Tooling / environment problems

- The first baseline run used `timeout`, which is not available on macOS.
  Re-run without it.
- The second baseline run (`tests/integration/test_cli.py::test_ctrl_c_cancels_the_run_cooperatively`
  failed) ran **while source files were being edited**. The CLI test runs
  a subprocess that imported half-edited code. Verified by stashing the
  changes: the test passes on clean `main`, and it passes in the final
  suite. It was not a product bug.

### Pre-existing bugs discovered

None beyond the findings in §9. F1 and F4 are behavior gaps rather than
defects against a spec.

---

## 11. Fixes Applied

| Symptom | Root cause | Implementation | Verification |
|---|---|---|---|
| Retry policy refused | Runtime guard from Phase 08 | Retry enforcement in `_record`/`_launch`/`_schedule` | `test_transient_failure_is_retried_then_succeeds`, `test_backoff_is_exponential_and_recorded`, `test_node_timeout_is_retried` |
| Execution stuck `RUNNING` after a crash | No liveness or recovery | Heartbeat + `recover_stale` + `Execution.recover` | `test_process_restart_resumes_without_rerunning_finished_nodes`, `test_worker_crash_mid_run_is_recovered_and_resumed` |
| Remote cancel not noticed by a blocked owner | Detection only at CAS save | Heartbeat CAS in the loop | `test_out_of_process_cancel_reaches_an_idle_owner_via_heartbeat` |
| A resumed run would get a fresh deadline | Deadline based on loop time | `elapsed` subtraction | `test_resumed_run_keeps_the_original_deadline` |

---

## 12. Testing & Verification

### Unit tests (domain, `tests/domain/test_execution.py`)

- **Spec:** `EXPECTED_EXECUTION` updated with the `recover` edges.
- **Matrix:** every state × action now includes `recover`.
- **Fuzz:** the Hypothesis `ExecutionMachine` gained a `recover` op (with
  one side-effect-free node) and an invariant `state_survives_rehydration`
  (every reachable state passes the constructor's validation).
- **`TestRecovery`** (18 collected cases, one parametrized over 6 states)
  covers:
  - repeatable node requeued, with event order and payloads
  - side-effect node fails
  - retry-policy opt-in
  - policy without `TRANSIENT` does not repeat
  - interruption limit of 3
  - cancellation honoured
  - deadline → TIMEOUT
  - nothing in flight → requeue
  - only started states recoverable
  - resume keeps `started_at` and versions and emits `execution.resumed`
  - rehydration valid vs. a `RUNNING` node in QUEUED rejected
  - `can_retry_node` truth table
- **Result:** `tests/domain` 602 passed at the time of the domain run.

### Integration tests (real PostgreSQL 17.2, Testcontainers)

`tests/integration/test_reliability.py` has 25 tests. They inject a
failure, recover, and assert on persisted state and on the `Probe` record
of what node code actually ran.

- **Retry:**
  - transient failure then success
  - exhaustion after 3 attempts
  - permanent failure not retried
  - default policy not retried
  - exponential backoff (`backoff_ms == [20, 40]`, wall time ≥ 60 ms)
  - node timeout retried
  - retry state persisted before the wait
  - `node_execution_id` stable across attempts
  - retry metric incremented
- **Cancellation / timeout:**
  - cancel during a 5-minute backoff returns at once, and the node is
    never re-run
  - execution timeout during backoff
  - a `CANCELLATION`-kind node error is final
  - a stale execution with a cancel request ends `CANCELLED`
  - a stale execution past its deadline ends `TIMEOUT`
  - a resumed run keeps the original deadline
- **Crash / restart:**
  - restart resumes (A once, B twice, C once; `execution.started` once,
    `recovered → resumed` order)
  - interrupted side effect fails with `NODE_INTERRUPTED`
  - retry policy opts a side effect into a repeat
  - live owner never recovered
  - owner that lost its run stops (`ExecutionOwnershipLostError`, node
    cleanup saw `is_cancelled`)
  - out-of-process cancel reaches an idle owner
  - resume refuses changed node versions
- **Duplicates:**
  - 4 concurrent recoverers → exactly 1 takeover
  - duplicate resume requests → 1 run, 1 refused
  - recovering a finished execution is a no-op
- **Partially persisted:**
  - torn progress (A completed, C left `PENDING`) is re-derived on resume
  - crash right after `RUNNING` was persisted: settled as interrupted;
    node code ran once in all

`tests/integration/test_execution_persistence.py` adds:

- heartbeat CAS on the revision
- every save refreshes the heartbeat
- `list_stale` filters started rows, stalest first, with a limit
- `claim_stale` skips fresh rows and rows locked by another recoverer
- an owner heartbeat waits for a recoverer and then loses
- migration upgrade 0009 → 0010 with an orphaned `RUNNING` row:
  recovered and resumed, the finished node not re-run

### Infrastructure tests (PostgreSQL + Redis 8.2.1)

- `test_worker_crash_mid_run_is_recovered_and_resumed`: real Redis queue,
  two `Worker`s, and a `WorkerManager.recover()` pass returning
  `(1, 0, 1)`. The survivor completes the run, the gate ran twice, and the
  queue is empty afterwards.
- Migration round trip (`test_migrations_upgrade_downgrade_upgrade_on_empty_database`)
  with head `0010`.

### Flakiness check

The reliability and heartbeat/stale persistence tests (31 selected) were
repeated 5 times: all green each time.

### Security tests

No new security-specific tests. The new event payloads are enforced
scalar-only by the existing `Event` validation (`tests/domain/test_event.py`
still passes).

### Not performed

- E2E and API tests beyond the existing suite (no HTTP changes).
- Performance tests.
- Accessibility tests: not applicable.

---

## 13. Quality Gates

Final run after all changes (commands run in `backend/`):

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 182 files already formatted |
| Linter (`uv run ruff check .`) | PASS | Intermediate E501/RUF059/ASYNC110/I001 findings fixed (§10) |
| Type Checker (`uv run mypy .`) | PASS | 178 source files. An intermediate failure (`FakeExecutions` abstract) was fixed |
| Unit Tests | PASS | Part of the full suite |
| Integration Tests (PostgreSQL/Redis Testcontainers) | PASS | Part of the full suite |
| Full suite (`uv run pytest`) | PASS | 1737 passed, 5 skipped |
| API Tests | PASS | Existing API tests in the full suite; no new ones |
| E2E Tests | PASS | Existing backend journey tests in the suite; no new ones |
| Build | N/A | No backend build step; the migration applies on an empty and on a 0009 database |
| Security Checks | N/A | No dedicated security scan run this phase |
| Accessibility | N/A | Backend only |

Skipped: 5 tests in `tests/external/test_openrouter_smoke.py`. They are
opt-in external smoke tests (`AKASHA_SMOKE_OPENROUTER_API_KEY` not set)
and are skipped by design.

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| The system never silently loses execution state | PASS | A crashed run leaves `RUNNING` + a frozen heartbeat (`crashed_mid_b` asserts `RUNNING`). Recovery turns it into an explicit state: resumed QUEUED → COMPLETED (`test_process_restart_…`, `test_worker_crash_mid_run_is_recovered_and_resumed`), FAILED `NODE_INTERRUPTED` (`test_interrupted_side_effect_…`), CANCELLED, or TIMEOUT. Retry decisions are persisted before waiting (`test_retry_state_is_persisted_before_the_backoff_wait`). Torn/partial states are resumed (`test_torn_progress_…`, `test_crash_right_after_running_was_persisted`). Pre-0010 orphans are recovered (`test_upgrade_from_0009_…`). Precondition: at least one worker manager (recovery pass) is running; without Redis/workers nothing runs recovery automatically (§20) |

Roadmap scope and test items, individually:

| Item | Status | Evidence |
|---|---|---|
| Retry policies | PASS | `_record` + `can_retry_node`; retry tests |
| Exponential backoff | PASS | `test_backoff_is_exponential_and_recorded` |
| Timeouts | PASS | Node/execution timeouts pre-existing. New: timeout retry, timeout during backoff, deadline across resume, deadline at recovery |
| Cancellation | PASS | During backoff, remote via heartbeat, requested-before-crash |
| Stale execution detection | PASS | Repository and runtime stale tests |
| Worker crash recovery | PASS | Real-Redis worker e2e test |
| Execution recovery | PASS | Resume tests |
| Error categories transient/permanent/validation/configuration/timeout/cancellation | PASS | `NodeErrorKind`; retry only for `RetryPolicy.retryable_kinds` (default transient + timeout); `CANCELLATION` rejected from retryable kinds |
| Error category "provider" | PARTIAL | No separate kind. Provider errors map onto the kinds above (§9 F3). Not blocking: the retry semantics are defined for every provider error |
| Test: Node failure | PASS | Permanent/transient tests |
| Test: provider timeout | PARTIAL | `ProviderErrorKind.TIMEOUT → NodeErrorKind.TIMEOUT` is tested in `tests/nodes/test_llm_node.py` (Phase 14), and TIMEOUT-kind retry is tested generically (`test_node_timeout_is_retried`). There is no test running `core.llm` through the retry path. Not blocking |
| Test: worker crash | PASS | See above |
| Test: process restart | PASS | `test_process_restart_…`, migration upgrade test |
| Test: retry exhaustion | PASS | `test_retries_exhausted_fail_the_execution` |
| Test: cancellation | PASS | See above |
| Session: duplicate execution request | PASS | Concurrent recoverers, duplicate resume, plus the Phase 22 duplicate-delivery tests |
| Session: idempotency boundaries | PASS (documented + tested) | §5.4; `node_execution_id` stability asserted. The `has_side_effects` accuracy of built-ins is not audited (§9 F7) |

---

## 15. Security Considerations

- **Event payloads.** The new events carry only ints and fixed codes.
  Closed payload key sets are enforced by `Event.__post_init__`. No node
  data or free text is included.
- **Error disclosure.** The `NODE_INTERRUPTED` and `NODE_VERSIONS_CHANGED`
  messages are fixed strings. The `execution_recovery_failed` log line
  contains the execution id and error code only.
- **Configuration.** The new setting holds no secret. A misconfigured stale
  threshold (below 3 heartbeats) fails at startup. Otherwise, live runs
  could be taken over.
- **Duplicate side effects.** This is a safety-relevant behavior: external
  effects are never repeated after a crash without an opt-in (§5.4). Its
  correctness depends on node authors declaring `has_side_effects` (F7).
- **Other areas.** No changes to authentication, authorization,
  credentials, SSRF, file handling, plugins or expressions.

---

## 16. Performance Considerations

- **Heartbeat writes.** One single-row `UPDATE` per running execution per
  `AKASHA_WORKER_HEARTBEAT_SECONDS` (default 5 s). Not measured.
- **Loop wake-ups.** The run loop now wakes at least once per heartbeat
  interval even when idle. This is negligible.
- **Stale scan.** Uses the partial index on started rows, so its cost
  scales with the number of running executions, not with history size.
  `limit=100` per pass. Runs every `AKASHA_QUEUE_RECOVERY_SECONDS` in
  every worker process.
- **Save cost.** `save` writes one more column (`heartbeat_at`). The
  existing O(nodes) node-row upsert per save is unchanged (existing
  `ponytail:` note, Phase 24).
- **Measurements.** None beyond the suite runtimes (full suite about 150 s,
  reliability file about 9 s).

---

## 17. Documentation Changes

| File | What was documented |
|---|---|
| `docs/architecture.md` §3.6 | `recover` transition; meaning of QUEUED with `started_at`; resume semantics |
| `docs/architecture.md` §11 (runtime notes) | Replaced "recovery is Phase 23 / retries refused" with the retry and crash behavior |
| `docs/architecture.md` §19 (events) | `node.retry_scheduled`, `execution.recovered`, `execution.resumed` and their payloads |
| `docs/architecture.md` §72 | Worker failure/crash notes now point to stale recovery |
| `docs/architecture.md` metrics section | New counters; removed "retries not emitted" |
| `docs/architecture.md` §138 | Full implementation description: liveness, stale claim, settling rules, resume, idempotency boundary, remote-cancel window |
| `docs/sdk.md` | `attempt` is 1-based; new subsection "Retries, recovery and idempotency" |
| `backend/README.md` | `AKASHA_EXECUTION_STALE_SECONDS` |

Should be updated in a future phase:

- An ADR in `/docs/adr/` for the recovery model (§5.1–5.4). None was
  created; the decisions are recorded in architecture §138 and in this
  report.
- Frontend documentation on handling the new event types and
  QUEUED-after-RUNNING, when the frontend consumes events.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies.** None added, removed or upgraded.
- **Database.** Migration `0010_execution_heartbeat` (column + partial
  index).
- **Environment variable.** `AKASHA_EXECUTION_STALE_SECONDS` (default
  60.0).
- **Redis.** No key layout change.
- **Docker / test infrastructure.** No changes. Existing Testcontainers
  images: `postgres:17.2`, `redis:8.2.1-alpine`.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects Phase 23 completion |
|---|---|---|---|
| Retry/timeout configuration surface (API request fields, trigger/workflow settings, engine defaults) | Not in Phase 23 scope; needs product/API design | Phase 24 (limits/defaults) or a workflow-settings phase | No; enforcement exists and is reachable via `ExecutionService` |
| Default node timeouts (architecture §132) | Resource limits phase | Phase 24 | No |
| API idempotency keys for execution creation (§148) | Not in roadmap Phase 23 scope | Unassigned | No |
| Audit of `has_side_effects` on built-in nodes | Found late (F7); outside the runtime change | Next backend phase touching nodes | No for the mechanism; YES for the correctness of recovery of affected nodes (§21) |
| Backoff jitter | Deterministic backoff kept (existing `ponytail:` note on `RetryPolicy.backoff`) | When lock-step retries are observed | No |
| ADR for the recovery model | Documented in architecture §138 instead | Next documentation pass | No |

---

## 20. Known Limitations

- **Default behavior in a running engine.** Every execution runs with
  `RetryPolicy()` (no retries) and `TimeoutPolicy()` (no limits), because
  no production caller sets them.
- **Recovery needs a worker manager.** Recovery runs only inside
  `WorkerManager`'s periodic pass, i.e. with `AKASHA_REDIS_URL` configured.
  An installation without Redis has no automatic stale recovery.
  `recover_stale` is not exposed through the CLI.
- **Remote cancel and takeover window.** After an out-of-process cancel or
  a takeover, the old owner's node code may run for up to one heartbeat
  interval while the database already says `CANCELLED`/`QUEUED`.
- **A node that swallows `CancelledError`** blocks its run loop, so no
  heartbeat is sent. The run is recovered as stale while its code still
  runs.
- **Live-but-slow owners.** An owner stalled longer than
  `AKASHA_EXECUTION_STALE_SECONDS` (for example an event loop blocked by
  CPU work) is taken over; it stops at its next heartbeat.
- **`PAUSED`/`WAITING` recovery** is tested in the domain only, because no
  runtime path drives those states.
- **Fake-clock backoff waits.** A non-zero backoff waits in real time for
  `retry_at - clock()` (§8). This matters only for tests with fake clocks.
- **Interruption limit.** `INTERRUPTED_ATTEMPTS_LIMIT` (3) is a constant,
  not configurable.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| `has_side_effects` classification (F7): `core.llm`/`core.web_search` are side-effect-free by declaration; plugins not reviewed | An interrupted LLM call is repeated (cost, repeated data transmission) | MEDIUM | nodes, plugins | Decide the LLM classification; add a contract test pinning the flag for effectful built-ins (`core.http`, `core.smtp` already `True`) | Next backend phase touching nodes |
| No configuration surface for retry/timeout policies (F2) | Retries and timeouts are unusable from the API, triggers or CLI | MEDIUM | API, application | Design engine defaults + per-workflow/trigger policy fields | Phase 24 or later |
| No automatic recovery without Redis | Stale executions stay `RUNNING` in Redis-less installations | LOW | operations | Offer a CLI command or a scheduler-driven pass when no queue is configured | Unassigned |

---

## 22. Architectural / Product Decisions

- **Recovered means QUEUED again.** It is not a new status. A QUEUED
  execution with `started_at` is a resume and emits `execution.resumed`,
  never a second `execution.started`.
- **Liveness is judged on the database clock**, never on host clocks.
- **Ownership stays single-writer.** The heartbeat is a revision CAS inside
  the owner loop, and losing it stops the run. Do not move heartbeats into
  a separate task without revisiting that.
- **The recovery claim is `FOR UPDATE SKIP LOCKED` plus a re-check** in the
  same transaction as the save. This is what makes concurrent recoverers
  safe.
- **Interrupted attempts** repeat only on a retry-policy opt-in, or for
  side-effect-free node types (max 3 attempts). Otherwise they fail with
  `NODE_INTERRUPTED`.
- **A resume refuses node-version drift** (`NODE_VERSIONS_CHANGED`) rather
  than mixing implementations.
- **The deadline counts from the first start**, across resumes.
- **Nothing is retried once a run is ending** (cancel, timeout or failure
  verdict).
- **The retry policy stays opt-in** (default `max_attempts=1`).

---

## 23. Repository / Git State

- Branch: `main`. Last commit: `820eac6 backend phase 22 — workers and
  queue architecture`.
- The working tree is uncommitted. 22 modified files (+1142/−106 per
  `git diff --stat`) and 3 untracked files:
  - `backend/migrations/versions/0010_execution_heartbeat.py`
  - `backend/tests/integration/test_reliability.py`
  - `docs/reports/backend/phase-23-reliability-recovery.md`
- Unrelated changes: none detected. Every modified file belongs to this
  phase.
- During implementation, `git stash -u` / `git stash pop` was used once to
  verify a baseline test on clean `main` (§10). The working tree was
  restored completely.
- Nothing was committed.

---

## 24. Phase Completion Assessment

### Implemented
- Retry enforcement with exponential backoff.
- Owner heartbeat (CAS) and ownership-loss stop.
- Stale detection and a claim-once recovery pass.
- The `Execution.recover` transition with deterministic settling rules.
- Resume with deadline continuity and version-drift refusal.
- The idempotency boundary.
- Events, metrics, the setting, and migration `0010`.

### Verified
- Full suite green (1737 passed).
- ruff, ruff format and mypy clean.
- Every requested failure scenario has at least one test against real
  PostgreSQL. The worker crash is also tested against real Redis.
- Stable across 5 repeated runs.

### Not Implemented
- A separate `provider` error kind (mapped instead, F3).
- An end-to-end `core.llm` provider-timeout retry test.

### Deferred
- Policy configuration surface.
- Default node timeouts.
- API idempotency keys.
- `has_side_effects` audit.
- Backoff jitter.
- An ADR for the recovery model.

### Open
- F7 (`has_side_effects` accuracy).
- F2 (no policy configuration).
- No recovery without Redis.

### Blocking Issues
None for Phase 23's acceptance criterion.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

- **Can assume:**
  - Executions survive crashes.
  - Started executions carry a live `heartbeat_at`.
  - Stale ones are recovered by every `WorkerManager` pass.
  - Retries follow `Execution.retry_policy`.
  - A QUEUED execution may be a resume (`is_resumed`).
- **Available APIs:**
  - `ExecutionService.create_execution(retry_policy=, timeout_policy=)`
  - `ExecutionRuntime(heartbeat_seconds=)`
  - `ExecutionRuntime.recover_stale(stale_after=, limit=)`
  - `Execution.recover(repeatable=, now=)`, `Execution.can_retry_node`,
    `Execution.is_resumed`
  - `ExecutionOwnershipLostError`
  - Repository methods `heartbeat`, `list_stale`, `claim_stale`
  - Events `node.retry_scheduled`, `execution.recovered`,
    `execution.resumed`
  - Metrics `akasha_node_retries_total`,
    `akasha_executions_recovered_total`
- **Phase 24 (resource limits) should know:**
  - Engine/workflow defaults for `TimeoutPolicy` and `RetryPolicy` are the
    natural place to close F2.
  - A max-retries limit must stay compatible with
    `INTERRUPTED_ATTEMPTS_LIMIT`.
  - The heartbeat loop and the per-save node-row upsert are the recurring
    database writes to watch.
- **Must not be changed casually:**
  - Single-writer ownership in `_schedule`, with the heartbeat inside the
    loop.
  - The claim-in-the-same-transaction recovery.
  - Staleness on the database clock.
  - "Nothing retries while ending".
  - `recover`'s settling precedence (cancel, then deadline, then
    interrupted nodes).
  - The `retry_at` save before the backoff wait.
- **Prerequisites:** no new ones. Keep
  `AKASHA_EXECUTION_STALE_SECONDS ≥ 3 × AKASHA_WORKER_HEARTBEAT_SECONDS`
  (validated).
