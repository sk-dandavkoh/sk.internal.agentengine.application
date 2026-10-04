# Phase 16 — Monitoring

Report date: 2026-10-04. Branch `main`. Base commit `6fd7583` (frontend phase 15 — execution inspector and logs). All work described here is **uncommitted** in the working tree (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap §22 (`docs/roadmap-frontend.md`, "Phase 16 — Monitoring"): a system-wide monitoring view.
- Dashboard scope: active executions, execution rate, success rate, failure rate, duration, node performance, token usage, cost and error trends.
- Time ranges: Last Hour, Last 24 Hours, Last 7 Days and Custom.
- The session prompt added retries and "system/runtime metrics available from backend".
- The prompt also required: implement and test any missing backend API, and do not invent metrics.

**Discovered.** The backend had no time-windowed aggregate API. Three possible sources existed, and none fits:
- `GET /executions/status-counts`: all-time counts only.
- The execution report: one run only.
- Prometheus `/metrics`: counted per process, reset on restart, admin/token only, and without tokens or cost. Aggregating it in the browser would mislead.

**Actually implemented.**
- **Backend:** `GET /api/v1/monitoring/summary?since=&until=`, which aggregates persisted executions created in a window of at most 31 days, with the caller's owner filter. Also:
  - a `MonitoringService`;
  - two repository read queries;
  - `MetricsTally`, the report's metric sums extracted so the report and monitoring share one definition;
  - index migration `0013`.
- **Frontend:** the `/monitoring` page (`frontend/src/features/monitoring/`):
  - a range picker (presets plus custom, kept in the URL);
  - headline stats;
  - a stacked timeline with a table alternative;
  - duration, error and node performance tables;
  - a "Right now" card and the system status card.
- **Also fixed:** a pre-existing flaky frontend test (`ExecutionInspector.test.tsx`) in the shared test harness.

**Resulting state.** A user picks a window and sees, for the runs created in it:
- success and failure rates over finished runs, with the denominator shown;
- durations per outcome;
- a per-bucket timeline;
- the most frequent error codes;
- per node type completions, failures, retries, durations, tokens and cost.

Every number comes from the backend.

**Acceptance criteria.** All four roadmap criteria pass (§14). Roadmap scope items without backend data are not shown, as the "no invented data" criterion requires: trigger breakdown, provider usage, queue depth and resource usage (§19/§20).

**Phase Status:** COMPLETE WITH LIMITATIONS

## 2. Phase Objective

Roadmap §22:
- **Goal:** "Systemweite Monitoring-Oberfläche" (a system-wide monitoring interface).
- **OpenCode prompt:**
  - show execution rate, success/failure, duration, node performance, token usage, cost and errors;
  - "Implementiere Zeitfilter nur für tatsächlich unterstützte Backend-Funktionen" (implement time filters only for functions the backend actually supports);
  - "STOP nach Phase 16" (stop after Phase 16).

`frontend/AGENTS.md` rules that apply:
- §45 Monitoring: executions, durations, errors, node performance, triggers, provider usage, token usage, resources; "nicht ausschließlich auf lokalem Browser-State" (not based only on local browser state).
- §46 Charts: correct data, clear units, clear time ranges, loading/error/empty states, performant with large data, nothing misleading, no invented data.

**Not responsible for:**
- the credentials UI (Phase 17);
- the reports UI (Phase 19);
- alerting;
- metric retention and rollups;
- Prometheus/Grafana integration.

## 3. Scope Implemented

### Backend / Application
- `akasha.application.monitoring` (new):
  - `MonitoringService.summary(since, until, owner_id)`;
  - pure `summarize(since, until, executions, nodes)`;
  - `check_window`, `bucket_size`;
  - response models `MonitoringSummary`, `DurationStats`, `TimelineBucket`, `ErrorCount`, `NodeTypeStats`;
  - constants `MAX_WINDOW` (31 days), `MAX_BUCKETS` (100), `TOP_ERRORS` (10).
- `akasha.application.reports` (modified):
  - new `MetricsTally` (sums status counts, attempts, retries, failed attempts, tokens and per-currency cost, plus `cost_complete`);
  - `_metrics()` now delegates to it;
  - `_usage_and_cost` renamed to the public `usage_and_cost` (now used by monitoring).
- `akasha.application.persistence` (modified):
  - read models `ExecutionSample` and `NodeSample`;
  - `ExecutionRepository.sample_executions` / `sample_node_executions` protocol methods.

### Backend / Persistence
- `SqlAlchemyExecutionRepository.sample_executions`: narrow rows (status, timestamps, `error.code`, `error.kind`) for `created_at ∈ [since, until)`, owner-filtered through the existing `_owned()`, `ORDER BY created_at, id`.
- `SqlAlchemyExecutionRepository.sample_node_executions`:
  - joins `node_executions → executions → project_versions`;
  - resolves `node_type` with `jsonb_path_query_first(graph, '$.nodes[*] ? (@.id == $id).node_type', jsonb_build_object('id', node_id)) #>> '{}'`;
  - reads `jsonb_array_length(failures)` and `outputs['metadata']`;
  - `ORDER BY execution id, node_id`, a stable order so float cost sums are reproducible.
- Migration `0013_executions_created_at_index` with `ix_executions_created_at`, mirrored in `db_models.executions`.

### Backend / API
- `akasha.api.monitoring.create_monitoring_router()`: `GET /api/v1/monitoring/summary`, registered on the protected router in `api/routes.py`.
- `bootstrap.Container.monitoring: MonitoringService | None`, built in `build_container` from the same unit-of-work factory as `ReportService`.

### Frontend / API layer
- `frontend/src/api/monitoring.ts`:
  - zod schemas `durationStatsSchema` and `monitoringSummarySchema`;
  - the `MonitoringWindow` type;
  - `getMonitoringSummary()`.
- `frontend/src/api/queries.ts`: `queryKeys.monitoring(window)` → `['monitoring', 'summary', window]`, and `monitoringQueries.summary`.
- `frontend/src/api/openapi.json`: regenerated snapshot.

### Frontend / UI (`frontend/src/features/monitoring/`)
- `model.ts`:
  - `PRESETS`, `isRange`, `presetWindow` (anchored to the minute), `customWindow` (client-side mirror of the backend rules), `toLocalInput`;
  - `OUTCOMES` / `outcomeCounts` / `rates`;
  - `formatCount`, `formatPercent`, `formatCost`, `formatBucket`.
- `RangePicker.tsx`: a `Select` with the presets and Custom; Custom shows native `datetime-local` From/To inputs with validation messages.
- `MonitoringPage.tsx`:
  - `useWindow()` keeps the range in the URL (`?range=…&since=&until=`);
  - `WindowSummary` renders the window's data or its empty state;
  - Refresh moves a preset window to the current time and refetches the current-state cards.
- `sections.tsx`: `ActiveNowCard`, `StatsGrid`, `TimelineCard`, `DurationsCard`, `ErrorsCard`, `NodePerformanceCard`.
- `frontend/src/app/routes.tsx`: `/monitoring` now lazy-loads `MonitoringPage` instead of the placeholder.

### Test infrastructure
- `frontend/src/test/app.tsx`: `renderRoute()` also returns `ready`, a promise that resolves on the router's initial navigation (`state.initialized`).

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/application/monitoring.py` | Created | Aggregation, service, response models |
| `backend/src/akasha/api/monitoring.py` | Created | HTTP adapter `GET /monitoring/summary` |
| `backend/migrations/versions/0013_executions_created_at_index.py` | Created | Range scans on `executions.created_at` |
| `backend/src/akasha/application/reports.py` | Modified | `MetricsTally` extracted; `usage_and_cost` made public |
| `backend/src/akasha/application/persistence.py` | Modified | `ExecutionSample`, `NodeSample`, two protocol methods |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | SQL for both sample queries; removed the obsolete `ponytail:` note about the missing index |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | `ix_executions_created_at` in table metadata |
| `backend/src/akasha/bootstrap.py` | Modified | `Container.monitoring` field and wiring |
| `backend/src/akasha/api/routes.py` | Modified | Register the monitoring router (protected) |
| `backend/tests/application/test_monitoring.py` | Created | Pure aggregation unit tests |
| `backend/tests/integration/test_monitoring.py` | Created | PostgreSQL and HTTP integration tests |
| `backend/tests/application/test_reports.py` | Modified | Rename `_usage_and_cost` → `usage_and_cost` |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeExecutions` implements the two new protocol methods |
| `backend/tests/integration/test_persistence.py` | Modified | `_HEAD` = `0013_executions_created_at_index` |
| `frontend/src/api/monitoring.ts` | Created | Contract and service function |
| `frontend/src/api/queries.ts` | Modified | Query key and options |
| `frontend/src/api/openapi.json` | Modified (generated) | Regenerated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1` |
| `frontend/src/api/contract.node.test.ts` | Modified | Covers `getMonitoringSummary` |
| `frontend/src/features/monitoring/` (new directory) | Created | `model.ts`, `RangePicker.tsx`, `MonitoringPage.tsx`, `sections.tsx`, `model.test.ts`, `MonitoringPage.test.tsx` |
| `frontend/src/app/routes.tsx` | Modified | Real `/monitoring` route |
| `frontend/src/app/RouteErrorBoundary.test.tsx` | Modified | Its API-failure case used the Monitoring placeholder; moved to the Credentials placeholder |
| `frontend/src/test/app.tsx` | Modified | `ready` promise (flaky test fix) |
| `frontend/src/features/executions/ExecutionInspector.test.tsx` | Modified | Awaits `ready` before its first query |
| `docs/architecture.md` | Modified | API table row; new §113.1 "Monitoring summary" |
| `docs/reports/frontend/phase-16-monitoring.md` | Created | This report |

No files were deleted. No dependencies were added.

## 5. Architecture Changes

### Decision: monitoring aggregates persisted state, not Prometheus counters

**Context.** The UI needs windowed numbers across all processes, including tokens and cost.

**Chosen approach.** A read-only projection over the `executions` / `node_executions` / `project_versions` tables (`MonitoringService`), served at `/api/v1/monitoring/summary`.

**Alternatives considered.**
- Parse `/metrics` in the browser. Rejected:
  - the counters are per process and reset on restart;
  - the endpoint is admin/token only;
  - it has no tokens or cost;
  - summing it would be a misleading aggregation.
- Add the endpoint to the executions router (`/executions/summary`) via `ExecutionService`. Rejected because it caused an import cycle (`executions → monitoring → reports → executions`).
- SQL `GROUP BY` / percentile aggregation. Deferred, because it is larger and Postgres-specific (see Consequences).

**Reason.**
- The database is shared by every process and survives restarts.
- Reusing the report's tally guarantees the same numbers in both views.

**Consequences.**
- Aggregation runs in the API process over one row per execution and node execution in the window. This is marked `ponytail:` in `MonitoringService.summary`.
- The upgrade path is SQL aggregates or rollups.
- Documented in `docs/architecture.md` §113.1.

### Decision: one population, "executions created in the window"

**Context.** Mixing "created in" with "finished in" windows makes counts and rates inconsistent.

**Chosen approach.**
- Every number covers executions whose `created_at` is in `[since, until)`.
- Durations are only of finished runs and are kept per terminal status.
- Node durations are of the last attempt, as in the report.

**Consequences.** A run created just before `until` and still running counts as "not finished" and is excluded from rates. The UI says so ("Executions created from … to …", "N not finished yet").

### Decision: `MetricsTally` shared by the report and monitoring

**Context.** Monitoring needed the same retry, token and cost sums as `MetricsReport`.

**Chosen approach.** Extract the sums from `reports._metrics` into `MetricsTally`; both callers use it.

**Consequences.** The rules for retries (`max(attempts - 1, 0)`), cost per currency and `cost_complete` live in one place. The integration test `test_report_and_monitoring_agree` pins this.

## 6. Domain / Data Model Changes

- No domain entity, value object, enum or state transition changed.
- New application read models: `ExecutionSample` and `NodeSample` (frozen dataclasses, not aggregates).
- New API models (Pydantic, frozen, `extra="forbid"`): `MonitoringSummary`, `DurationStats`, `TimelineBucket`, `ErrorCount`, `NodeTypeStats`. `MetricsReport` is reused for per-type stats and totals.
- **Database:** new index `ix_executions_created_at` on `executions(created_at)` (migration `0013`, downgrade drops it). No tables, columns or constraints changed. Adding an index needs no data changes.

## 7. API / Contract Changes

### `GET /api/v1/monitoring/summary` (new)

**Purpose.** Aggregates over the executions created in a window.

**Request.**
- `since` (required, ISO datetime with timezone, inclusive);
- `until` (optional, exclusive; default: now).
- Rules: `until > since`, span ≤ 31 days.

**Response 200 (`MonitoringSummary`).**
- `since`, `until`, `bucket_seconds`;
- `executions`;
- `status_counts` (every `ExecutionStatus`, zeros included);
- `durations` (terminal statuses with samples → `{count, mean_ms, p50_ms, p95_ms, max_ms}`);
- `timeline[]` (`{start, end, counts}`; the last bucket may end early at `until`);
- `errors[]` (`{code, kind, count}`, at most 10, by count desc then code) and `errors_total`;
- `nodes[]` (`{node_type, metrics: MetricsReport, durations | null}`, by `node_type`);
- `totals: MetricsReport`.

**Errors.**
- 401 without a session.
- 422 `DOMAIN_VALIDATION_ERROR` for a naive timestamp, `until ≤ since`, or a span over 31 days.
- 422 request validation error for a missing or unparseable `since`.
- 500 `CONFIGURATION_ERROR` when the container has no `MonitoringService`.

**Visibility.** `AuthorizationService.owner_filter`: members see only their projects' runs, admins see all.

**Compatibility.** Additive; no existing endpoint changed.

### Report contract

- `GET /reports/executions/{id}` is byte-for-byte unchanged.
- `MetricsReport` construction moved into `MetricsTally` with identical semantics. The existing report tests pass unchanged apart from the function rename.

### Frontend/backend contract

`openapi.json` was regenerated. `contract.node.test.ts` checks `monitoringSummarySchema` against it (PASS).

## 8. Implementation Details

**Backend control flow.**
1. `GET /monitoring/summary` → `MonitoringService.summary`.
2. `check_window` (fails fast before any query).
3. One unit of work for both sample queries, so node rows belong to the executions counted.
4. `summarize()`, which is pure.

**Inside `summarize()`.**
- Picks the smallest bucket from (1 min, 5 min, 15 min, 1 h, 6 h, 1 day) that gives ≤ 100 buckets.
- Buckets each execution by `(created_at - since) // size`. A sample outside the window raises `ValueError`, so a repository bug cannot wrap into a negative index.
- Collects durations per terminal status.
- Counts `(error_code, kind)` for FAILED and TIMEOUT only.
- Sums nodes per type with `MetricsTally`, parsing usage and cost with the report's `usage_and_cost`: malformed metadata counts as "not reported".
- Percentiles are nearest-rank, so each one is an actually observed duration.

**Node type resolution.**
- Done in SQL against the pinned version graph.
- A missing match maps to `"unknown"`, so no row is dropped. This is unreachable while versions are immutable.

**Frontend state.**
- **Server state:** TanStack Query, keyed by the exact window, so a window is fixed data.
- **Range:** URL search params.
- **Preset anchor:** `now`, in `useState`. `presetWindow` rounds `until` down to the minute, so the key does not change on re-render. Only Refresh or a range change moves it.
- **Custom inputs:** local form state until Apply.
- An invalid custom link (`?range=custom&since=nonsense`) shows "Choose a time range" and sends no request.
- An unknown `range` falls back to `24h`.

**Display rules (`model.ts`).**
- Rates are over finished runs (completed + failed/timeout + cancelled) and show the denominator.
- With no finished runs, rates show "—", not 0 %.
- Costs are listed per currency and never summed across currencies.
- `formatCost` keeps up to 6 fraction digits, so small LLM costs do not round to 0.00. A non-ISO currency falls back to `"<amount> <currency>"`.

**Chart.**
- CSS flex bars; segment heights are inline styles because they are data-driven.
- The bars are `aria-hidden`. The legend plus a "Show as table" `<details>` with a `Table` carry the same numbers, so color is not the only carrier.

**Loading, error and stale data.**
- Each region uses the existing `QuerySection` (loading, error with Retry, stale-data warning).
- The summary, the current counts and the system status load and fail independently.

**Test harness.** `renderRoute().ready` subscribes to `router.subscribe` and resolves when `state.initialized` is true.

## 9. Findings

### Finding: no backend API for windowed monitoring
- **Impact:** HIGH. The phase could not be built without one.
- **Resolution:** FIXED. New endpoint (§7).
- **Notes:** `/metrics` exists but is per process and admin-only, and has no tokens or cost (architecture §113 already listed `llm_tokens_total` / `llm_cost` as "not yet emitted").

### Finding: an import cycle blocked placing monitoring on `ExecutionService`
- **Impact:** LOW.
- **Resolution:** FIXED. Separate `MonitoringService` and router, following the `ReportService` pattern.
- **Notes:** The cycle is `application.executions → monitoring → reports → executions`.

### Finding: `executions.created_at` had no general index
- **Impact:** MEDIUM. Every monitoring window, and the existing newest-first listing, scanned the table.
- **Resolution:** FIXED. Migration `0013`.
- **Notes:** This also resolves the earlier `ponytail:` note in `list_summaries`.

### Finding: no node type on `node_executions`
- **Impact:** LOW.
- **Resolution:** WORKAROUND. SQL JSON-path lookup into the version graph per node row.
- **Notes:** Storing `node_type` on the row would remove the lookup; deferred.

### Finding: some roadmap scope has no backend data
- **Impact:** MEDIUM.
- **Resolution:** DEFERRED.
- **Notes:** Not exposed by any API: per-trigger breakdown, per provider/model usage, queue depth, resource usage. Not shown, per "keine erfundenen Daten" (no invented data).

### Finding: a pre-existing flaky frontend test
- **Impact:** MEDIUM. The full frontend suite failed in most runs on the base commit.
- **Resolution:** FIXED (§10).

### Finding: test assumption about a failed run's pending nodes
- **Impact:** LOW.
- **Resolution:** FIXED (test).
- **Notes:** When an execution fails, nodes that never started end as `cancelled`, not `pending`. The domain behavior is correct; my integration test expectation was wrong.

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Origin |
|---|---|---|---|---|---|
| 1 | `?range=toString` was accepted as a preset range | `value in PRESETS` matches `Object.prototype` members | `Object.hasOwn(PRESETS, value)` in `isRange` | `model.test.ts` "recognizes only known ranges"; `MonitoringPage.test.tsx` "falls back to the default range" | Introduced this phase, caught before completion |
| 2 | `ExecutionInspector.test.tsx` › "shows a successful run" failed in full-suite runs | Cold lazy import of the execution page (React Flow) measured at ~600 ms alone and ~980 ms under load, plus render and fetch, exceeded the first `findBy` 1 s budget. Only the first test of the file paid it. | `renderRoute()` returns `ready` (router `initialized`); the test awaits it | Six consecutive full suite runs: 385/385 | Pre-existing (fails on `6fd7583`: 3 of 3 runs) |
| 3 | `.astext` failed on the portable JSON column type | `JSONB_VARIANT` is `JSON().with_variant(JSONB)`; the generic JSON comparator has no `.astext` | `.as_string()` | `test_monitoring.py` integration tests | Introduced and fixed during implementation |
| 4 | Unit test helper produced −1 failed attempts for a 0-attempt node | Test helper arithmetic | Corrected helper | the same test | Test-only |
| 5 | `RouteErrorBoundary.test.tsx` failed once `/monitoring` became a real page | The test used the Monitoring placeholder to simulate an API crash | Moved to the Credentials placeholder | the test itself | Consequence of this phase |

## 11. Fixes Applied

1. **`isRange` prototype bug.**
   - Symptom: `?range=toString` passed the range check.
   - Fix: `Object.hasOwn`.
   - Verified by unit and page tests.
2. **Flaky inspector test.**
   - Symptom: intermittent `Unable to find role="heading" and name "Run snapshot"`.
   - Root cause: measured with a temporary probe test, then removed.
   - Fix: an event-driven wait on router initialization, with no longer timeouts or sleeps.
   - Verified: six consecutive full runs, all 385 tests passing.
3. **JSON accessor.** Symptom: `AttributeError: … has an attribute 'astext'`. Fix: `.as_string()`. Verified by integration tests on PostgreSQL 17.2.
4. **Import cycle.** Symptom: `ImportError … partially initialized module`. Fix: a standalone `MonitoringService` and router. Verified by the full backend suite.

## 12. Testing & Verification

### Unit Tests
- **Backend** `tests/application/test_monitoring.py` (17 executed cases, PASS):
  - empty window (all statuses zero, 60 one-minute buckets);
  - counts and durations per terminal status, with no mixing;
  - nearest-rank percentiles;
  - error ranking, cap of 10, cancelled runs excluded;
  - per node type retries, tokens, partial cost (`cost_complete=false`) and malformed metadata;
  - a 31-day window with ~6,400 runs (≤ 100 buckets, totals preserved);
  - bucket sizes for 1 min, 1 h, 24 h, 7 d and 31 d;
  - partial last bucket;
  - four invalid windows;
  - out-of-window sample.
- **Frontend** `model.test.ts` (9, PASS):
  - preset anchoring and stability;
  - custom validation (empty, reversed, equal, over 31 days);
  - `datetime-local` round trip;
  - `isRange`;
  - outcome grouping;
  - percent, cost, bucket and count formatting.

### Component Tests
`MonitoringPage.test.tsx` (10, PASS) uses the real router, ApiClient and QueryClient with a stubbed `fetch`:
- normal data: rates with denominators, timeline table rows, durations, errors with "1 more", node rows, the "Right now" card, the default 24 h request;
- no data;
- partial data: queued only, so rates show "—", incomplete cost, and empty-table messages;
- loading, then an API error with Retry while other sections keep working;
- malformed backend data rejected;
- a large dataset: 100 buckets, 200 node types, 100,000 runs;
- preset requests and URL state, custom validation and apply;
- an invalid custom link sends no request;
- an unknown range falls back to the default;
- Refresh refetches the current state.

### Integration / API Tests
`tests/integration/test_monitoring.py` (11, PASS; Testcontainers PostgreSQL `postgres:17.2`):
- aggregation over persisted runs, including retries, tokens, cost, JSON-path node types and cancelled sink nodes;
- half-open window bounds;
- report/monitoring agreement;
- HTTP 200 with an empty window and with data;
- member isolation;
- five invalid-window 422s;
- 401 without a session.

### Contract Tests
- `contract.node.test.ts`: PASS, including `GET /monitoring/summary`.
- `tests/test_openapi_contract.py`: PASS after regenerating the snapshot.

### Infrastructure Tests
`test_persistence.py` migrates an empty database to head (`0013`): PASS. No migration test from a previous schema with data was added for `0013`; it only adds an index.

### Security Tests
- Owner filter (a member sees 0 of the admin's runs).
- 401 without a session.
- The response contains no error message text (asserted in `test_api_serves_the_summary`).

### E2E Tests
**Not run.** No Playwright spec covers `/monitoring`, and the page was not opened in a real browser.

### Accessibility Tests
None automated beyond the role/label queries in the component tests (named tables, labelled form controls). No axe or manual screen-reader check.

### Performance Tests
No benchmarks. The only measurement is the cold-import timing in §10 #2.

## 13. Quality Gates

Results from the final run on 2026-10-04:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `ruff format --check .`; `prettier --check .` |
| Linter | PASS | `ruff check .`; `eslint .` |
| Type Checker | PASS | `mypy .`; `tsc -b` |
| Unit Tests | PASS | Backend `tests/application`; frontend Vitest |
| Integration Tests | PASS | Backend full suite (see the result line below) |
| API Tests | PASS | `tests/integration/test_monitoring.py` |
| Frontend tests | PASS | 31 files, 385 tests. Failed before the flaky-test fix (§10 #2). |
| E2E Tests | N/A | Not run (§12) |
| Build | PASS | `npm run build`; `MonitoringPage` chunk 11.61 kB (4.15 kB gzip) |
| Security Checks | PASS | The security tests above. No dependency audit was run. |
| Accessibility | N/A | No automated audit |

Backend full suite, final run: **1976 passed, 6 skipped** in 272 s (`uv run pytest`, Docker/Testcontainers available). The 6 skips are pre-existing.

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Metriken stammen aus Backend-Daten (metrics come from backend data) | PASS | Every figure is rendered from `MonitoringSummary` / `status-counts` / `system/status`. `model.ts` only groups and formats. Backend integration tests compute from persisted PostgreSQL rows. |
| Zeitraumfilter funktionieren (the time range filter works) | PASS | Page test "asks for each preset window and validates a custom one" checks the request spans (1 h, 24 h, custom 1 day) and URL state. Backend `test_window_bounds_are_half_open` and the invalid-window tests. |
| Empty States funktionieren (empty states work) | PASS | Page tests "shows an empty state…", "handles partial data…" and "asks to choose a range for an invalid custom link". Backend `test_empty_window_is_all_zero_not_missing`. |
| Keine erfundenen Daten (no invented data) | PASS | Scope items without backend data (trigger, provider, queue depth, resources) are omitted, not approximated. Rates show "—" without a denominator. The chart draws only backend counts. |

Roadmap scope items, checked individually:

| Scope item | Status |
|---|---|
| Active executions | Done ("Right now", current counts) |
| Execution rate | Done (timeline per bucket) |
| Success rate | Done |
| Failure rate | Done |
| Duration | Done |
| Node performance | Done |
| Token usage | Done |
| Cost | Done |
| Error trends | Partial: failures per bucket plus top codes per window; no per-code time series |

## 15. Security Considerations

- **Authentication / authorization:**
  - the route sits on the protected router (session required);
  - visibility uses the same `owner_filter` as the cross-project execution listing;
  - tested for members and anonymous callers.
- **Data exposure:** only counts, statuses, error codes and kinds, timings, node types, tokens and costs. No error messages, ids, inputs, outputs or project names (asserted).
- **Input validation:**
  - window rules are enforced server-side (`check_window`) and mirrored client-side for UX only;
  - unknown `range` values fall back to the default;
  - the custom window is parsed with `Date.parse` and re-serialized as ISO; raw user text is never put into the query.
- **Resource limits:** the 31-day cap and 100 buckets bound the response size. Row count per request is not capped (§16).
- **Output encoding:** all values render as React text. No `dangerouslySetInnerHTML`. The only inline style is a numeric bar height.
- **Error disclosure:** errors use the existing `ErrorResponse` envelope; the UI shows `errorMessage()` text.

## 16. Performance Considerations

- **Queries:** two `SELECT`s per request, using the new `ix_executions_created_at` index for the range. The node query evaluates one JSON path per node row against its version graph.
- **Memory:** every execution and node row in the window is loaded into the API process (`ponytail:` marker). For high-volume installations this is the first bottleneck; the upgrade is SQL aggregation or rollups.
- **Payload:**
  - bounded by ≤ 100 timeline buckets × 9 statuses;
  - ≤ 10 errors;
  - one entry per installed node type.
- **Rendering:**
  - ≤ 100 bars;
  - the node table is unvirtualized, bounded by node types (200 tested in jsdom).
- **Caching:** query key = exact window. Preset windows are rounded to the minute, so re-renders do not refetch.
- **Measurements:** none beyond the test-import timing. No load test was run.

## 17. Documentation Changes

| File | What |
|---|---|
| `docs/architecture.md` | API table row for `/api/v1/monitoring/summary`; new §113.1: population, contents, shared tally, no ids or messages, ceiling and upgrade path |
| `docs/reports/frontend/phase-16-monitoring.md` | This report |
| Code docstrings | `application/monitoring.py` (semantics the UI must not reinterpret), `api/monitoring.py`, migration `0013` |

Should be updated later:
- `docs/roadmap-frontend.md` could note that trigger and provider breakdowns depend on backend support.
- §113's "not yet emitted" list is unchanged; it is still accurate.

## 18. Dependencies / Infrastructure Changes

- No new or removed dependencies, and no version changes.
- No environment variables, Docker or Redis changes.
- **Database:** migration `0013_executions_created_at_index` (index only, reversible).
- **Test infrastructure:** `renderRoute()` returns `ready` (additive; existing callers are unaffected).

## 19. Deferred Work

| Item | Why deferred | Intended phase | Blocks Phase 16? |
|---|---|---|---|
| Per-trigger breakdown | Not in the summary API; `trigger_type` is in execution metadata but not aggregated | Unassigned; needs backend work | No |
| Provider/model usage | Usage is reported per node output; there is no provider or model aggregation API | Unassigned; needs backend work | No |
| Queue depth / runtime gauges / resources | Prometheus-only and per process; no UI-facing API | Unassigned | No |
| Per-error-code time series | Not in the API | Unassigned | No |
| SQL-side aggregation / rollups | Not needed at current volumes | When window loads become costly | No |
| Auto-refresh / live updates | Not required; Refresh is explicit | Phase 24 (performance and UX) or later | No |
| Playwright full-stack check of `/monitoring` | Not done this phase | Phase 26 (E2E) | No |
| `node_type` column on `node_executions` | Avoids a JSON-path lookup per row | Unassigned | No |

## 20. Known Limitations

- Error trends: failed/timed-out counts per bucket plus the top 10 codes for the whole window; no time series per code.
- The last timeline bucket can be shorter than `bucket_seconds` (custom windows). The table shows its exact `end`, but the chart bar does not mark it as partial.
- "Right now" counts are all-time, not windowed. The card's description says so.
- Preset windows end at the minute boundary and do not move until Refresh.
- No real-browser rendering check of the chart layout, the narrow-screen layout or dark mode.
- Tokens and cost appear only where nodes report them in `outputs.metadata` (currently `core.llm`). `cost_complete=false` marks gaps.

## 21. Open Issues

| Issue | Impact | Severity | Area | Next action | Target |
|---|---|---|---|---|---|
| In-memory aggregation has no row cap | A very large 31-day window loads every row | MEDIUM | Backend monitoring | Measure on realistic volume; move to SQL aggregates if slow | Backend performance work |
| No E2E or real-browser coverage of `/monitoring` | Layout regressions go unnoticed | LOW | Frontend | Add to the Playwright suite | Phase 26 |
| Work uncommitted | — | — | Repository | Commit as separate focused commits (Phase 16; flaky-test fix) | Before Phase 17 |

## 22. Architectural / Product Decisions

- Monitoring numbers come from persisted state, not Prometheus. Prometheus stays the operator's scrape target.
- One population: runs *created* in the window. Rates use finished runs only, always with the denominator shown.
- Durations are never mixed across outcomes.
- The report and monitoring share `MetricsTally`. Change it in one place, and the report/monitoring agreement test must still pass.
- Costs are never summed across currencies.
- The range lives in the URL (shareable); preset windows are anchored and rounded to the minute so cached data does not churn.
- Route tests that load heavy lazy pages should await `renderRoute().ready` rather than raise timeouts.

## 23. Repository / Git State

- **Branch:** `main`. **HEAD:** `6fd7583` (frontend phase 15). Nothing committed for this phase.
- **Modified (17):**
  - `backend/src/akasha/api/routes.py`
  - `backend/src/akasha/application/persistence.py`
  - `backend/src/akasha/application/reports.py`
  - `backend/src/akasha/bootstrap.py`
  - `backend/src/akasha/infrastructure/db_models.py`
  - `backend/src/akasha/infrastructure/persistence.py`
  - `backend/tests/application/test_reports.py`
  - `backend/tests/application/test_trigger_service.py`
  - `backend/tests/integration/test_persistence.py`
  - `docs/architecture.md`
  - `frontend/src/api/contract.node.test.ts`
  - `frontend/src/api/openapi.json`
  - `frontend/src/api/queries.ts`
  - `frontend/src/app/RouteErrorBoundary.test.tsx`
  - `frontend/src/app/routes.tsx`
  - `frontend/src/features/executions/ExecutionInspector.test.tsx`
  - `frontend/src/test/app.tsx`
- **Untracked:**
  - `backend/migrations/versions/0013_executions_created_at_index.py`
  - `backend/src/akasha/api/monitoring.py`
  - `backend/src/akasha/application/monitoring.py`
  - `backend/tests/application/test_monitoring.py`
  - `backend/tests/integration/test_monitoring.py`
  - `docs/reports/frontend/phase-16-monitoring.md`
  - `frontend/src/api/monitoring.ts`
  - `frontend/src/features/monitoring/`
- **Unrelated changes:** none detected.

## 24. Phase Completion Assessment

### Implemented
- Backend summary endpoint, service, repository queries, index migration and shared `MetricsTally`.
- Frontend monitoring page with ranges, stats, timeline, durations, errors, node performance, current state and system status.
- The flaky-test fix.

### Verified
- Backend unit, integration (PostgreSQL), API, contract and migration tests.
- Frontend unit, component and contract tests.
- All formatters, linters, type checkers and the build.

### Not Implemented
- Trigger, provider/model and resource/queue monitoring (no backend data).
- Per-code error time series.

### Deferred
See §19.

### Open
See §21: aggregation row cap, E2E coverage, commit.

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

**Phase 17 (Credentials UI) can assume:**
- an unchanged credentials API;
- `renderRoute()` returns `ready` for route tests;
- the `/credentials` placeholder is now the route `RouteErrorBoundary.test.tsx` uses to simulate an API crash. When Phase 17 replaces that placeholder, move the boundary test to another placeholder (e.g. `/triggers`, `/reports`, `/plugins` or `/settings`).

**Available contracts:**
- `GET /api/v1/monitoring/summary` (`frontend/src/api/monitoring.ts`);
- `monitoringQueries.summary`;
- `queryKeys.monitoring(window)`.

**Do not change casually:**
- `MetricsTally` semantics (shared with the report);
- the "created in `[since, until)`" population;
- the 31-day cap (mirrored in `model.ts` `MAX_WINDOW_MS`);
- migration `0013` (already applied in dev databases; add new migrations instead).

**Prerequisite:** commit the Phase 16 work before starting Phase 17.
