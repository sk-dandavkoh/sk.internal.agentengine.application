# Phase 19 — Reports

## 1. Executive Summary

**Supposed to achieve.** Roadmap §23 asks the backend to "generate useful
execution reports from persisted data". It names `ReportService`,
`ExecutionReport` and `ReportGenerator`. It asks for:
- report contents: project, project version, execution status, duration,
  nodes, errors, token usage, cost estimates, outputs, artifacts and
  timestamps;
- at least one export format;
- tests for successful reports, failed execution reports, cost information,
  secret redaction and artifact references.

The session request added more requirements:
- trigger, retries, logs "where appropriate", metrics and partial executions;
- reports must come from authoritative persisted state, with events never
  treated as canonical;
- reports must be deterministic.

**Actually implemented.**
- A report module, `akasha.application.reports`:
  - typed, frozen Pydantic report models (`ExecutionReport` and its
    sections);
  - a pure generator function, `build_report`;
  - a canonical JSON export, `report_json`;
  - the use case, `ReportService.execution_report`.
- One HTTP endpoint, `GET /api/v1/reports/executions/{execution_id}`, which
  returns the canonical export.
- Composition-root wiring (`Container.reports`).
- Implementation notes in `docs/architecture.md` §55.

There are no schema changes, no migrations and no new dependencies.

**Resulting system state.**
- Any persisted execution can be rendered as a deterministic, secret-redacted
  JSON report, whatever its status (created, running, completed, failed,
  cancelled or timed out).
- Every field is derived from the persisted Execution aggregate and its pinned
  ProjectVersion.
- The persisted event log is attached as a timeline only.

**Acceptance criteria.** The single roadmap acceptance criterion ("Reports
can be generated without requiring the frontend") is satisfied. Two roadmap
content and test items cannot be satisfied, because the concepts they
depend on do not exist yet:
- **Artifacts / artifact references:** there is no artifact concept in the
  codebase.
- **Logs:** node logs are never persisted.

Both are reported as deferred (§19).

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §23 (Phase 19 — Reports):**
- Goal: generate useful execution reports from persisted data.
- Scope: `ReportService`, `ExecutionReport`, `ReportGenerator`.
- Contents: project, project version, execution status, duration, nodes,
  errors, token usage, cost estimates, outputs, artifacts, timestamps.
- At least one export format.
- Tests: successful report, failed execution report, cost information,
  secret redaction, artifact references.
- Acceptance: reports can be generated without requiring the frontend.

**Other normative sources:**
- **`backend/AGENTS.md` §47:**
  - reports are built from persisted data and must not depend on volatile
    runtime memory;
  - they must be secret-safe;
  - minimum content: project, version, execution, start/end, status, node
    executions, errors, run times, relevant metrics, and token usage and cost
    where available.
- **`backend/AGENTS.md` §8:** names the use case `GenerateExecutionReport`.
- **Architecture §13 and `AGENTS.md` §35:** secrets never appear in reports.
- **Architecture §22:** reserves `/api/v1/reports`.

**Not responsible for:**
- authentication and authorization (Phase 20);
- CLI access to reports (Phase 21);
- workers and queues (Phase 22);
- retry enforcement in the runtime (Phase 23);
- resource limits in general (Phase 24);
- any frontend report view.

---

## 3. Scope Implemented

### Application (`backend/src/akasha/application/reports.py`, new)
- **Report models:** `_Model` is the base, with `frozen=True` and
  `extra="forbid"`. On top of it:
  - `ErrorReport`
  - `TokenUsageReport`
  - `CostReport`
  - `ProjectReport`
  - `CancellationReport`
  - `ExecutionSection`
  - `NodeReport`
  - `MetricsReport`
  - `EventReport`
  - `ExecutionReport`, the root
- **`build_report(execution, *, project_name, events=(), events_truncated=False)`:**
  a pure function from persisted state to an `ExecutionReport`. It does no
  I/O and reads no clock. This is the roadmap's "ReportGenerator" (see §5,
  D-4).
- **`report_json(report)`:** the export format. It produces canonical JSON
  (`sort_keys=True`, `ensure_ascii=False`, `allow_nan=False`, `indent=2`).
- **`ReportService(uow).execution_report(execution_id)`:** the use case. It
  loads events, the execution and the project in one unit of work.
- **Helpers:**
  - `_node` and `_metrics`;
  - `_usage_and_cost`, a defensive parser for node-reported usage and cost;
  - `_error`, which maps `ExecutionError` to a redacted `ErrorReport`;
  - `_redacted_object`;
  - `_duration_ms`;
  - `_load_events`, a paginated loader with a cap.
- **Constants:**
  - `REPORT_SCHEMA_VERSION = 1`
  - `MAX_REPORT_EVENTS = 10_000`
  - `_EVENT_PAGE = 500`

### API (`backend/src/akasha/api/reports.py`, new)
- `create_reports_router()`: prefix `/reports`, tag `reports`, with
  `ErrorResponse` documented for 404 and 500.
- `GET /executions/{execution_id}` (full path
  `/api/v1/reports/executions/{execution_id}`):
  - declares `response_model=ExecutionReport` for OpenAPI;
  - returns `Response(report_json(report), media_type="application/json")`.
- `_reports` dependency: raises `ConfigurationError` when the container has
  no report service.

### Composition
- **`backend/src/akasha/api/routes.py`:** the reports router is included in
  the v1 router.
- **`backend/src/akasha/bootstrap.py`:**
  - new `Container.reports: ReportService | None = None` field;
  - `create_container` builds `ReportService(uow)` when a database is
    configured.

### Testing
- `backend/tests/application/test_reports.py` (new): 18 test cases.
- `backend/tests/integration/test_reports.py` (new): 9 tests.
- Details in §12.

### Documentation
- `docs/architecture.md` §55: "Implementation (Phase 19)" block.
- This report.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/application/reports.py` | Created (367 lines) | Report models, generator, export, service |
| `backend/src/akasha/api/reports.py` | Created (37 lines) | HTTP adapter under `/api/v1/reports` |
| `backend/src/akasha/api/routes.py` | Modified (+2) | Include the reports router |
| `backend/src/akasha/bootstrap.py` | Modified (+3) | `Container.reports` field and wiring |
| `backend/tests/application/test_reports.py` | Created (68 lines) | Unit tests: usage/cost parser, unwired endpoint |
| `backend/tests/integration/test_reports.py` | Created (426 lines) | PostgreSQL-backed report scenarios, API tests |
| `docs/architecture.md` | Modified (+25) | §55 implementation notes |
| `docs/reports/backend/phase-19-reports.md` | Created | This report |

- **Directories:** no new directories.
- **Migrations:** none.
- **Configuration:** no changes (no new settings or environment variables).
- **Generated files:** none.
- **Deleted files:** none.

---

## 5. Architecture Changes

The dependency direction is unchanged:
- `application/reports.py` imports only `pydantic`, `akasha.domain.*`,
  `akasha.application.*` and `akasha.security.redaction`;
- `api/reports.py` imports only `akasha.api.schemas`,
  `akasha.application.reports` and `akasha.errors`;
- `tests/test_architecture.py` passes (3 tests).

The following decisions clarify how architecture §55 is realised.

### D-1: Reports are computed on request, not stored
- **Decision:** a report is a projection that is generated every time it is
  requested. There is no report table.
- **Context:** the roadmap does not say whether reports are persisted.
- **Chosen approach:** `ReportService` reads the aggregate and events and
  builds the report in memory.
- **Alternatives considered:** persisting a report row when an execution
  finishes.
- **Reason:** the projection is deterministic over immutable, terminal
  state. A stored copy would add a migration, retention questions and
  possible staleness, and no information.
- **Consequences:** report cost is paid per request (see §16). Stored or
  scheduled reports, if ever needed, can persist `report_json` output
  unchanged.

### D-2: The Execution aggregate is canonical; events are a timeline only
- **Decision:** no report field is derived from events.
- **Context:** the session requirement "Events alone must not be treated as
  canonical state", and architecture §18.
- **Chosen approach:**
  - status, timings, attempts, failures, outputs, errors and metrics come
    from `Execution`, its `NodeExecution`s and its pinned `ProjectVersion`;
  - `events` is a verbatim, redacted list of the stored event rows (position,
    type, time, node id, payload).
- **Alternatives considered:** reconstructing node timelines from
  `node.started` and `node.completed` events. That would give per-attempt
  timings, but it would make the event log a second source of truth.
- **Reason:** single source of truth.
- **Consequences:** per-attempt start times of earlier attempts appear only
  in the event timeline, not in `NodeReport` (the aggregate keeps only the
  last attempt's `started_at`).

### D-3: Determinism over convenience
- **Decision:** reports contain no generation timestamp and read no clock.
- **Context:** the session requirement "Reports are deterministic
  representations of persisted execution information".
- **Chosen approach:**
  - `duration_ms` is `None` unless both start and finish are persisted, so a
    running execution has no "elapsed so far" value;
  - nodes are ordered by `node_id`;
  - events are ordered by store position;
  - `node_versions` and the status-count keys are sorted;
  - costs are summed in `node_id` order, so float addition order is fixed;
  - `report_json` uses sorted keys.
- **Alternatives considered:**
  - a `generated_at` field;
  - ordering nodes by start time, which can tie and is `None` for unstarted
    nodes.
- **Reason:** byte-identical output for identical persisted state, verified
  by test.
- **Consequences:** consumers that want "generated at" must record it
  themselves (for example, the HTTP `Date` header).

### D-4: `ReportGenerator` is a function, not a class
- **Decision:** the roadmap's `ReportGenerator` is `build_report()`.
- **Context:** the roadmap lists three names. A generator class would have no
  state and a single method.
- **Reason:** `AGENTS.md` §119 (no premature abstraction). A pure function
  is the simplest testable form.
- **Consequences:** if more report kinds appear (project-level, aggregate),
  add sibling functions or introduce a class then.

### D-5: Token usage and cost are recognised by output shape
- **Decision:** a node contributes usage when its persisted outputs contain a
  `metadata` object with `usage.input_tokens` and `usage.output_tokens`
  (non-negative ints). It contributes cost when `metadata.cost` has a finite,
  non-negative numeric `amount` and a non-blank `currency`.
- **Context:** token usage and cost are persisted only inside `core.llm`'s
  `metadata` output port (`akasha/nodes/llm.py`, `_metadata`). No dedicated
  usage record exists on `NodeExecution`.
- **Alternatives considered:**
  - matching on `node_type == "core.llm"`, which would exclude SDK and plugin
    AI nodes using the same shape;
  - adding a usage column to `node_executions`, which is a schema change
    outside this phase.
- **Reason:** no schema change, and any node that follows the documented
  shape is counted.
- **Consequences:**
  - node outputs are untrusted (plugins), so malformed values count as "not
    reported" instead of failing the report;
  - `total_tokens` is recomputed and never trusted;
  - `cost_complete=false` flags usage without a price.

### D-6: Report JSON is also the HTTP body
- **Decision:** the endpoint returns `report_json(report)` bytes, not a
  FastAPI-serialised model.
- **Reason:** "what the API returns" and "what the user downloads" are the
  same deterministic artifact. `response_model` still publishes the schema in
  OpenAPI (verified: `ExecutionReport` is present in
  `components.schemas`).
- **Consequences:** FastAPI does not re-validate the response. Validity comes
  from constructing the frozen `extra="forbid"` Pydantic model in
  `build_report`.

---

## 6. Domain / Data Model Changes

- **Domain:** no changes to entities, value objects, enums or state
  transitions.
- **Database:** no tables, columns, indexes or constraints added. No
  migration.
- **New application-level models:** report DTOs in
  `akasha.application.reports`, all Pydantic, frozen and closed:

| Model | Fields |
|---|---|
| `ExecutionReport` | `schema_version`, `execution_id`, `project`, `execution`, `nodes`, `metrics`, `events`, `events_truncated` |
| `ProjectReport` | `id`, `name` (nullable), `version`, `version_description`, `published_at` |
| `ExecutionSection` | `status`, `trigger_type`, `trigger_id`, `correlation_id`, `engine_version`, `node_versions`, `created_at`, `started_at`, `finished_at`, `duration_ms`, `input`, `error`, `cancellation` |
| `NodeReport` | `node_id`, `node_type`, `label`, `status`, `attempts`, `retries`, `started_at`, `finished_at`, `duration_ms`, `error`, `failures`, `outputs`, `usage`, `cost` |
| `MetricsReport` | `node_status_counts` (all 7 statuses, zero-filled), `attempts`, `retries`, `failed_attempts`, `usage`, `costs`, `cost_complete` |
| `ErrorReport` | `code`, `message`, `kind`, `retryable`, `node_id`, `details` |
| `TokenUsageReport` | `input_tokens`, `output_tokens`, `total_tokens` |
| `CostReport` | `amount`, `currency` |
| `CancellationReport` | `requested_at`, `reason` |
| `EventReport` | `position`, `event_type`, `occurred_at`, `node_id`, `payload` |

**Semantics:**
- `retries = max(attempt - 1, 0)`.
- `failures` lists every failed attempt, including those followed by a
  successful retry.
- `error` is the failure that ended the node (`NodeExecution.error`).
- `duration_ms` is floored milliseconds. For nodes it covers the last
  attempt only.

**Compatibility:**
- `schema_version = 1` (`REPORT_SCHEMA_VERSION`). The code comment says to
  bump it when a field changes meaning; adding an optional field does not
  require a bump.
- Because every model is `extra="forbid"`, re-validating a *newer* report
  with an *older* model would reject added fields. Consumers should not
  round-trip across versions with this model.

---

## 7. API / Contract Changes

### New: `GET /api/v1/reports/executions/{execution_id}`
- **Purpose:** return the execution report (the export).
- **Request:** path parameter `execution_id`. No body, no query parameters.
- **Response 200:** `Content-Type: application/json`. The body is exactly
  `report_json(report)`, an `ExecutionReport` document with sorted keys and
  2-space indentation.
- **Errors (standard `ErrorResponse`: `code`, `message`, `details`,
  `request_id`):**
  - `404 EXECUTION_NOT_FOUND`: unknown id (verified by test).
  - `500 CONFIGURATION_ERROR`: no database configured (verified by test).
  - `500 INTERNAL_ERROR`: unexpected failures, through the existing global
    handler.
- **Compatibility:** additive. No existing endpoint changed.
- **Authentication:** none, like every other endpoint until Phase 20.

### Other contracts
- **Node output contract (documented, not new):** the report reads
  `outputs["metadata"]["usage"|"cost"]` as `core.llm` writes them. The shape
  is documented in `_usage_and_cost` and architecture §55. `core.llm` did not
  change.
- **Unchanged:** event payloads, node contracts, the plugin/SDK contract and
  WebSocket.

---

## 8. Implementation Details

### Control flow (`ReportService.execution_report`)
1. It opens one unit of work (`UowFactory`).
2. `_load_events` pages through `uow.events.list(execution_id=..., after=cursor, limit=500)`
   until it gets a short page or holds more than 10,000 events. It returns
   the first 10,000 and `truncated=True` when there were more.
3. `load_execution(uow, id)` (existing helper in `application/executions.py`)
   raises `ExecutionNotFoundError` for unknown ids.
4. `uow.projects.get(project_id)` supplies the project name. `None` becomes
   `name=None`. This cannot normally happen: the `project_versions` foreign
   key to `projects` is `ON DELETE RESTRICT`.
5. The unit of work is left without commit (read-only, so the transaction
   rolls back).
6. `build_report(...)` runs.

### Read ordering
- Events are read *before* the execution. The unit of work uses PostgreSQL's
  default READ COMMITTED, so each statement sees its own snapshot.
- In this order a concurrent runtime commit between the two reads can only
  make the state newer than the timeline, never the reverse. The timeline
  never shows transitions the state does not yet reflect.
- For terminal executions (no writer left) the two reads always agree.

### Redaction (all in `build_report` and helpers, via `akasha.security.redaction`)
- **`redact()`** (structural: sensitive keys become `[REDACTED]`; text rules
  cover bearer/basic tokens, URL userinfo, `key=value` secrets, and
  `sk-`/GitHub/Slack/AWS/JWT token formats):
  - `execution.input`
  - node `outputs`
  - event payloads
- **`redact_text()`:**
  - error `message`
  - cancellation `reason`
  - node `label`
  - version `description`
- **`redact_mapping()`:** error `details`.
- **Not loaded:** credential plaintext is never loaded. The runtime's
  known-value masking (`register_secret`) is not active in the report
  context, so the structural pass is the report's own defence.

### Usage and cost parsing (`_usage_and_cost`)
- Rejected as usage: `bool` (an `int` subclass), negative numbers, floats
  and missing keys.
- Rejected as cost: `bool`, negative values, `inf`, `NaN` (via
  `not 0 <= amount < inf`), non-numeric amounts and blank currencies.
- Rejecting cost keeps usage. Rejecting usage drops both.

### Metrics (`_metrics`)
- Sums over `NodeReport`s, not raw state, so metrics are always consistent
  with the node list in the same report.

### Dependency injection
- The service receives a `UowFactory`. The API dependency reads it from
  `request.app.state.container.reports`, following the pattern of the
  existing routers.

### Concurrency, caching, lifecycle
- Read-only, no caching, no background work.

---

## 9. Findings

### F-1: Node logs are not persisted anywhere
- **Finding:** node loggers (`logging.getLogger(f"akasha.nodes.{type}")` in
  `runtime/runtime.py`) write only to the process log handlers. No log table
  or log store exists.
- **Impact:** MEDIUM
- **Resolution:** DEFERRED
- **Notes:**
  - The session's "logs where appropriate" and `backend/AGENTS.md` §47
    ("must not depend on volatile runtime memory") conflict here. Reports
    therefore contain no logs.
  - The persisted event timeline (including `node.emitted`) is the closest
    persisted equivalent and is included.

### F-2: No artifact concept exists
- **Finding:** the roadmap lists "artifacts" in report contents and
  "artifact references" as a test. The codebase has no artifact entity,
  storage or reference type.
- **Impact:** MEDIUM
- **Resolution:** DEFERRED
- **Notes:** building an artifact subsystem to satisfy a report field would
  violate `AGENTS.md` §3.1 (no future subsystems inside the current phase).
  The roadmap assumed artifacts would exist by Phase 19; they do not.

### F-3: Token usage of failed attempts is lost
- **Finding:** `NodeExecution.outputs` is set only on completion, and
  `ExecutionError` carries no usage.
- **Impact:** LOW (costs are understated for failed or retried LLM calls)
- **Resolution:** OPEN
- **Notes:** fixing this requires persisting usage per attempt (a domain and
  schema change). It is not attempted here.

### F-4: Retry history cannot currently be produced by the runtime
- **Finding:** `ExecutionRuntime` rejects `retry_policy.max_attempts > 1`
  (`runtime/runtime.py:219`), pending Phase 23. `ExecutionService` always
  uses the default no-retry policy.
- **Impact:** LOW
- **Resolution:** WORKAROUND (test-only)
- **Notes:** the retry test records the history through the real domain
  transitions (`Execution.retry_node`) and persists it through the real
  repository. The report path is therefore exercised against real persisted
  retry state, but not against a runtime-driven retry.

### F-5: Persisted node outputs are only known-value masked
- **Finding:** `runtime/executor.py` applies `mask_known_secrets` to outputs
  before persisting. Structural redaction is not applied, by design: outputs
  are data flowing to downstream nodes.
- **Impact:** LOW for this phase
- **Resolution:** handled for reports
- **Notes:** reports apply `redact()` to outputs. Any future endpoint that
  exposes raw outputs needs the same treatment.

### F-6: `ConfigurationError` maps to HTTP 500, not 503
- **Finding:** `akasha/errors.py` sets `ConfigurationError.http_status = 500`.
  The first version of the unwired-endpoint test assumed 503.
- **Impact:** LOW
- **Resolution:** FIXED in the test (existing error semantics kept)
- **Notes:** unchanged, to stay consistent with the other routers.

---

## 10. Bugs / Problems Found

**Pre-existing bugs discovered:** none. F-1 to F-5 are missing capabilities,
not defects.

**Problems introduced during this phase (all caught before the phase ended):**

| Problem | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|
| Test called `project.publish_draft(now=T0)` | Wrong keyword; the signature is `publish_draft(*, published_at=...)` | Changed to `published_at=T0` | All integration report tests exercise it | Tests |
| Unwired-endpoint test expected 503 | Wrong assumption about `ConfigurationError` (F-6) | Expect 500 `CONFIGURATION_ERROR`; moved to the Docker-free unit file | `test_report_endpoint_without_persistence` | Tests |
| mypy: `amount > float` on `Any \| None`; `float(Any \| None)` | `type(x) in (...)` does not narrow for mypy | `isinstance(amount, int \| float) and not isinstance(amount, bool)` | `test_malformed_cost_keeps_usage` (bool, NaN, inf cases) | `application/reports.py` |
| mypy: `sorted()` over `list[int \| None]` in a test | Optional positions | Assert `None not in positions`, then sort the filtered list | `test_successful_execution_report` | Tests |

**Infrastructure and environment problems:** none. Docker was available and
Testcontainers ran `postgres:17.2`.

---

## 11. Fixes Applied

1. **Cost-amount type narrowing.**
   - Symptom: mypy strict errors in `_usage_and_cost`.
   - Root cause: non-narrowing type check.
   - Implementation: `isinstance` checks plus an explicit `bool` exclusion;
     the `NaN` rejection is documented inline.
   - Verification: `uv run mypy .` passes, and the parametrised malformed-cost
     tests pass.
2. **Test fixes** (keyword and status code, see §10).
   - Verification: all 27 report tests pass.
3. **Formatting.** `ruff format` reformatted `tests/integration/test_reports.py`
   once; there were no semantic changes.

**Redaction mutation check (verification, not a fix):**
- I temporarily replaced `redact(value)` with `dict(value)` and dropped
  `redact_text` on error messages in `application/reports.py`.
- `test_report_redacts_secrets` and `test_report_endpoint_serves_the_export`
  both failed (secret `sk-live-…` found in the export).
- The source was restored, and `grep` confirmed `redact(value)` is back.

---

## 12. Testing & Verification

### Unit Tests (`tests/application/test_reports.py`, no Docker)
- `test_malformed_usage_counts_as_not_reported`: 8 parametrised cases
  (`None`, `{}`, non-object metadata, null usage, a missing key, bool,
  negative and float tokens), each giving `(None, None)`. **PASS**
- `test_malformed_cost_keeps_usage`: 8 cases (`None`, a string, a string
  amount, bool, negative, `inf`, `NaN`, a blank currency). Usage is kept and
  cost becomes `None`. **PASS**
- `test_integer_cost_is_accepted`: an int amount is normalised to float.
  **PASS**
- `test_report_endpoint_without_persistence`: the HTTP stack without a
  database returns `500 CONFIGURATION_ERROR`. **PASS**

### Integration Tests (`tests/integration/test_reports.py`, real PostgreSQL)
The scenario is a published 3-node graph (`test.emit` → `core.llm` →
`test.sink`) with fixed timestamps from `T0 = 2100-01-01`. It is driven
through the real `Execution` transitions and persisted with
`save_execution` (aggregate plus events, one transaction).

Every scenario calls `assert_matches_persisted`. This compares report
status, timestamps, node order, per-node status, attempts, failure count
and timings against the execution **reloaded from the database**.

- `test_successful_execution_report`: **PASS**. It checks:
  - project name, version and description;
  - trigger `api` and the correlation id;
  - node versions;
  - `duration_ms = 7000` and the input;
  - LLM node type, label, 1000 ms, outputs, usage 42 and cost 0.25;
  - metrics: counts, attempts 3, retries 0, usage totals, `costs=[0.25 USD]`,
    `cost_complete=true`;
  - timeline starting with `execution.created`, ending with
    `execution.completed`, three `node.completed`, and positions ascending
    and non-null.
- `test_failed_execution_report`: **PASS**. It checks:
  - the full structured `ErrorReport`;
  - the failed node's error equals the execution error, with no outputs;
  - the downstream node is `cancelled`;
  - counts 1/1/1, zero usage, no costs;
  - the last event is `execution.failed` with its `error_code`.
- `test_partial_execution_report`: **PASS**. A RUNNING execution has no
  duration, the running node has no duration, and the pending node shows 0
  attempts; counts are 1/1/1.
- `test_retry_history_in_report`: **PASS**. It checks:
  - attempts 2, retries 1, no final error;
  - the failures list holds the transient `LLM_RATE_LIMITED` error with
    `retryable=true`;
  - `started_at` is the last attempt;
  - metrics: retries 1, failed attempts 1, attempts 4;
  - usage without cost gives `cost_complete=false`;
  - `node.started` attempts are `[1, 2]`.
- `test_report_redacts_secrets`: **PASS** (see Security below).
- `test_export_is_deterministic_and_round_trips`: **PASS**. Two generations
  are byte-identical; `model_validate_json` → `report_json` reproduces the
  bytes; non-ASCII is not escaped.
- `test_unknown_execution`: **PASS** (`ExecutionNotFoundError`).

### API Tests (real HTTP stack via `httpx.ASGITransport`, real PostgreSQL)
- `test_report_endpoint_serves_the_export`: **PASS**. 200 with
  `application/json`; the body equals `report_json`; no secret in the body.
- `test_report_endpoint_unknown_execution`: **PASS**. 404
  `EXECUTION_NOT_FOUND` with exactly the standard error keys.
- OpenAPI (checked manually by a script, not by a test): the path
  `/api/v1/reports/executions/{execution_id}` and the schema `ExecutionReport`
  are present.

### Infrastructure Tests
- Testcontainers PostgreSQL `postgres:17.2`, migrated by the existing session
  fixture and truncated per test (`clean_db`). **PASS**
- No new infrastructure.

### Security Tests (`test_report_redacts_secrets`)
Secrets were placed in:
- the execution input (`api_key` key; a GitHub token in free text);
- node outputs (`password=` in text; an `Authorization` key with a bearer
  token);
- an error message (URL userinfo);
- error details (an `authorization` key; a raw `sk-` key in a non-sensitive
  key);
- the cancellation reason (`token=`).

Results:
- None of the five secret values appears in `report_json` output.
- Structural masks are asserted (`[REDACTED]` in the right places), and
  non-secret context survives (`internal.test` host kept).
- The raw value is asserted to be **still present in the persisted row**,
  which proves that the report itself redacts.

**PASS.** The redaction mutation check is described in §11.

### Architecture guard
- `tests/test_architecture.py`: **PASS** (3 tests), so the new modules
  respect the import rules.

### Not performed
- Component tests, E2E tests, performance tests and accessibility tests
  (backend-only phase; no UI).
- No runtime-driven report test: reports were built from domain-driven,
  really persisted executions (F-4).

---

## 13. Quality Gates

All gates were run on the final tree during the report's verification pass,
from `backend/`.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `uv run ruff format --check .`: 163 files already formatted (one reformat during the phase, §11) |
| Linter | PASS | `uv run ruff check .`: all checks passed; no suppressions added |
| Type Checker | PASS | `uv run mypy .` (strict): no issues in 159 source files. Initially 4 errors in 2 files, fixed (§10) |
| Unit Tests | PASS | Included in the full run |
| Integration Tests | PASS | Included in the full run (Docker available) |
| API Tests | PASS | Included in the full run |
| Full suite | PASS | `uv run pytest`: **1517 passed, 5 skipped**, 1 warning, 75.98 s |
| Phase tests | PASS | 27 passed (18 unit, 9 integration/API) |
| E2E Tests | N/A | Not part of this phase |
| Build | N/A | No build or packaging change; the app factory is exercised by the API tests |
| Security Checks | PASS | Redaction tests plus the mutation check; no separate SAST or dependency audit was run |
| Accessibility | N/A | Backend only |

- **Skipped:** the 5 skips are `tests/external/test_openrouter_smoke.py`
  (`AKASHA_SMOKE_OPENROUTER_API_KEY` not set). These are opt-in external
  smoke tests by design (`AGENTS.md` §26).
- **Warning:** a pre-existing `StarletteDeprecationWarning` from
  `fastapi/testclient.py` (httpx with starlette.testclient). It is unrelated
  to this phase.

---

## 14. Acceptance Criteria Verification

The roadmap acceptance criterion comes first. The scope, content and test
items from the roadmap and the session request are evaluated individually
after it.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Reports can be generated without requiring the frontend | PASS | `ReportService.execution_report` and the HTTP endpoint; integration and API tests |
| Scope: `ReportService` | PASS | `application/reports.py` |
| Scope: `ExecutionReport` | PASS | `application/reports.py` |
| Scope: `ReportGenerator` | PASS (as a function) | `build_report`; decision D-4 |
| Content: project | PASS | `ProjectReport`; success test |
| Content: project version | PASS | `ProjectReport.version`, `published_at`, `version_description` |
| Content: execution status | PASS | `ExecutionSection.status`; every scenario |
| Content: duration | PASS | `duration_ms` (execution and node); success and partial tests |
| Content: nodes | PASS | `NodeReport` per node; `assert_matches_persisted` |
| Content: errors | PASS | `ErrorReport` on execution and nodes; failed and retry tests |
| Content: token usage | PASS | `NodeReport.usage`, `MetricsReport.usage`; success and retry tests |
| Content: cost estimates | PASS | `NodeReport.cost`, `MetricsReport.costs`, `cost_complete`; success, retry and unit tests |
| Content: outputs | PASS | `NodeReport.outputs` (redacted) |
| Content: artifacts | FAIL (deferred) | No artifact concept exists (F-2); not blocking, deferred |
| Content: timestamps | PASS | Created, started and finished (execution); started and finished (nodes); event times |
| At least one export format | PASS | `report_json`; determinism and round-trip test |
| Test: successful report | PASS | `test_successful_execution_report` |
| Test: failed execution report | PASS | `test_failed_execution_report` |
| Test: cost information | PASS | Success and retry tests, plus 9 unit cost cases |
| Test: secret redaction | PASS | `test_report_redacts_secrets`, endpoint test, mutation check |
| Test: artifact references | FAIL (deferred) | Nothing to reference (F-2) |
| Session: trigger | PASS | `trigger_type` and `trigger_id` asserted |
| Session: partial execution | PASS | `test_partial_execution_report` |
| Session: retries | PASS (domain-driven) | `test_retry_history_in_report`; runtime cannot retry yet (F-4) |
| Session: logs where appropriate | PARTIAL | Logs are not persisted (F-1); the event timeline is included instead |
| Session: metrics | PASS | `MetricsReport` |
| Session: authoritative persisted state; events not canonical | PASS | Decision D-2; `assert_matches_persisted` against the reloaded execution |
| Session: serialization/export test | PASS | `test_export_is_deterministic_and_round_trips` |
| Session: deterministic | PASS | Decision D-3; byte-identical test |
| `backend/AGENTS.md` §47: secret-safe, from persisted data, required content | PASS | See the rows above |

**Blocking assessment.** The artifact items and the logs item depend on
subsystems that do not exist (F-1, F-2). Building them is out of Phase 19's
scope under `AGENTS.md` §3.1. They do not block the roadmap's acceptance
criterion, hence **COMPLETE WITH LIMITATIONS** rather than COMPLETE.

---

## 15. Security Considerations

- **Secrets:**
  - every free-text and payload field of the report is redacted (§8);
  - credential plaintext is never decrypted to build a report;
  - the report depends on structural rules plus the known-value masking
    already applied when the runtime persisted outputs and errors.
- **Residual risk:** a secret with no recognisable shape, under a
  non-sensitive key, that did not come from the credential store (for
  example, a password typed into execution input under `"note"`) cannot be
  detected. This is inherent to pattern-based redaction.
- **Sensitive business data:**
  - reports include redacted execution input and node outputs by design (the
    roadmap lists "outputs"), so a report is as sensitive as the execution
    data itself;
  - there is no per-field opt-out.
- **Authorization:** none. Any caller who can reach the API can read any
  execution's report. The same holds for every other endpoint until Phase
  20.
- **Error disclosure:** errors go through the existing redacting `_error`
  handler in `api/app.py`. An unknown id returns a 404 with the id in
  `details` (the same as existing endpoints).
- **Input validation:** the only input is a path string, passed to a
  parameterised repository lookup. Malformed ids simply produce 404s.
- **Logging:** the report path logs nothing beyond the existing request log
  (path only, no query string).
- **Resource exhaustion:** the event timeline is capped at 10,000 entries.
  Node outputs are not capped (see §16 and §21).
- **Not applicable:** XSS, CSRF, SSRF, path traversal, plugin trust,
  expression evaluation and WebSocket. No such surface was added.

---

## 16. Performance Considerations

**Queries per report:**
- 1 execution load (the existing repository: the execution row plus its
  node executions);
- 1 project load (the existing repository loads the full project row
  including the draft graph and files, but only `name` is used);
- `ceil(events / 500)` event queries, with a maximum of 21. They use
  `ix_events_execution_position`.

**Payload size:**
- The report embeds every node's full persisted outputs and the execution
  input.
- Node outputs are bounded only by existing per-node limits (for example,
  `http_max_response_bytes`). Phase 24 is not in place yet.
- A graph with many large outputs can produce a large response. There is no
  pagination or streaming for a single report.

**CPU:** `redact()` walks every output value; its cost is linear in output
size.

**Measurements:** none performed. There are no benchmarks.

**Caching:** none. It would be safe for terminal executions (the report is
deterministic), but it is not needed yet.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §55 | Record the realised design | "Implementation (Phase 19)": service and endpoint, canonical source versus timeline, read ordering, usage and cost shape, determinism rules, redaction, event cap |
| `docs/reports/backend/phase-19-reports.md` | Phase report | This document |

**Should be updated in later phases:**
- `docs/roadmap-backend.md` §23 still lists "artifacts" and "artifact
  references". Either move them to the phase that introduces artifacts, or
  keep them as known gaps.
- A frontend report view (roadmap-frontend) should consume
  `ExecutionReport`. No frontend contract documentation exists for it yet.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded. `pydantic` was already a
  dependency (via FastAPI) and was already used in the application layer
  (`application/project_package.py`).
- **Configuration and environment variables:** none.
- **Docker / database / Redis:** none. No migration.
- **Test infrastructure:** existing fixtures reused (`database`, `uow`,
  `clean_db`); no new fixtures in `conftest.py`.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Dependency / blocker | Affects completion |
|---|---|---|---|---|
| Artifacts and artifact references in reports | No artifact concept exists (F-2) | Unassigned in the roadmap | Artifact storage subsystem | Yes: reason for "WITH LIMITATIONS" |
| Logs in reports | Logs are not persisted (F-1) | Unassigned (log persistence/retention) | Persisted log store | Partially; the timeline substitutes |
| Per-attempt token usage and cost | Needs a domain and schema change (F-3) | Could fit Phase 23 (retry enforcement) | Persisting usage per attempt | No |
| Runtime-driven retry report test | Runtime refuses retry policies (F-4) | Phase 23 | Retry enforcement | No |
| Authorization on `/api/v1/reports` | Out of scope | Phase 20 | Auth subsystem | No |
| CLI access (`akasha report …`) | Out of scope | Phase 21 | CLI | No |
| Additional export formats (Markdown, PDF) | Roadmap requires one; YAGNI | When a consumer needs it | None | No |
| Report listing, search, project or aggregate reports | Not in roadmap scope | Unassigned | None | No |

---

## 20. Known Limitations

- **No logs:** reports contain no log lines (F-1).
- **No artifacts:** reports contain no artifacts (F-2).
- **Usage from failed attempts is not counted:** `metrics.usage` and
  `metrics.costs` count only completed nodes (F-3).
- **Usage is recognised by output shape (D-5):** AI nodes that report usage
  in a different shape are silently not counted.
- **Last attempt only:** node `started_at` and `duration_ms` describe only
  the last attempt. Earlier attempts' start times appear only in the event
  timeline.
- **Running executions:** they have `duration_ms = null` by design (D-3).
- **Not a snapshot read:** the report of a *running* execution is not a
  single snapshot. The timeline may lag the state slightly (§8).
- **Truncated timeline:** it is cut at 10,000 events (`events_truncated`).
  There is no way to fetch the remainder through this endpoint; the event
  store itself is unaffected.
- **Large payloads:** report size is unbounded in node outputs and input
  (§16).
- **Pattern-based redaction:** unrecognisable secrets under non-sensitive
  keys pass through (§15).
- **Not validated on output:** the response is not validated by FastAPI
  against `response_model`. Correctness relies on model construction (D-6).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target phase |
|---|---|---|---|---|---|
| Report payload unbounded by node output size | Large responses and memory use for output-heavy runs | MEDIUM | `application/reports.py`, API | Add an output-size cap or summarisation, or artifact references for large outputs | Phase 24 (resource limits) |
| Reports unauthenticated | Any API client can read any execution's data | MEDIUM (same as all current endpoints) | `api/reports.py` | Put it behind the Phase 20 router dependency | Phase 20 |
| Token usage of failed attempts not persisted | Costs understated | LOW | Domain (`NodeExecution`), `nodes/llm.py` | Persist usage per attempt | Phase 23 |
| Roadmap lists artifacts for Phase 19 | Documentation mismatch | LOW | `docs/roadmap-backend.md` | Re-home the artifact items when artifacts are planned | Next roadmap revision |
| Project load reads the full draft for one field | Unnecessary I/O per report | LOW | `ReportService` | Only if measured: a name-only query | Phase 24, if profiling shows it |

---

## 22. Architectural / Product Decisions

- **Canonical versus history (D-2).** Future report fields must come from
  aggregate state. If a field is only derivable from events, the right fix is
  to persist it on the aggregate, not to compute it from events.
- **No clock in reports (D-3).** Do not add `generated_at` or "elapsed so
  far". It would break the byte-stability that the export guarantees and
  tests enforce.
- **Report bytes equal the API body (D-6).** The endpoint deliberately
  bypasses FastAPI serialisation so that downloads and API responses are
  identical.
- **Usage and cost contract (D-5).** AI nodes (built-in, SDK or plugin) that
  want their usage in reports must emit
  `metadata: {usage: {input_tokens, output_tokens}, cost: {amount, currency} | null}`.
  Changing `core.llm`'s `metadata` shape breaks report metrics silently, so
  treat it as a contract.
- **Computed, not stored (D-1).** There is no report table, so there is no
  migration or retention policy for reports.
- **Function over class (D-4).** `build_report` stays a pure function until a
  second report kind justifies more structure.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **Last commit:** `599e011 backend phase 18 - project import and export`.
  Phase 19 is **not committed**.
- **Working tree (from `git status --short`):**
  ```
   M backend/src/akasha/api/routes.py
   M backend/src/akasha/bootstrap.py
   M docs/architecture.md
  ?? backend/src/akasha/api/reports.py
  ?? backend/src/akasha/application/reports.py
  ?? backend/tests/application/test_reports.py
  ?? backend/tests/integration/test_reports.py
  ?? docs/reports/backend/phase-19-reports.md
  ```
- **Unrelated changes:** none detected. The tree was clean at session start,
  and every change listed belongs to this phase.

---

## 24. Phase Completion Assessment

### Implemented
- `ExecutionReport` models, `build_report`, `report_json` and `ReportService`.
- `GET /api/v1/reports/executions/{execution_id}`.
- Container wiring.
- Architecture §55 notes.

### Verified
- 27 phase tests: 18 unit cases, 7 service-level integration tests and 2
  HTTP-level API tests, all against real PostgreSQL where persistence
  matters.
- Report fields cross-checked against the reloaded persisted execution in
  every scenario.
- Redaction proven by assertions and a mutation check.
- Determinism proven byte for byte.
- The full suite, ruff, ruff format and strict mypy all pass.

### Not Implemented
- Artifacts and artifact references (no artifact concept).
- Logs in reports (no persisted logs).

### Deferred
- See §19: artifacts, logs, per-attempt usage, a runtime-driven retry test,
  authorization, CLI, other formats.

### Open
- See §21: unbounded output payload, no authorization, failed-attempt usage,
  the roadmap artifact mismatch, the project over-fetch.

### Blocking Issues
- None for the roadmap acceptance criterion.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (Phase 20 — Authentication + Authorization)

**What the next phase can assume:**
- `ReportService.execution_report(execution_id) -> ExecutionReport` is
  available from `Container.reports`. It is `None` without a database.
- `report_json(report)` is the canonical, deterministic export.
- `/api/v1/reports/executions/{execution_id}` exists and is unauthenticated.
  It is built by `create_reports_router()` in `api/reports.py`, the same
  pattern as the other routers. A router-level auth dependency can be added
  there.
- Report data is as sensitive as execution input and outputs. Authorization
  should use the same ownership rule as executions and projects: a user who
  may read an execution may read its report.

**Must not be changed casually:**
- the `metadata.usage` and `metadata.cost` output shape of `core.llm` (D-5);
- the determinism rules and `REPORT_SCHEMA_VERSION` semantics (D-3);
- report fields must never be derived from events (D-2);
- redaction must stay in `build_report`, not only at the HTTP layer, so CLI
  and other consumers inherit it.

**Known constraints for later phases:**
- **Phase 21 (CLI):** reuse `ReportService` and `report_json`.
- **Phase 23:** once the runtime enforces retries, add a runtime-driven
  report test, and consider persisting usage per attempt.
- **Phase 24:** bound report size (outputs).

**Prerequisites:** none. Phase 20 can start from the current tree once
Phase 19 is committed.
