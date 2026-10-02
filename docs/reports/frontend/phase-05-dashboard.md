# Phase 05 — Dashboard

## 1. Executive Summary

**Goal.** Roadmap §11, "Phase 05 — Dashboard", asks for a first operational overview of the system. The dashboard should show:

- active executions
- recent executions
- a success/failure overview
- durations
- token usage
- costs
- recent errors
- system status

It must use real backend data, have loading, empty and error states, be refreshable, and contain no invented statistics.

**What was implemented.** `/dashboard` now renders `features/dashboard/DashboardPage` instead of the shared placeholder. It has six sections, and each one is backed by a backend endpoint:

| Section | Content | Endpoint |
|---|---|---|
| Execution outcomes | Active, Completed, Failed (with the timed-out share) and Cancelled counts | `GET /executions/status-counts` |
| Active executions | Up to 10 executions in a non-terminal status | `GET /executions?status=…` |
| Recent failures | The 5 newest `failed` or `timeout` executions; each row expands to show its errors | `GET /executions?status=…`; on expand, `GET /reports/executions/{id}` |
| Recent executions | The 10 newest executions, any status | `GET /executions` |
| System | Readiness, failing checks and engine version | `GET /system/status` |
| Projects | Totals by project status | `GET /projects?limit=100` |

**Behavior across sections:**

- Each section has its own loading, empty, error-with-retry and success state.
- A failed refresh keeps the last data and shows a warning.
- When every section has failed with nothing to show, a single page-level error replaces the six section errors. This happens when the backend is unreachable or there is no session.
- A Refresh button refetches every query on screen.

**Backend changes.** No cross-project execution endpoint existed. Three read-only endpoints were added:

- `GET /api/v1/executions`
- `GET /api/v1/executions/status-counts`
- `GET /api/v1/system/status`

All three are tested against real PostgreSQL and documented.

**Resulting state:**

| Area | Result |
|---|---|
| Frontend unit tests | 162/162 Vitest pass |
| Frontend E2E | 16/16 Playwright pass |
| Frontend format, lint, typecheck, build | Pass |
| Backend tests | 1858 passed, 6 skipped |
| Backend ruff, mypy | Clean |
| Commits | Nothing committed; the working tree holds the whole phase (§23) |

**Acceptance criteria.** All four roadmap acceptance criteria pass (§14). One scope item is not delivered:

- Token usage and cost: the backend offers them only per execution, inside the report. No aggregate exists.

"Recent errors" is served: each Recent failures row can be expanded, which fetches that execution's report on demand and shows its recorded execution and node errors.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §11.** Objective: "Eine erste operative Übersicht über das System." (A first operational overview of the system.)

Scope:

- active executions
- recent executions
- success/failure overview
- durations
- token usage
- costs
- recent errors
- system status

Acceptance criteria:

1. Real backend data.
2. Loading, empty and error states.
3. Data can be refreshed.
4. No invented statistics.

The prompt qualifies tokens and cost with "Token/Kosten sofern Backend diese liefert", that is, only if the backend provides them.

**The session prompt added:**

- Projects, relevant system information and navigation shortcuts.
- Missing backend functions must be implemented, tested and documented.
- Every data section needs loading, empty, error and success states.
- No decoration without operational value.
- Tests for rendering, data loading, empty states, API failure, navigation and responsive behavior.
- Run the quality gates.

**Explicitly not part of this phase:**

| Item | Phase |
|---|---|
| Project management | 06 |
| Execution UI and detail pages | 13 |
| Live updates over WebSocket | 14 |
| Monitoring (metrics, tokens, cost views) | 16 |
| Authentication UI | 21 |

---

## 3. Scope Implemented

### Backend / API

**Endpoints** (each declares 404/409/422/500 `ErrorResponse`, inherited from the executions router):

- `GET /api/v1/executions?status=&offset=&limit=`
  - Executions across the caller's visible projects, newest first.
  - `status` is a repeatable `ExecutionStatus` filter.
  - Standard `offset`/`limit` pagination.
- `GET /api/v1/executions/status-counts`
  - `{counts: {<status>: int}}` with all nine statuses, zeros included.
  - Same visibility as the listing.
- `GET /api/v1/system/status`
  - `{version, ready, failures}`.
  - Uses the same readiness checks as `/ready`, but always answers 200.

**Application layer:**

- `ExecutionService.list_recent(owner_id, statuses, offset, limit)`.
- `ExecutionService.count_by_status(owner_id)`, which fills in zeros for absent statuses.

**Persistence:**

- `ExecutionRepository.list_summaries` is generalized to keyword filters: `project_id`, `owner_id`, `statuses`.
- `ExecutionRepository.count_by_status(owner_id)` is new.
- The SQL implementation uses a shared `_owned()` helper, which joins `projects` when an owner filter applies.

### Frontend / API layer

- **`api/executions.ts`**
  - `executionStatusCountsSchema` and `ExecutionStatusCounts`.
  - `ExecutionListParams`: `PageParams` plus `status?`.
  - `listExecutions()` and `getExecutionStatusCounts()`.
- **`api/system.ts`** (new): `systemStatusSchema`, `SystemStatus`, `getSystemStatus()`.
- **`api/queries.ts`**
  - New keys: `queryKeys.executions.overview()`, `.list(params)`, `.statusCounts()`, `queryKeys.system()`.
  - New factories: `executionQueries.list`, `executionQueries.statusCounts`, `systemQueries.status`.
- **`api/mutations.ts`**: `useExecuteProjectVersion`, `useInvokeTrigger` and `useCancelExecution` also invalidate `queryKeys.executions.overview()`.
- **`api/client.ts`**: `RequestOptions.query` accepts `readonly string[]`, which is sent as repeated parameters.

### Frontend / UI

New directory `frontend/src/features/dashboard/`:

- **`DashboardPage.tsx`**
  - Page header with a Refresh button.
  - Responsive grid of six sections.
  - `useOutage()` decides when to collapse all sections into one page-level error.
- **`sections.tsx`**: `ExecutionCountsCard`, `ExecutionListCard` (used three times; `errorDetails` turns on row expansion), `SystemStatusCard`, `ProjectsCard`, and the private `ExecutionTable`, `StatusBadge`, `Time`, `ViewAll`.
- **`FailureDetails.tsx`**: shown under an expanded failure row. It mounts only when the row is expanded, then queries `executionQueries.report` and lists the execution error and every node error (code, message, node label/id, type, attempt).
- **`QuerySection.tsx`**: generic loading / error / stale / success rendering for one `UseQueryResult`.
- **`format.ts`**
  - Status labels and tones; `ACTIVE_STATUSES`, `FAILURE_STATUSES`.
  - The three list parameter sets.
  - `PROJECT_SAMPLE` (`{limit: 100}`).
  - `formatDateTime`, `formatDuration`, `errorMessage`.
- **`routes.tsx`**: `/dashboard` lazy-loads `DashboardPage`. The other nav items keep the placeholder.

**Design system:**

- `Table`'s scroll container is now `role="region"` with `aria-label={caption}`, `tabIndex={0}` and `focus-ring`. It is also `relative` (so it clips absolutely positioned content such as sr-only text) and a CSS size container (`@container`).
- `Table` has a new optional `details(row)` prop. When it returns content, a full-width row follows that row. The content is pinned (`sticky`, `100cqw` wide) to the visible part of the scroll container.
- `Card.className` accepts `undefined`.

### Testing

New test files:

- `features/dashboard/DashboardPage.test.tsx` (13 tests)
- `tests/e2e/dashboard.spec.ts` (5 tests)
- `src/test/app.tsx`, a screen test harness

Extended tests: `test_executions_api.py`, `serverState.test.tsx`, `client.node.test.ts`, `contract.node.test.ts`, `display.test.tsx`, `routes.test.tsx`, `App.test.tsx`. See §12 for details.

---

## 4. Files Changed

**New directories:** `frontend/src/features/` and `frontend/src/features/dashboard/`.

**Not changed:** no migrations, no dependency changes. Configuration changed in one place only: `vite.config.ts`.

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/api/executions.py` | Modified (+39/−2) | `GET /executions` and `GET /executions/status-counts`; `StatusCounts` model; module docstring |
| `backend/src/akasha/api/routes.py` | Modified (+24/−1) | `SystemStatus`, `create_system_router()` (`GET /system/status`) mounted in the protected router; access-policy docstring |
| `backend/src/akasha/application/executions.py` | Modified (+28/−2) | `list_recent`, `count_by_status`; `list_executions` passes `project_id=` by keyword |
| `backend/src/akasha/application/persistence.py` | Modified (+17/−3) | `ExecutionRepository.list_summaries` filters; `count_by_status` protocol method |
| `backend/src/akasha/infrastructure/persistence.py` | Modified (+48/−15) | `_owned()` helper; generalized `list_summaries`; `count_by_status` (GROUP BY status) |
| `backend/tests/application/test_trigger_service.py` | Modified (+13/−2) | `FakeExecutions` conforms to the changed protocol |
| `backend/tests/integration/test_executions_api.py` | Modified (+153/−1) | 6 new API tests; `Env.app` and `Env.client_with()` for second callers |
| `backend/README.md` | Modified (+3) | API table rows for the three endpoints |
| `docs/architecture.md` | Modified (+3) | §22 endpoint list |
| `frontend/src/api/openapi.json` | Modified (generated, +234) | Regenerated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py` |
| `frontend/src/api/executions.ts` | Modified (+35) | Cross-project list and status-counts services and schemas |
| `frontend/src/api/system.ts` | Created (19) | System status service and schema |
| `frontend/src/api/queries.ts` | Modified (+31/−1) | Keys and query options for the new endpoints |
| `frontend/src/api/mutations.ts` | Modified (+11/−2) | Execution writes invalidate the dashboard's cross-project views |
| `frontend/src/api/client.ts` | Modified (+8/−2) | Repeated query parameters |
| `frontend/src/api/client.node.test.ts` | Modified (+9/−2) | Asserts `&status=failed&status=timeout` |
| `frontend/src/api/contract.node.test.ts` | Modified (+5) | Covers the 3 new service functions |
| `frontend/src/api/serverState.test.tsx` | Modified (+19) | Execute and cancel refetch the cross-project list and counts |
| `frontend/src/features/dashboard/DashboardPage.tsx` | Created (120) | Page, layout, Refresh, outage collapse |
| `frontend/src/features/dashboard/sections.tsx` | Created (408) | The six section components; expandable failure rows |
| `frontend/src/features/dashboard/FailureDetails.tsx` | Created (77) | On-demand error details from the execution report |
| `frontend/src/features/dashboard/QuerySection.tsx` | Created (55) | Per-section state rendering |
| `frontend/src/features/dashboard/format.ts` | Created (77) | Status display, list params, formatting, error message |
| `frontend/src/features/dashboard/DashboardPage.test.tsx` | Created (580) | Dashboard behavior tests |
| `frontend/src/test/app.tsx` | Created (61) | `stubBackend()` and `renderRoute()`: real router, ApiClient and QueryClient with a stub `fetch` |
| `frontend/tests/e2e/dashboard.spec.ts` | Created (237) | Layout, overflow, keyboard, outage and expanded error details in a real browser |
| `frontend/src/app/routes.tsx` | Modified (+14/−4) | Dashboard route |
| `frontend/src/app/routes.test.tsx` | Modified (+12/−8) | Renders through `renderRoute` with a stub backend; narrowed the fallback assertion |
| `frontend/src/app/App.test.tsx` | Modified (+6) | Stub backend (no real network); asserts requests stay under `/api/v1/` |
| `frontend/src/components/ui/Table.tsx` | Modified (+53/−21) | Keyboard-scrollable region; `relative` containment; optional `details` row |
| `frontend/src/components/ui/display.test.tsx` | Modified (+4) | Asserts the focusable region |
| `frontend/src/components/ui/Card.tsx` | Modified (+1/−1) | `className?: string \| undefined` (`exactOptionalPropertyTypes`) |
| `frontend/vite.config.ts` | Modified (+1) | `test.unstubGlobals: true` |
| `frontend/README.md` | Modified (+23/−2) | Structure, server-state invalidation, Dashboard section |
| `docs/reports/frontend/phase-05-dashboard.md` | Created | This report |

---

## 5. Architecture Changes

### 5.1 Cross-project execution listing and counts over HTTP

**Decision.** Add `GET /api/v1/executions` and `GET /api/v1/executions/status-counts`.

**Context.** Executions could only be listed per project (`/projects/{id}/executions`). Recent executions across projects and status counts would otherwise require one request per project, which is N+1, and counting client-side. Counting client-side would also make the statistics depend on the page loaded.

**Chosen approach.**

- `ExecutionRepository.list_summaries` takes optional `project_id`, `owner_id` and `statuses`, so one query covers both listings.
- `count_by_status` is a single `GROUP BY status`.
- Visibility uses `AuthorizationService.owner_filter(identity)`: admins see everything, members see their own projects. This is the same rule as `GET /projects`.

**Alternatives considered.**

1. Fan out per project in the frontend. Rejected: N+1 requests, and "recent across all projects" would be incorrect.
2. Compute counts from a page of executions. Rejected: those would be invented statistics, and the roadmap forbids them.
3. A separate `list_recent` repository method. Rejected: it would duplicate the query and the row mapping.

**Consequences.**

- Phase 13 (Execution UI) can use `GET /executions` directly for its list and filters.
- The `/executions/status-counts` route must stay registered before `/executions/{execution_id}`, which would otherwise match it. A code comment says so.

### 5.2 System status under `/api/v1`

**Decision.** Add `GET /api/v1/system/status`, available to any session. It mirrors `/health` and `/ready`.

**Context.** The frontend's same-origin model proxies only `/api` (`vite.config.ts`). `/health` and `/ready` live at the root because they are container probes, and `/ready` answers 503 when not ready.

**Chosen approach.** One route that reuses `Container.readiness_failures()` and returns 200 with `ready: bool`.

**Alternatives considered.**

1. Proxy `/ready` in Vite and in every deployment. Rejected: it widens the deployment contract, and a 503 status would have to be read as data.
2. Expose readiness only to admins. Rejected: `/ready` is already public, so the same data behind a session reveals nothing new.

**Consequences.** The probes stay unchanged. The UI has a stable, typed status contract.

### 5.3 Dashboard composition: independent sections with outage collapse

**Decision.** Each section owns its query and its states. The page replaces all sections with one error only when every section has failed with no data.

**Reason.** A single failing endpoint must not blank the overview. But six identical alerts during a total outage are noise, especially for screen readers, where each `role="alert"` is announced.

**Consequences.**

- `useOutage()` subscribes to the same six queries; TanStack deduplicates them by key.
- When the sections' query parameters change, `useOutage()` must change with them. They share the constants in `format.ts`.

### 5.4 Error details on demand, not in the listing

**Decision.** Recent failures show their errors only when the user expands a row. Expanding fetches the existing `GET /reports/executions/{id}`.

**Context.** Error messages are stored per execution and per node, and only the report exposes them. The listing endpoints return `ExecutionDetail`, which has no error field.

**Alternatives considered.**

1. Add `error` to `ExecutionDetail` or the listing. Rejected for now: it widens a shared contract (every trigger response and listing), and node-level errors would still be missing.
2. Fetch every listed failure's report on load. Rejected: 5 extra requests on every dashboard load, and reports can be large (events, outputs).

**Chosen approach.** A disclosure button per row (`aria-expanded`, `aria-controls`). `FailureDetails` is mounted, and the report requested, only while the row is expanded. The report is already redacted by the backend (`application/reports.py`: messages and details pass through `redact_text`/`redact_mapping`).

**Consequences.**

- Phase 13/15 (execution inspector) can reuse `executionQueries.report`. The cache key is shared, so an expanded row warms the inspector.
- `Table.details` is available to other features for expandable rows.

No ADR was written. These are additive API endpoints and a feature-level UI decision, not a change of the documented architecture.

---

## 6. Domain / Data Model Changes

**Not changed:**

- No domain entities.
- No database tables, columns, indexes or constraints. No migration.

**Changed:**

- **Application read model:** `ExecutionSummary` is now also built for cross-project listings. `project_id` is read from the row instead of the method argument.
- **Repository protocol:** `ExecutionRepository.list_summaries` changed from `(project_id, *, offset, limit)` to keyword-only `(*, project_id=None, owner_id=None, statuses=(), offset, limit)`. There was one caller (`ExecutionService.list_executions`) and one fake (`FakeExecutions`); both were updated.

**Frontend types:**

- `ExecutionStatusCounts` is `{counts: Record<ExecutionStatus, number>}`. Zod's enum-keyed record requires all nine keys, which matches the backend's guarantee.
- `SystemStatus` is new.
- `ExecutionListParams` is new.

---

## 7. API / Contract Changes

All three endpoints are additive, so they are backward-compatible. All are in `openapi.json` and checked by `contract.node.test.ts`.

### `GET /api/v1/executions`

- **Purpose:** executions across projects, newest first (`created_at DESC, id DESC`).
- **Auth:** session required. Members see executions of projects they own; admins see all.
- **Request:**
  - `status`: repeatable `ExecutionStatus`; omitted means all statuses.
  - `offset >= 0`.
  - `1 <= limit <= 100`; default 50.
- **Response 200:** `ExecutionPage`, the same schema as the per-project listing: `{items: ExecutionDetail[], page: {offset, limit, has_more}}`.
- **Errors:**
  - 401 `AUTHENTICATION_REQUIRED`.
  - 422 for an unknown status or invalid paging.

### `GET /api/v1/executions/status-counts`

- **Purpose:** the number of visible executions in each status.
- **Auth:** as above.
- **Response 200:** `{counts: {created, queued, running, paused, waiting, completed, failed, cancelled, timeout: int}}`. Every key is always present.
- **Errors:** 401.
- **Compatibility:** a new backend status adds a key. The frontend's exhaustive record would then reject the response as `malformed_response`, so adding a status is a coordinated contract change. That is the same rule Phase 04 established for status enums.

### `GET /api/v1/system/status`

- **Purpose:** engine version and readiness for UI clients.
- **Auth:** any session.
- **Response 200:** `{version: str, ready: bool, failures: {check: reason}}`. It is 200 even when not ready.
- **Errors:** 401.

### Frontend/backend contract

- `contract.node.test.ts` checks the three new service functions: method, path, and exact response fields.
- Query parameters are not compared by the contract test; this was already true before. Their behavior is covered by `client.node.test.ts` and `DashboardPage.test.tsx`.

---

## 8. Implementation Details

### Data flow

```text
Section component
→ useQuery(executionQueries.* | systemQueries.status | projectQueries.list)
→ ApiClient
→ /api/v1/...
```

- There is no dashboard store. All server state is in the TanStack Query cache. The only UI state is the set of expanded failure rows (`useState` in `ExecutionTable`). It is not persisted and resets on reload.
- Expanding a failure row mounts `FailureDetails`, which runs `useQuery(executionQueries.report(client, id))`. Collapsing it unmounts the query observer; the cached report stays for TanStack's default `gcTime` (5 min).
- `useOutage()` uses `useQueries({ queries: [the same six], combine })`. It returns `{error}` only when every result `isError` and has no data, otherwise `null`.

### `QuerySection` state order

1. **Data present:** render the data. If the latest fetch failed, show a warning `Alert` with a Retry button above it (the stale state).
2. **Error with no data:** `ErrorState` (h3) with the API error message and Retry.
3. **Otherwise:** `LoadingState` labelled `Loading <subject>…`.

`errorMessage()` shows `ApiError.message`, which the backend redacts and writes for users. Any other error becomes "An unexpected error occurred."

### Refresh

- The page button calls `queryClient.refetchQueries({ type: 'active' })`, which refetches only mounted, observed queries.
- The button shows `loading`, with `aria-busy`, while `useIsFetching() > 0`.
- There is no polling. Refetch on mount and on window focus are the TanStack defaults.

### Project names

- `ExecutionTable` maps `project_id` to a name using the cached `/projects?limit=100` query, shared with `ProjectsCard`.
- When the name is unknown (still loading, failed, or beyond 100 projects), it shows the id. Long ids are truncated with `title`; the full text stays in the DOM.

### Durations

- `formatDuration(started_at, finished_at)` produces `ms`, `s`, `min s` or `h min`.
- An execution that has started but not finished shows "In progress".
- An execution that has not started shows "Not started".
- Timestamps are rendered in `<time dateTime=…>` with the user's locale (`Intl.DateTimeFormat`).

### Status display

`executionStatusDisplay` maps each status to a label and tone. Badges always carry a text label, so color is never the only signal. The frontend does not interpret state transitions.

### Cache invalidation

Execute, invoke and cancel invalidate `['executions','overview']`. That prefix covers `list(params)` and `statusCounts()`.

### Layout

- A single CSS grid with `grid-cols-1` and `lg:grid-cols-3`. DOM order is the reading order at every width:

  ```text
  outcomes → system → active → failures → recent → projects
  ```

- On `lg`:
  - the lists span two columns (`lg:col-span-2 lg:col-start-1`);
  - Projects sits at `lg:col-start-3 lg:row-start-2 lg:row-span-3`.

### Backend SQL

- **`list_summaries`:** `SELECT … FROM executions [JOIN projects ON projects.id = executions.project_id WHERE projects.owner_id = :owner] [AND project_id = …] [AND status IN …] ORDER BY created_at DESC, id DESC OFFSET … LIMIT limit+1`.
- **`count_by_status`:** `SELECT status, count(*) … GROUP BY status`, with the same optional join.
- Both run inside a Unit of Work; there are no writes.

---

## 9. Findings

### F1 — No cross-project execution listing existed

- **Impact:** HIGH (it blocked the main dashboard content)
- **Resolution:** FIXED
- **Notes:** §5.1.

### F2 — `/health` and `/ready` are unreachable for the UI

- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** The proxy forwards only `/api`. §5.2.

### F3 — Token usage and cost are only available per execution

- **Impact:** MEDIUM (a scope item is not delivered)
- **Resolution:** DEFERRED
- **Notes:**
  - Usage and cost are computed in the execution report from node execution outputs.
  - There is no stored aggregate column. Prometheus `/metrics` is process-local and admin- or scrape-token-only.
  - An aggregate needs a deliberate backend design: JSON aggregation over `node_executions.outputs`, or persisted totals. That belongs to Monitoring (Phase 16).

### F4 — No frontend authentication yet

- **Impact:** HIGH for using the app against a real backend; none for this phase's verification.
- **Resolution:** OPEN (Phase 21)
- **Notes:** `App.tsx` creates `ApiClient` without `getToken`, so every protected call returns 401. Against a real backend the dashboard currently shows the single page-level error "Authentication required."

### F5 — Phase 01–04 route tests rendered pages without providers

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** The first data-driven page made `routes.test.tsx` fail with "No QueryClient set". The new `src/test/app.tsx` provides the real providers and a stub backend.

### F6 — `App.test.tsx` would reach the real network

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** With a data-driven dashboard, rendering `<App/>` triggered real `fetch` calls (AGENTS §124). It now uses `stubBackend()` and asserts that every request stays under `/api/v1/`.

### F7 — The production bundle grew

- **Impact:** LOW
- **Resolution:** Accepted
- **Notes:**
  - The main chunk went from 443.31 kB (Phase 04) to 451.41 kB (137.98 kB gzip).
  - The new `DashboardPage` chunk is 28.29 kB (9.12 kB gzip).
  - The growth comes from the TanStack Query hooks and API modules now actually being used; Phase 04's were tree-shaken out.

### F8 — Error messages are only available through the execution report

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** The first version listed failed executions without their errors, because messages are only in `GET /reports/executions/{id}`. Rows are now expandable and fetch the report on demand (§5.4).

### F9 — SQLAlchemy deprecation

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** `Result.tuples()` is deprecated since 2.1. It is not used.

---

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Origin |
|---|---|---|---|---|---|
| B1 | On a 375 px viewport the page scrolled 307 px horizontally | The grid had no explicit columns, so the implicit `auto` column took the table's min-content width. `Table`'s `overflow-x-auto` never engaged. | `grid-cols-1` (`minmax(0,1fr)`) on the dashboard grid | `dashboard.spec.ts` › "mobile: sections stack and the page never scrolls sideways" | Introduced this phase; caught before completion |
| B2 | Keyboard users could not scroll wide tables horizontally | `Table`'s scroll container was not focusable (WCAG 2.1.1; axe `scrollable-region-focusable`) | `role="region"`, `aria-label`, `tabIndex={0}` and `focus-ring` on the container | `display.test.tsx` › Table test asserts `tabindex="0"` | Pre-existing in the Phase 02 design system |
| B3 | Long project ids stretched the tables | Unbounded monospace text | `max-w-40 truncate` plus `title` | Visual review (screenshots); not asserted automatically | Introduced this phase |
| B4 | The Duration column wrapped ("In progress" was cut off) | No `whitespace-nowrap` | Wrapped the cell in `whitespace-nowrap` | Visual review; not asserted automatically | Introduced this phase |
| B5 | A total backend outage showed six identical error alerts | Each section rendered its own error independently | `useOutage()` page-level collapse | `DashboardPage.test.tsx` › "shows one page-level error when every section fails, and recovers"; `dashboard.spec.ts` › "an unreachable backend shows one error, not one per section" | Introduced this phase; fixed after review |
| B7 | Opening a failure's details scrolled the table sideways and cut off the error text; on mobile the details were as wide as the whole table | The details cell lived inside the horizontally scrolling table and took the table's width | Details content is `sticky left-4` with `w-[calc(100cqw-2rem)]`; the scroll region is a size container (`@container`) | `dashboard.spec.ts` › "mobile: expanded error details stay within the visible area" | Introduced this phase |
| B8 | Adding the error-column header made the mobile page 368 px too wide | The header's sr-only `<span>` is `position: absolute`. Its containing block was outside the table's scroll container, so the scroller did not clip it. | The scroll region is now `relative` | `dashboard.spec.ts` › "mobile: sections stack and the page never scrolls sideways" | Latent in the Phase 02 `Table` (any absolutely positioned content in a cell); triggered this phase |
| B6 | On mobile, System status rendered last | The column-based layout placed it after all the lists | A single grid whose DOM order is the reading order | `dashboard.spec.ts` desktop/mobile position assertions | Introduced this phase |

### Tooling problems resolved during development (not defects)

- **`react-refresh/only-export-components`:** non-component exports were moved to `format.ts`.
- **`exactOptionalPropertyTypes` on `Card.className`:** widened the type.
- **mypy:** `int(n)` on an untyped `Row`; fixed with `.all()` and indexed access.
- **Playwright `tsconfig.node.json` has no DOM lib:** page-side expressions are passed as strings.
- **`role="status"` has no name from content:** the loading test asserts the label text plus its role.

---

## 11. Fixes Applied

| Fix | Symptom | Implementation | Verification |
|---|---|---|---|
| B1 mobile overflow | `scrollWidth − innerWidth = 307` | `DashboardPage.tsx` grid `grid-cols-1` | E2E overflow ≤ 0 passes |
| B2 keyboard table scroll | Scroll container not in the tab order | `Table.tsx` region/tabIndex | Unit assertion; E2E keyboard test |
| B3 / B4 table cell layout | Stretched or cut cells | `sections.tsx` cell classes | Before/after screenshots at 1440 px and 375 px |
| B7 details hidden by sideways scroll | Error text cut off | `Table.tsx` sticky, container-width details cell | E2E bounding box within 375 px; screenshots |
| B8 sr-only overflow | `scrollWidth` 743 px at 375 px | `Table.tsx` `relative` scroll region | E2E overflow test passes |
| B5 outage noise | Six alerts | `useOutage()` and a page-level `ErrorState` with Retry | Unit and E2E tests above |
| B6 mobile order | System at the bottom | Grid placement classes | E2E desktop/mobile box assertions |
| F5 route tests without providers | `routes.test.tsx` failed | `src/test/app.tsx` `renderRoute()` | 162/162 Vitest |
| F6 App test hitting the network | Real `fetch` from jsdom | `stubBackend()` in `App.test.tsx` | Test asserts all requests are `/api/v1/*` |
| Slow refresh-failure test | It waited through real retry backoff (503 is retried) | The test uses 500, which `shouldRetryQuery` does not retry | Test runs in milliseconds (AGENTS §24) |

---

## 12. Testing & Verification

### Unit / component tests (Vitest, jsdom)

**`DashboardPage.test.tsx` (13 tests, all PASS).** Each test renders the real route through `renderRoute('/dashboard')` with `stubBackend` replacing only `fetch`:

1. All sections from data:
   - counts per group, including the "1 timed out" note;
   - active rows with resolved project name, id fallback, "In progress" and "Not started";
   - failure and recent rows with version, trigger, duration "42 s" and `<time datetime>`;
   - system Ready and version;
   - project totals by status;
   - the exact query strings for the three lists.
2. Loading state per section (six labelled `role="status"` regions); Refresh is `aria-busy`.
3. Empty states for every section; no tables rendered.
4. One failing section (500) shows its alert while the others render; Retry recovers it.
5. All sections failing (401) gives exactly one page-level alert and no section cards; Retry restores the sections.
6. System not ready shows the failing check and its reason.
7. A failed refresh (500) keeps the earlier counts and shows a "showing earlier data" warning.
8. Truncation notes: "Showing the newest 1." and "1+" with "Status counts cover the first 1 projects."
9. and 10. Navigation: "All executions" leads to `/executions`; "All projects" leads to `/projects`.
11. The empty-projects state's "Go to Projects" leads to `/projects`.
12. Error details:
    - before expanding, no `/reports/` request is made;
    - expanding sets `aria-expanded="true"`, and the `aria-controls` region shows the execution error (code and message) and the node error with label, type and attempt;
    - "Hide error" collapses it.
13. Error details failure and empty cases: a 500 from the report shows "Could not load error details" inside the row while the rest of the dashboard is unaffected; Retry then shows "No error details were recorded for this execution." for a report without errors.

**Other unit and component tests:**

- `serverState.test.tsx`: execute and cancel refetch the mounted `executions.list` and `executions.statusCounts` queries (call counts 1→2→3). PASS.
- `client.node.test.ts`: array query values are sent as repeated parameters. PASS.
- `contract.node.test.ts`: `GET /executions`, `GET /executions/status-counts` and `GET /system/status` match OpenAPI. The coverage test counts the new functions. PASS.
- `display.test.tsx`: the Table scroll region is focusable. PASS.
- `routes.test.tsx` and `App.test.tsx`: unchanged intent, now run against the stub backend. PASS.

**Total:** 17 files, 162 tests (Phase 04: 146). PASS.

### Integration / API tests (pytest, real PostgreSQL via Testcontainers)

`backend/tests/integration/test_executions_api.py`. All PASS. They run through the real FastAPI app, ExecutionService, runtime and PostgreSQL:

- `test_recent_executions_span_projects_newest_first`: two projects, three runs; order across projects; pagination with `has_more`; empty listing shape.
- `test_recent_executions_filter_by_status`: completed, failed and queued runs; filters `failed,timeout` and `queued,running`; no filter; unknown status gives 422; bad paging gives 422.
- `test_status_counts_cover_every_status`: all nine keys at zero; then completed 2, failed 1, sum 3.
- `test_members_see_only_their_projects_executions`: a member sees none of the admin's runs in the list or the counts; then sees only their own; the admin sees both.
- `test_cross_project_routes_require_a_session`: 401 `AUTHENTICATION_REQUIRED` on both routes.
- `test_system_status_reports_version_and_readiness`: ready; an injected failing check gives `ready: false` with its reason and still 200; anonymous gives 401.

`tests/application/test_trigger_service.py` was updated for the protocol change and passes.

**Full backend suite:** 1858 passed, 6 skipped (Phase 04: 1852). The skips are external smoke tests that need a provider API key.

### E2E tests (Playwright, production bundle via `vite preview`, API stubbed with `page.route`)

`tests/e2e/dashboard.spec.ts`, all PASS:

1. Desktop at 1440 px: System sits to the right of Execution outcomes, at the same top.
2. Mobile at 375 px: sections stack; no horizontal page overflow.
3. Keyboard: Tab reaches Refresh, "All executions" and "All projects".
4. Unreachable backend (`route.abort('connectionrefused')`): after the client's retries, exactly one alert reading "Could not load the dashboard … The server could not be reached."
5. Mobile at 375 px: expanding a failure shows its error text, the toggle reports `aria-expanded="true"`, and the details box lies within the viewport.

The suite total is 16 tests (Phase 04: 11). All PASS.

### Security tests

- Owner isolation of the listing and the counts (integration test above).
- Authentication required on all three new routes.
- No new secrets path: the payloads carry execution metadata only.

### Accessibility tests

**Automated:**

- Labelled loading regions.
- `role="alert"` error states.
- Single h1, with h2 per card and h3 per state.
- Captioned tables (screen-reader-only captions).
- Focusable table scroll regions.
- Keyboard reachability (E2E).

**Manual review:** reviewed in light and dark mode at 1440 px and 375 px. Status badges always carry text, so color is not the only indicator.

**Not run:** no automated axe/contrast scan; no axe dependency is installed. Contrast relies on the Phase 02 tokens.

### Performance tests

None performed. See §16.

---

## 13. Quality Gates

All results below are from the final re-run, after the outage fix.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (frontend) | PASS | `npm run format:check` |
| Linter (frontend) | PASS | `npm run lint`. Failed during development (react-refresh, unnecessary-condition, void-expression, non-null style); all fixed, no rules suppressed |
| Type Checker (frontend) | PASS | `npm run typecheck`. Failed during development (`exactOptionalPropertyTypes`); fixed |
| Unit / component tests (frontend) | PASS | 162/162 |
| Build (frontend) | PASS | `index` 451.41 kB, `DashboardPage` 28.29 kB |
| E2E (frontend) | PASS | 16/16. Failed during development (mobile overflow B1 and B8; a focus-probe timeout in the test itself); all fixed |
| Formatter (backend) | PASS | `uv run ruff format --check .` |
| Linter (backend) | PASS | `uv run ruff check .` |
| Type Checker (backend) | PASS | `uv run mypy .`. Failed during development (`Row` typing); fixed |
| Unit + integration + API tests (backend) | PASS | 1858 passed, 6 skipped |
| OpenAPI snapshot | PASS | Regenerated; `tests/test_openapi_contract.py` passes |
| Frontend/backend contract | PASS | `contract.node.test.ts` |
| Security checks | PASS (tests only) | Authorization and authentication tests; no dependency audit was run (no dependency changes) |
| Accessibility | PASS (partial automation) | See §12; no axe scan |

---

## 14. Acceptance Criteria Verification

**Roadmap acceptance criteria:**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| The dashboard uses real backend data | PASS | Every section reads a `/api/v1` endpoint through `ApiClient` with Zod validation; there is no fixture data in production code. Schemas are checked against OpenAPI (`contract.node.test.ts`). Backend behavior is verified on PostgreSQL. |
| Loading, empty and error states exist | PASS | `QuerySection` and per-section `EmptyState`; tests 2, 3, 4 and 5 in §12; outage E2E. |
| Data can be refreshed | PASS | The Refresh button refetches active queries (test 7); per-section Retry (tests 4 and 5); mutation invalidation (`serverState.test.tsx`). |
| No invented statistics | PASS | Counts come from the server (`status-counts`). Partial project counts are labelled as partial. Token and cost are absent rather than estimated. Durations are computed only from the backend's `started_at` and `finished_at`. |

**Roadmap scope items** (not acceptance criteria, listed for completeness):

| Scope item | Status | Evidence / gap |
|---|---|---|
| Active executions | PASS | "Active executions" card |
| Recent executions | PASS | "Recent executions" card |
| Success/failure overview | PASS | "Execution outcomes" card |
| Durations | PASS | Per-execution duration column (no aggregate) |
| Token usage | NOT DELIVERED | No backend aggregate (F3); the roadmap qualifies it with "sofern Backend diese liefert" (if the backend provides it). Does not block. |
| Costs | NOT DELIVERED | As for token usage |
| Recent errors | PASS | Failed and timed-out executions are listed; each expands to its recorded execution and node errors (tests 12 and 13; E2E 5) |
| System status | PASS | "System" card |

**Session-prompt items:**

| Item | Status | Evidence |
|---|---|---|
| Projects | PASS | "Projects" card |
| Navigation shortcuts | PASS | Card header links; navigation tests 9–11 |
| Tests: rendering, loading, empty, failure, navigation, responsive | PASS | §12 |
| Missing backend functions implemented, tested and documented | PASS | §3, §7, §12, §17 |

---

## 15. Security Considerations

- **Authorization:**
  - The cross-project routes have no id in the path, so `authorize_path` does not apply to them. Visibility is enforced in the query through `AuthorizationService.owner_filter`, which is the rule `GET /projects` already uses.
  - Members cannot see counts or executions of other users' projects. This is tested.
- **Authentication:** all three routes sit in the `protected` router (`current_identity`). 401 is tested.
- **Information exposure:**
  - `system/status` returns the same `failures` reasons as the unauthenticated `/ready`. Putting it behind a session adds no exposure.
  - The version string was already public through `/health`.
- **Input validation:** `status` is an enum (422 otherwise); paging is bounded at 100. No user input reaches SQL other than as bound parameters.
- **Error disclosure:** the UI shows only `ApiError.message`, the backend's user-facing, redacted message, or a generic fallback. Request ids and stack traces are not shown.
- **XSS:** everything is rendered as React text. No `dangerouslySetInnerHTML`. Project names, ids and error messages are text nodes.
- **Error details:** the messages and details come from the execution report, which the backend redacts (`redact_text`, `redact_mapping`), and which `authorize_path` limits to callers who can see the execution. The frontend adds no new exposure and shows only `code`, `message`, node label/id/type and attempt count, not error `details`, inputs or outputs.
- **Secrets:** none in these payloads. No change to credential handling.
- **Test hygiene:** `App.test.tsx` and the route tests no longer make real network requests.

---

## 16. Performance Considerations

**Database: cross-project listing.**

- `ORDER BY created_at DESC, id DESC` has no supporting index. `ix_executions_project_id` and the partial index on queued rows exist; there is none on `created_at`.
- Each page therefore sorts the visible history, as a top-N heapsort. This is marked with a `ponytail:` comment in `infrastructure/persistence.py`.
- Recommended: add `ix_executions_created_at` (with a migration and a migration test) when request timings show the cost.
- Not measured.

**Database: status counts.** A full aggregate over `executions` (joined to `projects` for members) on each dashboard load. Not measured.

**Requests per dashboard load:**

- 6 HTTP requests: 3 execution lists, counts, system, projects.
- The projects list is shared, deduplicated by query key, between the Projects card and the name lookup in the tables.
- No polling.

**On-demand reports:** expanding a failure row costs one `GET /reports/executions/{id}`. A report includes the execution's events and node outputs, so it can be large, but it is fetched only on explicit request, once per row while it is cached.

**Payload sizes:**

- Execution lists are capped at 10, 5 and 10 items.
- The projects list is capped at 100 `ProjectSummary` items. Summaries carry no draft graph.

**Bundle:** see F7. The dashboard is code-split.

No benchmarks or load tests were run.

---

## 17. Documentation Changes

| File | What was documented |
|---|---|
| `backend/README.md` | API table rows for `GET /api/v1/executions`, `/executions/status-counts` and `/system/status` |
| `docs/architecture.md` §22 | The implemented endpoint list gains the three routes |
| `backend/src/akasha/api/routes.py` docstring | The access policy now names the session-only routes |
| `backend/src/akasha/api/executions.py` docstring | The route list and the owner-filter visibility rule |
| `frontend/README.md` | `features/` and `test/app.tsx` in Structure; the `['executions','overview']` invalidation rule; a new "Dashboard" section (sections, endpoints, states, outage collapse, what is not shown and why) |
| `docs/reports/frontend/phase-05-dashboard.md` | This report |

**Should be updated in a future phase:**

- `docs/architecture.md` has no section describing dashboard data sources or the cross-project listing's visibility rule beyond the endpoint list.
- Phase 16 should document the token and cost aggregation design.

---

## 18. Dependencies / Infrastructure Changes

**Not changed:**

- No new, removed or upgraded dependencies, frontend or backend.
- No Docker, Compose, database schema, Redis or environment-variable changes.

**Changed:**

- **Test configuration:** `frontend/vite.config.ts` sets Vitest's `test.unstubGlobals: true`. Any `vi.stubGlobal` (such as `fetch`) is undone after each test, so stubs cannot leak between tests.
- **Test infrastructure:** `frontend/src/test/app.tsx` is a reusable screen harness for later feature phases.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects this phase? |
|---|---|---|---|
| Token usage and cost on the dashboard | No backend aggregate exists; it needs a backend design (F3) | 16 Monitoring | No (the roadmap qualifies it with "sofern Backend diese liefert") |
| Links from rows to execution and project detail | Those routes are placeholders | 06, 13 | No |
| Live updates | WebSocket is out of scope | 14 | No (manual Refresh satisfies "can be refreshed") |
| Time windows for counts ("last 24 h") | Not required; all-time counts are labelled | Open, possibly 16 | No |
| `executions.created_at` index | Not measured | When timings show the need | No |

---

## 20. Known Limitations

- **Authentication:** against a real backend, the dashboard shows "Authentication required." until the frontend has a login (Phase 21).
- **Project coverage:** project names and status counts cover at most the first 100 projects, ordered by `created_at` ascending, which is the backend page limit. Beyond that the UI shows "N+" and a note. Executions of later projects show the project id instead of the name.
- **Counts:** all-time only, with no time window.
- **Durations:** per execution only. There is no average or percentile.
- **Brief flicker during an outage:** before the outage collapse, some section errors can appear for a moment, until the last query fails. With network errors this happens after the client's retry backoff (about 3 s).
- **Stale relative state:** "In progress" and "Not started" reflect the last fetch; there is no ticking clock or polling.
- **Visual details:** the B3/B4 layout fixes are verified by screenshot review only, not by an automated assertion.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| No frontend authentication: every API call returns 401 against a real backend | The dashboard is unusable against a real backend until login exists | HIGH (product), not a defect of this phase | Frontend auth | Wire `ApiClient.getToken` with the session | Phase 21 |
| No index for the cross-project ordering | Possible latency with a large execution history | LOW (unmeasured) | Backend persistence | Measure; add `ix_executions_created_at` with a migration | When measured |
| Token and cost aggregates are missing | Roadmap scope gap | MEDIUM | Backend + Monitoring | Design the aggregation (persisted totals vs. JSON aggregation) | Phase 16 |

---

## 22. Architectural / Product Decisions

1. **Server-side statistics only.** The dashboard never derives counts from a page of executions. If the backend cannot provide a number, the dashboard does not show it (tokens, cost).
2. **Cross-project execution visibility = project ownership.** `GET /executions` and `/executions/status-counts` apply `AuthorizationService.owner_filter`, matching `GET /projects`. Changing who can see what must change both in step.
3. **Readiness for the UI is a 200 with `ready: bool`.** `/ready` keeps its 503 semantics for orchestrators. The UI must not rely on `/health` or `/ready`.
4. **Route order matters:** `/executions/status-counts` is declared before `/executions/{execution_id}`.
5. **Sections fail independently; total failure collapses.** Each data region owns its loading, empty, error and stale states (`QuerySection`). The page shows one error only when every region has failed with no data (`useOutage`).
6. **`['executions','overview']` is the invalidation prefix** for every cross-project execution view. Any future execution write hook must invalidate it.
7. **Screen tests replace only the network.** `src/test/app.tsx` uses the real router, `ApiClient`, `QueryClient` and retry policy. Feature phases should reuse it rather than mocking hooks.
8. **DOM order equals reading order** in responsive layouts. Placement uses CSS grid positions, not `order`, so keyboard and screen-reader order match the visual order.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **HEAD:** `c45b1bd` ("frontend phase 04 - domain types and server state"). Phase 05 is not committed.
- **Modified, 25 tracked files, +749/−50:**
  - Backend: `README.md`, `api/executions.py`, `api/routes.py`, `application/executions.py`, `application/persistence.py`, `infrastructure/persistence.py`, `tests/application/test_trigger_service.py`, `tests/integration/test_executions_api.py`.
  - Docs: `docs/architecture.md`.
  - Frontend: `README.md`, `api/client.node.test.ts`, `api/client.ts`, `api/contract.node.test.ts`, `api/executions.ts`, `api/mutations.ts`, `api/openapi.json`, `api/queries.ts`, `api/serverState.test.tsx`, `app/App.test.tsx`, `app/routes.test.tsx`, `app/routes.tsx`, `components/ui/Card.tsx`, `components/ui/Table.tsx`, `components/ui/display.test.tsx`, `vite.config.ts`.
- **Untracked:**
  - `docs/reports/frontend/phase-05-dashboard.md`
  - `frontend/src/api/system.ts`
  - `frontend/src/features/` (5 files)
  - `frontend/src/test/app.tsx`
  - `frontend/tests/e2e/dashboard.spec.ts`
- **Unrelated changes:** none detected. Every changed file belongs to this phase.
- **Build artifacts:** `frontend/dist/` and `frontend/test-results/` were produced by the gates; both are git-ignored (`frontend/.gitignore`).

---

## 24. Phase Completion Assessment

### Implemented

- The dashboard page with six data sections, per-section states, Refresh, and outage collapse.
- Expandable recent-failure rows showing their recorded errors, fetched on demand.
- Three backend read endpoints with application and repository support.
- Frontend services, schemas, query options and invalidation for them.
- Keyboard-scrollable tables in the design system.
- A reusable screen test harness.

### Verified

- 162 Vitest tests.
- 16 Playwright tests (layout, overflow, keyboard, outage, error details).
- 6 new backend integration tests on PostgreSQL, and the full backend suite (1858 passed).
- Contract and snapshot tests.
- All format, lint, type and build gates on both sides.

### Not Implemented

- Token usage and cost display.

### Deferred

See §19.

### Open

Frontend authentication (Phase 21); an unmeasured index need; the token and cost aggregate design (Phase 16).

### Blocking Issues

None for this phase. All four acceptance criteria pass.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**Next: Phase 06 — Project Management.**

**What the next phase can assume:**

- `features/<area>/` is the place for feature code. `features/dashboard/` shows the pattern: page component, sections, `QuerySection`, and a non-component `format.ts`. The react-refresh lint rule requires non-component exports in non-component files.
- `src/test/app.tsx` provides `stubBackend(handlers)` (keys like `"GET /projects"`; a handler gets the `URL`; there are `ok`, `apiError` and `pending` helpers) and `renderRoute(path)`.
- Routes are registered in `app/routes.tsx`. Replace the placeholder for `/projects` the way `/dashboard` was replaced.

**APIs available:**

- `GET /api/v1/executions?status=` and `/executions/status-counts` (owner-filtered).
- `GET /api/v1/system/status`.
- Everything from Phase 04.

**Must not change casually:**

- The `['executions','overview']` invalidation prefix.
- The owner-filter visibility of the cross-project routes.
- The registration order of `status-counts` before `{execution_id}`.
- `PROJECT_SAMPLE` and the list parameter constants: `useOutage()` and the sections must use identical query keys.

**Known constraints:**

- No authentication in the frontend, so real-backend usage returns 401.
- Project lists are offset-paginated (maximum 100), ordered by `created_at` ascending. There is no total count in `PageInfo`, so a full project list must paginate using `has_more`.
- `QuerySection` lives in `features/dashboard/`. If Phase 06 needs the same per-query state rendering, move it to a shared location then. It was not generalized pre-emptively.

**Prerequisites:** none outstanding from this phase.
