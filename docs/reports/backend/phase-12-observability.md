# Phase 12 — Observability

## 1. Executive Summary

**What the phase had to achieve.** Roadmap §16 (Phase 12) asks to make executions "professionally observable". The requested scope is:
- structured logging;
- execution and node correlation IDs;
- metrics;
- execution and node timing;
- error recording.

The metrics must cover execution count, success and failure, duration, node duration, active executions, and queue depth where applicable. The acceptance criterion is: *"A failed execution can be diagnosed from structured backend observability data."* The phase prompt added four requirements: secret-safe logging with central redaction, structured errors, metrics for retries and queue/runtime timing where available, and tests for correlation propagation, the log schema, metrics emission, error serialization, redaction and timing.

**What was implemented.**
- **Redaction module (`akasha.security.redaction`).** It is applied at three choke points: every log formatter, every API error body, and node errors before they are persisted.
- **Log context (`log_context`).** A `ContextVar` that propagates `request_id`, `execution_id`, `node_execution_id`, `node_id`, `node_type`, `project_id` and `correlation_id` into every log line. This includes lines emitted by node code and libraries, because asyncio tasks copy the context they are created in.
- **HTTP middleware.** Assigns one validated request id per request, echoes it in `X-Request-ID`, and writes an `http_request` access line with its duration.
- **Execution observer (`observe_execution`).** After each committed execution save, it turns every persisted lifecycle event into one structured log line with timings and error classification, and into Prometheus metrics.
- **`EngineMetrics`.** 9 instruments, served at `GET /metrics`.
- **Runtime cleanup.** The runtime's previous ad-hoc `_log(...)` lifecycle logging was removed, because the observer replaces it.

**Resulting state.**
- Every execution and node transition that the runtime drives produces exactly one structured log line after commit. The line carries ids, the correlation id, `duration_ms` / `queue_wait_ms`, and for failures `error_code`, `error_kind`, `error_message` (redacted) and `error_node_id`.
- The metrics reflect the same transitions.
- The full backend suite passes: 973 tests, 0 skipped (905 before; 68 net new). Format, lint, strict mypy and `uv build` pass as well.

**Acceptance.** The roadmap acceptance criterion is satisfied with direct evidence (§14). Several measurable items cannot be emitted yet because the underlying subsystem does not exist:
- queue depth (no queue before Phase 22);
- retries (no retries before Phase 23);
- resource usage (no token/cost accounting before Phases 14/15).

In addition, the runtime's metrics are not yet visible on the container's `/metrics` endpoint, because no production code constructs `ExecutionRuntime` yet (pre-existing Phase 11 open issue). These are documented limitations rather than failed criteria. "Queue depth where applicable" is not yet applicable.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §16, Phase 12 — Observability**
- **Goal:** "Make executions professionally observable."
- **Scope:** structured logging, execution correlation IDs, node correlation IDs, metrics, execution timing, node timing, error recording.
- **Collect:** execution count, success/failure, duration, node duration, active executions, queue depth where applicable.
- **Tests:** correlation IDs, structured log fields, metrics, execution timing, secret-safe logging.
- **Acceptance criterion:** "A failed execution can be diagnosed from structured backend observability data."

**Phase prompt additions**
- Implement node execution IDs, structured errors, execution observability and secret-safe logging.
- Metrics for retries, queue/runtime timing where available, and resource usage where already supported.
- Secrets must never appear in logs, exceptions, events, metric labels or debug output.
- Redaction must be central, not scattered string replacements.
- Tests for correlation propagation, the structured log schema, metrics emission, error serialization, secret redaction and execution timing.
- Completion condition: "An operator can understand what happened during an Execution without inspecting source code."

**Architecture references**
- §50 Observability: logs, metrics, traces, events, execution history; example log line.
- §51 Logging rules: structured, correlation ids, no secrets, no raw payloads.
- §52 Correlation IDs: `request_id`, `execution_id`, `node_execution_id`, `project_id`, `project_version_id`; `trace_id`/`span_id` where appropriate.
- §109 Secrets redaction at multiple levels.
- §113 Recommended metrics.

**Explicitly not in this phase**
- Credential store and known-value secret redaction (Phase 13).
- LLM token and cost metrics (Phases 14/15).
- Authentication of operational endpoints (Phase 20).
- Queue and worker metrics (Phase 22).
- Retry behaviour (Phase 23).
- Distributed tracing.
- HTTP or WebSocket access to event and log history.

---

## 3. Scope Implemented

### Security
- **New `backend/src/akasha/security/redaction.py`:**
  - `MASK = "[REDACTED]"`.
  - `is_sensitive_key(key)` (§8.1).
  - `redact_text(text)`: 8 compiled text rules.
  - `redact_mapping(data)`.
  - `redact(value)`: recursive and JSON-friendly; stringifies unknown objects before masking.

### Observability
- **`backend/src/akasha/observability/logging.py` (modified):**
  - New: `log_context(**fields)` context manager, `current_log_context()`, `TextFormatter`.
  - Changed `JsonFormatter`: ISO-8601 UTC millisecond timestamp, merges the bound context, `redact(payload)` before `json.dumps`.
  - Changed `configure_logging`: sets the UTC converter on the text formatter instance instead of mutating the global `logging.Formatter.converter`.
- **New `backend/src/akasha/observability/metrics.py`:** `EngineMetrics` (own `CollectorRegistry`, 9 instruments, `render()`), plus `CONTENT_TYPE`.
- **New `backend/src/akasha/observability/execution.py`:** `observe_execution(execution, events, metrics)`, `_error_fields`, `_seconds`.
- **`backend/src/akasha/observability/__init__.py`:** the layer docstring was updated. The layer now depends on domain (read-only), `prometheus_client` and `security.redaction`, so it is no longer described as a leaf.

### Runtime
- **`backend/src/akasha/runtime/runtime.py`:**
  - `StateManager.__init__` takes `metrics`.
  - `StateManager.save` pulls events after commit, calls `observe_execution`, then publishes.
  - `ExecutionRuntime.__init__(..., metrics=None)`.
  - `run()` increments and decrements `active_executions` in `try/finally` and binds `log_context(execution_id=...)`.
  - `_launch` creates each node task inside `log_context(execution_id, project_id, correlation_id, node_id, node_execution_id, node_type)`.
  - Removed: the module-level `logger`, the `_log()` function, the `_finish()` method, all 9 `_log(...)` calls (8 direct, 1 inside `_finish`) and the 4 `_finish(...)` calls, which now call `self._state.save(...)` directly.
- **`backend/src/akasha/runtime/executor.py`:**
  - `execute_node` now wraps a renamed `_execute` and returns `ExecutionError`s with `message` passed through `redact_text` and `details` through `redact_mapping`.
  - The comment on `node_unexpected_error` was updated.

### Application
- **`backend/src/akasha/application/executions.py`:**
  - `ExecutionService.__init__(uow, events=None, metrics=None)`.
  - `create_execution` observes the pulled events before publishing.
  - The hand-written `execution_created` log and the module `logger` were removed; the observer emits `execution.created` instead.
- **`backend/src/akasha/application/triggers.py`:** `TriggerService._create` defaults `correlation_id` to `new_id("corr")` when the trigger context has none (scheduler-fired executions).

### API
- **`backend/src/akasha/api/app.py`:**
  - New `register_request_observability(app)`: an HTTP middleware that assigns the request id, binds `log_context(request_id=...)`, logs `http_request`, and sets the `X-Request-ID` response header.
  - New `_error(...)` helper, used by all four exception handlers: it redacts `message` and `details` and sets `X-Request-ID`.
- **`backend/src/akasha/api/schemas.py`:**
  - New `accept_request_id(header)`.
  - `request_id(request)` now returns the middleware-assigned id from `request.state`. It previously minted a new UUID per call when the header was absent.
- **`backend/src/akasha/api/routes.py`:** new `GET /metrics` on the operational router (`include_in_schema=False`).
- **`backend/src/akasha/api/triggers.py`:** the trigger context and manual execute use `request_id(request)` as the correlation id instead of the raw `x-request-id` header.

### Composition
- **`backend/src/akasha/bootstrap.py`:**
  - `Container.metrics: EngineMetrics`, with a default factory.
  - `create_container` builds one instance, passes it to `ExecutionService` and stores it on the container.

### Testing
- **New test files:**
  - `tests/test_redaction.py` (43 tests);
  - `tests/test_execution_observer.py` (4 tests);
  - `tests/test_request_observability.py` (10 tests);
  - `tests/integration/test_observability.py` (3 tests).
- **Rewritten:** `tests/test_logging.py` (1 → 9 tests).
- **Helper:** `tests/support.py` gains `capture_json_logs()`, which captures lines through the production `JsonFormatter`.
- **Adapted:** `tests/integration/test_triggers_api.py` (2 assertions, §7).

### Documentation
- `docs/architecture.md`: "Implementation (Phase 12)" subsections under §52, §109 and §113.
- This report.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/security/redaction.py` | Created (112 lines) | Central redaction |
| `backend/src/akasha/observability/metrics.py` | Created (86 lines) | `EngineMetrics`, Prometheus exposition |
| `backend/src/akasha/observability/execution.py` | Created (109 lines) | Committed events → log lines + metrics |
| `backend/src/akasha/observability/logging.py` | Modified (+56/−) | Log context, redaction, UTC ISO timestamps, `TextFormatter` |
| `backend/src/akasha/observability/__init__.py` | Modified | Layer dependency rules updated |
| `backend/src/akasha/runtime/runtime.py` | Modified (99 lines changed) | Observer hook, gauge, node log context; `_log`/`_finish` removed |
| `backend/src/akasha/runtime/executor.py` | Modified | Redacts node error message and details |
| `backend/src/akasha/application/executions.py` | Modified | Metrics injection; observer on create |
| `backend/src/akasha/application/triggers.py` | Modified | Default correlation id for scheduled runs |
| `backend/src/akasha/api/app.py` | Modified (99 lines changed) | Request middleware; redacted `_error` helper |
| `backend/src/akasha/api/schemas.py` | Modified | `accept_request_id`; single request id per request |
| `backend/src/akasha/api/routes.py` | Modified | `GET /metrics` |
| `backend/src/akasha/api/triggers.py` | Modified | Correlation id from the assigned request id |
| `backend/src/akasha/bootstrap.py` | Modified | `Container.metrics` wiring |
| `backend/pyproject.toml` | Modified | `prometheus-client~=0.26` |
| `backend/uv.lock` | Modified | Locks `prometheus-client` 0.26.0 |
| `backend/tests/test_redaction.py` | Created (43 tests) | Redaction unit tests |
| `backend/tests/test_logging.py` | Rewritten (9 tests) | Schema, context, propagation, redaction |
| `backend/tests/test_execution_observer.py` | Created (4 tests) | Observer logs, timings, metrics, label safety |
| `backend/tests/test_request_observability.py` | Created (10 tests) | Request id, access log, error redaction, `/metrics` |
| `backend/tests/integration/test_observability.py` | Created (3 tests) | Acceptance scenario on PostgreSQL; gauge |
| `backend/tests/support.py` | Modified | `capture_json_logs()` helper |
| `backend/tests/integration/test_triggers_api.py` | Modified | Correlation id is now always set (§7) |
| `docs/architecture.md` | Modified (+78 lines) | §52, §109, §113 implementation notes |
| `docs/reports/backend/phase-12-observability.md` | Created | This report |

- No directories were created: `security/` and `observability/` already existed.
- No files were deleted.
- No migrations.
- No generated files in the repository; the `uv build` output went to a scratch directory outside it.
- Diff totals for tracked files: 17 files changed, 499 insertions, 154 deletions. In addition there are 8 untracked new files, including this report.

---

## 5. Architecture Changes

The decisions below are recorded in `docs/architecture.md` §52, §109 and §113.

### 5.1 Observability is derived from committed events plus the aggregate

**Decision.** Lifecycle logs and metrics are produced by a single function, `observe_execution`. It is called right after an execution save commits and receives the events that save persisted, together with the aggregate that produced them.

**Context.**
- Phase 11 made the `Execution` aggregate record every lifecycle transition as an event, including the terminal cascade that cancels unfinished nodes.
- The runtime also had 9 separate `_log(...)` calls with inconsistent fields: no `node_execution_id`, no durations.
- The terminal cascade was not logged per node at all.

**Chosen approach.**
- `StateManager.save` and `ExecutionService.create_execution` pull the events after commit.
- `observe_execution(execution, events, metrics)` then derives the log line and the metrics for each event.
- Durations are computed from the event's `occurred_at` and the aggregate's `started_at` / `created_at`.
- The runtime's `_log` calls were deleted.

**Alternatives considered.**
- (a) An `EventPublisher` subscriber. Rejected: events carry no timings or node execution ids, so a subscriber would need its own start-time state. That state would leak entries on at-most-once delivery, and would give wrong values across processes.
- (b) Keep instrumenting at runtime call sites. Rejected: it can miss transitions, for example the cascade, and it duplicates field sets.
- (c) Add timings to event payloads. Rejected: `PAYLOAD_KEYS` requires exact key sets, so persisted schema-1 events would no longer validate.

**Reason.** Every evented transition is observed exactly once, from one place, with the same field set. Using domain timestamps makes timings follow the injected clock, which makes them deterministic in tests.

**Consequences.**
- Do not add lifecycle `logger.info` calls to the runtime.
- New evented transitions (e.g. retries in Phase 23) are observed automatically. If they need extra fields, `observe_execution` must handle them.
- Observation shares the at-most-once property of publication (§20).

### 5.2 Redaction at choke points, not at call sites

**Decision.** A single module, `akasha.security.redaction`, is applied at three choke points only:
1. the log formatters;
2. the API error mapper `_error`;
3. `execute_node`, for node-authored error text.

**Context.** The phase prompt requires central redaction. Backend AGENTS §29 requires redaction to be structural, not only filtering known field names.

**Chosen approach.** Two rule families:
- **Key-based:** a mapping value under a sensitive key is masked whole, whatever its type.
- **Text-based:** credential-shaped substrings are masked.

`redact()` walks nested mappings and sequences and stringifies unknown objects *before* masking.

**Alternatives considered.** A `logging.Filter` per handler (it would miss pytest's handler and would need attaching everywhere). Redacting at each log call (scattered, which the prompt forbids).

**Reason.** Every emitted log line passes through a formatter. Every error body passes through `_error`. Every node failure passes through `execute_node`. Call sites cannot forget.

**Consequences.**
- The module lives in `security/` because it is a security primitive. Observability and the API depend on it; the domain does not.
- Phase 13 is expected to add known-value masking to the same module.

### 5.3 Correlation through a `ContextVar`

**Decision.** Correlation fields are bound with `log_context(...)` and read by `JsonFormatter`. The bound values sit between the fixed fields and the record's own `extra`, so explicit fields win.

**Context.** Node code (for example `core.log`) and libraries log through ordinary loggers. The prompt requires node execution ids in node logs without changing the Node contract.

**Chosen approach.** The runtime creates each node task inside a `log_context`. `asyncio.create_task` copies the current context, so every line the task emits carries the ids. The HTTP middleware binds `request_id` the same way.

**Alternatives considered.** A `LoggerAdapter` on `NodeExecutionContext.logger` (would not cover library loggers or module-level loggers inside nodes). A global `logging.setLogRecordFactory` (process-global side effect at import).

**Consequences.**
- Fields are attached at *format* time. A future asynchronous handler (`QueueHandler`) would format in another thread and lose the context; such a handler would first need a filter that copies the context onto the record.
- `caplog.records` do not contain bound fields. Tests use `capture_json_logs()`.

### 5.4 One request id per request; it becomes the correlation id

**Decision.** The middleware assigns the request id exactly once:
- the caller's `X-Request-ID` if it is 1–128 printable characters;
- otherwise a new UUID4.

It is stored on `request.state`, bound to the logs, echoed in the response header and used by every error body. Trigger endpoints pass it as the execution's `correlation_id`.

**Context.**
- Previously `schemas.request_id()` minted a new UUID on every call when the header was absent, so a request's error body and its log lines could carry different ids.
- Previously triggers passed the raw header. Any header over 128 characters or non-printable reached `ExecutionMetadata` validation and failed.
- Previously executions created without a header had no correlation id.

**Consequences.**
- Every API-created execution has a correlation id (§7).
- Scheduler-created executions get `corr_<uuid>`.
- Executions created directly through `ExecutionService` without metadata keep `correlation_id=None`.

### 5.5 Prometheus via `prometheus-client`, per-instance registry

**Decision.** Metrics use `prometheus-client`. Each `EngineMetrics` owns a `CollectorRegistry`; the global default registry is never used.

**Alternatives considered.**
- A hand-written counter/histogram registry with text exposition (≈100 lines, plus escaping and histogram semantics to maintain).
- OpenTelemetry (a larger dependency and an exporter setup with no consumer yet).

**Reason.** The Prometheus text format is the de-facto scrape standard. The library has zero transitive dependencies, is Apache-2.0 licensed and ships `py.typed`. Per-instance registries avoid hidden singletons (AGENTS §68) and allow test isolation.

**Consequences.** One `/metrics` per process. With workers (Phase 22), each process exposes its own metrics and Prometheus aggregates them.

---

## 6. Domain / Data Model Changes

No changes to domain entities, value objects, enums, event types, event payload schemas, database tables, indexes or migrations. Specifically:
- `Event`, `PAYLOAD_KEYS` and `EVENT_SCHEMA_VERSION = 1` are unchanged.
- `Execution` and `ExecutionError` are unchanged.

**Semantic change to persisted data (not schema).** `ExecutionError.message` and `ExecutionError.details` produced by node execution are redacted before persistence. Rows written before this phase are not rewritten.

**New non-domain types:**
- `EngineMetrics` (observability);
- `JsonFormatter` / `TextFormatter` behaviour (observability);
- the redaction functions (security).

---

## 7. API / Contract Changes

| Contract | Change | Details | Compatibility |
|---|---|---|---|
| All HTTP responses | **New header** `X-Request-ID` | The caller's valid id or a minted UUID4. Also set on responses produced by the 500 handler. | Additive |
| `ErrorResponse.request_id` | **Changed semantics** | Equals the response's `X-Request-ID` and the `request_id` in the server logs. Previously a fresh UUID per call when the header was absent. | Compatible schema; value now meaningful |
| `ErrorResponse.message`, `.details` | **Redacted** | Credential-shaped text and values under sensitive keys become `[REDACTED]` | Compatible schema |
| `X-Request-ID` request header | **Validated** | Values that are empty, longer than 128 characters or non-printable are replaced by a minted id instead of being passed on | Previously such values could cause a 422 through `ExecutionMetadata` validation on trigger endpoints |
| `ExecutionDetail.correlation_id` (trigger endpoints) | **Changed** | Always set: the caller's request id or the minted one. Previously `null` when no header was sent. | Behavioural change. `tests/integration/test_triggers_api.py::test_invoke_api_trigger` and `::test_invoke_manual_trigger_allowed` updated to expect `response.headers["x-request-id"]`. |
| `GET /metrics` | **New endpoint** | Prometheus text exposition, content type `text/plain; version=…`. Not in OpenAPI. No authentication (like `/health`). | Additive |
| Log contract | **Changed names** | Runtime lifecycle lines were `execution_started`, `node_started`, `node_completed`, `node_failed`, `node_skipped`, `execution_completed`/`_failed`/`_cancelled`/`_timed_out` (logger `akasha.runtime`); the service line was `execution_created`. They are now the event type values (`execution.started`, `node.failed`, …) on logger `akasha.execution`, plus `node.cancelled`. New `http_request` line on logger `akasha`. | Breaking for log consumers matching old names; no consumers exist in the repository |
| `ExecutionService.__init__` | **Extended** | `(uow, events=None, metrics=None)` | Backward compatible |
| `ExecutionRuntime.__init__` | **Extended** | `(..., events=None, metrics=None)` | Backward compatible |
| `StateManager.__init__` | **Changed** | `(uow, events, metrics)`: new required parameter | Internal; only constructed by `ExecutionRuntime` |
| `Container` | **Extended** | `metrics: EngineMetrics` field | Backward compatible |

**Unchanged:** event payloads, the node contract, `NodeExecutionContext`, and the plugin/SDK contracts.

---

## 8. Implementation Details

### 8.1 Redaction rules (`security/redaction.py`)

**Key-based rule.** `is_sensitive_key` splits the key on non-alphanumerics and camelCase boundaries and lower-cases the parts.
- A key is **not** sensitive if its last part is one of `{id, ids, ref, reference, type, kind, count, usage, total}`. This keeps `credential_id`, `token_type`, `token_count` and `token_usage` readable.
- Otherwise it is sensitive if any part is one of `{password, passwd, pwd, passphrase, secret, token, apikey, authorization, auth, cookie, credential, credentials, privatekey, signature}`, or if the joined parts contain `apikey` or `privatekey`.
- Examples: `API_KEY`, `apiKey` and `x-api-key` are sensitive; `max_tokens` and `author` are not.

**Text rules**, applied in this order:
1. URL userinfo: `scheme://user:pass@` → `scheme://[REDACTED]@`.
2. `Bearer|Basic <8+ token chars>` (case-insensitive).
3. `<sensitive-key>` followed by `=`, `:` or `"key": "`, then the value. Plurals are matched only for `passwords`, `secrets`, `api_keys`, `credentials` and `private_keys`, so `tokens: 42` survives.
4. `sk-…` (16+ characters).
5. `ghp_`/`gho_`/`ghs_`/`github_pat_…`.
6. `xox[abprs]-…`.
7. `AKIA` + 16 characters.
8. JWT-shaped `eyJ….….…`.

**`redact(value)` by type:**
- `None`, `bool`, `int`, `float`: returned unchanged.
- `str`: text rules.
- `Mapping`: key rule, then recursion.
- `list`/`tuple`/`set`: become a list.
- Anything else: `str()`, then text rules.

### 8.2 Log line assembly (`JsonFormatter.format`)

**Field order** (later sources win):
1. fixed fields: `timestamp`, `level`, `logger`, `message`;
2. the bound `log_context`;
3. the record's own `extra` (non-reserved record attributes);
4. `exception` text, if any.

The whole dict is passed through `redact` and then `json.dumps` (no `default=str` needed, since `redact` stringifies).

### 8.3 Execution observer (`observe_execution`)

**Common fields on every event:** `event`, `event_id`, `execution_id`, `project_id`, `project_version`, `correlation_id`, plus the event payload (`trigger_type`, `trigger_id`, `attempt`, `error_code`, `error_kind`, `error_node_id`, as defined per type).

**Per event type:**

| Event | Extra log fields | Metrics |
|---|---|---|
| `execution.created` | payload | `executions_created{trigger_type}` |
| `execution.started` | `queue_wait_ms` | `executions_started`; `execution_queue_wait_seconds` |
| `execution.completed` / `failed` / `cancelled` / `timed_out` | `duration_ms` if started; error fields from `execution.error` | `executions_finished{status}`; `execution_duration_seconds{status}` if started |
| `node.started` | `node_id`, `node_execution_id`, `node_type` | — |
| `node.completed` / `failed` / `skipped` / `cancelled` | node ids; `duration_ms` if the node started; for `failed`, error fields from the last failure | `node_executions_finished{node_type,status}`; `node_execution_duration_seconds{node_type,status}` if started; for `failed`, `node_execution_errors{node_type,error_kind}` |

- `node_type` is looked up in `execution.project_version.graph.nodes[node_id].node_type`.
- The `status` label is the event-type suffix, a closed set.
- Level is WARNING for `execution.failed`, `execution.timed_out` and `node.failed`; INFO otherwise.

### 8.4 Runtime flow with observability

1. `ExecutionRuntime.run(id)` increments `active_executions`, binds `log_context(execution_id=id)` and runs `_run`. A `finally` block decrements the gauge.
2. Every `self._state.save(execution)` runs: UoW `save_execution` → commit → `pull_events()` → `observe_execution` → `EventPublisher.publish`.
3. `_launch` persists nodes as RUNNING, then creates each node task inside a node-level `log_context`.
4. `execute_node` redacts any `ExecutionError` before returning it. The runtime then records the redacted error, persists it, and logs it through the observer.

### 8.5 HTTP flow

1. The middleware, registered with `@app.middleware("http")`, runs `accept_request_id` and sets `request.state.request_id`.
2. `call_next` runs inside `log_context(request_id=...)`. Starlette's task group copies the context into the endpoint task.
3. `finally`: log `http_request` with `method`, `path` (no query string), `status_code` (500 if `call_next` raised) and `duration_ms`.
4. The response gets `X-Request-ID`.
5. Unhandled exceptions: they propagate through the middleware to Starlette's `ServerErrorMiddleware`. There the `Exception` handler logs `unhandled_error` with an explicit `request_id` (read from `request.state`) and returns `_error(...)`, which also sets `X-Request-ID`.

### 8.6 Dependency injection

- `create_container` builds one `EngineMetrics` and shares it between `ExecutionService` and `Container.metrics`, which backs `/metrics`.
- `ExecutionService`, `ExecutionRuntime` and `Container` without an explicit instance create a private one. This matches the `events` pattern from Phase 11.

---

## 9. Findings

### F1 — Request id was not stable within a request
- **Finding:** `api/schemas.request_id()` minted a new UUID on every call when the header was absent. A request's error body and its log lines therefore carried unrelated ids. Only the 500 handler logged at all, and it minted yet another id.
- **Impact:** MEDIUM. Operators could not correlate a client-reported error with server logs.
- **Resolution:** FIXED (§5.4). The id is assigned once per request.
- **Notes:** Pre-existing since Phase 01.

### F2 — Raw `x-request-id` could fail execution creation
- **Finding:** `api/triggers.py` passed the raw header as `correlation_id`. `ExecutionMetadata` rejects values that are not 1–128 printable characters with `DomainValidationError`. An over-long header would therefore turn a valid execute or webhook call into a 422.
- **Impact:** LOW. It is client-triggerable, but only with malformed tracing headers.
- **Resolution:** FIXED. Header validation in `accept_request_id` uses the same rule, and invalid values are replaced.
- **Notes:** Pre-existing since Phase 10. There is no dedicated regression test for the trigger endpoint. The replacement behaviour is tested at the HTTP layer (`test_missing_or_unsafe_request_id_is_replaced`).

### F3 — Terminal cascade was invisible in logs
- **Finding:** Nodes cancelled by `Execution._finish` had no log line. The runtime's `_log` only covered transitions it drove itself.
- **Impact:** MEDIUM for diagnosis. Which downstream nodes never ran could only be read from the database.
- **Resolution:** FIXED by §5.1. `node.cancelled` lines are asserted in `test_failed_execution_is_diagnosable_from_logs_and_metrics`.

### F4 — `configure_logging` mutated a global
- **Finding:** `logging.Formatter.converter = time.gmtime` changed the converter for every formatter in the process.
- **Impact:** LOW.
- **Resolution:** FIXED. The JSON timestamp is built from `record.created` in UTC, and the text formatter sets its own `converter`.

### F5 — Naive text redaction produced false positives
- **Finding:** Two patterns in the first draft masked ordinary prose: `token <word>` and the plural `tokens: 42`. For example, "token validation failed" became "token [REDACTED] failed".
- **Impact:** LOW. It would degrade diagnostics, not security.
- **Resolution:** FIXED before the tests were written. `token` was removed from the scheme rule, and plurals are limited to secret-bearing words. Guarded by `test_ordinary_text_is_left_alone`.

### F6 — Docker probe flakes on a cold Docker Desktop
- **Finding:** Two full-suite runs skipped 175 and 178 PostgreSQL/Mailpit-backed tests because `docker_available()` (2 s ping timeout) returned False. Immediately after `docker info`, the probe returned True and all tests ran.
- **Impact:** LOW (environmental), but integration tests can be silently skipped.
- **Resolution:** WORKAROUND. Re-runs after warming Docker; the final results in §13 have 0 skipped.
- **Notes:** Same as Phase 11 F5; still open (§21).

### F7 — Runtime metrics cannot reach the container's `/metrics` yet
- **Finding:** `create_container` does not construct an `ExecutionRuntime`; no production code path does yet.
- **Impact:** MEDIUM for operations. In a deployed API process, `/metrics` exposes only `akasha_executions_created_total`; the run, node and duration metrics stay at zero there.
- **Resolution:** DEFERRED to Phase 21/22, the same open issue as Phase 11's `events=` wiring.
- **Notes:** Documented in architecture §113 and on the `Container.metrics` field comment.

---

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Area | Origin |
|---|---|---|---|---|---|---|
| B1 | Error body `request_id` did not match the logs | `request_id()` minted an id per call (F1) | Middleware assigns one id | `test_error_body_and_log_share_one_request_id`, `test_caller_request_id_is_echoed_and_bound_to_request_logs` | API | Pre-existing (Phase 01) |
| B2 | Malformed `X-Request-ID` could cause a 422 on trigger endpoints | Raw header passed to validated metadata (F2) | `accept_request_id` | `test_missing_or_unsafe_request_id_is_replaced` (HTTP layer) | API / triggers | Pre-existing (Phase 10) |
| B3 | Five source files blanked during the first mutation run | See §11.4 | Rebuilt and verified | Full suite 973/973; mutation re-run with checksum verification | Tooling (this session) | Introduced and fixed during this phase |
| B4 | Botched splice while tightening redaction patterns (the first `_TEXT_RULES` mention sat inside the docstring) | String index hit the docstring | File rewritten whole | `tests/test_redaction.py` | Tooling (this session) | Introduced and fixed during this phase |
| B5 | Integration test referenced fixture `h`, which is defined in `test_runtime.py` | Fixtures are not shared by importing a module | Local `h` fixture | the tests themselves | Tests | Introduced and fixed during this phase |
| B6 | mypy errors in the new tests: `utcoffset()` Optional; un-annotated `stored` | Test typing | `== timedelta(0)`; `list[str]` annotation | mypy strict pass | Tests | Introduced and fixed during this phase |
| B7 | Two trigger API assertions expected `correlation_id is None` | Intended contract change (§7) | Assertions now require the echoed request id | the updated tests | Tests | Behaviour change of this phase |

No pre-existing product bugs were found beyond B1 and B2.

---

## 11. Fixes Applied

1. **Stable request id (B1).**
   - Symptom: mismatched ids.
   - Implementation: `accept_request_id` plus the middleware; `request_id()` reads `request.state`.
   - Verification: 3 API tests (§12).
2. **Header validation (B2).**
   - Implementation: 1–128 printable characters, otherwise a UUID4.
   - Verification: a 5-way parametrised test (`None`, `""`, 129 characters, newline, tab).
3. **Cascade and lifecycle logging (F3).**
   - Implementation: `observe_execution` replaces `_log`.
   - Verification: an exact event-sequence assertion on PostgreSQL.
4. **Blanked-file recovery (B3).**
   - Symptom: after the first mutation script, `runtime/executor.py`, `runtime/runtime.py`, `api/app.py`, `observability/logging.py` and `observability/execution.py` were 0 bytes.
   - Root cause: the shell function backed up to `/tmp/_mut_bak`, but a preceding `cp /dev/null "$TMPDIR/_mut_bak"` left an empty file at the restore source `$TMPDIR/_mut_bak`, and the restore command preferred that path.
   - Implementation:
     - `observability/execution.py` was restored from the one valid backup at `/tmp/_mut_bak`, which held its unmutated content.
     - `executor.py`, `runtime.py` and `app.py` were restored from `git show HEAD:…`, and the exact edit scripts used earlier in the session were re-applied.
     - `logging.py` was rewritten from its known final content.
     - All temporary backups were deleted.
   - Verification: `ruff`, strict `mypy` and the full suite (973 passed, 0 skipped).
   - Process correction: the mutation check was re-run with a Python script that keeps the original content in memory, restores it in `finally` and asserts the SHA-256 digest of every restored file ("all restored" printed).
5. **Global converter (F4).** Removed; the UTC ISO timestamp is asserted in `test_json_line_schema`.
6. **Redaction false positives (F5).** Patterns narrowed; 6 negative cases in the tests.

---

## 12. Testing & Verification

### Unit Tests
- **`tests/test_redaction.py` (43 tests, all pass):**
  - 15 credential shapes masked, with the secret substring asserted absent: URL userinfo, Bearer, Basic, `api_key=`, JSON `"password"`, `client_secret:`, `X-Api-Key=`, `refresh_token='…'`, `sk-ant-…`, `sk-live-…`, GitHub PAT, Slack, AWS, JWT, `password=` in prose.
  - 6 ordinary strings unchanged.
  - 12 sensitive and 9 non-sensitive keys.
  - Nested structure: non-string values under sensitive keys, tuples to lists, an object whose `__str__` contains a secret.
- **`tests/test_logging.py` (9 tests, all pass):**
  - schema: required fields and a UTC offset of 0;
  - context bind, nest and restore, with an empty context afterwards;
  - `extra` precedence over the bound context;
  - propagation into two interleaved asyncio tasks without bleed;
  - redaction of the message, context, `extra` and exception text (canary absent from the whole output);
  - unknown objects;
  - `TextFormatter`;
  - `configure_logging` in both modes writes redacted lines to stdout.
- **`tests/test_execution_observer.py` (4 tests, all pass)**, with the real `Execution` aggregate and fixed timestamps:
  - A successful run: exact event sequence and ids on every line. Durations 250 ms / 1000 ms / 4000 ms and queue wait 2000 ms, exact. Exact counter and histogram sums. The node output is absent from the logs.
  - Failure diagnosis: WARNING level, error fields, cascade `node.cancelled` without a duration, `executions_finished{status="failed"}`, `node_execution_errors{…, error_kind="transient"}`.
  - Cancelled before start: no duration, the cancellation reason `password=hunter2` absent, no duration histogram sample.
  - Label safety: the exposition output contains no execution or node id, correlation id, secret, error code or `api_key`. Label names ⊆ `{trigger_type, status, node_type, error_kind, le}`.

### API Tests
**`tests/test_request_observability.py` (10 tests, all pass; FastAPI `TestClient`, no database):**
- A caller's id is echoed and appears on the handler's log line and on the access line (method, path, status, integer `duration_ms`).
- Missing or unsafe ids (5 cases) are replaced, with a fresh id per request.
- The query string is absent from the logs.
- On a 500, body, header, `unhandled_error` log and access line share one id; the traceback is logged but redacted; the client gets neither the message nor the exception type.
- `AkashaError` message and details are redacted in the body.
- `/metrics` returns 200, `text/plain`, and all six asserted metric families.

**Existing API tests** (`test_projects_api.py`, `test_triggers_api.py`, `test_errors.py`, `test_health.py`) pass. Two assertions were adapted (§7, B7).

### Integration Tests
**`tests/integration/test_observability.py` (3 tests, all pass on PostgreSQL 17.2 via Testcontainers):**
- `test_failed_execution_is_diagnosable_from_logs_and_metrics` (acceptance):
  - Graph A→B→C, where B raises `NodeError` with `api_key=<canary>` in its message and `authorization: Bearer <canary>` in its details.
  - Node code logs without ids; the captured line has `execution_id`, `node_id`, `node_execution_id`, `node_type` and `correlation_id`.
  - The execution logger's sequence is exactly started → A started/completed → B started/failed → C cancelled → execution failed.
  - Error fields are present. `duration_ms` equals the persisted `finished_at − started_at`, for the node and for the execution. `queue_wait_ms` equals `started_at − created_at`.
  - Metrics match, and `active_executions` is 0 afterwards.
  - The canary is absent from all log lines, from the loaded `ExecutionError.message`/`details`, and from the raw `executions.error` and `node_executions.failures` rows (3 rows checked).
- `test_active_executions_gauge_tracks_concurrent_runs`: two runs blocked in a node; the gauge reads 2, then 0 after release; `finished{completed}` is 2.
- `test_active_executions_gauge_is_released_when_a_run_errors`: an unknown execution id raises `ExecutionNotFoundError`; the gauge is 0.

### Infrastructure Tests
- No infrastructure changes (no migration, no container change).
- The existing migration tests and the Testcontainers PostgreSQL (`postgres:17.2`) and Mailpit (`axllent/mailpit:v1.21.8`) fixtures run in the full suite and pass.

### Security Tests
Redaction, header validation, query-string omission, error-body redaction, persisted-error redaction on PostgreSQL, metric-label safety, and absence of the cancellation reason, all as listed above.

### Mutation Verification
14 mutations were run against the 69 new tests with the checksum-verified script (§11.4). **All 14 were killed**:
1. executor message redaction removed;
2. executor details redaction removed;
3. formatter redaction removed;
4. bound context dropped from the formatter;
5. `node_execution_id` dropped from the node context;
6. runtime observer unhooked;
7. gauge decrement removed;
8. API message redaction removed;
9. API details redaction removed;
10. `X-Request-ID` echo removed;
11. request-id validation removed;
12. node error fields dropped;
13. execution duration measured from `created_at`;
14. non-secret suffix allowlist removed.

### Not Performed
- E2E tests: no backend E2E suite exists.
- Performance tests and benchmarks: none were run.
- Accessibility: backend only.
- A real Prometheus scrape: the exposition format was verified by content type and `# TYPE` lines only.

---

## 13. Quality Gates

All gates were executed in `backend/` on the final working tree.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `uv run ruff format --check .` → 114 files already formatted |
| Linter | PASS | `uv run ruff check .` → All checks passed |
| Type Checker | PASS | `uv run mypy .` (strict) → no issues in 112 source files. Two test-typing errors during development were fixed (B6). |
| Unit Tests | PASS | Included in the full run |
| Integration Tests | PASS | Included in the full run; Docker reachable, 0 skipped. Two earlier runs skipped 175/178 tests because of F6 and were repeated. |
| API Tests | PASS | Included; 2 assertions adapted (B7) |
| Full test suite | PASS | `uv run pytest` → **973 passed, 0 skipped**, 1 warning (≈38–41 s). New-test subset: 69 passed. |
| E2E Tests | N/A | No backend E2E suite exists |
| Build | PASS | `uv build` → wheel built (output in a scratch directory outside the repository) |
| Security Checks | PASS | Ruff `S` rules and the security tests in §12. No dependency-audit tool is configured; `prometheus-client` was reviewed manually (§18). |
| Accessibility | N/A | Backend only |
| Architecture guard | PASS | `tests/test_architecture.py` (dependency direction) |
| Mutation check | PASS | 14/14 killed |

**Warning (pre-existing, unrelated):** `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`, raised from `fastapi/testclient.py`.

**Intermediate failures:**
- Two trigger API tests failed after the correlation change (B7) and were adapted.
- During recovery (B3), mypy reported the two test errors (B6), which were fixed.

---

## 14. Acceptance Criteria Verification

**Roadmap §16:**

| Acceptance Criterion / Required Item | Status | Evidence |
|---|---|---|
| A failed execution can be diagnosed from structured backend observability data | PASS | `test_failed_execution_is_diagnosable_from_logs_and_metrics`: failing node, error code/kind/message, cascade, durations, correlation id, metrics — from log lines and metrics only |
| Scope: structured logging | PASS | `JsonFormatter` schema; `test_json_line_schema` |
| Scope: execution correlation IDs | PASS | `execution_id` + `correlation_id` on every execution line; request id → correlation id (`test_triggers_api.py`); scheduled runs get `corr_…` |
| Scope: node correlation IDs | PASS | `node_id`, `node_execution_id`, `node_type` on node lines and on lines emitted by node code (integration test) |
| Scope: metrics | PASS | `EngineMetrics`, `/metrics`; `test_metrics_endpoint_serves_prometheus_text` |
| Scope: execution timing | PASS | `duration_ms`, `queue_wait_ms`, histograms; equal to persisted timestamps (integration test) |
| Scope: node timing | PASS | Node `duration_ms` and histogram; exact values in observer tests |
| Scope: error recording | PASS | `error_code`/`error_kind`/`error_message`/`error_node_id` on failure lines; `node_execution_errors_total` |
| Collect: execution count | PASS | `executions_created_total`, `executions_started_total` |
| Collect: success/failure | PASS | `executions_finished_total{status}` |
| Collect: duration | PASS | `execution_duration_seconds{status}` |
| Collect: node duration | PASS | `node_execution_duration_seconds{node_type,status}` |
| Collect: active executions | PASS (per runtime instance) | Gauge test (2 → 0). Not yet exposed on the container's `/metrics` (F7). |
| Collect: queue depth where applicable | N/A (not applicable yet) | No queue exists before Phase 22; queue *wait* time is collected |
| Test: correlation IDs | PASS | `test_logging.py` (context/tasks), `test_request_observability.py`, integration test |
| Test: structured log fields | PASS | `test_json_line_schema`, observer and integration field assertions |
| Test: metrics | PASS | Observer, gauge and endpoint tests |
| Test: execution timing | PASS | Exact durations (unit); durations = persisted timestamps (integration) |
| Test: secret-safe logging | PASS | `test_secrets_are_redacted_from_message_fields_context_and_exception`, integration canary test |

**Phase prompt:**

| Requirement | Status | Evidence |
|---|---|---|
| Structured errors / error serialization | PASS | API error bodies (`test_error_messages_and_details_are_redacted`, `test_error_body_and_log_share_one_request_id`); `_error_fields` in logs |
| Metrics: retries | NOT APPLICABLE YET | The runtime refuses retry policies with more than 1 attempt (Phase 08) and drives no retry transitions; retries arrive in Phase 23. No metric was added for a code path that cannot occur. |
| Metrics: queue/runtime timing where available | PASS | `execution_queue_wait_seconds`, execution and node durations |
| Metrics: resource usage where already supported | NOT APPLICABLE YET | No resource accounting exists (tokens and cost: Phases 14/15; limits: Phase 24) |
| Secrets never in logs | PASS | Formatter redaction tests; integration canary |
| Secrets never in exceptions | PASS | API bodies redacted; node errors redacted before persistence (raw database rows checked); unexpected node exceptions persist only the type (pre-existing) |
| Secrets never in events | PASS | Closed scalar payloads (Phase 11, unchanged); cancellation reason absent (observer test) |
| Secrets never in metric labels | PASS | `test_metric_labels_are_bounded_and_carry_no_ids_or_text` |
| Secrets never in debug output | PASS | `TextFormatter` redaction test; exception text redacted |
| Redaction central, not scattered | PASS | One module; three choke points (§5.2); mutation-verified |
| Operator can understand an execution without source code | PASS | Event-named, ordered, id-carrying log lines with timings and error classification (field set in §8.3; the integration test asserts the full story) |
| Quality gates pass | PASS | §13 |

**Blocking assessment.**
- No criterion is FAIL.
- The two "not applicable yet" items depend on subsystems that do not exist, and the "where applicable / where available / where already supported" wording in the roadmap and prompt covers them.
- F7 (runtime metrics absent from the container's `/metrics`) is a wiring gap inherited from Phase 11. It does not block the acceptance criterion, which the integration test proves with a runtime-owned `EngineMetrics` plus logs.
- Together these are the reason for the status **COMPLETE WITH LIMITATIONS**.

---

## 15. Security Considerations

- **Secrets:**
  - Logs are redacted at the formatter: message, all fields, bound context and exception text.
  - API error bodies are redacted.
  - Node error text is redacted before it is persisted, published, returned or logged.
  - The existing rule stays: unexpected node exceptions persist only the type.
- **Limit of pattern redaction:** secrets without a recognisable shape (a bare password in free text, an unlabelled opaque token) are not detected. Known-value masking requires the credential store (Phase 13).
- **Logging of new data:** `error_message` is now logged on failure lines. It is node-authored text, redacted twice (executor and formatter). Node inputs, outputs and config values are still never logged by the engine. `core.log` with `include_value=true` logs a user-selected value; it passes through key- and text-redaction but can still contain business data (pre-existing, opt-in).
- **Input validation:** `X-Request-ID` is limited to 1–128 printable characters, which prevents log-line and header injection through CR/LF. The same rule as `ExecutionMetadata.correlation_id`.
- **Access log:** path only, no query string (tokens in query strings are not logged). Webhook paths contain project and trigger ids. Those are client-chosen, guessable identifiers, not capability secrets. Webhook authentication is planned as signatures or secrets (Phase 10 report, OI11).
- **API exposure:** `GET /metrics` is unauthenticated, like `/health` and `/ready`, until Phase 20. It exposes counts, timings, node type names and trigger types; no ids, codes, messages or payloads. Deployments exposing the API publicly should restrict `/metrics` at the proxy until authentication exists.
- **Error disclosure:** unchanged policy (no traceback to clients); now also redacted server-side.
- **Metric cardinality (a DoS-adjacent concern):** labels are bounded (§8.3). `node_type` becomes author-extensible with plugins (Phase 17; §21).
- **Not affected:** authentication/authorization, SSRF, path traversal, XSS/CSRF, expressions, plugin loading, WebSockets.

---

## 16. Performance Considerations

- **Per log line:** one recursive `redact` walk plus 8 regex passes over each string field. The context merge is a dict copy. No measurements were taken; the cost scales with line size, and lines carry ids and scalars only.
- **Per execution save:** `observe_execution` does O(events) work with dictionary lookups and in-memory counter updates, and no I/O. It runs inline after commit, before publishing.
- **HTTP middleware:** Starlette `BaseHTTPMiddleware` (`@app.middleware("http")`) adds one task hop per request plus one log line. Not measured.
- **Metrics memory:** histograms have 13 buckets. Series count is roughly (#node types × 4 statuses) for the node histograms, plus small fixed sets.
- **Suite runtime:** about 38–41 s, against about 38–40 s in Phase 11. The difference reflects the added tests, not a benchmark.
- No performance claims are made.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §52 | Record the implemented correlation and logging design | Log schema and field precedence; `log_context` propagation; HTTP request-id rules; trigger → correlation id; runtime context fields; the observer and its fields; `project_version_id` and `trace_id`/`span_id` not implemented, and why |
| `docs/architecture.md` §109 | Record the redaction design | Key and text rules; the three choke points; events and labels secret-free by construction; the known-value limitation (Phase 13) |
| `docs/architecture.md` §113 | Record the metrics | Metric table with types and labels; bounded labels; per-instance registry and wiring note; per-process counters; not-yet-emitted metrics |
| Module docstrings | Maintainer knowledge | `security/redaction.py`, `observability/{__init__,logging,metrics,execution}.py`, `runtime/runtime.py` (concurrency notes), `runtime/executor.py` (redaction rule) |
| `docs/reports/backend/phase-12-observability.md` | Phase report | This document |

**Should be updated in a future phase:**
- Architecture §18 still names `project_version_id`. §52 now documents the deviation, but §18 itself is unchanged (carried over from Phase 11).
- The §112 operational architecture: `/metrics` once authentication (Phase 20) and workers (Phase 22) exist.
- `backend/README.md`: no operator-facing section on log fields or `/metrics` exists yet (Phase 26, operations).

---

## 18. Dependencies / Infrastructure Changes

- **New dependency:** `prometheus-client~=0.26`, locked at 0.26.0 in `uv.lock`.
  - Why: a standard exposition format for the metrics the roadmap requires; rejected alternatives in §5.5.
  - Review (manual): no transitive dependencies (2 packages were installed, the project itself and `prometheus-client`); Apache-2.0 licence; maintained under the Prometheus/CNCF organisation; ships `py.typed` (strict mypy passes without overrides).
- **Removed or upgraded dependencies:** none.
- **Configuration and environment variables:** none added. `log_level` is unchanged. Structured versus text log output is still only a `configure_logging(structured=...)` argument, not a setting.
- **Database, Redis, Docker, external services:** none.
- **Test infrastructure:** `tests/support.capture_json_logs()` was added. The Testcontainers images are unchanged.

---

## 19. Deferred Work

| Item | Why deferred | Intended phase | Dependency / blocker | Affects Phase 12 completion? |
|---|---|---|---|---|
| Wire `ExecutionRuntime` to `Container.metrics` (and `.events`) | No production code constructs the runtime yet | 21/22 | Runtime/worker composition | No (acceptance proven with a runtime-owned instance); listed as a limitation |
| Queue depth metric | No queue exists | 22 | Queue/worker architecture | No ("where applicable") |
| Retry metrics | No retry transitions are driven | 23 | Retry semantics | No |
| Token/cost/resource-usage metrics | Not supported yet | 14/15/24 | Provider layer, limits | No ("where already supported") |
| Known-value secret redaction | Needs the credential store | 13 | `CredentialService` | No (central module ready to extend) |
| `/metrics` authentication | No auth system | 20 | Authentication | No |
| Distributed tracing (`trace_id`/`span_id`) | No tracing backend or consumer | Unassigned | — | No (architecture says "where appropriate") |
| HTTP request metrics (`http_requests_total`) | The `http_request` log line covers requests; no consumer requires a metric | Unassigned / 26 | — | No |
| Execution log/event query endpoint | Needs auth and the inspector design | Frontend live phase / 20 | Auth | No |

---

## 20. Known Limitations

1. **Runtime metrics are not exposed by the API process** (F7). A runtime must be constructed with `metrics=container.metrics` to appear on `/metrics`.
2. **Pattern-based redaction.** Unshaped secrets are not caught until Phase 13. There are also known false positives: any value after a sensitive-looking key in text is masked, and words after `Basic ` / `Bearer ` with 8+ characters are masked.
3. **At-most-once observation.** Observation runs after commit. A crash between commit and observation loses that log line and metric update (the state and the persisted event remain). This matches event publication.
4. **Per-process, in-memory metrics.** They reset on restart. `active_executions` counts runs of one runtime instance, not cluster-wide.
5. **Context bound at format time.** A future asynchronous or queued log handler would lose `log_context` fields unless they are copied onto the record first.
6. **Scheduler-fired executions** get a correlation id that is not linked to any inbound request (by design, there is none). Executions created directly via `ExecutionService` without metadata still have `correlation_id=None`.
7. **No tracing and no HTTP request metrics.**
8. **`/metrics` is unauthenticated.**

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target phase |
|---|---|---|---|---|---|
| Runtime not constructed by the container; its metrics and events are not shared (F7, carried from Phase 11) | Operators see no run, node or duration metrics on `/metrics` in a deployment | MEDIUM | Composition | Construct the runtime or worker with `events=container.events, metrics=container.metrics` | 21/22 |
| `/metrics` unauthenticated | Operational data exposed to anyone who can reach the API | LOW–MEDIUM | API / security | Protect it with operator auth, or bind it to an internal listener | 20 / 26 |
| `node_type` label cardinality with plugins | Author-defined node types could grow series | LOW | Metrics | Revisit the label when plugins land (cap, or plugin-namespace only) | 17 |
| Docker probe flake (F6, carried from Phase 11) | Integration tests silently skipped in a slow environment | LOW | Test infrastructure | Longer ping timeout, or fail instead of skip when `CI` expects Docker | CI hardening |
| Architecture §18 `project_version_id` naming (carried from Phase 11) | Documentation inconsistency | LOW | Docs | Update §18 | Frontend live phase |
| No trigger-endpoint regression test for an oversized `X-Request-ID` (B2) | Fix covered only at the HTTP-middleware level | LOW | Tests | Add a webhook call with a 200-character header asserting 202 | Any |

---

## 22. Architectural / Product Decisions

1. **Lifecycle observability comes only from `observe_execution` after commit.** Do not add lifecycle log calls in the runtime or services. New evented transitions get observed automatically.
2. **Redaction lives in `akasha.security.redaction` and runs at choke points** (formatter, `_error`, `execute_node`). New output channels (reports in Phase 19, WebSocket in the frontend live phase) must route through it rather than adding local filters.
3. **Node error text is redacted at the executor boundary before persistence.** Stored `ExecutionError` messages are therefore already safe for API and report output.
4. **Correlation travels in a `ContextVar`,** bound around `create_task` for node tasks and around `call_next` for requests. The Node contract did not change for this.
5. **The request id is the correlation root for API-created executions;** scheduler runs get a fresh `corr_…` root. The request id is accepted from callers only if it is log-safe.
6. **`prometheus-client` with per-instance registries.** The global default registry is never used.
7. **No metric labels from ids, error codes, messages or user input.** `error_code` stays in logs only.
8. **Metrics that cannot occur were not added** (retries, queue depth). They arrive with their subsystems.
9. **Log event names equal event type values** (`node.failed`). Log names, event types and metric status labels share one vocabulary.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **Base commit:** `1a52688 backend phase 11 - event system`.
- **Working tree:** uncommitted Phase 12 changes. Nothing was staged or committed during this phase.
- **Modified (17):**
  - `backend/pyproject.toml`, `backend/uv.lock`
  - `backend/src/akasha/api/{app,routes,schemas,triggers}.py`
  - `backend/src/akasha/application/{executions,triggers}.py`
  - `backend/src/akasha/bootstrap.py`
  - `backend/src/akasha/observability/{__init__,logging}.py`
  - `backend/src/akasha/runtime/{executor,runtime}.py`
  - `backend/tests/integration/test_triggers_api.py`
  - `backend/tests/support.py`
  - `backend/tests/test_logging.py`
  - `docs/architecture.md`
- **Untracked (8):**
  - `backend/src/akasha/observability/{execution,metrics}.py`
  - `backend/src/akasha/security/redaction.py`
  - `backend/tests/integration/test_observability.py`
  - `backend/tests/{test_execution_observer,test_redaction,test_request_observability}.py`
  - `docs/reports/backend/phase-12-observability.md`
- **Unrelated changes:** none detected. The tree was clean at the start of the phase.
- **Temporary files:** the mutation backup files were removed. The mutation script and the build output live in the session scratchpad outside the repository.

---

## 24. Phase Completion Assessment

### Implemented
- Central redaction (`security/redaction.py`) at three choke points.
- JSON logging with a UTC ISO schema and `log_context` propagation.
- HTTP request-id middleware and access log.
- Request id → correlation id; scheduler correlation roots.
- `observe_execution` (log lines, timings, error fields, metrics).
- `EngineMetrics` (9 instruments) and `GET /metrics`.
- Runtime active-executions gauge and node-task log context.
- Container wiring for `ExecutionService`.
- Architecture documentation.

### Verified
- 68 net new tests (69 in the new or rewritten files). 973/973 pass with 0 skipped, including 3 PostgreSQL integration tests.
- 14/14 mutations killed.
- Format, lint, strict mypy and build pass.

### Not Implemented
- Queue-depth, retry and resource-usage metrics (their subsystems do not exist).
- Tracing.
- HTTP request metrics.
- Known-value redaction.

### Deferred
See §19.

### Open
See §21. The most significant item is the runtime ↔ container metrics wiring (Phases 21/22).

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**What Phase 13 (Credentials + Secrets) and later phases can assume:**
- `akasha.security.redaction` is the single redaction point. Phase 13 should add known credential values there, for example a registry of resolved secret values that `redact_text` also masks. It should not introduce per-call-site scrubbing. All logs, API error bodies and node errors already route through it.
- Node errors are redacted before persistence (`execute_node`). Resolved credential values that appear in node error text will be masked once the known-value rule exists.
- `log_context(**fields)` adds correlation fields to any async scope. Workers (Phase 22) should bind a worker id or job id the same way.
- `observe_execution(execution, events, metrics)` must be called after every committed execution save. Use `StateManager.save` or mirror `ExecutionService.create_execution`.
- `EngineMetrics` instruments are listed in architecture §113. Add new instruments there, with bounded labels only.

**Contracts:**
- `X-Request-ID` semantics (§7).
- The log field names (§8.3) and the event-type-named log lines on logger `akasha.execution`.
- `/metrics` exposition.

**Constraints:**
- No metric labels from ids, error codes, messages or user input.
- No lifecycle log calls in the runtime.
- Never log node inputs, outputs or config values by default.
- Any new output channel (reports, WebSocket) must use `redact`.

**Must not be changed casually:**
- The after-commit order: observe, then publish.
- The executor redaction boundary.
- The request-id validation rule, which must stay aligned with `ExecutionMetadata.correlation_id`.
- The field precedence in `JsonFormatter`.

**Prerequisites and open items:**
- Pass `metrics=container.metrics` and `events=container.events` when the runtime or workers are constructed (Phases 21/22).
- Decide on `/metrics` protection together with authentication (Phase 20).
