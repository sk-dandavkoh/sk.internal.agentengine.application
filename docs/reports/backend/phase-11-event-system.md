# Phase 11 — Event System

## 1. Executive Summary

**Goal of the phase.** Roadmap §15 (Phase 11) introduces structured application and runtime events. It asks for:
- `Event`, `EventType`, `EventPublisher`, `EventSubscriber`, `EventRepository`;
- the execution events `execution.created/started/completed/failed/cancelled`;
- the node events `node.started/completed/failed/skipped`;
- persistence of the events.

The acceptance criterion is: *"Execution lifecycle can be reconstructed from persisted events plus canonical execution state."*

The phase prompt added four requirements:
- "relevant project events";
- correlation IDs, failure handling and duplicate handling;
- proof that domain state stays correct when event consumers fail;
- the loop IMPLEMENT → TEST → BREAK EVENT CONSUMER → VERIFY DOMAIN STATE → FIX.

**What was built.**
- **Domain model** (`akasha.domain.event`):
  - An immutable, validated `Event`.
  - 12 event types: the 5 execution and 4 node types from the roadmap, plus `execution.timed_out`, `node.cancelled` and `project.published`.
  - Payloads are closed per type and contain only scalar values.
- **Events recorded by the aggregate.** The `Execution` aggregate records its lifecycle events inside its transition methods.
- **Transactional persistence.** Events are appended to a new append-only `events` table (migration `0005_events`) in the same database transaction as the state change they describe.
  - The execution paths use `save_execution`.
  - `project.published` is appended inside `ProjectService.publish_project`.
- **In-process publisher.** `EventPublisher` delivers events only after commit. Each subscriber is isolated from errors and bounded by a timeout.
- **Wiring.** One shared publisher lives on `Container.events` and is injected into `ProjectService` and `ExecutionService`. `ExecutionRuntime` accepts one through the new `events=` parameter.

**Resulting state.**
- Every execution transition that the runtime drives produces a persisted event. It commits atomically with the state and carries the execution's correlation ID.
- Live subscribers see the same events in the same order as the persisted log.
- A raising or hanging subscriber does not change the outcome of an execution or its persisted state. Integration tests on PostgreSQL 17.2 prove this.
- The full backend suite passes: 905 tests, 0 skipped. Ruff format and lint, mypy (strict) and `uv build` pass as well.

**Acceptance.** The roadmap criterion and every listed test area are satisfied with evidence (§14). Some limitations remain (§20): delivery is at-most-once, subscribers run inline, and there is no global ordering across streams. These limitations are documented deliberately and do not violate any criterion.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

**Roadmap §15, Phase 11 — Event System**

- **Goal:** "Introduce structured application/runtime events."
- **Scope:**
  - `Event`, `EventType`, `EventPublisher`, `EventSubscriber`, `EventRepository`;
  - the execution events and node events listed above;
  - "Persist events."
- **Tests:** event creation, publication, persistence, subscription, event ordering where guaranteed, event payload validation.
- **Acceptance criterion:** the execution lifecycle can be reconstructed from persisted events plus canonical execution state.

**Phase prompt additions**
- Relevant execution, node *and project* events.
- Test serialization, correlation IDs, failure handling and duplicate handling.
- Test that domain state stays correct even when event consumers fail.
- "Events must not become the canonical source of domain state."
- Completion condition: the event infrastructure is usable by later observability and frontend live-update work.

**Architecture references**
- §18 describes the event architecture and its fields: event_id, event_type, timestamp, execution_id, project_id, project_version_id, node_id, payload. It requires events to be immutable.
- §19 describes the event flow Runtime → Event Publisher → Persistence / WebSocket / Metrics / Audit.
- §95 requires domain events and infrastructure events to be kept separate.

**Explicitly not in this phase**
- Structured logging and metrics consumers (Phase 12).
- WebSocket live updates and any frontend-facing endpoint for events.
- Queue or Redis based event fan-out (Phase 22).
- Retry and recovery semantics (Phase 23).
- Event-driven triggers (architecture §129).

---

## 3. Scope Implemented

### Domain
- **New module `backend/src/akasha/domain/event.py`:**
  - `EventType` (StrEnum) with 12 values: `execution.created`, `execution.started`, `execution.completed`, `execution.failed`, `execution.cancelled`, `execution.timed_out`, `node.started`, `node.completed`, `node.failed`, `node.skipped`, `node.cancelled`, `project.published`.
  - `PAYLOAD_KEYS`: the exact payload keys for each event type (a closed schema).
  - `EVENT_SCHEMA_VERSION = 1`.
  - `PayloadValue = str | int | None`.
  - `Event`, a frozen dataclass. Its constructor validates the event (§6).
- **Changes to `backend/src/akasha/domain/execution.py`:**
  - A private `_events` list on `Execution`.
  - The `pending_events` property, which returns a snapshot tuple.
  - `pull_events()`, which returns the recorded events and clears them.
  - The `_emit(...)` helper.
  - `Execution.create` records `execution.created`.
  - `start`, `complete`, `fail`, `cancel` and `time_out` record their execution events.
  - `start_node`, `complete_node`, `fail_node` and `skip_node` record their node events.
  - `_finish` (the terminal cascade) records `node.cancelled` for every unfinished node, in sorted node-id order, and now returns the finish timestamp.

### Application
- **New module `backend/src/akasha/application/events.py`:** the `EventSubscriber` type alias (`Callable[[Event], Awaitable[None]]`) and `EventPublisher` (`subscribe`, `publish`).
- **`backend/src/akasha/application/persistence.py`:** a new `EventRepository` protocol (`append`, `list`) and a new `UnitOfWork.events` member.
- **`backend/src/akasha/application/executions.py`:**
  - New function `save_execution(uow, execution)`: it saves the aggregate and appends its pending events in the same transaction.
  - `ExecutionService.__init__` takes an optional `events: EventPublisher`.
  - `create_execution` publishes after commit.
- **`backend/src/akasha/application/projects.py`:**
  - `ProjectService.__init__` takes an optional `events: EventPublisher`.
  - `publish_project` builds and appends a `project.published` event in the publish transaction and publishes it after commit.

### Runtime
- **`backend/src/akasha/runtime/runtime.py`:**
  - `StateManager` takes a publisher.
  - `StateManager.save` now calls `save_execution` and publishes `execution.pull_events()` after commit.
  - `ExecutionRuntime.__init__` takes an optional `events: EventPublisher`.
  - The module docstring was updated: "no events (Phase 11)" was removed and the event rule was added.

### Persistence
- **`backend/src/akasha/infrastructure/db_models.py`:** new `events` table (§6).
- **`backend/migrations/versions/0005_events.py`:** new migration that creates the table and two indexes. Its downgrade drops the table.
- **`backend/src/akasha/infrastructure/persistence.py`:**
  - New `SqlAlchemyEventRepository`.
  - `SqlAlchemyUnitOfWork.events` is wired to it.

### Composition
- **`backend/src/akasha/bootstrap.py`:**
  - `Container.events: EventPublisher`, using a default factory.
  - `create_container` builds one publisher and passes it to `ProjectService` and `ExecutionService`.

### Testing
- **New test files:**
  - `backend/tests/domain/test_event.py` (40 tests);
  - `backend/tests/application/test_event_publisher.py` (9 tests);
  - `backend/tests/integration/test_events.py` (15 tests).
- **Adapted test files:**
  - `backend/tests/application/test_trigger_service.py`: a `FakeEvents` repository was added because the `UnitOfWork` protocol now has a new member.
  - `backend/tests/integration/conftest.py`: `clean_db` truncates `events`.
  - `backend/tests/integration/test_persistence.py`: migration head `0005_events`, and the `events` table is checked after upgrade and downgrade.
  - `backend/tests/test_lifecycle.py`: asserts that the container shares one publisher across services.

### Documentation
- `docs/architecture.md` §19: an "Implementation (Phase 11)" subsection.
- This report.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/event.py` | Created (112 lines) | `Event`, `EventType`, payload schema, validation |
| `backend/src/akasha/domain/execution.py` | Modified | Aggregate records lifecycle events on its transitions |
| `backend/src/akasha/application/events.py` | Created (83 lines) | `EventPublisher`, `EventSubscriber` |
| `backend/src/akasha/application/persistence.py` | Modified | `EventRepository` protocol; `UnitOfWork.events` |
| `backend/src/akasha/application/executions.py` | Modified | `save_execution`; publisher injection; publish after commit |
| `backend/src/akasha/application/projects.py` | Modified | `project.published` recorded, persisted and published |
| `backend/src/akasha/runtime/runtime.py` | Modified | `StateManager` persists and publishes events; `ExecutionRuntime(events=)` |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | `events` table definition |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | `SqlAlchemyEventRepository`; UoW wiring |
| `backend/src/akasha/bootstrap.py` | Modified | Shared `Container.events` publisher |
| `backend/migrations/versions/0005_events.py` | Created (migration) | Schema for the event log |
| `backend/tests/domain/test_event.py` | Created (40 tests) | Event model and aggregate-recorded events |
| `backend/tests/application/test_event_publisher.py` | Created (9 tests) | Publisher semantics and isolation |
| `backend/tests/integration/test_events.py` | Created (15 tests) | Store, lifecycle, broken consumers, atomicity (PostgreSQL) |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeEvents` for the extended `UnitOfWork` protocol |
| `backend/tests/integration/conftest.py` | Modified | Truncate `events` between tests |
| `backend/tests/integration/test_persistence.py` | Modified | `_HEAD = "0005_events"`; upgrade/downgrade checks `events` |
| `backend/tests/test_lifecycle.py` | Modified | Shared-publisher wiring assertion |
| `docs/architecture.md` | Modified (§19) | Documents the implemented event design |
| `docs/reports/backend/phase-11-event-system.md` | Created | This report |

No directories were created and no files were deleted. No generated files were produced (the `uv build` output went to a scratch directory outside the repository). There were no configuration or dependency changes (`pyproject.toml` and `uv.lock` are untouched).

Diff totals for tracked files: 13 files changed, 327 insertions, 30 deletions. In addition there are 7 untracked new files, one of which is this report.

---

## 5. Architecture Changes

The event design follows architecture §18/§19. The decisions below fill in details that the architecture left open. They are recorded in `docs/architecture.md` §19 ("Implementation (Phase 11)").

### 5.1 Events are recorded by the aggregate

**Decision.** `Execution` transition methods record their own events.

**Context.** Execution state changes originate in several places:
- `ExecutionService.create_execution`;
- the runtime's `_run`, `_launch`, `_record`, `_advance` and `_end`;
- the out-of-process `ExecutionRuntime.cancel`;
- the implicit terminal cascade in `Execution._finish`, which cancels every unfinished node.

**Chosen approach.**
- `Execution._emit` is called from inside each transition method.
- The runtime and the services never construct execution events themselves.
- The aggregate collects events in `_events` until `pull_events()` is called.

**Alternatives considered.** The runtime could have emitted events next to its existing `_log(...)` calls.

**Reason.** Emitting from the runtime would let any current or future caller change state without an event. It would also miss the node cancellations that the terminal cascade performs inside the domain, which the runtime never sees individually. Recording in the aggregate makes "state change without event" structurally impossible for execution transitions.

**Consequences.**
- New transitions must add an `_emit` call.
- Retry and pause transitions exist in the domain but emit nothing yet (§19).
- `project.published` is the exception: `Project` does not record events, and `ProjectService` builds this one (§5.5).

### 5.2 Transactional append (same transaction as the state)

**Decision.** Events are appended in the unit of work that saves the state change.

**Context.** Events must never become canonical state. The history still has to be reliable enough to reconstruct the lifecycle.

**Chosen approach.**
- `save_execution(uow, execution)` calls `uow.executions.save(execution)` and then `uow.events.append(execution.pending_events)`.
- The caller commits, and then publishes `execution.pull_events()`.
- `publish_project` appends inside its existing publish transaction.

**Alternatives considered.** A persisting subscriber on the publisher.

**Reason.** A persisting subscriber could fail independently. The result would be state with no history, or history for state that was rolled back.

**Consequences.**
- A failure to store events aborts the state change. `test_event_store_failure_rolls_back_the_state_change` proves this.
- The event store is part of the write path of every execution transition.

### 5.3 Publish after commit, at-most-once, with isolation

**Decision.** `EventPublisher.publish` is called only after `uow.commit()` has succeeded.

**Chosen approach.**
- Delivery is in-process and sequential: events in order, and each event to subscribers in subscription order.
- Each subscriber call is wrapped in `asyncio.timeout(subscriber_timeout)` (default 5.0 s) and in a deliberate broad `except Exception`, which logs `event_subscriber_failed`.
- `CancelledError` (a BaseException) still propagates.

**Alternatives considered.** An outbox relay or a background delivery task per subscriber. Both were deferred (§19).

**Reason.**
- No subscriber can observe an event whose state was rolled back.
- No subscriber can fail, roll back or indefinitely block the producer, as AGENTS §44 and the phase prompt require.
- This is the smallest implementation that guarantees both.

**Consequences.**
- Delivery is at-most-once: a crash between commit and publish loses the live notification, but not the stored event.
- Consumers that need completeness must catch up from `EventRepository.list(after=position)` and deduplicate by `event_id`.

### 5.4 Ordering model: `position` per stream

**Decision.** A `BIGINT GENERATED ALWAYS AS IDENTITY` column `position` defines event order. Events are inserted one statement at a time, in order.

**Reason.**
- Each execution and each project has exactly one writer at a time, enforced by the existing compare-and-swap on `revision`.
- The writer's transactions are sequential.
- Identity values drawn in insertion order are therefore monotonic within a stream.

**Consequences.**
- Order is guaranteed per `execution_id` and per `project_id`.
- Across unrelated streams, positions only approximate commit order. A global "after N" cursor may skip an event that committed concurrently. This is documented on `EventRepository.list`.

### 5.5 `project.published` built by the service

**Decision.** `ProjectService.publish_project` constructs the event.

**Reason.**
- It is the only project event in scope.
- Adding event recording to `Project` for one event was not justified.
- The event is appended in the same transaction as the version insert, so atomicity is the same as for execution events.

**Consequences.** If more project events are added (created, draft updated, deleted), consider moving recording into `Project` for consistency with §5.1.

### 5.6 Closed, scalar payloads

**Decision.** Each `EventType` declares its exact payload keys. Values are restricted to `str | int | None` (`bool` is rejected).

**Reason.** This is a security and privacy rule (AGENTS §35, §76, §77). Node inputs and outputs, configuration values, error messages and details (free text), and cancellation reasons may contain business data or secrets. With a closed schema they cannot structurally enter an event.

**Consequences.** Adding payload fields requires a change to `PAYLOAD_KEYS`, and a `schema_version` bump if the meaning of an existing type changes (AGENTS §81).

---

## 6. Domain / Data Model Changes

### `Event` (new, frozen dataclass)

| Field | Type | Notes |
|---|---|---|
| `event_type` | `EventType` | Coerced from its string value (`EventType("node.skipped")`) |
| `occurred_at` | `datetime` | Must be timezone-aware |
| `project_id` | `str` | Validated domain ID |
| `execution_id` | `str \| None` | Required for `execution.*` and `node.*`; forbidden for `project.*` |
| `node_id` | `str \| None` | Required for `node.*`; forbidden otherwise |
| `project_version` | `int \| None` | Execution events: the pinned version; `project.published`: the new version |
| `correlation_id` | `str \| None` | Copied from `ExecutionMetadata.correlation_id` for execution/node events |
| `payload` | `Mapping[str, str\|int\|None]` | Defensive copy; keys must equal `PAYLOAD_KEYS[event_type]` |
| `event_id` | `str` | Default `new_id("evt")` |
| `schema_version` | `int` | Default `EVENT_SCHEMA_VERSION = 1`; must be ≥ 1 |
| `position` | `int \| None` | `None` until persisted; set when read back from the store |

**Deviation from architecture §18.** The architecture lists `project_version_id`. The implementation carries `project_version` (the version number) instead. The rest of the persistence model identifies a version by `(project_id, version)`: the `executions.project_version` column and the FK to `project_versions(project_id, version)`. Logs also use the number.

**Payload schema (`PAYLOAD_KEYS`)**

| Event type | Payload keys |
|---|---|
| `execution.created` | `trigger_type`, `trigger_id` |
| `execution.started` | — |
| `execution.completed` | — |
| `execution.failed` | `error_code`, `error_kind`, `error_node_id` |
| `execution.cancelled` | — (the reason is deliberately excluded) |
| `execution.timed_out` | `error_code` |
| `node.started` | `attempt` |
| `node.completed` | `attempt` |
| `node.failed` | `attempt`, `error_code`, `error_kind` |
| `node.skipped` | — |
| `node.cancelled` | — |
| `project.published` | — |

### `Execution` (changed)

- **New field:** `_events: list[Event]` (`init=False`, `repr=False`). It is not persisted and not part of rehydration.
- **New members:**
  - `pending_events` (a tuple snapshot);
  - `pull_events()` (returns and clears);
  - `_emit(...)`.
- **Transition → event mapping:**

| Transition | Event(s) |
|---|---|
| `Execution.create` | `execution.created` (at `created_at`) |
| `start` | `execution.started` (at `started_at`) |
| `start_node` | `node.started` |
| `complete_node` | `node.completed` |
| `fail_node` | `node.failed` |
| `skip_node` | `node.skipped` |
| `complete` / `fail` / `cancel` / `time_out` | one `node.cancelled` per unfinished node (sorted by node id), then `execution.completed` / `.failed` / `.cancelled` / `.timed_out` |
| `enqueue`, `mark_node_ready`, `pause`, `wait`, `resume`, `retry_node`, `request_cancellation` | none |

- State transition rules are unchanged. An illegal transition raises before `_emit`, so it records nothing (tested).
- **Refactor:** `_finish` now returns the finish timestamp. The node transition methods compute `now = now or utcnow()` before delegating to `NodeExecution`, so the event and the state carry the identical timestamp. Behaviour is unchanged; all 841 pre-existing tests pass.

### Database: `events` table (migration `0005_events`)

| Column | Type | Constraints |
|---|---|---|
| `position` | BIGINT | `GENERATED ALWAYS AS IDENTITY`, primary key |
| `event_id` | VARCHAR | NOT NULL, UNIQUE |
| `event_type` | VARCHAR | NOT NULL |
| `schema_version` | INTEGER | NOT NULL |
| `occurred_at` | TIMESTAMPTZ | NOT NULL |
| `project_id` | VARCHAR | NOT NULL |
| `execution_id` | VARCHAR | NULL |
| `node_id` | VARCHAR | NULL |
| `project_version` | INTEGER | NULL |
| `correlation_id` | VARCHAR | NULL |
| `payload` | JSONB | NOT NULL |

- **Indexes:** `ix_events_execution_position (execution_id, position)` and `ix_events_project_position (project_id, position)`.
- **Relationships:** none (deliberately no FKs, §22).

**Compatibility.**
- The migration is purely additive; no existing table changes.
- Executions created before Phase 11 have no events. Their lifecycle can be read only from state.
- Downgrade drops the table (tested by the upgrade → downgrade → upgrade test).

**Serialization.** `SqlAlchemyEventRepository` maps `Event` to and from rows explicitly:
- `payload` is stored as JSONB;
- `occurred_at` is normalised to UTC via `_ensure_utc`;
- `position` is filled in on read.

A round-trip test confirms that every field survives persistence.

---

## 7. API / Contract Changes

**HTTP API.** No endpoints were added, changed or removed. No request, response or error schemas changed.

**New internal contracts** (application layer, used by later phases):

| Contract | Purpose | Input | Output | Errors / semantics |
|---|---|---|---|---|
| `EventRepository.append(events)` | Persist events in the caller's transaction | `Sequence[Event]` | None | Idempotent on `event_id` (`ON CONFLICT DO NOTHING`). Participates in the UoW transaction; any failure aborts it. |
| `EventRepository.list(*, execution_id=None, project_id=None, after=0, limit=100)` | Read history / catch up | Optional stream filters, position cursor | Events with `position > after`, ascending | Order is guaranteed per execution and per project; across streams it is approximate. |
| `UnitOfWork.events` | New protocol member | — | — | **Breaking for implementations of `UnitOfWork`:** the only other implementation, `FakeUow` in `tests/application/test_trigger_service.py`, was updated. |
| `EventPublisher.subscribe(subscriber, *, event_types=None)` | Register a consumer | Async callable; optional type filter | Unsubscribe callable | — |
| `EventPublisher.publish(events)` | Fan out after commit | `Sequence[Event]` | None | Never raises for subscriber errors or timeouts (logged as `event_subscriber_failed`); propagates `CancelledError`. |
| `EventPublisher(subscriber_timeout=5.0)` | — | Positive float | — | `ConfigurationError` if ≤ 0 |
| `save_execution(uow, execution)` | Stage aggregate + events | UoW, Execution | None | Events stay pending until the caller pulls them after commit. |

**Constructor changes (backward compatible, optional keyword or positional):**
- `ExecutionService(uow, events=None)`
- `ProjectService(uow, node_definitions, events=None)`
- `ExecutionRuntime(..., events=None)`

When `None` is passed, each creates a private `EventPublisher` with no subscribers. Events are still persisted but nobody is notified.

**Event payload contract.** The table in §6 is the contract for future WebSocket and frontend consumers. It is versioned by `schema_version` (currently 1).

---

## 8. Implementation Details

**Write path for an execution transition (runtime):**
1. The scheduler loop mutates the aggregate, for example `execution.complete_node(...)`. The aggregate appends an `Event` to `_events`.
2. `StateManager.save(execution)` opens a new UoW and calls `save_execution`. That runs the CAS update of `executions` plus an upsert of `node_executions`, followed by `events.append(pending_events)`.
3. `uow.commit()` runs.
4. After a successful commit, `publisher.publish(execution.pull_events())` runs.
5. If step 2 or 3 fails (for example `ConcurrentModificationError`), the transaction rolls back and nothing is published. The events remain pending on an aggregate that is now stale; the run aborts as before.

**Batching.** Several transitions between two saves are persisted together in one transaction, in the order they were recorded. Examples are node completions recorded by `_record` and skips by `_advance`. This matches the runtime's existing "dirty" save strategy.

**Create path.**
- `ExecutionService.create_execution` does `Execution.create(...)` (records `execution.created`), then `enqueue()`, `save_execution`, commit and publish.
- `ExecutionRuntime.cancel` for an execution not running locally goes through `StateManager.save`, so it is evented too (tested).

**Publish path (projects).** `publish_project`:
1. validates the draft;
2. calls `publish_draft()`;
3. builds `Event(PROJECT_PUBLISHED, occurred_at=version.published_at, project_version=version.version)`;
4. saves the project and the version, appends the event, commits;
5. then publishes.

A failed validation raises before anything is appended (tested).

**Repository.**
- `SqlAlchemyEventRepository.append` issues one `INSERT ... ON CONFLICT (event_id) DO NOTHING` per event, in order. One statement per event guarantees that identity values are drawn in event order.
- `list` builds `SELECT ... WHERE position > :after [AND execution_id = ...] [AND project_id = ...] ORDER BY position LIMIT :limit`.

**Publisher concurrency.**
- `publish` iterates over a copy of the subscription list, so a subscriber may unsubscribe during delivery (tested).
- Delivery is sequential and awaited inline by the producer. With N subscribers the worst case per event is N × `subscriber_timeout`.

**Error handling.**
- The publisher's `except Exception` is the only broad catch introduced. It is commented with its reason, as AGENTS §10 and backend §4.3 require.
- Log record fields: `event`, `event_id`, `event_type`, `execution_id`, `correlation_id`, `subscriber` (qualname), plus `exc_info` for the server-side log.

**Dependency injection.**
- `create_container` builds one `EventPublisher` and passes it to both services.
- Consumers subscribe via `container.events.subscribe(...)`.
- `Container` without a database (no `database_url`) still has an `events` publisher with no producers.

**Layering.**
- `domain/event.py` imports only stdlib, `domain.primitives` and `errors`.
- `application/events.py` imports domain and errors only.
- The existing AST dependency-direction test (`tests/test_architecture.py`) passes.

**Time.** Event timestamps come from the same `now` used for the state transition. The runtime's injected clock therefore controls them (tests use `TickClock`).

---

## 9. Findings

### F1 — Single-node graphs are rejected by validation
- **Finding:** A draft with one node fails validation with `DISCONNECTED_NODE`. My first integration tests used single-node graphs and failed at `publish_project`.
- **Impact:** LOW (test authoring only).
- **Resolution:** FIXED. The tests use two-node graphs.
- **Notes:** This is existing Phase 04 behaviour and did not change.

### F2 — Only control-flow nodes may omit output ports
- **Finding:** `NodeDefinition._validate_ports` requires every declared output unless `capabilities.is_control_flow` is set. A test node that returns `{}` without that flag failed with a node error instead of producing a skip.
- **Impact:** LOW (test authoring).
- **Resolution:** FIXED. The skip scenario uses a local `Silent` node with `NodeCapabilities(is_control_flow=True)`.
- **Notes:** This is existing Phase 03/09 behaviour.

### F3 — Mutation "publish before commit" initially survived the test suite
- **Finding:** When publishing was moved inside the transaction, the verifying subscriber's `executions.get` (`SELECT ... FOR SHARE`) blocked on the writer's uncommitted row lock. After 5 s it timed out and was isolated by the publisher. Because it recorded only *mismatches*, the test passed.
- **Impact:** MEDIUM. The test could not detect the defect it was written for.
- **Resolution:** FIXED. `test_subscribers_only_see_committed_state` now requires a positive observation of the committed status for every event and uses `subscriber_timeout=1`. The same mutation in the runtime and in `ExecutionService` now fails the test.
- **Notes:** This also shows a property of the isolation design: a consumer that reads the database is safe only after commit. Publishing before commit would deadlock that consumer against its producer until the timeout.

### F4 — Pre-existing: repositories advance `revision` in memory before commit
- **Finding:** `SqlAlchemyExecutionRepository.save` (and the project repository) increments `execution.revision` right after the UPDATE, before `commit()`. If the commit then fails, the in-memory aggregate claims a revision that was never persisted.
- **Impact:** LOW for this phase. The runtime aborts on persistence errors, and pending events are not pulled on failure.
- **Resolution:** OPEN (pre-existing, not changed).
- **Notes:** This is why pulling events *after* commit is sufficient but not a retry mechanism. A retried save of the same aggregate would fail CAS anyway. Relevant for Phase 23 recovery.

### F5 — Transient Docker ping failure on the first test run
- **Finding:** The first full-suite run skipped 160 PostgreSQL-backed tests. `docker_available()` returned False even though `docker info` succeeded. Every subsequent run (`DockerClient().client.ping()`, then the full suite) reached Docker with 0 skips.
- **Impact:** LOW (environmental).
- **Resolution:** WORKAROUND (re-run). No code change.
- **Notes:** The final results in §13 have 0 skipped tests. The cause looked like a slow daemon response against the 2 s ping timeout in `tests/support.py`; this was not investigated further.

### F6 — Ruff `S101` forbids `assert` in `src`
- **Finding:** My first version of the aggregate used `assert finished_at is not None` to narrow types.
- **Impact:** LOW.
- **Resolution:** FIXED. `_finish` returns the timestamp and node methods compute `now` explicitly. There are no asserts and no `type: ignore` in `src`.

---

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Area | Origin |
|---|---|---|---|---|---|---|
| B1 | A committed-state test could not detect early publication | The subscriber checked only for mismatches; a timed-out subscriber was silently isolated | The test now requires positive observation of every event | `test_subscribers_only_see_committed_state` (verified by mutation) | Tests | Introduced and fixed during this phase |
| B2 | A test helper `event(event_type, **overrides)` raised `TypeError` when overriding `event_type` | Parameter name collided with the override key | Renamed the parameter to `kind` | `test_event_type_is_coerced_from_its_wire_value`, `test_malformed_events_are_rejected[overrides5]` | Tests | Introduced and fixed during this phase |
| B3 | An integration assertion expected no events at all after a rolled-back `create_execution` | The test's own `publish` legitimately records `project.published` | The assertion now checks that only `project.published` exists | `test_event_store_failure_rolls_back_the_state_change` | Tests | Introduced and fixed during this phase |
| B4 | Two convoluted assertions (out-of-process cancel, cancel cascade) in the first draft | Unclear authoring | Rewritten as explicit trail assertions (sorted cascade, since node ids are random) | the tests themselves | Tests | Introduced and fixed during this phase |

No product-code bugs from earlier phases were found. F4 is a pre-existing design property, not a demonstrated defect. F5 was environmental.

---

## 11. Fixes Applied

1. **Committed-state verification (B1 / F3)**
   - Symptom: mutation "publish before commit" passed all tests.
   - Root cause: see F3.
   - Implementation: the observed `(event_type, status)` list must equal the expected seven-tuple sequence; the timeout is 1 s.
   - Verification: re-running the mutation in `runtime.py` and in `executions.py` → 1 failed each.
2. **Assert-free timestamp handling (F6)**
   - Symptom: ruff `S101`.
   - Implementation: `_finish` returns `now`; node transitions compute `now` first.
   - Verification: ruff passes; 841 pre-existing tests pass unchanged.
3. **Test helper collision (B2):** renamed the parameter; 40/40 domain tests pass.
4. **Test graphs (F1, F2):** two-node graphs and a control-flow `Silent` node; 15/15 integration tests pass.
5. **Line-length violation:** a runtime docstring (E501) introduced while editing was re-wrapped; ruff passes.

---

## 12. Testing & Verification

### Unit Tests
**`tests/domain/test_event.py` (40 tests, all pass)**
- **Event model:**
  - all 12 types constructible with their schema (parametrized);
  - wire-value coercion;
  - unique IDs;
  - immutability plus a defensive payload copy;
  - scope-id rules (5 cases);
  - a closed, scalar payload: missing key, unknown key, bool, float, dict and list all rejected;
  - malformed input: naive datetime, bad project/event/node ids, `schema_version=0`, unknown type.
- **Aggregate recording:**
  - `execution.created` context (project, version, correlation, trigger payload);
  - full success order and timestamps;
  - failure followed by the sorted cascade;
  - cancel records no reason (`password=hunter2` absent);
  - timeout payload;
  - an illegal transition records nothing;
  - pull and snapshot semantics;
  - rehydration records nothing;
  - outputs, error messages and details are absent from events.

**`tests/application/test_event_publisher.py` (9 tests, all pass)**
- ordered fan-out;
- type filter;
- unsubscribe, including a subscriber unsubscribing itself during delivery;
- no subscribers;
- a failing subscriber is isolated and its structured log fields are asserted;
- a hanging subscriber times out (0.01 s) and later subscribers still receive the event;
- cancellation propagates;
- `subscriber_timeout ≤ 0` is rejected.

### Integration Tests
**`tests/integration/test_events.py` (15 tests, all pass on PostgreSQL 17.2 via Testcontainers)**

| Test | Verifies |
|---|---|
| `test_events_round_trip_through_the_store` | Serialization of every field; `position` assigned; tz-aware time |
| `test_append_is_idempotent_on_event_id` | Duplicate handling (re-append, duplicate within one call) |
| `test_events_are_ordered_by_position_and_paged_by_cursor` | Order by position, stream filtering, `after` cursor, `limit` |
| `test_uncommitted_events_are_rolled_back` | Transaction participation |
| `test_positions_cannot_be_forged` | `GENERATED ALWAYS` rejects explicit positions |
| `test_successful_run_reconstructs_from_events_plus_state` | **Acceptance criterion:** exact trail, live = persisted, correlation/project/version on all events, event timestamps equal state timestamps, no outputs in payloads |
| `test_failure_skip_and_cascade_events` | Failure, skip via a control-flow node, cancel cascade, failure payload |
| `test_cancel_running_execution_events` | Cancel while running; the reason does not leak |
| `test_out_of_process_cancel_of_queued_execution_is_evented` | The out-of-process cancel path records events |
| `test_concurrent_executions_keep_per_execution_order` | 6 concurrent runs; exact per-execution order |
| `test_broken_and_hanging_consumers_do_not_affect_the_run` | **Broken consumer:** raising plus hanging subscribers leave the status COMPLETED, outputs persisted, 7/7 events stored, healthy subscriber received all |
| `test_subscribers_only_see_committed_state` | Delivery only after commit (positive observation) |
| `test_event_store_failure_rolls_back_the_state_change` | Atomicity: no execution row, nothing published |
| `test_publish_records_project_published` | Project event persisted and published; timestamp = `published_at` |
| `test_failed_publish_records_no_event` | Failed validation → no event |

**Break-the-code loop (mutation verification).** Each mutation was applied, the 64 new tests were run, and the source was restored from a backup copy (`git status` afterwards showed only the intended changes). The first "publish before commit" run survived and led to fix §11.1; the table shows the final results.

| Mutation | Result |
|---|---|
| Runtime publishes before commit | killed (1 failed) |
| ExecutionService publishes before commit | killed (1 failed) |
| No event persistence in `save_execution` | killed (7 failed) |
| Publisher only catches `KeyError` (errors propagate) | killed (3 failed) |
| No subscriber timeout | killed (2 failed) |
| No `ON CONFLICT DO NOTHING` | killed (1 failed) |
| No `node.cancelled` in the cascade | killed (6 failed) |
| No `project.published` append | killed (2 failed) |

### Infrastructure Tests
- `tests/integration/test_persistence.py::test_migrations_upgrade_downgrade_upgrade_on_empty_database` now expects head `0005_events` and asserts that the `events` table exists after upgrade and is gone after downgrade to base. It passes.
- The existing `0002` upgrade-with-data test also migrates through `0005` to head. It passes.
- `tests/test_lifecycle.py::test_container_wires_project_service_with_database` asserts `container.executions._events is container.events is container.projects._events`. It passes.

### Security Tests
- A closed payload schema rejects unknown keys (`outputs`) and non-scalar values (domain tests).
- Absence of sensitive data is asserted in the unit and integration tests: node outputs (`"secret"`), error message and details (`"customer data"`), cancellation reasons (`"password=hunter2"`, `"secret-reason"`).

### Not performed
- **API tests:** no API changes.
- **E2E, accessibility and performance tests:** not applicable to this phase, and none were run.

---

## 13. Quality Gates

All gates were executed in `backend/` on the final working tree.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `uv run ruff format --check .` → 107 files already formatted |
| Linter | PASS | `uv run ruff check .` → All checks passed. One intermediate E501 and one S101 during development were fixed (§11). |
| Type Checker | PASS | `uv run mypy .` (strict) → no issues in 105 source files |
| Unit Tests | PASS | Included in the full run |
| Integration Tests | PASS | Included in the full run; Docker reachable, 0 skipped |
| Full test suite | PASS | `uv run pytest` → **905 passed, 0 skipped**, 1 warning (≈38 s). Before this phase: 841. |
| API Tests | PASS | The existing API tests (projects, triggers) pass; no new API |
| E2E Tests | N/A | No E2E suite exists for the backend in this phase |
| Build | PASS | `uv build` (output to a scratch dir outside the repo) |
| Security Checks | PASS | Ruff `S` rules pass; the security-relevant tests in §12 pass. No separate dependency audit was run (no dependency changes). |
| Accessibility | N/A | Backend only |
| Architecture guard | PASS | `tests/test_architecture.py` (dependency direction) |

**Warning (pre-existing, unrelated):** `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`, raised from `fastapi/testclient.py`. It is not caused by this phase.

**Documented type-ignore comments in tests:**
- `tests/domain/test_event.py:77` `# type: ignore[misc]  # proving immutability`;
- `tests/integration/test_events.py:473` `# type: ignore[assignment]  # partial fake`.

There are none in `src`.

---

## 14. Acceptance Criteria Verification

**Roadmap §15 acceptance criterion and required tests:**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Execution lifecycle can be reconstructed from persisted events plus canonical execution state | PASS | `test_successful_run_reconstructs_from_events_plus_state` (exact trail; event times == state times); `test_failure_skip_and_cascade_events`; `test_cancel_running_execution_events`; `test_out_of_process_cancel_of_queued_execution_is_evented`; `test_concurrent_executions_keep_per_execution_order` |
| Scope: `Event`, `EventType`, `EventPublisher`, `EventSubscriber`, `EventRepository` | PASS | `domain/event.py`; `application/events.py`; `application/persistence.py`; `infrastructure/persistence.py` |
| Scope: events `execution.created/started/completed/failed/cancelled`, `node.started/completed/failed/skipped` | PASS | `EventType`; each is emitted and asserted in domain and integration tests |
| Scope: persist events | PASS | `events` table, migration `0005_events`, `SqlAlchemyEventRepository`; store tests |
| Test: event creation | PASS | 40 domain tests |
| Test: publication | PASS | Publisher tests; live = persisted assertion in integration |
| Test: persistence | PASS | Round-trip, rollback and atomicity tests |
| Test: subscription | PASS | Subscribe, filter, unsubscribe and self-unsubscribe tests |
| Test: event ordering where guaranteed | PASS | Position/cursor test; per-execution order under 6 concurrent runs; the guarantee's limits are documented on `EventRepository.list` |
| Test: event payload validation | PASS | `test_payload_is_closed_and_scalar` (6 cases), scope and malformed tests |

**Phase-prompt requirements:**

| Requirement | Status | Evidence |
|---|---|---|
| Relevant execution/node/project events | PASS | 11 execution/node types + `project.published`; `test_publish_records_project_published`. Only `project.published` among project events (§19). |
| Serialization | PASS | `test_events_round_trip_through_the_store` |
| Correlation IDs | PASS | Asserted on every event of a run (domain + integration); included in subscriber-failure logs |
| Failure handling | PASS | Subscriber errors and timeouts isolated; event-store failure aborts the transaction |
| Duplicate handling | PASS | `test_append_is_idempotent_on_event_id`; consumer idempotency contract documented |
| Domain state correct when consumers fail | PASS | `test_broken_and_hanging_consumers_do_not_affect_the_run` |
| Events are not the canonical state | PASS | The runtime and services read state only from `executions`/`node_executions`; nothing reads events to derive state |
| Usable by later observability / frontend live update | PASS | `Container.events.subscribe(...)` for live delivery; `EventRepository.list(after=...)` for catch-up; stable payload schema with `schema_version` |
| Quality gates pass | PASS | §13 |

No criterion is PARTIAL or FAIL.

---

## 15. Security Considerations

- **Sensitive data and secrets:**
  - Events can structurally not carry node inputs or outputs, config values, error messages or details, or cancellation reasons (closed scalar payloads).
  - `trigger_id` and IDs are validated domain identifiers.
  - `correlation_id` is already constrained by `ExecutionMetadata`: 1–128 printable characters.
- **Logging:**
  - `event_subscriber_failed` logs IDs, the type, the correlation ID and the subscriber qualname. It does not log the payload.
  - `exc_info` is attached for server-side diagnosis. A subscriber's own exception message could contain data the subscriber put there. Consumers are in-process trusted code, and this matches the existing unhandled-error logging in `api/app.py`.
- **Integrity:**
  - `position` is `GENERATED ALWAYS`, so it cannot be set explicitly (tested).
  - `event_id` is unique.
  - The table has no update or delete path in the repository protocol (append-only by contract). A database role with UPDATE/DELETE privileges could still alter rows; database-level immutability (permissions or triggers) was not added.
- **Denial of service:** a hanging subscriber is bounded by `subscriber_timeout`. Unbounded event-table growth is discussed in §20.
- **Authentication, authorization, API exposure:** no new endpoints. Access control for reading events is future work, together with the endpoint (Phase 20 auth).
- **SSRF, path traversal, XSS, CSRF, expressions, plugins:** not affected.

---

## 16. Performance Considerations

- **Write amplification per save:** one extra `INSERT` per recorded event.
  - A typical runtime save records 1–3 events.
  - A terminal cascade records one `node.cancelled` per unfinished node plus the terminal event, i.e. N+1 round trips.
  - This is marked with a `ponytail:` comment in `SqlAlchemyEventRepository.append`, with the upgrade path "batch when large graphs make it show".
- **Publisher latency:** delivery is inline. Each event adds the sum of the subscriber durations to the producer, bounded by `subscriber_timeout` per subscriber and event. With no subscribers the cost is a loop over an empty list. This is marked with a `ponytail:` comment in `EventPublisher.publish`.
- **Reads:** `(execution_id, position)` and `(project_id, position)` indexes serve the stream-cursor query. An unfiltered `list()` scans by the primary key.
- **Growth:** the table grows by roughly (2 × nodes + 3) rows per execution. There is no retention yet.
- **Measurements:** none were taken. The full suite runtime went from about 36 s to about 38–40 s, which reflects the added tests rather than a benchmark. No performance claims are made.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §19 | Record the implemented design | "Implementation (Phase 11)" subsection: event model and closed payloads, aggregate recording, transactional append and idempotency, ordering guarantee and its limit, after-commit at-most-once delivery with isolation, catch-up via `list(after=position)`, canonical state remains the execution rows |
| `docs/reports/backend/phase-11-event-system.md` | Phase report | This document |
| Module docstrings | Maintainer knowledge | `domain/event.py`, `application/events.py` (delivery contract), `domain/execution.py` (which transitions are evented and which are not), `runtime/runtime.py` (event rule), `application/persistence.py` (`EventRepository` contract), `0005_events.py` |

**Should be updated in a future phase:**
- architecture §18 still lists `project_version_id` (the implementation uses `project_version`, see §6);
- the WebSocket event schema section (architecture "Event Stream") once live updates exist;
- retention policy documentation when event retention is implemented.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded (`pyproject.toml` and `uv.lock` unchanged).
- **Configuration and environment variables:** none. `subscriber_timeout` is a constructor argument with a default (5.0 s) and is not exposed in `Settings`.
- **Database:** migration `0005_events` (new table plus 2 indexes); additive.
- **Docker, Redis, external services:** none.
- **Test infrastructure:**
  - the `clean_db` fixture truncates `events`;
  - the migration head constant was updated;
  - Testcontainers PostgreSQL image unchanged (`postgres:17.2`).

---

## 19. Deferred Work

| Item | Why deferred | Intended phase | Affects Phase 11 completion? |
|---|---|---|---|
| HTTP/WebSocket access to events (e.g. `GET /api/v1/executions/{id}/events`, live stream) | Not in Phase 11 scope; needs auth and connection management | Frontend live execution / Phase 20 (auth) | No |
| Metrics and audit subscribers | Observability is Phase 12 | 12 | No |
| Outbox relay / cross-process fan-out (Redis) | Only needed with workers; at-most-once in-process is sufficient now | 22 | No |
| Events for `retry_node`, `pause`, `wait`, `resume`, `mark_node_ready`, `enqueue` | No runtime path drives retry/pause/wait/resume yet; `enqueue` is folded into `execution.created`; readiness is internal scheduling detail | 23 (retries/recovery) | No: the roadmap does not list these events |
| Additional project events (created, draft updated, deleted) | Not required; `project.published` is the lifecycle event executions depend on | Unassigned | No |
| Event retention/pruning | No retention framework exists yet | Later operational phase | No |
| Passing `container.events` into `ExecutionRuntime` from the container | The runtime is not yet constructed by `create_container` | 21/22 | No |

---

## 20. Known Limitations

1. **At-most-once live delivery.** If the process dies after commit and before `publish`, subscribers miss the notification. The event itself is persisted.
2. **Inline subscribers.** A slow subscriber delays the execution by up to `subscriber_timeout` (5 s default) per event and subscriber. Live-update consumers must only enqueue.
3. **No global order.** `position` orders events within one execution or one project. Across streams, a global `after` cursor can skip concurrently committed events.
4. **Timeout does not stop the subscriber's side effects.** The timeout cancels the awaited coroutine. A subscriber that spawned its own tasks or threads is not stopped.
5. **Not every transition is evented** (see §19). Pause, wait, resume and retry states would currently not appear in the event log if some code drove them.
6. **Executions created before migration `0005_events` have no events.** Their lifecycle is available only from state.
7. **Append-only by contract, not by database permission.** A role with UPDATE/DELETE privileges on `events` could alter history.
8. **The event store is on the write path.** If the `events` insert fails, the state transition fails with it. This is by design (atomicity), but the event table's health now affects execution progress.
9. **`project.published` is recorded by the service, not the `Project` aggregate** (see §5.5).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target phase |
|---|---|---|---|---|---|
| Runtime not wired to `Container.events` | Runtime events would reach only a private publisher if the runtime were constructed without `events=` | MEDIUM | Composition | Pass `events=container.events` when the container constructs the runtime or worker | 21/22 |
| Architecture §18 names `project_version_id`; the implementation uses the version number | Documentation inconsistency | LOW | Docs | Update §18 or add the id if a consumer needs it | 12 or frontend live phase |
| Pre-existing: in-memory `revision` advanced before commit (F4) | Stale aggregate after a failed commit | LOW | Persistence | Review together with recovery semantics | 23 |
| Transient Docker ping (F5) | Integration tests may be skipped silently in a slow environment | LOW | Test infrastructure | Consider a longer ping timeout or failing in CI when Docker is expected | CI hardening |

---

## 22. Architectural / Product Decisions

1. **Execution events are recorded inside `Execution` transitions**, not by the runtime. This guarantees completeness, including the terminal cascade. Do not emit execution events from runtime code.
2. **Events are appended in the state's transaction** via `save_execution`. Never save an `Execution` with `uow.executions.save` alone in production code, or its events stay unpersisted.
3. **Publish only after commit.** Consumers may safely read the database for the state an event describes. Publishing earlier would make database-reading consumers block on the writer's locks (F3).
4. **Closed, scalar payloads** are a security boundary. Extending a payload is an explicit, reviewed change to `PAYLOAD_KEYS`.
5. **No cancellation reason, error message or error details in events.** They are caller-supplied free text; the canonical state keeps them.
6. **`position` is the stream cursor; `event_id` is the deduplication key.** Consumers must be idempotent on `event_id`.
7. **No foreign keys on `events`.** History must stay readable if the described rows are pruned by future retention.
8. **`execution.timed_out` and `node.cancelled` were added** beyond the roadmap list. Without them a timed-out run, or nodes stopped by a terminal cascade, could not be reconstructed from events.
9. **`EventPublisher` is a concrete class, not a protocol.** There is one implementation. Replace it with a protocol when a second (e.g. Redis-backed) one exists.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **Base commit:** `dd7cb18 backend phase 10 - trigger system`.
- **Working tree:** uncommitted Phase 11 changes; nothing staged or committed during this phase.
- **Modified (13):**
  - `backend/src/akasha/application/{executions,persistence,projects}.py`
  - `backend/src/akasha/bootstrap.py`
  - `backend/src/akasha/domain/execution.py`
  - `backend/src/akasha/infrastructure/{db_models,persistence}.py`
  - `backend/src/akasha/runtime/runtime.py`
  - `backend/tests/application/test_trigger_service.py`
  - `backend/tests/integration/{conftest,test_persistence}.py`
  - `backend/tests/test_lifecycle.py`
  - `docs/architecture.md`
- **Untracked (7):**
  - `backend/migrations/versions/0005_events.py`
  - `backend/src/akasha/application/events.py`
  - `backend/src/akasha/domain/event.py`
  - `backend/tests/application/test_event_publisher.py`
  - `backend/tests/domain/test_event.py`
  - `backend/tests/integration/test_events.py`
  - `docs/reports/backend/phase-11-event-system.md`
- **Unrelated changes:** none detected. The tree was clean at the start of the phase.
- The mutation checks modified source files temporarily and restored each from a backup copy. `git status` afterwards showed only the files listed above.

---

## 24. Phase Completion Assessment

### Implemented
- `Event`, `EventType`, the closed payload schema;
- `EventPublisher`, `EventSubscriber`, `EventRepository` and its SQLAlchemy implementation;
- migration `0005_events`;
- lifecycle recording in the `Execution` aggregate (11 execution/node event types);
- `project.published`;
- transactional append;
- after-commit, isolated publication;
- container wiring for the services.

### Verified
- 64 new tests (40 unit, 9 publisher, 15 PostgreSQL integration) and 841 pre-existing tests: 905/905 passed, 0 skipped.
- 8 mutations killed.
- Format, lint, strict mypy and build pass.

### Not Implemented
- Event access over HTTP or WebSocket.
- Events for retry, pause, wait and resume.
- Project events other than `published`.

### Deferred
See §19. None are in the Phase 11 roadmap scope.

### Open
See §21: runtime-to-container wiring (21/22), architecture §18 field naming, the pre-existing F4, and Docker-ping robustness.

### Blocking Issues
None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase

**What Phase 12 (Observability) and later phases can assume:**
- Every execution and node lifecycle transition driven by the runtime produces a persisted `Event`. It is atomic with the state and carries `correlation_id`, `project_id`, `project_version`, `execution_id` and, for node events, `node_id`.
- `container.events.subscribe(callback, event_types=[...])` delivers events in order after commit. It is suitable for metrics counters (execution/node success and failure counts, and durations derived from start/finish events of the same id).
- `uow.events.list(execution_id=..., after=position, limit=...)` returns the ordered history of one execution for catch-up and inspection.

**Contracts:**
- the `PAYLOAD_KEYS` table (§6) and `schema_version = 1`;
- `EventRepository` (append-only, idempotent);
- `EventPublisher` (at-most-once, isolated, inline);
- `save_execution`.

**Constraints:**
- Subscribers must be fast (only enqueue), idempotent on `event_id`, and must not depend on delivery.
- Metrics labels must not be built from `payload` values beyond bounded enums such as `error_kind` (AGENTS §41). `error_code` is bounded by node implementations, not by users.
- Do not use events to derive execution state. The `executions`/`node_executions` rows remain canonical.

**Must not be changed casually:**
- the after-commit publish order;
- the transactional append in `save_execution`;
- the closed payload schema;
- `position` as `GENERATED ALWAYS` identity with per-event ordered inserts;
- the absence of free-text fields in events.

**Prerequisites and open items for next phases:**
- Pass `events=container.events` when the container starts constructing `ExecutionRuntime` or workers.
- Resolve the §18 `project_version_id` naming when the first external consumer (WebSocket) is designed.
- For multi-process workers (Phase 22), add an outbox relay that reads `events` by position, because in-process publication does not cross processes.
