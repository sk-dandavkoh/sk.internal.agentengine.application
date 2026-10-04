# Phase 15 — Execution Inspector + Logs

Report date: 2026-10-04. Branch `main`. Base commit: `afdfda6` (frontend phase 14 — live execution and websocket).

Everything described here is uncommitted in the working tree (§23). The phase had two parts:
- the phase implementation;
- a pre-Phase-16 follow-up, done in the same session at the user's request (§11, "Follow-up").

Both are covered here.

## 1. Executive Summary

**Supposed to achieve.** Roadmap §21 (`docs/roadmap-frontend.md`, "Phase 15 — Execution Inspector + Logs"):
- **Goal:** "Executions detailliert untersuchen können" (inspect executions in detail).
- **Execution Inspector scope:** Overview, Graph, Nodes, Logs, Errors, Events, Metrics, Cost.
- **Node Inspector scope:** Status, Inputs, Outputs, Duration, Retry Count, Error, Logs, Token Usage, Cost.
- **Acceptance:**
  - a failed node can be investigated comprehensibly;
  - sensitive data is not shown, per the backend redaction.

The session prompt added:
- explicit display requirements: metadata, version, trigger, states, timings, errors, retries, logs, events, safe outputs;
- security rules: never render secrets, treat log/LLM output as untrusted, no raw HTML;
- a test list: successful run, failed run, node failure, retries, logs, large logs, missing data, secret redaction, untrusted output rendering;
- the instruction to implement and test any missing backend API.

**Discovered at the start.** The backend did not persist node logs. A node's `context.logger` lines went only to the process log (stdout JSON). The phase therefore needed backend log persistence first.

Everything else the inspector needed already existed in the execution report (`GET /reports/executions/{id}`): node states, timings, errors, the retry history (`failures`), outputs, usage, cost and the event timeline.

**Actually implemented.**
- **Backend node log history.** Implemented in:
  - `akasha.runtime.node_logs` (capture);
  - a `NodeLogEntry` value plus aggregate recording in `akasha.domain.execution`;
  - a `node_logs` table with migration `0012_node_logs`;
  - `NodeLogRepository` (repository);
  - `ExecutionService.list_logs` (service);
  - `GET /api/v1/executions/{id}/logs` (API);
  - backup/restore support.
- **Frontend Execution Inspector.** `/executions/:id` became a tabbed inspector (Overview, Graph, Nodes, Logs, Errors, Events) with a per-node inspector. It is split into `frontend/src/features/executions/inspector/`.
- **Follow-up:**
  - live refetch throttle;
  - attempt numbers on failed attempts (report field);
  - log text search (`q`), required by `frontend/AGENTS.md` §44 and missed in the first pass;
  - a full-stack E2E suite that now runs real executions (Redis + worker).

**Resulting state.**
- A user can open a failed execution and see which node failed, with which error code and message.
- They can also see the retried attempts with their numbers and errors, and the node's outputs.
- They can see the lines the node logged (filterable by level, node and text), and the graph with each node's state.
- No backend access is needed for any of this.
- Secrets appear as `[REDACTED]`, as the backend sends them.
- Untrusted text is rendered as text only. This is verified in jsdom and in a real browser against the real backend.

**Acceptance criteria.**
- Both roadmap acceptance criteria pass (§14).
- One scope item is not implemented: **node Inputs**. The backend does not persist node inputs, and adding input persistence was judged out of proportion for this phase (§19).
- Metrics and Cost are sections of the Overview tab rather than their own tabs.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 2. Phase Objective

Roadmap §21 objective: inspect executions in detail. The OpenCode prompt adds:
- "Node Execution Details müssen nachvollziehbar dargestellt werden" (node execution details must be shown comprehensibly);
- "Keine Secrets oder redigierte Daten rekonstruieren" (do not reconstruct secrets or redacted data);
- "STOP nach Phase 15" (stop after Phase 15).

`frontend/AGENTS.md` sections that apply:
- §42 Execution Inspector: metadata, graph, node states, inputs, outputs, logs, errors, timings, retries, token usage;
- §43 secret-safe outputs, large outputs;
- §44 Logs UI: filterable, time-sortable, attributable to execution/node, searchable, secret-safe, pagination/virtualization for large volumes;
- §61 large data;
- §64/§65 untrusted user-generated and LLM content;
- §100 accessible alternatives to the graph;
- §101 the execution graph visualizes backend state and never simulates the runtime.

**Not responsible for:**
- system-wide monitoring (Phase 16);
- reports UI and downloads (later phase);
- the login UI (Phase 21);
- live per-line log streaming;
- log retention.

## 3. Scope Implemented

### Backend / Domain
- `LogLevel` (StrEnum, ordered by severity, `at_least()`) and `NodeLogEntry`, a frozen value object in `backend/src/akasha/domain/execution.py`. `NodeLogEntry` validates:
  - the ids;
  - `attempt >= 1`;
  - a timezone-aware time;
  - storable JSON fields.

  Unstorable characters in messages are replaced through the existing `storable_text`.
- Aggregate methods on `Execution`:
  - `record_node_logs(node_id, lines)`, allowed only while the node is RUNNING, mirroring `record_node_event`;
  - `pending_logs`;
  - `pull_logs()`.
- Follow-up: a new rehydration invariant in `NodeExecution.__post_init__`. It rejects `len(failures) < attempt - 1`, i.e. an earlier attempt without a recorded failure (§5, D4).

### Backend / Runtime
- `backend/src/akasha/runtime/node_logs.py`:
  - `NodeLogCapture` (per-attempt, bounded, redacting);
  - the `capturing()` context manager;
  - a module-level `_CaptureHandler` installed once on the `akasha.nodes` logger;
  - limits `MAX_LINES_PER_ATTEMPT = 200`, `MAX_MESSAGE_CHARS = 4_000`, `MAX_FIELDS_BYTES = 8 * 1024`.
- `backend/src/akasha/runtime/runtime.py`:
  - `_launch` creates a capture per attempt and creates the node task inside `capturing(logs)`;
  - `_Attempt` gained `logs`;
  - `_flush` calls `execution.record_node_logs(...)`;
  - `StateManager._committed` pulls logs after commit.

### Backend / Persistence
- New table `node_logs` (`db_models.py`, migration `backend/migrations/versions/0012_node_logs.py`).
- `NodeLogRepository` protocol (`application/persistence.py`) and `SqlAlchemyNodeLogRepository` (`infrastructure/persistence.py`), wired into `SqlAlchemyUnitOfWork.node_logs`.
- `save_execution` (`application/executions.py`) appends `execution.pending_logs` in the same transaction as the aggregate and its events.
- `infrastructure/backup.py`: identity-position restore generalized from `events` to `_ORDERED = ("events", "node_logs")`. The sequence reset uses a SQLAlchemy `func.setval` expression, not string SQL (§11).

### Backend / API
- `GET /api/v1/executions/{execution_id}/logs?after=&limit=&node_id=&level=&q=`, implemented with `LogLineOut` and `LogPage` in `api/executions.py`.
- `ExecutionService.list_logs(..., node_id, min_level, contains)`.
- Follow-up: the report's `NodeReport.failures` items are now `FailureReport` (an `ErrorReport` plus `attempt`) in `application/reports.py`.

### Backend / Observability
- `record_extras(record)` was extracted in `observability/logging.py`. It is shared by `JsonFormatter` and the log capture, so both agree on which record attributes are user `extra`.

### Frontend / API layer
- `frontend/src/api/executions.ts`:
  - `logLevelSchema`, `logLineSchema`, `logPageSchema` (zod);
  - the `LogFilter` type (`node_id`, `level`, `q`);
  - `listExecutionLogs()`.
- `frontend/src/api/queries.ts`:
  - `queryKeys.executions.logs(id, filter)`, which nests under `['executions', id]` so existing invalidations reach it;
  - `executionQueries.logs()` as `infiniteQueryOptions`, cursor `next_after`;
  - `LOG_PAGE_SIZE = 100`, the backend's `MAX_PAGE_SIZE`.
- `frontend/src/api/reports.ts`: `nodeFailureSchema` (follow-up).
- `frontend/src/api/openapi.json`: regenerated snapshot.

### Frontend / UI (`frontend/src/features/executions/`)
- `ExecutionDetailPage.tsx`, rewritten:
  - the failure card above the tabs;
  - the live indicator;
  - the `Tabs` with tab and node selection kept in the URL (`?tab=&node=`, `useSearchParams`, `replace: true`);
  - per-tab report loading through `QuerySection`.
- `inspector/OverviewPanel.tsx`:
  - Details;
  - Usage (tokens, cost, attempts/retries/failed attempts, node status counts, cancellation);
  - Run snapshot (engine version, version publish time and description, node versions, redacted input).
- `inspector/FlowPanels.tsx`:
  - `GraphPanel`: the pinned version graph via `SnapshotCanvas`, with a status/retries/duration footer per node; clicking a node opens it in Nodes;
  - `NodesPanel`: node table and the inspector for the selected node.
- `inspector/NodeInspector.tsx`:
  - status, attempts/retries, duration, timestamps, tokens, cost;
  - error, retried failures with attempt numbers, outputs;
  - the node's logs with a level filter.
- `inspector/HistoryPanels.tsx`:
  - `LogsPanel` (level, node and "Message contains" search);
  - `ErrorsPanel` (execution error plus every failed attempt per node, "Inspect" jump);
  - `EventsPanel` (report timeline, 200 at a time, truncation notice).
- `inspector/LogList.tsx`: an infinite-query list with "Load more lines", level badges, collapsible fields, and empty/error/partial states. Also `LevelFilter`.
- `inspector/display.tsx`: `NodeStatusBadge`, `UntrustedText` (20,000-character render cap with Show all/less), `JsonBlock`, `ErrorText`, `Item`, `Mono`, `None`.
- `inspector/format.ts`: `nodeStatusDisplay`, `ms`, `MAX_RENDERED_CHARS`, `EVENT_STEP`, `MAX_SEARCH_CHARS`, `nodeNamer`.
- `useLiveExecution.ts`:
  - `describeEvent` accepts report events and a node namer;
  - follow-up: a reconcile throttle (`RECONCILE_INTERVAL_MS = 1_000`).
- Shared components extended:
  - `editor/NodeCard.tsx`: optional `footer`;
  - `versions/SnapshotCanvas.tsx`: optional `label`, `footer(nodeId)`, `onNodeClick`;
  - `dashboard/format.ts`: `formatMilliseconds` extracted from `formatDuration`.

### Testing / Infrastructure
- New backend unit, integration and API tests, a frontend component test file, a throttle test, and a full-stack E2E journey (§12).
- `frontend/tests/fullstack/backend.sh` now starts Redis and runs the in-process worker (§18).

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/migrations/versions/0012_node_logs.py` | Created | `node_logs` table, index, check constraints |
| `backend/src/akasha/runtime/node_logs.py` | Created | Per-attempt log capture, bounds, redaction |
| `backend/src/akasha/domain/execution.py` | Modified | `LogLevel`, `NodeLogEntry`, `record_node_logs`/`pending_logs`/`pull_logs`; failure/attempt invariant |
| `backend/src/akasha/runtime/runtime.py` | Modified | Capture per attempt in `_launch`, record in `_flush`, pull in `_committed` |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | `node_logs` table definition |
| `backend/src/akasha/application/persistence.py` | Modified | `NodeLogRepository` protocol, `UnitOfWork.node_logs` |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | `SqlAlchemyNodeLogRepository`, UoW wiring |
| `backend/src/akasha/application/executions.py` | Modified | `save_execution` appends logs; `ExecutionService.list_logs` |
| `backend/src/akasha/api/executions.py` | Modified | `GET /executions/{id}/logs`, `LogLineOut`, `LogPage` |
| `backend/src/akasha/application/reports.py` | Modified | `FailureReport` with derived `attempt` |
| `backend/src/akasha/infrastructure/backup.py` | Modified | Restore identity positions of `events` and `node_logs` |
| `backend/src/akasha/observability/logging.py` | Modified | `record_extras` shared helper |
| `backend/tests/runtime/test_node_logs.py` | Created | 13 capture/value-object unit tests |
| `backend/tests/integration/test_node_logs.py` | Created | 3 runtime+PostgreSQL tests (retries, failed/cancelled, large logs) |
| `backend/tests/integration/test_executions_api.py` | Modified | Sink node logs; 3 logs API tests |
| `backend/tests/domain/test_execution.py` | Modified | 2 log recording tests, 1 invariant test |
| `backend/tests/integration/test_cli.py` | Modified | Backup→restore round trip includes node logs |
| `backend/tests/integration/test_persistence.py` | Modified | Migration head `0012_node_logs` |
| `backend/tests/integration/test_reports.py` | Modified | Asserts `attempt` on failures |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeNodeLogs` for the fake UoW |
| `frontend/src/features/executions/inspector/` | Created (directory, 7 files) | Inspector panels and display helpers |
| `frontend/src/features/executions/ExecutionDetailPage.tsx` | Modified (rewrite) | Tabbed inspector shell |
| `frontend/src/features/executions/ExecutionInspector.test.tsx` | Created | 10 component tests |
| `frontend/src/features/executions/ExecutionsPage.test.tsx` | Modified | Throttle test |
| `frontend/src/features/executions/useLiveExecution.ts` | Modified | Throttle; `describeEvent` generalized |
| `frontend/src/api/executions.ts`, `queries.ts`, `reports.ts` | Modified | Logs contract, infinite query, failure schema |
| `frontend/src/api/openapi.json` | Modified (generated) | Snapshot regenerated by `test_openapi_contract.py` |
| `frontend/src/api/contract.node.test.ts` | Modified | Records `listExecutionLogs` for the contract check |
| `frontend/src/features/editor/NodeCard.tsx` | Modified | `footer` prop |
| `frontend/src/features/versions/SnapshotCanvas.tsx` | Modified | `label`, `footer`, `onNodeClick` |
| `frontend/src/features/dashboard/format.ts` | Modified | `formatMilliseconds` |
| `frontend/tests/fullstack/backend.sh` | Modified | Redis container, worker on, `AKASHA_LOG_LEVEL=INFO` |
| `frontend/tests/fullstack/projects.spec.ts` | Modified | `publishChain`/`publishSlow`; 2 tests adapted; 1 new inspector journey |
| `frontend/tests/fullstack/teardown.ts` | Modified | Comment: removes both containers |
| `docs/architecture.md` | Modified | §22 endpoint list, §52 node log history, §55 failure attempts |
| `docs/sdk.md` | Modified | §4 Logging: stored lines and their limits |
| `frontend/README.md` | Modified | Execution inspector section, throttle, full-stack setup; stale line removed |

No files were deleted.

## 5. Architecture Changes

### D1 — Node logs are a persisted history separate from events
- **Context.** Node logs were process-log-only. Events (`domain/event.py`) are closed scalar payloads by design ("node inputs/outputs, config values and free-text reasons … can structurally not carry them"). Log messages are free text.
- **Chosen approach.** A dedicated append-only `node_logs` table. Rows commit in the same transaction as the attempt outcome (`save_execution`) and are ordered by an identity `position`.
- **Alternatives considered.**
  - A `node.log` event type: rejected, because it breaks the closed-payload security rule.
  - A per-call-site API such as `context.log(...)`: rejected, because node authors already use `context.logger` (SDK §4).
  - Reading the process log: not possible, there is no log store.
- **Consequences.**
  - Logs commit with the outcome they belong to, never earlier or later.
  - Logs are not streamed over the WebSocket. The UI refetches them when events arrive, which coincide with the attempt's outcome events.
  - Documented in `docs/architecture.md` §52.

### D2 — Capture via a ContextVar-routed handler on `akasha.nodes`
- **Context.** `context.logger` is the shared `logging.getLogger("akasha.nodes.<type>")`. Attempts run concurrently. Node tasks must not touch the aggregate (runtime single-writer rule).
- **Chosen approach.** One handler on `akasha.nodes`, installed once at module import, forwards each record to the `NodeLogCapture` held in a `ContextVar`. The runtime sets that variable when it creates the node task, so the task copies it, the same mechanism `log_context` and known-secret masking already use. The capture is flushed once per attempt in `_flush`, like `NodeEvents`.
- **Alternatives considered.** An unregistered per-attempt `logging.Logger` with a manually set `parent`: rejected, because it relies on logging internals.
- **Consequences.**
  - Only records from the `akasha.nodes.*` hierarchy are captured; library loggers are not.
  - Records below the logger's effective level are dropped before any handler runs, so lines below the process log level are not stored.
  - Both are documented in §52 and `docs/sdk.md`.

### D3 — Redaction at capture time, inside the node task
- **Context.** Credential values resolved by a node are masked exactly only within that node's task context (`_known_secrets` ContextVar, architecture §32).
- **Chosen approach.** `NodeLogCapture.add` runs synchronously in the emitting task and applies `redact_text` and `redact`, which include the known-value masking. The API redacts again on output (`LogLineOut.from_domain`), like `EventOut`.
- **Consequences.** Plaintext never reaches the database, even for shapeless secrets resolved from the credential store.

### D4 — Failure attempt numbers are derived, not stored (follow-up)
- **Context.** The UI wanted to say "Attempt n" for each recorded failure. `ExecutionError` carries no attempt.
- **Analysis.** In `NODE_ACTIONS`, `retry` (RUNNING→READY) is the only transition back to READY, and `NodeExecution.retry` appends a failure. `start` is only legal from READY. Every non-final attempt therefore ended in a recorded failure, and the n-th failure ended attempt n.
- **Chosen approach.**
  - `FailureReport.attempt` is computed in `build_report`.
  - `NodeExecution.__post_init__` now rejects rehydrated state with `len(failures) < attempt - 1`, so the derivation's precondition is enforced on load.
- **Alternatives considered.** Persisting `attempt` on each failure: it would need a JSONB schema change and a migration for no extra information.
- **Consequences.** The report field is additive; `REPORT_SCHEMA_VERSION` stays 1, per that constant's own comment. If a future transition allows READY without a failure, both the invariant and the derivation must change together.

### D5 — Inspector navigation state in the URL
Tab and selected node live in `?tab=&node=` (`frontend/AGENTS.md` §99), so an inspected node is a shareable link. Only ids go into the URL, never data.

## 6. Domain / Data Model Changes

- **New value object:** `NodeLogEntry(execution_id, node_id, attempt, level, message, logged_at, fields, position)`.
- **New enum:** `LogLevel` with `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
- **Aggregate:**
  - `Execution._logs` (not persisted state; pending until the next save);
  - `record_node_logs`, `pending_logs`, `pull_logs`.
- **Invariant (new):** `NodeExecution`: `len(failures) >= attempt - 1`.
  - Valid persisted data always satisfies it: running/completed/cancelled nodes have `attempt - 1` failures, failed nodes have `attempt` failures, pending nodes have 0 and 0.
  - All 1,948 backend tests and the domain property tests pass with it.
- **Table `node_logs`:**

  | Column | Type | Notes |
  |---|---|---|
  | `position` | BIGINT | `GENERATED ALWAYS AS IDENTITY`, primary key |
  | `execution_id` | VARCHAR | FK `executions.id` `ON DELETE CASCADE` |
  | `node_id` | VARCHAR | |
  | `attempt` | INTEGER | |
  | `level` | VARCHAR | |
  | `message` | VARCHAR | |
  | `logged_at` | TIMESTAMPTZ | |
  | `fields` | JSONB | |

  Constraints and indexes:
  - `ck_node_logs_attempt` (`attempt >= 1`);
  - `ck_node_logs_level` (the five level names);
  - index `ix_node_logs_execution_position (execution_id, position)`.
- **Compatibility.** Executions that ran before `0012` have no stored lines; the UI says "No log lines were recorded." The migration's downgrade drops the table.

## 7. API / Contract Changes

### `GET /api/v1/executions/{execution_id}/logs` (new)

**Query parameters:**

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `after` | int ≥ 0 | 0 | Cursor: return lines with `position > after` |
| `limit` | 1–100 | 100 | Page size |
| `node_id` | string, optional | | Only lines of this node |
| `level` | `DEBUG` … `CRITICAL` | `DEBUG` | Minimum level |
| `q` | string 1–200, optional | | Literal, case-insensitive substring of the message; `%` and `_` are escaped (`icontains(..., autoescape=True)`); fields are not searched |

**Response:** `LogPage {items: LogLineOut[], next_after, has_more}`.
- `LogLineOut` is `{position, node_id, attempt, level, message, logged_at, fields}`.
- Paging works exactly like `GET …/events`.

**Errors:**

| Status | Code | When |
|---|---|---|
| 401 | `AUTHENTICATION_REQUIRED` | No session |
| 404 | `EXECUTION_NOT_FOUND` | Unknown execution, and an execution in a project the caller cannot see (`authorize_path`) |
| 422 | | Bad `level`, `limit`, `after` or `q` |

**Compatibility:** additive.

### `GET /api/v1/reports/executions/{id}` (changed, additive)
`nodes[].failures[]` items gain `attempt: int` (`FailureReport`). `schema_version` stays 1.

### Frontend contract
- `openapi.json` was regenerated (`AKASHA_UPDATE_OPENAPI_SNAPSHOT=1`).
- `contract.node.test.ts` records `listExecutionLogs(…, {after, node_id, level, q, limit})` and checks the zod schemas against the snapshot (PASS).
- No WebSocket, event, node, plugin or SDK API changes. The SDK *behavior* changed: `context.logger` lines are now also stored (documented in `docs/sdk.md`).

## 8. Implementation Details

### Capture flow
1. `_launch` builds `NodeLogCapture(self._clock)`.
2. Inside `with capturing(logs), log_context(...)`, it calls `create_task(execute_node(...))`.
3. Node code calls `context.logger.warning(...)`. The record propagates to `akasha.nodes`, where `_CaptureHandler.emit` reads the ContextVar.
4. `NodeLogCapture.add`:
   - formats the message, redacts it and truncates it at 4,000 characters (`" […truncated]"`);
   - collects `record_extras` minus `execution_id`/`node_execution_id`/`node_id`, adds `exception` (type name only), redacts, and replaces the fields with `{"fields_omitted": …}` if they are non-storable or over 8 KiB;
   - appends `(level, message, clock(), fields)`;
   - beyond 200 lines, counts drops instead.
5. `lines()` adds a closing WARNING "N further log lines … were dropped" when anything was dropped.
6. `_flush` (also for failed and cancelled attempts) calls `record_node_logs` while the node is still RUNNING.
7. The next `StateManager.save` writes the logs via `save_execution`, and `_committed` pulls them.

Malformed records (bad `%` args) are caught in `emit` and passed to `Handler.handleError`, so the node is not failed.

### Ordering
`SqlAlchemyNodeLogRepository.append` uses executemany in list order, so identity values follow the recorded order. One CAS-guarded writer per execution (runtime single-writer rule) keeps an execution's positions in order.

### Frontend data flow
- `ExecutionView` holds:
  - `executionQueries.report` (one query, shared by all tabs);
  - `useLiveExecution`;
  - the URL state.
- Tabs render only the selected panel (`Tabs` filters items), so the Graph (`projectQueries.version`, immutable, `staleTime: Infinity`) and the logs queries load only when their tab is opened.
- `LogList` uses `useInfiniteQuery` with key `['executions', id, 'logs', filter]`, so a filter change is a new query.
- Live reconciliation invalidates `['executions', id]`, which covers detail, report, events and logs.

### Throttle (follow-up)
`reconcile()` behaves as a leading-edge throttle:
- if no window is open, it refetches immediately and opens a 1 s window;
- calls inside the window set `pending`, and the window's end refetches once if pending;
- `onEnd` clears the window and refetches immediately;
- the effect cleanup clears the timer.

### Untrusted rendering
All node, log and LLM text goes through React text nodes (`UntrustedText`, `JsonBlock` via `JSON.stringify`, `ErrorText`). There is:
- no `dangerouslySetInnerHTML`;
- no Markdown;
- no auto-linking.

### Search UX
The search input applies on form submit (`role="search"`), not per keystroke; clearing the box resets the search.

## 9. Findings

### F1 — Backend did not persist node logs
- **Impact:** HIGH (the Logs requirement was not achievable).
- **Resolution:** FIXED (D1–D3).
- **Notes:** the backend `docs/sdk.md` §4 said only that output is redacted centrally; nothing stored it.

### F2 — Stored logs depend on the process log level
- **Impact:** MEDIUM.
- **Resolution:** DEFERRED / documented.
- **Notes:** Python logging drops records below a logger's effective level before handlers run. With `AKASHA_LOG_LEVEL=WARNING`, `info` lines of nodes are not stored. Fixing it means giving `akasha.nodes` its own level and filtering the stream handler, which changes process-log behavior. The full-stack backend sets `AKASHA_LOG_LEVEL=INFO`.

### F3 — Node inputs are not persisted
- **Impact:** MEDIUM (roadmap Node Inspector scope "Inputs", `frontend/AGENTS.md` §42).
- **Resolution:** DEFERRED.
- **Notes:** a node's inputs are the outputs of its upstream nodes, which are shown. Deriving inputs in the frontend would re-implement the runtime's merge/port semantics (`ExecutionPlan.inputs_for`), which `frontend/AGENTS.md` §101 and §106 forbid.

### F4 — Live updates refetched everything on every events frame
- **Impact:** MEDIUM (performance).
- **Resolution:** FIXED (follow-up throttle).
- **Notes:** introduced in Phase 14 and amplified in this phase, because the report (up to 10,000 events plus all outputs) and every loaded log page are refetched on each frame. The backend polls every 0.5 s.

### F5 — Failure attempt numbers derivable from the state machine
- **Impact:** LOW.
- **Resolution:** FIXED (D4).
- **Notes:** the first implementation labelled failures "Failure n" because the correspondence was not yet established.

### F6 — Full-stack E2E never ran an execution
- **Impact:** MEDIUM (test coverage).
- **Resolution:** FIXED (follow-up).
- **Notes:** `backend.sh` set `AKASHA_WORKER_ENABLED=false` and had no Redis, so the full-stack suite could only observe QUEUED runs. `frontend/AGENTS.md` §73 lists "Start Execution → Observe Execution → Inspect Logs" as a required journey.

### F7 — Log search requirement initially missed
- **Impact:** LOW.
- **Resolution:** FIXED (follow-up, `q`).
- **Notes:** `frontend/AGENTS.md` §44 ("durchsuchbar", searchable).

### F8 — The backend refuses secret-shaped strings in node config
- **Impact:** none (confirmation).
- **Resolution:** n/a.
- **Notes:** while seeding a manual test project, a `core.log` message containing `sk-live-…` was rejected with 422 `PLAINTEXT_SECRET`. Secrets can reach logs only through runtime data (inputs), which the capture redacts; that was verified manually.

### F9 — The roadmap's eight tabs vs. the implementation
- **Impact:** LOW.
- **Resolution:** decided.
- **Notes:** Metrics and Cost are Overview sections ("Usage"), not tabs. The report's `MetricsReport` is per execution and small; separate tabs would hold three numbers each.

## 10. Bugs / Problems Found

**Introduced during this phase and fixed before completion** (found by tests, lint or manual runs):

| Problem | Root cause | Fix | Regression test / Area |
|---|---|---|---|
| Malformed-record test failed | pytest's own `LogCaptureHandler` re-raises formatting errors; the first fix disabled propagation on the child logger, which also cut off the `akasha.nodes` handler | Propagation disabled at `akasha.nodes` in the test only | `test_a_malformed_record_does_not_break_the_node` (test code) |
| Capture tests saw no INFO lines | Setting `Logger.level` directly does not clear the logger's enabled-cache | Fixture uses `setLevel` | `tests/runtime/test_node_logs.py` (test code) |
| Integration graphs rejected | Single-node graphs fail validation (`DISCONNECTED_NODE`) | Test graphs get an upstream emitter | `tests/integration/test_node_logs.py` (test code) |
| Log page size 200 rejected by API | Backend `MAX_PAGE_SIZE` is 100 | `LOG_PAGE_SIZE = 100` | `queries.ts`; component test checks paging params |
| Ruff S608 in `backup.py` | f-string SQL for `setval` | `select(func.setval(...))` | Backup round-trip test |
| ESLint fast-refresh errors | Constants exported from component files | Moved to `inspector/format.ts` | Lint |
| Huge-output test race | `findAllByText` resolved before the log line loaded | `waitFor(... toHaveLength(2))` | `ExecutionInspector.test.tsx` |
| Full-stack slow runs failed `INPUT_FIELD_MISSING` | `core.input` in the slow graph, runs started with `{}` | Slow graph starts at `core.delay` | `projects.spec.ts` |
| Full-stack cancel assertion | A RUNNING run's cancel shows "Cancellation requested", not "Execution cancelled" | Assert the request toast, then the persisted "Cancelled" | `projects.spec.ts` |

**Pre-existing, found during this phase:**
- F4: Phase 14 refetch amplification.
- F6: full-stack suite without a worker.
- `frontend/README.md` stated "Logs are the execution events; the backend has no separate log API", which is stale now (removed).

There were no environment problems.

## 11. Fixes Applied

**Phase work (beyond the bug table above):**
- `formatDuration` was refactored into `formatMilliseconds`, so report `duration_ms` values use the same formatting.
- `describeEvent` was generalized to report events and node labels, so the Events tab names nodes by label.

### Follow-up (pre-Phase-16, same session)

1. **Throttle.**
   - Symptom: refetch per events frame (F4).
   - Implementation: `useLiveExecution.ts`.
   - Verification: `ExecutionsPage.test.tsx` "folds a burst of events into at most one refetch per second", where 10 frames lead to exactly one trailing detail read and the end is immediate.
2. **Failure attempts.**
   - Symptom: "Failure n" labels (F5).
   - Implementation: `FailureReport`, the invariant, `nodeFailureSchema`, "Attempt n" labels in `NodeInspector` and `ErrorsPanel`.
   - Verification: `test_reports.py` (attempt asserted), `test_constructor_rejects_an_earlier_attempt_without_failure`, component test asserts "Attempt 1"/"Attempt 2".
3. **Log search.**
   - Symptom: not searchable (F7).
   - Implementation: `q` in repository, service, API and UI.
   - Verification: API test (case-insensitive match; `%`/`_` literal; `q=""` and 201 characters give 422), component test (no request per keystroke; submit sends trimmed `q`), full-stack test (no-match message).
4. **Full-stack executions.**
   - Symptom: no real runs in E2E (F6).
   - Implementation: Redis and worker in `backend.sh`; `publishChain`/`publishSlow`; the two cancel tests now cancel a RUNNING run.
   - Verification: 13/13 passed in two consecutive runs; no containers left behind.

## 12. Testing & Verification

### Unit Tests (backend)
- `tests/runtime/test_node_logs.py` (13 items) covers:
  - level and fields capture; custom level 25 rounds down;
  - nothing captured outside an attempt or from non-node loggers;
  - concurrent attempts isolated;
  - 200-line bound plus the drop note;
  - message truncation; fields over 8 KiB and NaN replaced;
  - pattern and known-value redaction (`register_secret`);
  - exception type only; malformed record tolerated;
  - `LogLevel.at_least`;
  - `NodeLogEntry` validation (attempt, timezone, storable) and NUL replacement.
- `tests/domain/test_execution.py`: logs recorded with the right attempt and pulled; only RUNNING nodes may log; the new invariant rejects a gap.
- Result: PASS.

### Integration Tests (backend, real PostgreSQL via Testcontainers)
- `tests/integration/test_node_logs.py`:
  - a retried node keeps attempt-1 and attempt-2 lines in order, with fields and injected-clock timestamps;
  - failed and cancelled attempts keep their lines;
  - a node logging 250 DEBUG lines stores 201 (200 plus the note); paging with `limit=100` returns all of them; `min_level=WARNING` returns only the note.
- `tests/integration/test_cli.py::test_backup_destroy_restore_run`: a `core.log` line survives backup → truncate → restore; new log positions continue after the restored ones.
- `tests/integration/test_persistence.py`: migrations upgrade/downgrade/upgrade on an empty database at head `0012_node_logs`.
- `tests/integration/test_reports.py`: `failures[].attempt`.
- Result: PASS.

### API Tests (real HTTP stack via httpx ASGI transport + PostgreSQL)
- `tests/integration/test_executions_api.py`:
  - `test_node_logs_are_filterable_and_resumable` (level, node, `q`, cursor paging);
  - `test_node_logs_never_return_secrets` (an `sk-live-…` value in the message and an `api_key` field come back redacted);
  - `test_node_logs_errors_and_visibility` (404 unknown, 422 ×5, member 404, anonymous 401).
- Result: PASS.

### Component Tests (frontend, Vitest + RTL, stubbed `fetch`)
`ExecutionInspector.test.tsx`, 10 tests:
1. successful run: snapshot, node table, inspect via URL, outputs, event timeline with labels, no errors;
2. graph with statuses; node click opens it;
3. failed node: errors tab count, final error and details, retried failures with attempt numbers, node logs requested with `node_id`;
4. log paging (100 + 1), node labels, search on submit, level and node filters sent to the backend;
5. huge output and log line capped at 20,000 characters with toggle; 450 events shown 200 at a time; truncation notice;
6. missing data: pending node, no label, no versions/events/logs;
7. unknown linked node;
8. report or logs failing: error state with Retry;
9. redacted values shown verbatim; `localStorage`/`sessionStorage` empty; URL holds only ids;
10. hostile `<img onerror>`, `<script>`, `<b>`, `javascript:` link in label, outputs, error and log: shown as text, no elements created.

Plus `ExecutionsPage.test.tsx` throttle test. Existing Phase 13/14 tests pass unchanged. Result: PASS.

### E2E Tests
- **Full stack** (`npm run test:fullstack`: production bundle + real backend + PostgreSQL 17.2 + Redis 8.2.1):
  - new "a failed run is diagnosed in the inspector: errors, node, logs":
    - Errors tab shows `DESTINATION_BLOCKED`;
    - Inspect opens the node via URL;
    - log line `<b>bold?</b>` shown as text;
    - input `password` shown as `[REDACTED]` and its value appears nowhere;
    - search no-match message;
    - graph shows 1 Failed and 2 Completed;
  - adapted: UI run cancelled while RUNNING; live follow across a dropped connection with cancel while RUNNING;
  - 13 passed, twice.
- **Mocked UI** (`npm run test:e2e`): 23 passed.

### Manual Verification (built-in browser, real local stack)
Backend with worker, PostgreSQL and Redis containers, Vite dev server. A run that logs and fails was inspected in each tab. Checked:
- no `img`/`script` elements in `main`;
- browser storage empty;
- no console errors;
- 375 px width without horizontal overflow.

The stack and containers were removed afterwards.

### Security Tests
Redaction (unit, API, full-stack, manual), untrusted rendering (component, full-stack, manual), authorization of the logs endpoint (API). Result: PASS.

### Accessibility Tests
Not performed as a dedicated check: no axe or automated a11y tool ran. What exists:
- tests query by role and accessible name (tabs, tablist, regions, lists, buttons with names starting with their visible text, labelled selects and search input);
- status is shown as text badges, not color only.

### Performance Tests
No benchmarks or measurements were performed. Only the throttle behavior and the render caps are tested functionally.

## 13. Quality Gates

Final results after the follow-up:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `ruff format --check` (201 files); `prettier --check` |
| Linter | PASS | `ruff check`; `eslint .` (earlier S608 and fast-refresh failures fixed, §10) |
| Type Checker | PASS | `mypy .` (196 files); `tsc -b` |
| Unit Tests | PASS | Backend inside the full suite; frontend `vitest run` 366 passed (29 files) |
| Integration Tests | PASS | Backend `pytest`: 1948 passed, 6 skipped (the skips existed before this phase) |
| API Tests | PASS | Within the backend suite |
| E2E Tests | PASS | Full stack 13/13 (twice); mocked 23/23 |
| Build | PASS | `npm run build` |
| Security Checks | PASS | Redaction and untrusted-rendering tests; no dependency audit run |
| Accessibility | N/A | No automated a11y check; semantic queries only (§12) |
| Contract | PASS | `test_openapi_contract.py` (snapshot regenerated); `contract.node.test.ts` |

## 14. Acceptance Criteria Verification

**Roadmap acceptance criteria:**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| A failed node can be investigated comprehensibly | PASS | Errors tab → Inspect → node error code/message/details, numbered retried failures, outputs, node logs, graph state. Component test "lets a failed node be diagnosed…"; full-stack "a failed run is diagnosed in the inspector…"; manual check |
| Sensitive data is not shown, per the backend redaction | PASS | Backend redacts at capture and on output (API test `test_node_logs_never_return_secrets`); UI renders `[REDACTED]` verbatim and reconstructs nothing (component test); full stack: input password never appears |

**Roadmap scope items:**

| Scope Item | Status | Evidence |
|---|---|---|
| Overview | PASS | `OverviewPanel` |
| Graph | PASS | `GraphPanel`; graph test |
| Nodes | PASS | `NodesPanel` |
| Logs | PASS | `LogsPanel` / `LogList`; logs tests |
| Errors | PASS | `ErrorsPanel` |
| Events | PASS | `EventsPanel` |
| Metrics | PARTIAL | Overview "Usage": attempts, retries, failed attempts, node status counts, tokens. Not a separate tab (F9). Does not block |
| Cost | PARTIAL | Overview "Usage" (estimate, completeness note) and per node. Not a separate tab. Does not block |
| Node Inspector: Status, Outputs, Duration, Retry Count, Error, Logs, Token Usage, Cost | PASS | `NodeInspector`; component tests |
| Node Inspector: Inputs | FAIL (deferred) | Not persisted by the backend (F3). Does not block the acceptance criteria: upstream outputs are visible |

**Session-prompt requirements:**

| Requirement | Status |
|---|---|
| Metadata, version, trigger, states, timings, errors, retries, logs, events, safe outputs | PASS |
| Successful / failed / node failure / retries / logs / large logs / missing data / redaction / untrusted rendering tests | PASS (§12) |

## 15. Security Considerations

- **Secrets in logs:**
  - pattern redaction plus exact known-value masking at capture (inside the node task);
  - second redaction on API output;
  - exceptions stored as type name only (no messages, tracebacks or paths, AGENTS §8);
  - plaintext secrets in project config are rejected by the existing `PLAINTEXT_SECRET` rule (F8);
  - limit: shapeless secrets that never went through the credential store (e.g. typed into an execution input under a non-sensitive key) cannot be recognized. This is unchanged backend policy (architecture §109).
- **Authorization:** the logs route uses the existing `authorize_path` dependency (session required; project visibility; unknown and hidden executions both answer 404).
- **Input validation:** `level` enum; `limit` 1–100; `after` ≥ 0; `q` 1–200 characters; `q` is passed as a bound parameter with LIKE wildcards escaped (no injection, no wildcard scans).
- **XSS / output encoding:** all untrusted strings render as React text. There is no `dangerouslySetInnerHTML`, `innerHTML`, Markdown or auto-linking in the inspector. JSON is shown via `JSON.stringify`. Tested with `<script>`, `<img onerror>`, `<b>` and `javascript:` payloads.
- **Browser storage / URL:** nothing stored; the URL holds tab and node ids only (tested).
- **Resource exhaustion:** per-attempt line/size bounds on the backend; render caps on the frontend.
- **Unchanged:** CSRF (no cookies), WebSocket security (unchanged from Phase 14).

## 16. Performance Considerations

- **Backend writes:** one executemany INSERT per save that carries logs, at most 200 rows per attempt.
- **Backend reads:** indexed on `(execution_id, position)`. The level filter uses `IN`. The `q` filter is a sequential ILIKE over one execution's lines, marked with a `ponytail:` comment naming a trigram index as the upgrade path.
- **Frontend payloads:**
  - logs 100 lines per request;
  - events rendered 200 at a time (the report may carry up to 10,000);
  - text over 20,000 characters cut in the DOM;
  - Graph and Logs queries load only when their tab is open.
- **Live refetch:** at most one refetch of execution, report and loaded log pages per second during a live run (follow-up).
- **Remaining:**
  - an infinite log query refetches all loaded pages on invalidation;
  - the log list is paginated, not virtualized (`frontend/AGENTS.md` §44 allows pagination);
  - no measurements were taken.

## 17. Documentation Changes

| File | What was documented |
|---|---|
| `docs/architecture.md` §22 | `/executions/{id}/logs?node_id=&level=&q=` in the endpoint list |
| `docs/architecture.md` §52 | "Node log history": capture mechanism, redaction, bounds, storage/transaction, why not events, API, ceilings |
| `docs/architecture.md` §55 | "Failed attempts": derived `attempt`, the invariant, schema version unchanged |
| `docs/sdk.md` §4 | `context.logger` lines are stored; limits; level gating; other loggers not captured |
| `frontend/README.md` | New "Execution inspector" section; throttle in "Live execution"; full-stack suite now with Redis and worker; stale "no separate log API" line removed |
| `backend/migrations/versions/0012_node_logs.py` | Migration docstring (reason, behavior for older executions) |

To update in a future phase: none required by this phase. Phase 16 docs should state whether monitoring uses node logs.

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or changed (backend or frontend).
- **Database:** migration `0012_node_logs` (new table, index, two check constraints).
- **Configuration / environment variables:** no new settings.
- **Full-stack test infrastructure** (`frontend/tests/fullstack/backend.sh`):
  - additional Redis container `redis:8.2.1-alpine` (same pin as `docker-compose.yml`), labelled `akasha-e2e=fullstack`, readiness via `redis-cli ping`;
  - `AKASHA_REDIS_URL` set;
  - `AKASHA_WORKER_ENABLED=false` removed (default true);
  - `AKASHA_LOG_LEVEL=INFO` added;
  - the scheduler stays disabled.

  Reason: F6. `teardown.ts` already removes everything with the label.
- **Backup format:** `node_logs` rows are included automatically (all tables except `sessions`). Backups are bound to the schema revision, so pre-`0012` backups restore only with a pre-`0012` engine (existing rule).

## 19. Deferred Work

| What | Why deferred | Target | Affects completion |
|---|---|---|---|
| Node inputs in the Node Inspector | Backend does not persist them; frontend derivation would duplicate runtime semantics (F3) | Unassigned; needs a backend decision on persisting inputs (size, redaction) | Scope item FAIL; acceptance criteria unaffected |
| Storing log lines below the process log level | Requires changing logging-level behavior (F2) | Unassigned | No |
| Log retention policy | Operational concern | Later ops/retention work | No |
| Live, per-line log streaming | Needs a node→loop channel and a save per line; conflicts with the single-writer, per-attempt flush design | Not planned | No |
| Separate Metrics/Cost tabs | Content too small (F9) | Phase 16 may cover system metrics | No |
| Log virtualization | Pagination is sufficient at current bounds | When measured necessary | No |
| Automated accessibility checks (axe) | Not set up in the project | Unassigned | No |

## 20. Known Limitations

- Only `context.logger` (`akasha.nodes.*`) lines are stored, not other loggers.
- Lines below the process log level are not stored.
- Lines appear when an attempt ends, not while it runs.
- Per attempt: at most 200 lines (plus a drop note), 4,000-character messages, 8 KiB of fields; otherwise `fields_omitted`.
- Search covers messages only, not fields; substring only (no regex or full text).
- Executions from before migration `0012` have no logs.
- The report caps events at 10,000 (pre-existing); the Events tab shows the report's timeline, not the paginated events endpoint.
- Node inputs are not shown.
- On a live run, invalidation refetches every loaded log page, throttled to once per second.
- Without the login UI (Phase 21), the browser has no session token, so the app needs an injected `Authorization` header (as in the full-stack suite) to show any data. Unchanged from previous phases.

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Node inputs not available for inspection | Users infer inputs from upstream outputs | Medium | Backend persistence + inspector | Decide whether to persist node inputs (redacted, size-capped) with the outcome | Unassigned |
| Stored logs depend on `AKASHA_LOG_LEVEL` | Operators running at WARNING lose INFO node lines in the UI | Medium | Runtime logging | Give `akasha.nodes` its own level, or document the setting in `deployment.md` | Unassigned |
| No log retention | `node_logs` grows with executions | Low (bounded per attempt) | Operations | Retention together with execution retention | Ops phase |

## 22. Architectural / Product Decisions

- Node logs are history next to state, like events, but in their own table because they are free text (D1). Do not move log text into event payloads.
- Logs are captured through `context.logger` via a ContextVar-routed handler and flushed per attempt in the same save as the outcome (D2). Node tasks still never touch the aggregate or the database.
- Redaction happens at capture inside the node task (known values) and again at the API (D3).
- `failures[n-1]` is attempt n. This is enforced by the node state machine and the rehydration invariant, and exposed as a derived report field (D4).
- The inspector's navigation state is URL state (D5). Server data stays in TanStack Query; filters are component state.
- Metrics and Cost live in Overview, not their own tabs (F9).
- The full-stack E2E environment runs a real worker; tests needing a long-running execution use `core.delay` and cancel it.
- Live reconciliation is throttled to once per second, with the end of the stream never deferred.

## 23. Repository / Git State

- **Branch:** `main`. HEAD `afdfda6` (frontend phase 14 — live execution and websocket).
- **Working tree:** 33 modified files (`git diff --stat`: 1392 insertions, 269 deletions) and untracked:
  - `backend/migrations/versions/0012_node_logs.py`;
  - `backend/src/akasha/runtime/node_logs.py`;
  - `backend/tests/integration/test_node_logs.py`;
  - `backend/tests/runtime/test_node_logs.py`;
  - `frontend/src/features/executions/ExecutionInspector.test.tsx`;
  - `frontend/src/features/executions/inspector/`;
  - this report.
- **Unrelated changes:** none detected; all changes belong to this phase or its follow-up.
- **Commits:** nothing committed. The user stated they handle git themselves.
- **Temporary artifacts:** the manual-verification stack (containers labelled `akasha-phase15=manual`, servers on ports 8766 and 5179) was stopped and removed. Its scripts live only in the session scratchpad, outside the repository. Playwright writes `frontend/test-results/` (existing, ignored by git, as before).

## 24. Phase Completion Assessment

### Implemented
- Backend node log persistence, API and backup support.
- Frontend tabbed Execution Inspector with node inspector, logs, errors, events and graph.
- Follow-up: throttle, failure attempt numbers, log search, full-stack runs.

### Verified
All gates in §13. Both acceptance criteria are verified by component, API, full-stack and manual evidence.

### Not Implemented
Node inputs (Node Inspector scope).

### Deferred
§19.

### Open
§21 (node inputs, level-dependent logs, retention).

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase (Phase 16 — Monitoring)

**The next phase can assume:**
- `GET /executions?status=` and `GET /executions/status-counts` (unchanged);
- `GET /reports/executions/{id}` with per-node usage and cost, and `failures[].attempt`;
- `GET /executions/{id}/logs` (paged; filters `node_id`, `level`, `q`);
- `/metrics` (Prometheus) as before.

Monitoring aggregates across executions; there is no aggregate metrics API yet besides status counts. Check `docs/architecture.md` and the roadmap for what Phase 16 needs from the backend before building UI.

**Reusable pieces:**
- `executionStatusDisplay` (dashboard) and `nodeStatusDisplay` (`inspector/format.ts`);
- `formatMilliseconds`;
- `UntrustedText` / `JsonBlock` for any untrusted text;
- `SnapshotCanvas` with `footer` and `onNodeClick`;
- `RECONCILE_INTERVAL_MS` pattern for live data.

**Must not be changed casually:**
- the closed-payload rule for events (do not put log text into events);
- the per-attempt flush and single-writer rule in the runtime;
- the `NodeExecution` failure/attempt invariant (the report derives attempt numbers from it);
- `node_logs` positions restored by backup (`_ORDERED` in `backup.py`) when adding further identity-ordered tables.

**Constraints and open issues:** stored node logs depend on `AKASHA_LOG_LEVEL`; node inputs are not persisted; no log retention.

**Prerequisites:** run migration `0012_node_logs` (`akasha migrate`). The full-stack suite now requires Docker for both PostgreSQL and Redis.
