# Phase 22 — Workers + Queue Architecture

## 1. Executive Summary

**Supposed to achieve.** Roadmap §26 (Phase 22): "Separate long-running
execution from API request lifetime." It asks for `ExecutionQueue`,
`Worker`, `WorkerManager`, `Job` and `Heartbeat`, with the flow
`API → Create Execution → Queue → Worker → Runtime`. Redis may back the
first queue. The session request added:

- a queue lifecycle;
- execution dispatch;
- graceful shutdown;
- worker registration and state;
- real-Redis integration tests without sleep-based synchronization.

It also required that workers must not become a second execution
implementation.

**State before this phase.** Discovered while inspecting the code:

- `ExecutionService.create_execution` stored executions as `QUEUED`, but
  nothing ran them.
- Only the CLI's `akasha workflow run` called `ExecutionRuntime.run`, in the
  same process.
- Every execution created through the API (manual execute, trigger invoke,
  webhook, scheduler) stayed `QUEUED` forever.

**Actually implemented.**

- **Contract:** a technology-free queue contract,
  `backend/src/akasha/application/queue.py`.
- **Redis queue:** a reliable-queue implementation,
  `backend/src/akasha/infrastructure/redis_queue.py`.
- **Worker and manager:** `Worker` and `WorkerManager` in
  `backend/src/akasha/workers.py`.
- **Dispatch:** `ExecutionService` now enqueues a job after every commit.
- **Queue recovery:** jobs held by dead workers are redelivered, and
  executions left `QUEUED` are dispatched again.
- **Where workers run:** an embedded worker in the API process, and a new
  `akasha worker` CLI command for dedicated worker processes.
- **Database:** migration `0009` adds a partial index.
- **Metrics:** new `akasha_queue_depth` gauge.
- **Readiness:** new `queue` readiness check.
- **Tests:** 24 new integration tests against real Redis 8.2.1 and
  PostgreSQL 17.2, plus CLI, repository and migration tests.

**Resulting state.**

- An execution created through the API is run by a worker after the HTTP
  response is sent.
- `ExecutionRuntime` is unchanged in behavior. The worker only calls
  `ExecutionRuntime.run` and `ExecutionRuntime.cancel`.
- The acceptance criterion is shown directly by the API-level test
  `test_execution_outlives_the_http_request`.

Stale `RUNNING` executions left by a crashed worker are deliberately out of
scope (roadmap Phase 23) and are documented as a limitation.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 2. Phase Objective

**Roadmap scope:**

- `ExecutionQueue`, `Worker`, `WorkerManager`, `Job`, `Heartbeat`;
- a Redis-backed queue where appropriate;
- tests for enqueue, dequeue, worker execution, worker failure, duplicate
  delivery handling, heartbeat and queue recovery.

**Acceptance criterion:** "Long-running Workflows do not depend on an open
HTTP request."

**Responsible for:**

- dispatching executions out of the request path;
- worker lifecycle: start, heartbeat, graceful stop;
- at-least-once delivery with duplicate protection;
- recovering lost jobs.

**Explicitly NOT responsible for** (roadmap Phase 23, "Reliability +
Recovery"):

- retry policies and exponential backoff;
- stale-execution detection;
- recovering executions left `RUNNING` by a crashed worker;
- cancellation across processes.

Resource limits belong to Phase 24.

## 3. Scope Implemented

### Architecture

- **Queue contract** (`application/queue.py`): the `ExecutionQueue`
  protocol, the `Job` dataclass, `WorkerInfo`, `WorkerState` (`running`,
  `stopping`) and `QueueUnavailableError` (`QUEUE_UNAVAILABLE`, HTTP 503).
  The application layer has no Redis import; the existing architecture test
  enforces this.
- **Workers module** (`akasha/workers.py`): new single-module layer. It
  must not import `fastapi`, `starlette`, `uvicorn`, `sqlalchemy` or
  `redis`, and must not use the internal layers `api` or `infrastructure`
  (`tests/test_architecture.py`).
- **Source of truth:** the PostgreSQL execution row. A Redis job is only an
  ephemeral signal.

### Infrastructure

- `RedisExecutionQueue`, with these operations: `enqueue`, `dequeue`,
  `ack`, `heartbeat`, `deregister`, `recover`, `workers`, `depth`, `ping`,
  `close` and `from_url`.
- `encode_job` / `decode_job` write and read a versioned JSON job payload
  (`{"v": 1, "execution_id": ...}`).
- `SqlAlchemyExecutionRepository.list_queued(created_before, limit)`.
- Partial index `ix_executions_queued_created_at`, in `db_models.py` and in
  migration `0009_queued_executions_index`.

### Application

- `ExecutionService`:
  - takes an optional `queue` constructor argument;
  - `create_execution(..., dispatch=True)` enqueues after the commit;
  - new `dispatch_queued(older_than, limit=500)`;
  - private `_dispatch`, which logs `execution_dispatch_failed` on
    `QueueUnavailableError` instead of failing the request.
- `TriggerService.fire_manual(..., dispatch=True)` and `_create(...)` pass
  `dispatch` through.
- The `ExecutionRepository` protocol gains `list_queued`.

### Workers

- **`Worker`**:
  - runs jobs up to `concurrency` at a time, using poll-bounded dequeue;
  - heartbeat: `beat()` plus a background loop;
  - graceful stop: `request_stop()` and `run()` → `_take_jobs` →
    `_drain` → `deregister`;
  - handles each job in `_process`: runtime call, skip/failure
    classification, ack.
- **`WorkerManager`**:
  - `start()` registers the worker, then starts the worker task and the
    recovery-loop task;
  - `stop()` is idempotent;
  - `recover()` redelivers orphaned jobs, re-dispatches stale `QUEUED`
    executions and samples queue depth;
  - `workers()` lists live registrations.

### Composition / CLI / Config

- **`Container`** (`bootstrap.py`):
  - new fields `queue` and `workers`;
  - `start()` starts the manager when `settings.worker_enabled`;
  - `shutdown()` stops workers and closes the queue *before* disposing the
    database;
  - adds the `queue` readiness check.
- **CLI:**
  - new `akasha worker` command: SIGTERM/SIGINT trigger a graceful stop,
    and it prints `worker <id> running` once registered;
  - `workflow run` now creates its execution with `dispatch=False` and still
    runs it in-process.
- **`Settings`:** `redis_url` (`SecretStr`), `queue_prefix`,
  `worker_enabled`, `worker_concurrency`, `worker_poll_seconds`,
  `worker_heartbeat_seconds`, `worker_shutdown_grace_seconds`,
  `queue_recovery_seconds`.
- **`EngineMetrics`:** new `queue_depth` gauge (`akasha_queue_depth`).

### Testing

- New `tests/integration/test_workers.py` with 24 tests.
- A session-scoped `redis_url` Testcontainer fixture.
- 2 CLI tests.
- 1 repository test.
- The migration head test was updated.

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/application/queue.py` | Created (93 lines) | Queue contract: `Job`, `ExecutionQueue`, `WorkerInfo`, `WorkerState`, `QueueUnavailableError` |
| `backend/src/akasha/infrastructure/redis_queue.py` | Created (232 lines) | Redis reliable-queue implementation |
| `backend/src/akasha/workers.py` | Created (329 lines) | `Worker`, `WorkerManager` |
| `backend/migrations/versions/0009_queued_executions_index.py` | Created | Partial index for the recovery scan |
| `backend/tests/integration/test_workers.py` | Created (715 lines, 24 tests) | Queue/worker integration tests |
| `backend/src/akasha/application/executions.py` | Modified (+52/−4) | Dispatch after commit; `dispatch_queued` |
| `backend/src/akasha/application/persistence.py` | Modified | `ExecutionRepository.list_queued` |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | `list_queued` implementation |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | Partial index declaration; imports `text` |
| `backend/src/akasha/application/triggers.py` | Modified | `dispatch` pass-through on `fire_manual` / `_create` |
| `backend/src/akasha/bootstrap.py` | Modified (+49/−5) | Wires queue, worker, manager, readiness; lifecycle |
| `backend/src/akasha/config.py` | Modified | Worker/queue settings |
| `backend/src/akasha/cli.py` | Modified | `worker` command; `workflow run` uses `dispatch=False` |
| `backend/src/akasha/observability/metrics.py` | Modified | `queue_depth` gauge |
| `backend/src/akasha/api/triggers.py` | Modified (docstring only) | Removed the stale statement "until then a caller must hand the id to ExecutionRuntime.run" |
| `backend/src/akasha/runtime/runtime.py` | Modified (docstrings only) | Removed stale "no queue/worker (Phase 22)" scope notes |
| `backend/src/akasha/runtime/scheduler.py` | Modified (docstring only) | Removed stale "until the worker/queue architecture exists" |
| `backend/pyproject.toml`, `backend/uv.lock` | Modified | `redis~=8.1` (resolved 8.1.0) |
| `backend/tests/integration/conftest.py` | Modified | `redis_url` fixture (`redis:8.2.1-alpine`, `LogMessageWaitStrategy`) |
| `backend/tests/integration/test_cli.py` | Modified (+2 tests) | `akasha worker` configuration error; subprocess SIGTERM test |
| `backend/tests/integration/test_execution_persistence.py` | Modified (+1 test) | `list_queued` semantics |
| `backend/tests/integration/test_persistence.py` | Modified | `_HEAD = "0009_queued_executions_index"` |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeExecutions.list_queued` (the protocol grew a member) |
| `backend/tests/test_architecture.py` | Modified | Import rules for the `workers` layer |
| `backend/README.md` | Modified | Worker/queue env var table, `worker` CLI row, layout |
| `docs/architecture.md` | Modified | §72 implementation notes, §58 CLI, §113 metrics |

No directories were created; `workers.py` is a single module. No generated
files were committed. `uv build` writes to `backend/dist/`, which git does
not track.

## 5. Architecture Changes

### 5.1 PostgreSQL is the truth, Redis holds only signals

**Decision.** A `Job` carries only the execution id. The durable state is
the `QUEUED` execution row.

**Context.** Architecture §24.2 says Redis "must not become the only
durable source for critical execution state". Enqueueing happens after the
database commit, and the two cannot share one transaction.

**Chosen approach.**

- Commit first, then enqueue.
- If the enqueue fails, the request still succeeds and the failure is
  logged.
- `WorkerManager.recover` calls `ExecutionService.dispatch_queued`, which
  enqueues every execution that has stayed `QUEUED` longer than
  `queue_recovery_seconds`.

**Alternatives considered.**

- A transactional outbox table. More moving parts, and the `QUEUED` row
  already serves as the outbox.
- Failing the API request when Redis is down. This would reject work that
  was already committed.

**Reason.** Losing Redis state, or a failed enqueue, can never lose an
execution. Recovery converges without new tables.

**Consequences.**

- Dispatch latency after a Redis outage is up to `queue_recovery_seconds`
  (default 30 s).
- Phase 23 retry/backoff must account for the fact that a `QUEUED`
  execution whose job keeps failing before start is re-dispatched every
  interval (§20).

### 5.2 At-least-once delivery; the runtime's compare-and-swap is the idempotency guard

**Decision.** The queue guarantees at-least-once delivery. Duplicates are
neutralized by existing runtime rules, not by new locking.

**Context.** Queue delivery must be assumed to be at-least-once
(AGENTS §46). A worker whose heartbeat expires while it is still alive
causes redelivery.

**Chosen approach.**

- A duplicate guard in Redis: the `{p}:queue:jobs` hash, checked and set in
  one Lua script. It prevents more than one job per execution while that
  execution is queued or held.
- Any duplicate that still arrives is refused by `ExecutionRuntime.run`:
  - `InvalidStateTransitionError` when the execution is no longer `QUEUED`;
  - `ConcurrentModificationError` from the compare-and-swap save;
  - `ExecutionAlreadyRunningError` for the same runtime instance.
- The worker logs this as `job_skipped` and acknowledges the job.

**Alternatives considered.**

- Redis leases/locks per execution. Redundant with the database
  compare-and-swap, which is authoritative.

**Reason.** It reuses a proven invariant (Phase 08 compare-and-swap) and
keeps workers free of execution semantics.

**Consequences.**

- Node code of one execution never runs twice
  (`test_crash_mid_run_never_runs_node_code_twice`,
  `test_duplicate_delivery_to_two_workers_runs_once`).
- An execution already `RUNNING` in a dead worker is not resumed (Phase 23).

### 5.3 Workers only move jobs

**Decision.** `Worker._process` calls `ExecutionRuntime.run(execution_id)`,
and `_drain` calls `ExecutionRuntime.cancel`. There is no other execution
logic in `workers.py`.

**Reason.** The session requirement: no second execution implementation.

**Consequences.** Execution semantics change only in `runtime/`.

### 5.4 Where the worker runs

**Decision.**

- By default (`AKASHA_WORKER_ENABLED=true`) each API process starts one
  embedded worker.
- `akasha worker` runs dedicated processes.

**Context.** Architecture §71 says "Version 1 may run the worker inside the
same deployment architecture if necessary". A single-container deployment
must work without an extra process.

**Alternatives considered.** Dedicated worker processes only. That would
break the out-of-the-box single-process setup.

**Consequences.** Operators running separate worker processes must set
`AKASHA_WORKER_ENABLED=false` on the API processes to keep all work on the
workers.

### 5.5 The CLI `workflow run` stays in-process

**Decision.** `workflow run` creates its execution with `dispatch=False` and
runs it itself. It keeps its Phase 21 Ctrl-C cancellation behavior.

**Context.** If it enqueued and also ran the execution itself, a worker
would race it for the same run.

**Chosen approach.**

- A `dispatch` flag on `create_execution` and `fire_manual`.
- Queue recovery only re-dispatches executions older than
  `queue_recovery_seconds`, so the in-process run starts long before
  recovery could pick it up.

**Consequences.** If a CLI process dies between creating and starting its
execution, that execution is dispatched to the workers after the recovery
age.

## 6. Domain / Data Model Changes

- **Domain:** no entity, value object or state-machine change.
  `ExecutionStatus` and `NodeExecutionStatus` are unchanged.
- **New application types** in `application/queue.py`:
  - `Job(execution_id: str)`: frozen dataclass;
  - `WorkerState`: StrEnum, `running` / `stopping`;
  - `WorkerInfo`: frozen dataclass with `worker_id`, `hostname`, `pid`,
    `state`, `concurrency`, `started_at`, `heartbeat_at`,
    `active_executions`;
  - `QueueUnavailableError`: `AkashaError`, code `QUEUE_UNAVAILABLE`,
    HTTP 503.
- **Database:** new partial index
  `ix_executions_queued_created_at ON executions (created_at) WHERE status = 'queued'`
  (migration `0009_queued_executions_index`, revises `0008_auth`, has a
  downgrade). There are no table or column changes, and existing rows need
  no backfill.
- **Redis keys**, all under `{prefix}` (default `akasha`):

  | Key | Type | Holds |
  |---|---|---|
  | `{p}:queue:pending` | list | waiting jobs |
  | `{p}:queue:held:{worker_id}` | list | jobs a worker dequeued and has not yet acknowledged |
  | `{p}:queue:jobs` | hash | duplicate guard: execution id → payload |
  | `{p}:workers` | set | registered worker ids |
  | `{p}:worker:{worker_id}` | hash with TTL | a worker's registration |

- **Job payload serialization:** `{"v": 1, "execution_id": "..."}`.
  Payloads with an unknown version or malformed payloads are logged as
  `queue_job_undecodable` and dropped.

## 7. API / Contract Changes

**HTTP API.** No endpoints were added, removed or changed in shape.
`POST /api/v1/workflows/{id}/versions/{v}/execute`, trigger invoke and
webhooks still return the created execution with status `queued` (201).

**Behavior change.** Those executions are now run by a worker; before this
phase they were never run.

**Error codes.** `QUEUE_UNAVAILABLE` (503) is new. No endpoint returns it
today, because the dispatch failure is absorbed and logged.

**Readiness.** `/ready` reports a `queue` check when `AKASHA_REDIS_URL` is
set (`RedisExecutionQueue.ping`). An unreachable Redis makes the service
not ready (`test_container_reports_an_unreachable_queue_not_ready`).

**Internal contracts:**

- `ExecutionRepository.list_queued(*, created_before, limit) -> Sequence[str]`
  is a new protocol member. Every implementation must add it; the test
  double `FakeExecutions` did.
- `ExecutionService.__init__(..., queue=None)`.
- `create_execution(..., dispatch=True)`.
- `dispatch_queued(*, older_than, limit=500) -> int`.
- `TriggerService.fire_manual(..., dispatch=True)`.

All new parameters have backward-compatible defaults.

**CLI.** New `akasha worker` command, with exit code 0 on a graceful stop.
It fails with `CONFIGURATION_ERROR` (exit 1) if `AKASHA_DATABASE_URL` or
`AKASHA_REDIS_URL` is missing.

**Metrics.** New gauge `akasha_queue_depth` (no labels).

No node, plugin, SDK or event contract changed.

## 8. Implementation Details

### Dispatch flow

`create_execution` → commit → publish events → `_dispatch` →
`queue.enqueue(Job(id))`.

`enqueue` runs one Lua script:

```text
HSETNX jobs <id> <payload>, and only if that succeeds, LPUSH pending <payload>
```

It returns `False` for a duplicate.

### Dequeue

`BLMOVE pending held:{wid} RIGHT LEFT <block_seconds>` returns the oldest
job first. The job is always in exactly one list, `pending` or a `held`
list, so a crash between dequeue and processing cannot lose it.

### Ack

One `MULTI`/`EXEC` transaction runs `LREM held:{wid} 1 payload` and
`HDEL jobs id`.

### Worker loop

`_take_jobs`:

- When all slots are busy, it waits on `asyncio.wait([*active, stop],
  FIRST_COMPLETED)`.
- Otherwise it dequeues with a `poll_seconds` block.
- A job dequeued after a stop request is not started; `deregister` hands it
  back.
- When `dequeue` raises `QueueUnavailableError`, it backs off for one poll
  interval; a stop request interrupts the backoff.

### Job handling (`_process`)

| Outcome | Log event | Execution state afterwards |
|---|---|---|
| Runtime completed the run | `job_completed` | whatever the runtime recorded |
| Stale job (see below) | `job_skipped`, with the error code as `reason` | unchanged |
| Any other `Exception` | `job_failed`, with traceback | still `QUEUED` if the run never started (recovery re-dispatches it); left `RUNNING` if it had started |

A stale job is one where the runtime raises
`InvalidStateTransitionError`, `ExecutionNotFoundError`,
`ExecutionAlreadyRunningError` or `ConcurrentModificationError`.

The job is then acknowledged. If the ack itself fails, the job stays held
and is redelivered on deregister or expiry; the redelivery is skipped.
`CancelledError` (worker crash) propagates without an ack.

### Heartbeat

- `beat()` writes the registration hash, sets `PEXPIRE` to
  `3 × heartbeat_seconds`, and runs `SADD workers`.
- A failed beat is logged as `worker_heartbeat_failed` and not raised.
- `WorkerManager.start()` awaits one beat before starting the tasks, so a
  worker is visible as soon as `start()` returns.

### Recovery (`WorkerManager.recover`)

1. `queue.recover()`: for each registered id whose registration key is
   gone, move all of its held jobs back to the serving end of `pending`
   (`LMOVE held pending RIGHT RIGHT`, so redelivered jobs go first), then
   remove the id from the set.
2. `executions.dispatch_queued(older_than=recovery_seconds)`.
3. `metrics.queue_depth.set(depth)`.

The pass logs `queue_recovered` when either count is non-zero. It repeats
every `recovery_seconds`. A failed pass is logged as
`queue_recovery_failed` and retried on the next interval.

### Graceful stop (`Worker.run` after `request_stop`)

1. The loop exits.
2. The worker beats once more, so its registry state shows `stopping`.
3. `_drain`: `asyncio.wait(active, timeout=grace)`. For each run still
   going after the grace period, the worker calls
   `runtime.cancel(id, reason="worker shutdown")`, then awaits the runs.
   An `AkashaError` from cancel (the run already finished) is logged as
   `worker_shutdown_cancel_skipped`.
4. `queue.deregister`: hands back held jobs, deletes the registration.
5. Logs `worker_stopped`.

### Crash semantics

If the `Worker.run` task is cancelled (the `finally` block), active run
tasks are cancelled and nothing is acknowledged or deregistered. The
runtime deliberately leaves started executions `RUNNING` (Phase 08, §139).

### Container order

`shutdown()` stops, in order:

1. the scheduler task;
2. `workers.stop()`, so runs can persist their final state;
3. `queue.close()`;
4. `database.dispose()`.

### Typing workarounds

- redis-py annotates the `blmove` timeout as `int`, but Redis 6+ accepts
  fractional seconds. It is passed as `cast("int", block_seconds)`, with a
  comment explaining why.
- redis-py returns `bytes | str`. The `_text()` helper decodes bytes
  explicitly instead of trusting `decode_responses`.

## 9. Findings

### F1 — API-created executions were never executed before this phase
- **Impact:** HIGH
- **Resolution:** FIXED
- **Notes:** Found by inspecting `ExecutionService`, `api/triggers.py` and
  `bootstrap.py`. It was documented in earlier docstrings ("until the
  worker/queue architecture exists"). Every trigger path now dispatches.

### F2 — `testcontainers[redis]` extra would downgrade testcontainers
- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:** `uv add --dev "testcontainers[postgres,redis]"` resolved
  testcontainers 4.15.0 → 4.13.3 together with `redis` 6.4. The change was
  reverted. The fixture uses the generic `DockerContainer` with
  `LogMessageWaitStrategy`, as the Mailpit fixture already did.

### F3 — `wait_for_logs` with a string predicate is deprecated in testcontainers 4.15
- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** Replaced by
  `.waiting_for(LogMessageWaitStrategy("Ready to accept connections"))`.

### F4 — redis-py type annotations are narrower or wider than the behavior
- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:** The `blmove` timeout is typed `int`, and responses are typed
  `bytes | str`. Handled by a documented `cast` and the `_text()` helper;
  no `type: ignore` was used.

### F5 — Ruff ASYNC109 forbids a `timeout` parameter on async functions
- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** The parameter was renamed to `block_seconds`. It is a
  server-side block time, not a client timeout.

### F6 — Login throttle remains per-process
- **Impact:** LOW
- **Resolution:** DEFERRED
- **Notes:** `application/auth.py` has a `ponytail:` note: "Move to Redis
  with the worker architecture (Phase 22) if several API processes serve
  logins". Phase 22 adds worker processes, not API processes, and the
  roadmap scope does not include it. Not done.

### F7 — Out-of-process cancel of a worker-run execution is slow to take effect
- **Impact:** MEDIUM
- **Resolution:** DEFERRED (Phase 23)
- **Notes:** Pre-existing behavior (Phase 08 compare-and-swap).
  `akasha execution cancel` records `CANCELLED` in the database. The
  worker's run fails its next save with `ConcurrentModificationError`
  (logged as `job_skipped`). Node code keeps running until that save.

## 10. Bugs / Problems Found

All of the following were introduced during this phase and caught before
completion.

| Problem | Root cause | Fix | Regression test |
|---|---|---|---|
| 16 of 24 new worker tests failed with `WorkflowValidationFailedError` | The test graph had one node; graph validation rejects `DISCONNECTED_NODE` | Test graph is now `test.gate → test.sink` | All tests in `test_workers.py` |
| Container test failed: `pydantic ValidationError` on `queue_prefix` | The test prefix `t-<32 hex>` exceeded the 32-character pattern | Truncated to `new_id("t")[:24]` | `test_container_wires_queue_worker_and_readiness` |
| `test_container_wires_queue_worker_and_readiness` found no registered worker | `WorkerManager.start()` returned before the worker task's first heartbeat (a real race, not only a test issue) | `start()` now awaits `worker.beat()` before creating tasks | Same test; 6 consecutive green runs |
| First draft of the concurrency test busy-waited with `asyncio.sleep(0)` (violates "no sleep-based synchronization") | Written without a start signal | `Gate.each_start` queue; the test awaits two starts | `test_worker_runs_up_to_its_concurrency_at_once` |
| Duplicate-delivery test did not force two workers | With default concurrency 4, the first worker could take both copies (refused as `ExecutionAlreadyRunningError`, a different path) | First worker uses `concurrency=1` | `test_duplicate_delivery_to_two_workers_runs_once` |
| API test risked disposing the session-shared database | Entering the app lifespan runs `Container.shutdown()`, which disposes `container.database` | API test container is built without `database` | `test_execution_outlives_the_http_request` |
| mypy: `FakeExecutions` could no longer be instantiated | The `ExecutionRepository` protocol gained `list_queued` | Added a stub returning `[]` | `tests/application/test_trigger_service.py` |

No pre-existing product bugs were fixed; F1 is a missing feature, not a
defect. No environment problems occurred: Docker 28.3.2 was available.

## 11. Fixes Applied

- **Worker registration race.**
  - Symptom: a worker was not listed right after `WorkerManager.start()`.
  - Root cause: registration happened inside the background task.
  - Fix: `start()` awaits `self.worker.beat()` first.
  - Verified by: the container test, plus 6 repeated runs of
    `test_workers.py`, all 24/24.
- **Type and lint fixes** (F4, F5): the `cast` / `_text` helpers and the
  `block_seconds` rename. Verified: `ruff check`, `mypy` strict clean.
- **Test infrastructure** (F2, F3): generic container with the structured
  wait strategy. Verified: no testcontainers deprecation warning in the
  final runs.
- **Stale documentation** in `api/triggers.py`, `runtime/runtime.py`,
  `runtime/scheduler.py`, `config.py` and architecture §58. Verified by
  `grep "Phase 22" src`: the remaining mentions are accurate.

## 12. Testing & Verification

### Integration tests, queue + workers (`tests/integration/test_workers.py`, 24 tests, real Redis + PostgreSQL)

**Queue:**
- `test_enqueue_dequeue_ack_is_fifo_and_empties_the_queue`
- `test_duplicate_jobs_are_refused_while_queued_or_held`
- `test_undecodable_job_is_dropped_not_redelivered`
- `test_unreachable_redis_is_a_queue_unavailable_error`: also asserts that
  the host is not in the message.

**Processing:**
- `test_created_execution_is_dispatched_and_run_by_a_worker`
- `test_worker_runs_up_to_its_concurrency_at_once`
- `test_manual_run_without_dispatch_is_not_queued`

**Failures and retries:**
- `test_failed_job_is_acked_and_the_queued_execution_redispatched`: a
  database outage injected into the runtime's unit-of-work factory.
- `test_execution_created_while_redis_is_down_is_dispatched_later`: real
  refused connection to `127.0.0.1:1`, then recovery. A second recovery
  pass queues nothing.
- `test_fresh_queued_executions_are_not_redispatched`

**Heartbeat:**
- `test_heartbeat_registers_the_worker_with_an_expiring_key`: asserts
  `PTTL` is at most 6000 ms for a 2 s interval, and the `stopping` state.
- `test_heartbeat_reports_the_executions_being_run`
- `test_expired_registration_is_not_listed_and_is_recovered`

**Graceful shutdown:**
- `test_graceful_stop_finishes_runs_and_takes_no_new_jobs`
- `test_graceful_stop_cancels_runs_that_outlast_the_grace`: `CANCELLED`
  with reason `worker shutdown`; node cleanup sees `is_cancelled()`.
- `test_job_dequeued_while_stopping_is_handed_back`

**Crash boundary:**
- `test_jobs_of_a_dead_worker_are_redelivered`
- `test_crash_mid_run_never_runs_node_code_twice`: the execution stays
  `RUNNING`, as documented for Phase 23.

**Duplicate delivery:**
- `test_duplicate_delivery_to_two_workers_runs_once`
- `test_redelivery_of_a_finished_execution_is_skipped`

**Manager and container:**
- `test_manager_start_stop_owns_the_worker`
- `test_container_wires_queue_worker_and_readiness`
- `test_container_reports_an_unreachable_queue_not_ready`

**Synchronization method.** Events and queues only:

- `Gate.started` and `Gate.each_start` signal node starts;
- `SpyQueue.acked` signals acknowledgements; it subclasses the real queue
  and only records acks.

Each wait is bounded by `asyncio.timeout(10)` as a deadlock guard. Heartbeat
expiry is simulated by deleting the registration key.

**Flakiness.** 6 consecutive runs of the file, all 24 passed (about 9.7 s
each).

### API test
- `test_execution_outlives_the_http_request`: runs through the real FastAPI
  app, its lifespan (which starts `WorkerManager`) and an authenticated
  admin session.
  1. `POST .../execute` returns 201 with `queued`.
  2. The node is observed running after the response.
  3. `GET /executions/{id}` shows `running`.
  4. After the node is released, the job is acknowledged and the execution
     shows `completed`.

### CLI tests (`tests/integration/test_cli.py`)
- `test_worker_needs_redis`: exit 1, error names `AKASHA_REDIS_URL`.
- `test_worker_process_registers_and_stops_gracefully_on_sigterm`:
  - a real `python -m akasha worker` subprocess;
  - the stderr `running` line is the readiness signal;
  - the worker is listed in Redis;
  - after SIGTERM: exit 0, and the worker is deregistered.

### Persistence and migration tests
- `test_list_queued_returns_old_queued_ids_oldest_first`: checks the status
  filter (excludes `CREATED` and `RUNNING`), the age cutoff, oldest-first
  order and `limit`.
- `test_migrations_upgrade_downgrade_upgrade_on_empty_database` with head
  `0009_queued_executions_index`: passes.

### Architecture test
- `test_dependency_direction` now covers `workers.py`: passes.

### Security tests
- Only the targeted assertion that the Redis host does not appear in
  `QueueUnavailableError.message`. No dedicated security suite was added.

### Not performed
- E2E tests, performance or load tests, and accessibility tests (not
  applicable to the backend).
- No test with a password-protected or TLS Redis.

## 13. Quality Gates

All results come from the final run on the current working tree,
2026-09-30.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 180 files already formatted |
| Linter (`uv run ruff check .`) | PASS | Earlier ASYNC109 and E501 errors were fixed during the phase |
| Type Checker (`uv run mypy .`, strict) | PASS | 176 source files. Earlier redis-py typing errors and the `FakeExecutions` abstract error were fixed |
| Unit Tests | PASS | Part of the full suite |
| Integration Tests | PASS | Full suite: 1675 passed, 5 skipped; `test_workers.py`: 24 passed |
| API Tests | PASS | Included in the full suite |
| E2E Tests | N/A | None exist for the backend at this phase |
| Build (`uv build`) | PASS | Exit 0 |
| Security Checks | N/A | No dependency audit tool is configured in the repository |
| Accessibility | N/A | Backend |
| Other: skipped tests | — | 5 skipped: all in `tests/external/test_openrouter_smoke.py` (`AKASHA_SMOKE_OPENROUTER_API_KEY` not set; intentional external smoke tests) |
| Other: warnings | — | 1 pre-existing `StarletteDeprecationWarning` from `fastapi.testclient` (unrelated) |

## 14. Acceptance Criteria Verification

Roadmap §26 has one acceptance criterion. Its test list and the
session-requested items are evaluated as well.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Long-running Workflows do not depend on an open HTTP request | PASS | `test_execution_outlives_the_http_request`: the response is 201/`queued` while the node is still blocked; the worker completes it after the response |
| Roadmap test: enqueue | PASS | `test_enqueue_dequeue_ack_is_fifo_and_empties_the_queue`, `test_created_execution_is_dispatched_and_run_by_a_worker` |
| Roadmap test: dequeue | PASS | same, plus `test_undecodable_job_is_dropped_not_redelivered` |
| Roadmap test: worker execution | PASS | `test_created_execution_is_dispatched_and_run_by_a_worker`, `test_worker_runs_up_to_its_concurrency_at_once` |
| Roadmap test: worker failure | PASS | `test_failed_job_is_acked_and_the_queued_execution_redispatched`, `test_crash_mid_run_never_runs_node_code_twice` |
| Roadmap test: duplicate delivery handling | PASS | `test_duplicate_delivery_to_two_workers_runs_once`, `test_redelivery_of_a_finished_execution_is_skipped`, `test_duplicate_jobs_are_refused_while_queued_or_held` |
| Roadmap test: heartbeat | PASS | three heartbeat tests (§12) |
| Roadmap test: queue recovery | PASS | `test_jobs_of_a_dead_worker_are_redelivered`, `test_execution_created_while_redis_is_down_is_dispatched_later` |
| Session: graceful shutdown | PASS | two graceful-stop tests, `test_job_dequeued_while_stopping_is_handed_back`, CLI SIGTERM test |
| Session: retries where applicable | PARTIAL (by scope) | Job-level redelivery and re-dispatch are implemented and tested. Retry *policies* and backoff are roadmap Phase 23, and the runtime still refuses `max_attempts > 1`. Does not block this phase |
| Session: no sleep-based synchronization | PASS | Tests use events and queues; the only timing values are deadlock guards and the 0.05 s dequeue block time, which never paces an assertion |
| Session: workers do not duplicate execution semantics | PASS | `workers.py` only calls `ExecutionRuntime.run` and `ExecutionRuntime.cancel`; `runtime/runtime.py` has docstring-only changes |

## 15. Security Considerations

- **Secrets:**
  - `AKASHA_REDIS_URL` is a `SecretStr`, because the URL may contain a
    password.
  - `QueueUnavailableError` messages contain only the exception class name
    (`details.reason`), never the URL. Tested for the host.
  - Log lines with `exc_info` (`execution_dispatch_failed`,
    `worker_dequeue_failed` context) may include redis-py exception text.
    That text contains host and port, not credentials. It goes through the
    existing redaction formatter. Not separately tested with a password URL.
- **Isolation:** installations sharing one Redis must use different
  `AKASHA_QUEUE_PREFIX` values. The pattern `^[a-z0-9][a-z0-9_-]{0,31}$`
  prevents injecting key separators.
- **Payload trust:**
  - Job payloads are parsed with `json.loads` and a strict schema check;
    unknown or malformed payloads are dropped.
  - A job carries only an id. The worker reloads everything from
    PostgreSQL, so Redis contents cannot change what executes.
  - Anyone with write access to Redis can still trigger runs of existing
    `QUEUED` executions, or delete jobs. Deleted jobs are recovered.
    Redis must be a private service.
- **Authorization:** unchanged. The API authorizes creation; workers act
  with engine authority, like the Phase 08 runtime.
- **Not implemented:** Redis TLS/ACL configuration guidance. `from_url`
  supports `rediss://` and credentials, but this was not tested.

## 16. Performance Considerations

- **Recovery scan:**
  - one indexed query per interval, `limit=500`, using the partial index
    `ix_executions_queued_created_at`, so its cost scales with the backlog,
    not the history;
  - the per-id `enqueue` calls are sequential, at most 500 round trips per
    pass.
- **Blocking dequeue:**
  - holds one pooled Redis connection per worker for up to
    `worker_poll_seconds` (1 s default);
  - an idle worker's graceful stop takes at most one poll interval.
- **Heartbeat:** one pipelined transaction every 5 s per worker.
- **`workers()`:** `SMEMBERS` plus one pipelined `HGETALL` per worker.
- **Per worker:** concurrency is bounded by `worker_concurrency` (default
  4). Node-level concurrency inside an execution is still the runtime's
  `max_concurrency`.
- **Measurements:** none were taken. The suite runtime (about 145 s) is not
  a benchmark.

## 17. Documentation Changes

| File | Reason | What |
|---|---|---|
| `docs/architecture.md` §72 | New implementation | Implementation note: truth vs. signal, recovery, reliable-queue layout, at-least-once and duplicates, heartbeat, worker failure, graceful stop, crash, placement |
| `docs/architecture.md` §58 | CLI behavior changed | `workflow run` does not dispatch; `akasha worker` |
| `docs/architecture.md` §113 | Metric added | `akasha_queue_depth`; removed from "not yet emitted" |
| `backend/README.md` | New config and command | Worker/queue env var table, `worker` CLI row, `workflow run` wording, layout entry for `workers.py` |
| Module docstrings | New and stale text | `application/queue.py`, `infrastructure/redis_queue.py`, `workers.py`, `application/executions.py`, `api/triggers.py`, `runtime/runtime.py`, `runtime/scheduler.py` |

**Should be updated later:**

- the §113 note about LLM metrics reaching the runtime ("Phases 21/22"),
  which is still open;
- the §1791 plugin note ("other processes see them after a restart
  (workers, Phase 22)"), which is still accurate but could name
  `akasha worker`;
- no ADR was created. The decisions are recorded in §72 and in this report.

## 18. Dependencies / Infrastructure Changes

- **New runtime dependency:** `redis~=8.1` (redis-py 8.1.0, ships
  `py.typed`). It is the Redis client for the queue, which architecture
  §24.2 and backend AGENTS §3 name.
- **Rejected:** `testcontainers[redis]` (F2).
- **Test infrastructure:** new `redis:8.2.1-alpine` image, pinned and
  session-scoped. Readiness is a log wait strategy; isolation is per-test
  key prefixes.
- **New environment variables:** `AKASHA_REDIS_URL`,
  `AKASHA_QUEUE_PREFIX`, `AKASHA_WORKER_ENABLED`,
  `AKASHA_WORKER_CONCURRENCY`, `AKASHA_WORKER_POLL_SECONDS`,
  `AKASHA_WORKER_HEARTBEAT_SECONDS`,
  `AKASHA_WORKER_SHUTDOWN_GRACE_SECONDS`,
  `AKASHA_QUEUE_RECOVERY_SECONDS`.
- **Database:** migration `0009` (index only).
- **Docker / Compose:** no changes. No Compose file exists yet (Phase 26).

## 19. Deferred Work

| What | Why deferred | Target | Affects this phase? |
|---|---|---|---|
| Recovering executions left `RUNNING` by a crashed worker | Roadmap Phase 23 scope ("stale execution detection, worker crash recovery") | Phase 23 | No; documented boundary, tested behavior |
| Retry policies, backoff, bounded re-dispatch of failing jobs | Phase 23 | Phase 23 | No |
| Cancel signal across processes (pub/sub to the owning worker) | Phase 23 ("cancellation") | Phase 23 | No |
| Login throttle in Redis | Not in roadmap scope; only needed with several API processes | Unassigned (Phase 25 hardening is a candidate) | No |
| Compose/deployment wiring for Redis and worker processes | Phase 26 (Operations + Deployment) | Phase 26 | No |
| Trigger scheduler still runs in every API process (duplicate fires with several API processes) | Pre-existing; not in Phase 22 scope | Unassigned | No |

## 20. Known Limitations

- A worker crash mid-run leaves the execution `RUNNING` permanently until
  Phase 23. Its redelivered job is skipped.
- A worker whose heartbeat expires while it is alive (a stall longer than
  3 × heartbeat) causes redelivery. It is safe (compare-and-swap), but it
  produces `job_skipped` noise.
- A `QUEUED` execution whose job fails before start on every attempt (for
  example, the database is persistently unreachable for that worker) is
  re-dispatched every `queue_recovery_seconds` without limit.
- Without `AKASHA_REDIS_URL`, API-created executions still stay `QUEUED`.
  This is documented, but no startup warning is logged.
- Graceful shutdown cancels runs that outlast the grace period (result:
  `CANCELLED`). There is no hand-over or resumption.
- Recovery handles at most 500 stale `QUEUED` executions per pass.
- Queue depth is a sampled gauge, only as fresh as the recovery interval.

## 21. Open Issues

| Issue | Impact | Severity | Area | Next action | Target |
|---|---|---|---|---|---|
| `RUNNING` executions orphaned by worker crash | Executions never reach a terminal state | MEDIUM | runtime / workers | Stale-execution detection, for example using heartbeat registrations plus `started_at` | Phase 23 |
| Unbounded re-dispatch of an always-failing `QUEUED` execution | Log noise, wasted work | LOW | application / workers | Delivery count or retry budget | Phase 23 |
| Cross-process cancellation is slow to take effect | Node code keeps running after cancel until the next save | MEDIUM | runtime | Cancel channel (Redis pub/sub) to the owning worker | Phase 23 |
| No warning when the database is configured but Redis is not | Operators may not notice that executions are never run | LOW | bootstrap | Log a warning at startup, or document it as required in the deployment guide | Phase 26 |

## 22. Architectural / Product Decisions

- **`ExecutionQueue` is a protocol with one implementation.** Architecture
  §72 requires that the runtime and application not depend on Redis APIs.
  The protocol is that boundary, not speculative reuse.
- **A job is only an execution id.** Everything else is reloaded from the
  pinned version snapshot. This keeps Redis free of workflow data (privacy,
  §77) and makes stale jobs harmless.
- **Enqueue failures do not fail the API request.** The execution is
  committed; recovery dispatches it. `QUEUE_UNAVAILABLE` exists for callers
  that must surface it, but none does today.
- **Failed jobs are acknowledged, not left held.** The database decides
  whether to retry: a `QUEUED` execution is re-dispatched by recovery. No
  dead-letter list was added, because it would be redundant with recovery.
- **Grace-period expiry records `CANCELLED`**, with reason
  `worker shutdown`, rather than leaving `RUNNING` or requeueing. It is
  truthful, terminal and needs no resumption support.
- **The recovery age doubles as the re-dispatch threshold.** One setting,
  `queue_recovery_seconds`. It also protects the CLI's in-process
  `workflow run` from being picked up by a worker.
- **`WorkerManager.start()` registers synchronously.** When `start()`
  returns, the worker is visible to operators and tests.

## 23. Repository / Git State

- **Branch:** `main`. HEAD is `5adcf5c backend phase 21 - cli`.
- **Working tree:** uncommitted Phase 22 changes. 22 modified files
  (+378/−30 per `git diff --stat` before this report), plus these untracked
  files:
  - `backend/migrations/versions/0009_queued_executions_index.py`
  - `backend/src/akasha/application/queue.py`
  - `backend/src/akasha/infrastructure/redis_queue.py`
  - `backend/src/akasha/workers.py`
  - `backend/tests/integration/test_workers.py`
  - this report
- **Unrelated changes:** none detected. The tree was clean at session start.
- **Note:** during implementation, a `git stash` / `git stash pop` was run
  once to compare against a baseline. The comparison was invalid, because
  untracked new tests stayed in place and ran against the stashed code. Its
  result was discarded. The pop restored the working tree completely; the
  final gate runs above were taken afterwards.
- **Nothing was committed.**

## 24. Phase Completion Assessment

### Implemented
The queue contract, the Redis reliable queue, `Worker`, `WorkerManager`,
heartbeat and registration, dispatch from `ExecutionService`, queue
recovery, graceful shutdown, `akasha worker`, settings, readiness, the
`queue_depth` metric and migration `0009`.

### Verified
- 24 worker/queue integration tests, 2 CLI tests, 1 repository test, the
  migration test and the architecture test.
- Full suite: 1675 passed.
- ruff, ruff format, mypy strict and the build all pass.

### Not Implemented
Retry policies and backoff, stale-`RUNNING` recovery, cross-process cancel
signalling: all Phase 23 scope.

### Deferred
See §19.

### Open
See §21. No item blocks this phase.

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

The acceptance criterion passes. The limitations are the documented Phase 23
boundary: crashed-worker `RUNNING` executions and retry policy.

## 25. Handoff to Next Phase (Phase 23 — Reliability + Recovery)

**What Phase 23 can assume:**

- Every API-created execution is dispatched.
- Workers call `ExecutionRuntime.run` and `ExecutionRuntime.cancel` only.
- Delivery is at-least-once; a duplicate never runs node code, because of
  the runtime's QUEUED check and compare-and-swap.

**Available APIs:**

- `ExecutionQueue.workers()`: live registrations, including
  `active_executions` and `heartbeat_at`.
- `ExecutionQueue.recover()`.
- `ExecutionService.dispatch_queued`.
- `WorkerManager.recover()`: the periodic hook where stale-execution
  detection can be added.
- `ExecutionRepository.list_queued`: the model for a `list_running`-style
  scan.

**Where to plug in:**

- **Stale `RUNNING` detection:** an execution is stale when it is `RUNNING`
  and no live worker lists it in `active_executions` (and it is older than
  a threshold). Runs started by the CLI's in-process `workflow run` are not
  in any worker registration, so account for them.
- **Retries:** the runtime still refuses `retry_policy.max_attempts > 1`.
  Implement node retries inside the runtime, not in the worker.
- **Cross-process cancel:** today it takes effect only when the worker's
  next save fails.

**Must not be changed casually:**

- The Redis key layout and the job payload version (`v: 1`). A new version
  requires workers able to decode it before producers emit it.
- The "PostgreSQL is truth" rule.
- The shutdown order in `Container.shutdown` (workers before database).
- The `dispatch=False` path used by the CLI.

**Prerequisites:** Docker for the Redis and PostgreSQL Testcontainers.
Nothing else.
