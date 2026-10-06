# Phase 13 — Execution UI

Report date: 2026-10-02, updated 2026-10-04 (follow-up: workflow names in execution listings, §1). Branch `main`. Base commit: `b83b75e` (frontend phase 12 — workflow versions). Everything described here is uncommitted in the working tree (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap §19 (`docs/roadmap-frontend.md`, "Phase 13 — Execution UI"): "Executions sichtbar und steuerbar machen."
- **Execution list:** Status, Workflow, Workflow Version, Trigger, Start, End, Duration, Cost, Tokens.
- **Actions:** Run, Cancel, Open Details.
- **States:** the nine backend execution states.

The session prompt added:
- the execution detail entry point;
- failure status;
- "use real backend state";
- tests for start, list, detail navigation, cancellation, failure, empty, loading and errors;
- implementing, testing and documenting any missing backend API.

**Actually implemented.**
- **Backend:**
  - One new response field: `ExecutionDetail.cancellable: bool`. It is derived from the domain transition table `EXECUTION_ACTIONS["cancel"]` and returned by every endpoint that returns an execution.
  - Execution listings (`GET /executions`, `GET /workflows/{id}/executions`) return a new model, `ExecutionListItem`. It is `ExecutionDetail` plus `workflow_name`, the workflow's current name, joined in the existing listing query. This was added in a follow-up on 2026-10-04.
  - Both changes are tested in the PostgreSQL-backed API tests and documented in `docs/architecture.md` §22.
  - The OpenAPI snapshot `frontend/src/api/openapi.json` is regenerated.
- **Frontend** (new feature module `frontend/src/features/executions/`):
  - **`/executions` list page:**
    - backend status filter, kept in the URL;
    - offset pagination;
    - refresh;
    - per-row Cancel, only where the backend flag allows it;
    - expandable error details for failed and timed-out rows.
  - **`/executions/:executionId` detail page:**
    - status, workflow, version, trigger and correlation id;
    - created, started and finished times, and duration;
    - a failure panel;
    - token usage, estimated cost and attempt/retry counts (from the execution report);
    - cancellation request time and reason;
    - Cancel.
  - **Run dialog** on the published-version page: takes a JSON-object input, starts a manual execution through `POST /workflows/{id}/versions/{n}/execute`, and navigates to the new execution.
  - **Execution ids link to the detail page** in the dashboard tables, on the list page and on the version page.

**Resulting state.**
- A user can start a run of a published version, find it in the cross-workflow list, filter by any backend state, open its details, see why it failed, and cancel it while the backend allows that.
- Every status, timestamp and cancellability shown comes from the backend.
- All quality gates pass (§13), including the full-stack Playwright suite against the real backend and PostgreSQL.

**Follow-up (2026-10-04).** The first version of this report listed the open issue "Execution responses carry no workflow name": the list resolved names from the first 100 workflows and showed ids beyond that. At the user's request this is now fixed:
- listings carry `workflow_name`;
- the frontend uses it and no longer fetches the workflow list for names.

Single-execution responses intentionally still omit it (§5, Decision 3).

**Acceptance criteria.** All four roadmap criteria pass (§14). Some limitations remain:
- tokens and cost are shown per execution on the detail page, not as list columns;
- there are no live updates (Phase 14);
- runs can only be started from a version page.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §19:**
- **Ziel:** "Executions sichtbar und steuerbar machen."
- **Scope:**
  - Execution List: Status, Workflow, Workflow Version, Trigger, Start, Ende, Duration, Kosten, Tokens.
  - Actions: Run, Cancel, Open Details.
- **Execution states:** `CREATED QUEUED RUNNING PAUSED WAITING COMPLETED FAILED CANCELLED TIMEOUT`.
- **Acceptance criteria:**
  - Status wird korrekt dargestellt.
  - Statusfarben sind nicht die einzige Informationsquelle.
  - Execution Details sind erreichbar.
  - Cancel wird nur angeboten, wenn Backend dies erlaubt.
- **Prompt constraints:**
  - "Implementiere Execution List und grundlegende Execution Details."
  - "Verwende ausschließlich die Backend Execution States."
  - "Implementiere Run und Cancel nur entsprechend der Backend-API."
  - "STOP nach Phase 13."

**Session prompt additions:**
- "Do not invent client-side execution states."
- "If any function or API is missing on the backend, implement it. It must then be properly tested and documented."

**Explicitly not this phase's responsibility:**
- live updates and WebSocket (Phase 14);
- the node-level execution inspector, event timeline and logs (Phase 15);
- monitoring (Phase 16);
- trigger management and invocation UI (Phase 18);
- reports UI (Phase 19).

---

## 3. Scope Implemented

### Backend/API
- `ExecutionDetail.cancellable` (`backend/src/akasha/api/trigger_schemas.py`):
  - The module-level helper `_cancellable(status)` returns `status in EXECUTION_ACTIONS["cancel"][0]`.
  - `ExecutionDetail.from_domain` and `ExecutionListItem.from_summary` set it.
- Since `ExecutionDetail` is the canonical execution response, the field appears in:
  - `GET /executions`
  - `GET /workflows/{id}/executions`
  - `GET /executions/{id}`
  - `POST /executions/{id}/cancel`
  - `POST /workflows/{id}/versions/{n}/execute`
  - the trigger invoke and webhook routes in `api/triggers.py`

Workflow names in listings (follow-up):
- **Repository:** `SqlAlchemyExecutionRepository.list_summaries` (`infrastructure/persistence.py`) now always selects from `executions JOIN workflows` and returns `workflows.name AS workflow_name`. The owner filter is now a plain `WHERE workflows.owner_id = :owner` on that join. Before, it was applied by `_owned()`, which joined `workflows` only when an owner was set; `_owned()` is still used by `count_by_status`. Still one query per page.
- **Read model:** `ExecutionSummary` (`application/persistence.py`) gains `workflow_name: str`.
- **API model:** `ExecutionListItem(ExecutionDetail)` (`api/trigger_schemas.py`) adds `workflow_name: str` and owns `from_summary`, which moved off `ExecutionDetail`. `ExecutionPage.items` (`api/executions.py`) is now `list[ExecutionListItem]`.
- **Inner join:** the join is inner because `workflow_versions.workflow_id` references `workflows` with `ON DELETE RESTRICT`, and every execution references a version. An execution's workflow row therefore always exists.

### Frontend/UI

**Routes** (`frontend/src/app/routes.tsx`):
- `/executions` → `ExecutionsPage`, lazily loaded.
- `/executions/:executionId` → `ExecutionDetailPage`, lazily loaded.
- `/executions` was removed from the placeholder-route filter.

**`ExecutionsPage.tsx`:**
- `GET /executions` with `status`, `offset`, and `limit=25`.
- `keepPreviousData` keeps rows visible while paging, with `aria-busy` on the region.
- Status `<Select>` listing all nine `executionStatusSchema.options` plus "All statuses".
- Filter and offset are stored in `useSearchParams`.
- Previous/Next pagination.
- A Refresh button.
- Two empty states: "No executions yet" (with a link to Workflows) and "No matching executions" when filtered.

**`ExecutionDetailPage.tsx`:**
- `GET /executions/{id}` for state and `GET /reports/executions/{id}` for usage and the workflow name.
- A not-found state for a 404.
- If the report fails, the details card still renders and the Usage card shows its own error state.
- A Failure card (reusing `FailureDetails`) for `failed` and `timeout`.
- Separate Refresh actions for the execution and for the usage card.

**`CancelExecution.tsx`:**
- Renders nothing unless `execution.cancellable`.
- Opens a `ConfirmDialog` and calls `useCancelExecution`.
- Toast "Execution cancelled" when the response status is `cancelled`. Otherwise "Cancellation requested", explaining cooperative cancellation.
- A backend error (e.g. 409) is shown inside the dialog.

**`RunVersion.tsx`:**
- "Run…" button and dialog on `VersionDetailPage`.
- JSON input validated locally by `parseInput` (`format.ts`).
- Calls `useExecuteWorkflowVersion`.
- On success: a toast and navigation to `/executions/{id}`.
- A backend error is shown in the dialog.

**`shared.tsx`:**
- `StatusBadge`: label plus tone dot.
- `Time`: renders `—` for null.
- `ExecutionLink`.
- `ExecutionTable`: moved from the dashboard and extended with an Execution-id column and an `extraColumns` prop. The error disclosure appears only on failed/timeout rows.
- Since the follow-up, the table's rows are `ExecutionListItem` and the Workflow cell renders `workflow_name` (with the workflow id as `title`). The dashboard's former lookup against `workflowQueries.list(WORKFLOW_SAMPLE)`, with its id fallback, was deleted.

**`format.ts`:**
- `executionPath`
- `duration(e)`: "In progress", "Not started", or "Never started" (finished without start)
- `isFailure`
- `parseInput`

**Dashboard** (`features/dashboard/sections.tsx`):
- Uses the shared `ExecutionTable`. The local `StatusBadge`, `Time`, `ExecutionTable` and `useState` were deleted.

**Version page** (`features/versions/VersionDetailPage.tsx`):
- `RunVersion` added to `VersionActions`.
- The execution-id cell now uses `ExecutionLink`, and the status cell uses `StatusBadge`.

### Contract
- `frontend/src/api/executions.ts`: `executionSchema` gains `cancellable: z.boolean()`.
- New `executionListItemSchema = executionSchema.extend({ workflow_name: z.string() })` and type `ExecutionListItem`. `executionPageSchema` now pages list items.

### Testing
- See §12.
- New file: `frontend/src/features/executions/ExecutionsPage.test.tsx` (16 tests).
- 2 new tests in `features/versions/VersionsPage.test.tsx`.
- 1 new full-stack Playwright test in `frontend/tests/fullstack/workflows.spec.ts`.
- Backend assertions extended in `backend/tests/integration/test_executions_api.py`, plus one new test, `test_listings_name_each_workflow_by_its_current_name`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/api/trigger_schemas.py` | Modified | `cancellable` field + `_cancellable` helper; `ExecutionListItem` (follow-up) |
| `backend/src/akasha/api/executions.py` | Modified (follow-up) | `ExecutionPage.items` uses `ExecutionListItem` |
| `backend/src/akasha/application/persistence.py` | Modified (follow-up) | `ExecutionSummary.workflow_name` |
| `backend/src/akasha/infrastructure/persistence.py` | Modified (follow-up) | `list_summaries` joins `workflows` for the name |
| `backend/tests/integration/test_executions_api.py` | Modified | Assert `cancellable` for queued (execute response + listing), cancelled, completed. Follow-up: `workflow_name` in both listings, renamed workflow, member listing; absent from the single-execution response |
| `docs/architecture.md` | Modified | §22: documents `cancellable`, the 202-while-RUNNING cancel semantics, and `ExecutionListItem.workflow_name` |
| `docs/reports/frontend/phase-13-execution-ui.md` | Created | This report |
| `frontend/src/api/openapi.json` | Modified (generated) | Regenerated via `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py` |
| `frontend/src/api/executions.ts` | Modified | `cancellable` in `executionSchema`; `executionListItemSchema` for pages (follow-up) |
| `frontend/src/features/dashboard/format.ts` | Modified (follow-up) | `WORKFLOW_SAMPLE` comment no longer claims it supplies names |
| `frontend/src/app/routes.tsx` | Modified | Real `/executions` and `/executions/:executionId` routes |
| `frontend/src/features/executions/` | Created (directory) | New feature module |
| `frontend/src/features/executions/ExecutionsPage.tsx` | Created | List page |
| `frontend/src/features/executions/ExecutionDetailPage.tsx` | Created | Detail page |
| `frontend/src/features/executions/CancelExecution.tsx` | Created | Cancel button + confirmation |
| `frontend/src/features/executions/RunVersion.tsx` | Created | Run dialog |
| `frontend/src/features/executions/shared.tsx` | Created | Shared components (incl. table moved from dashboard) |
| `frontend/src/features/executions/format.ts` | Created | Non-component helpers (split out for `react-refresh/only-export-components`) |
| `frontend/src/features/executions/ExecutionsPage.test.tsx` | Created | 16 component/unit tests |
| `frontend/src/features/dashboard/sections.tsx` | Modified | Execution table removed (moved), shared one imported |
| `frontend/src/features/versions/VersionDetailPage.tsx` | Modified | Run action, linked execution ids, shared `StatusBadge` |
| `frontend/src/features/versions/VersionsPage.test.tsx` | Modified | `cancellable` in fixture; 2 Run tests |
| `frontend/src/features/dashboard/DashboardPage.test.tsx` | Modified | `cancellable` and `workflow_name` in fixture; the out-of-sample workflow row now asserts the listed name instead of the id fallback |
| `frontend/src/api/serverState.test.tsx` | Modified | `cancellable` and `workflow_name` in fixture |
| `frontend/tests/e2e/dashboard.spec.ts` | Modified | `cancellable` and `workflow_name` in fixture |
| `frontend/tests/fullstack/workflows.spec.ts` | Modified | New full-stack execution test |

No migrations, configuration files or dependency manifests changed. No files were deleted.

---

## 5. Architecture Changes

### Decision 1: Cancellability is a backend-computed contract field
- **Context.** The roadmap requires "Cancel wird nur angeboten, wenn Backend dies erlaubt", and AGENTS §4/§23 forbid re-implementing execution state transitions in the frontend. The existing contract carried only `status`. The frontend would have had to copy the rule "cancel is legal from CREATED/QUEUED/RUNNING/PAUSED/WAITING" from `EXECUTION_ACTIONS`.
- **Chosen approach.** `ExecutionDetail.cancellable`, computed from `EXECUTION_ACTIONS["cancel"][0]`, so the flag and `Execution.cancel()` read the same table.
- **Alternatives considered:**
  - Mirror the active-status set in the frontend. Rejected: it duplicates the state machine.
  - Always show Cancel and rely on the 409. Rejected: it fails the acceptance criterion.
- **Consequences.**
  - A future change to the cancel rule changes the flag automatically.
  - Clients must treat the flag as authoritative and refetch after state changes rather than recompute it (relevant for Phase 14 events).

### Decision 2: The execution table is shared and lives in `features/executions`
- **Context.** The dashboard (Phase 05) had a private `ExecutionTable`. The list page needs the same columns plus timestamps and actions.
- **Chosen approach.** The table moved to `features/executions/shared.tsx` with an `extraColumns` prop. The dashboard imports it.
- **Alternatives considered.** A second, separate table, which would have duplicated code.
- **Consequences.**
  - Dashboard tables gained the Execution-id link column.
  - The error disclosure is now shown only on failed/timeout rows. Before, it was on every row of a table with `errorDetails`. The only such dashboard card lists failures only, so its visible behavior is unchanged.

### Decision 3: Workflow names belong to listings, not to every execution response (follow-up)
- **Context.**
  - Execution responses carried only `workflow_id`. The list resolved names client-side from the first 100 workflows and fell back to the id beyond that, so with more than 100 workflows some rows showed ids.
  - Fetching each missing workflow would be one request per workflow.
- **Chosen approach.** `ExecutionListItem` adds `workflow_name` to listings only, filled by a join inside the existing single listing query.
- **Alternatives considered:**
  - **Add `workflow_name` to `ExecutionDetail` itself.** Rejected for two reasons:
    - Single-execution responses are built from the `Execution` aggregate, which holds no workflow name, so every route (execute, invoke, cancel, detail, webhook) would need an extra workflow lookup.
    - The public `POST /webhooks/{workflow_id}/{trigger_id}` response returns `ExecutionDetail` to an external, non-user caller and would start disclosing the workflow name.
  - **Per-workflow lookups in the frontend.** Rejected: N requests.
  - **Keep the 100-workflow sample.** Rejected: names are wrong beyond it.
- **Consequences.**
  - The name is the workflow's *current* name, not its name at execution time. This is asserted by a test that renames the workflow after a run.
  - The detail page still takes the name from the execution report. If the report fails, it shows the workflow id.

These are clarifications within the documented architecture. No layer or dependency direction changed.

---

## 6. Domain / Data Model Changes

- **Domain:** no changes. `EXECUTION_ACTIONS` is read, not modified.
- **Persistence:** no tables, columns, indexes or migrations changed. `cancellable` is computed per response and not stored. `workflow_name` is read through a join (`executions ⋈ workflows`) in `list_summaries`, not stored on executions.
- **Read model:** `ExecutionSummary` gained `workflow_name: str` (follow-up).
- **API model:**
  - `ExecutionDetail` gained the required boolean `cancellable`.
  - New `ExecutionListItem(ExecutionDetail)` with the required `workflow_name: str` (follow-up).
- **Frontend schema:** `executionSchema` gained `cancellable: z.boolean()`. It is required, so a response without it fails runtime validation. Backend and frontend ship from one repository and the OpenAPI snapshot test enforces the pairing.

---

## 7. API / Contract Changes

| Contract | Change | Compatibility |
|---|---|---|
| `ExecutionDetail` (all responses listed in §3) | Added required `cancellable: boolean` | Additive for API consumers. The frontend schema requires it, so this frontend needs a backend from this change or later. |
| `ExecutionPage.items` (`GET /executions`, `GET /workflows/{id}/executions`) | Items are now `ExecutionListItem` = `ExecutionDetail` + required `workflow_name: string` (follow-up) | Additive. Single-execution responses (detail, execute, invoke, cancel, webhook) are unchanged and do not contain `workflow_name`. |

- **Endpoints:** no endpoints added, removed or changed in request shape or errors. Cancel still answers 202, 404 `EXECUTION_NOT_FOUND`, 409 `INVALID_STATE_TRANSITION`, and 422 for a reason longer than 500 characters.
- **Documented behavior:** a cancel of a run owned by a live in-process runtime answers 202 with `status: "running"` and `cancellable: true`. The run becomes `CANCELLED` when the scheduler loop observes the request. This was already the code's behavior (`ExecutionRuntime.cancel`) and is now stated in `docs/architecture.md` §22.
- **Contract enforcement:**
  - `backend/tests/test_openapi_contract.py`: the snapshot matches the API.
  - `frontend/src/api/contract.node.test.ts`: the frontend response schemas for `listExecutions`, `getExecution`, `cancelExecution`, `executeWorkflowVersion`, `invokeTrigger` etc. match the snapshot.
  - Both pass.

---

## 8. Implementation Details

**Server state.** Only existing query options and keys from `api/queries.ts` are used: `executionQueries.list`, `.detail` and `.report`. Since the follow-up, the execution table no longer reads `workflowQueries.list(WORKFLOW_SAMPLE)`; the dashboard still uses that query for its workflows card and counts. Mutations reuse `useExecuteWorkflowVersion` and `useCancelExecution` from `api/mutations.ts`, which already invalidate:
- `executions.detail(id)`, a prefix that also covers that execution's report key;
- `workflows.executions(workflowId)`;
- `executions.overview()`.

So after a cancel, the list, the detail, the report and the dashboard counts all refetch. No optimistic updates (AGENTS §85).

**Status display.**
- `executionStatusDisplay` (`features/dashboard/format.ts`, pre-existing) maps each backend status to a label and tone; `StatusBadge` renders both.
- No client-side states exist. The only derived text is the duration column, which reads timestamps only.

**URL state.**
- `useListParams()` in `ExecutionsPage.tsx` reads `status`, validated with `executionStatusSchema.safeParse` (an unknown value means "all"), and `offset` (non-negative integer, otherwise 0).
- Changing the filter resets the offset to 0.

**Run input validation.**
- `parseInput(text)` returns a `JsonObject` or an error string:
  - empty input → `{}`;
  - invalid JSON → "Not valid JSON.";
  - a valid non-object (array, string, …) → a "must be a JSON object" message.
- Validation uses the existing `jsonObject` Zod schema. The backend validates input authoritatively, and its errors are shown in the dialog.

**Report usage.**
- The detail page and `FailureDetails` use the same `executionQueries.report` key, so the report is fetched once per detail page.
- The workflow name on the detail page comes from `report.workflow.name` and falls back to the workflow id.

**Accessibility:**
- Cancel buttons are labelled `Cancel execution <id>`: the visible text first, then the id to tell rows apart (WCAG 2.5.3).
- Table regions are named by caption ("Executions").
- Pagination is a named `nav`.
- Dialogs use the existing accessible `Dialog`/`ConfirmDialog`.
- Status is conveyed as text.

---

## 9. Findings

### Finding 1: The execution contract had no cancellability signal
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** Without it, the acceptance criterion "Cancel nur wenn Backend erlaubt" could only be met by duplicating the state machine in the frontend. Fixed with `cancellable` (§5, Decision 1).

### Finding 2: Cancel of a running execution is accepted without changing the status
- **Impact:** LOW
- **Resolution:** FIXED (documentation + UI messaging)
- **Notes:**
  - `ExecutionRuntime.cancel` only signals the in-process scheduler when it owns the run, so the response is 202 with `running`. This was documented only in the runtime docstring.
  - It is now in `architecture.md` §22.
  - The UI shows "Cancellation requested" instead of claiming success.

### Finding 3: Tokens and cost are not available in execution listings
- **Impact:** MEDIUM
- **Resolution:** DEFERRED
- **Notes:**
  - Usage and cost exist only in the execution report (`application/reports.py`), aggregated from node outputs. `ExecutionSummary` (the listing read model) deliberately excludes node executions so a page costs one query.
  - Per-row tokens/cost would need a persisted aggregate or one report request per row.
  - Shown on the detail page instead. Roadmap list columns "Kosten" and "Tokens" are therefore not list columns (§20).

### Finding 4: Execution responses carry no workflow name
- **Impact:** LOW
- **Resolution:** FIXED for listings (follow-up, 2026-10-04)
- **Notes:**
  - *Originally:* the list resolved names from the first 100 workflows (`WORKFLOW_SAMPLE`), the rule the dashboard already used, and showed the workflow id beyond that.
  - *Now:* listings carry `workflow_name` (§5, Decision 3).
  - Single-execution responses still omit it on purpose. The detail page uses the report's workflow name.

### Finding 5: A run can finish without ever starting
- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:**
  - A cancelled-while-queued, or failed-before-start, execution has `finished_at` but no `started_at`. The previous dashboard duration text said "Not started" for it, which reads like a pending run.
  - `duration()` now says "Never started" for this case.
  - Verified by the cancel test, where the backend returns `cancelled` with `started_at: null`.

### Finding 6: The full-stack harness runs no worker
- **Impact:** LOW
- **Resolution:** No action needed. It was used deliberately.
- **Notes:** `tests/fullstack/backend.sh` sets `AKASHA_WORKER_ENABLED=false`, so a started run stays `queued`. This makes the full-stack cancel test deterministic, but it cannot exercise RUNNING/COMPLETED transitions in the browser against the real backend.

---

## 10. Bugs / Problems Found

All of the following were **introduced during this phase and fixed before completion**. No pre-existing product bugs were found, except the duration wording in Finding 5.

| Problem | Root cause | Fix | Regression coverage | Area |
|---|---|---|---|---|
| `tsc` TS2375 in `CancelExecution.tsx` | `size?: 'sm'` passed `undefined` to `Button` under `exactOptionalPropertyTypes` | `size = 'md'` default, type `'sm' \| 'md'` | `tsc -b` in build | Frontend |
| ESLint `react-refresh/only-export-components` (4 errors) | Helpers exported from component files `shared.tsx` and `RunVersion.tsx` | Moved `executionPath`, `duration`, `isFailure`, `parseInput` to `format.ts` | `npm run lint` | Frontend |
| Tests could not find links by name "Execution exec_…" | The link held an `sr-only` "Execution " span next to the id. The DOM text was right, but the role query did not match that name. | Removed the `sr-only` prefix (the column header "Execution" gives context); tests query the id | `ExecutionsPage.test.tsx` | Frontend |
| A test expected workflow names but saw ids | The test's workflows fixture lacked required workflow fields, so runtime validation rejected the list | Full workflow fixture | same test | Test |
| A detail test looked up the "Details" heading synchronously | The header renders before the execution query resolves | `findByRole` | same test | Test |
| The two Run tests were missing after the first insertion | Tooling: the scripted insertion used a marker absent from the file, and the script aborted | Re-inserted; `VersionsPage.test.tsx` passes with 25 tests | — | Tooling |
| Dashboard test "shows every section from real backend data" failed after the follow-up | It asserted the removed fallback (an unknown workflow shows its id) | The fixture now carries `workflow_name` and the test asserts that name. The fallback no longer exists by design. | `DashboardPage.test.tsx` | Test (follow-up) |

---

## 11. Fixes Applied

1. **Missing cancellability contract.**
   - *Symptom:* the frontend could not know when cancel is legal.
   - *Root cause:* not in the API.
   - *Implementation:* `ExecutionDetail.cancellable`.
   - *Verification:* `test_cancel_queued_execution_then_conflict` and `test_cancel_completed_or_unknown_execution` assert `cancellable` for queued, cancelled and completed. The full-stack test asserts `cancelled` / `false` after a UI cancel.
2. **Ambiguous duration text.**
   - *Symptom:* "Not started" for a finished-but-never-started run.
   - *Implementation:* `duration()` in `format.ts`.
   - *Verification:* the "cancels a queued execution after confirmation" test expects "Never started".
3. **Workflow names beyond the 100-workflow sample showed as ids** (follow-up).
   - *Root cause:* the names came from a client-side sample, not from the listing.
   - *Implementation:* §3, "Workflow names in listings".
   - *Verification:* `test_listings_name_each_workflow_by_its_current_name` checks both listings, a rename after the run, and the absence from the single-execution response. `test_members_see_only_their_workflows_executions` checks the name in a member's owner-filtered listing. In the frontend, the list test asserts the name and that no `/workflows` request is made; the dashboard test covers a workflow outside the sample.
4. The build, lint and test problems listed in §10.

---

## 12. Testing & Verification

### Unit Tests
- `parseInput`: empty input → `{}`, an object, an array, a JSON string, malformed JSON. PASS.

### Component Tests
Vitest + React Testing Library through the real router, `ApiClient` and `QueryClient`, with `fetch` stubbed by `test/app.tsx`.

`ExecutionsPage.test.tsx`, Executions list (8):
- loading state;
- all nine statuses shown by label, with workflow name (from the listing, with no `/workflows` request), version link, execution link and duration variants ("Not started", "In progress", "42 s");
- cancel offered only where `cancellable`, including a `waiting` row with `cancellable: false` that gets no button;
- empty state with a workflows link;
- status filter sent to the backend (`status=failed`) and kept in the URL, with the filtered empty state;
- pagination (offsets 0, then 25);
- load error, then retry;
- error expansion for a failed row.

`ExecutionsPage.test.tsx`, Execution detail (4):
- navigation from the list showing status, duration, correlation id, workflow link, tokens (1,500) and cost (`0.0125 USD`), with no cancel on a completed run;
- the failure panel showing the node error code and message;
- 404 not-found;
- report failure while the details stay visible.

`ExecutionsPage.test.tsx`, Cancel (3):
- queued run: confirm → POST → refetched as Cancelled, with "Never started" and exactly one cancel request;
- running run: 202 still running → "Cancellation requested";
- 409 conflict shown in the dialog.

`VersionsPage.test.tsx`, new (2):
- a non-object input is rejected locally with no request, then a valid input is POSTed (`{"lead": "acme"}`), the app navigates to `/executions/exec_new`, and Cancel is offered there;
- a backend 422 message is shown in the Run dialog.

Result: `npx vitest run`: 27 files, **341 tests, all PASS**.

### API / Integration Tests (backend, real PostgreSQL via Testcontainers)
- First pass: `tests/integration/test_executions_api.py` and `test_triggers_api.py`, 47 passed.
- Follow-up: `test_executions_api.py`, `test_triggers_api.py`, `test_reports.py` and `test_workers.py`, 82 passed.
- The full backend suite `uv run pytest` after the follow-up: **1916 passed, 6 skipped**.

### Contract Tests
- `tests/test_openapi_contract.py`: PASS after regenerating the snapshot (regenerated again in the follow-up).
- `src/api/contract.node.test.ts`: PASS (part of the Vitest run).

### E2E Tests
- **Mocked backend** (`npx playwright test`): 23 PASS. This includes the existing dashboard specs, which now render the shared table, including the mobile error-details layout test.
- **Full stack** (`npm run test:fullstack`: production bundle, real backend, PostgreSQL 17.2 container): 11 PASS. The new test "a run started in the UI is listed, opened and cancelled" goes from the version page Run, to the detail page (Queued), to the list row (Queued + workflow name), to the detail page, cancels, sees the toast, and then reads the backend: `{status: 'cancelled', cancellable: false}`.

### Accessibility Tests
- There was no dedicated accessibility audit (no axe run).
- Accessibility is covered indirectly: tests query by role and accessible name (buttons, links, tables, dialogs, headings), and status is asserted as text.

### Security / Performance Tests
- None were performed specifically for this phase.

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (frontend) | PASS | `npm run format:check` |
| Linter (frontend) | PASS | `npm run lint`. It failed once during the phase (§10, react-refresh); the final run is clean |
| Type Checker (frontend) | PASS | `npx tsc -b`. It failed once (TS2375, §10); the final run is clean |
| Unit/Component Tests (frontend) | PASS | 341/341. During the follow-up, one dashboard test failed first (§10) and was then fixed. |
| Build (frontend) | PASS | `npm run build` |
| E2E mocked | PASS | 23/23 (re-run after the follow-up) |
| E2E full stack | PASS | 11/11 (re-run after the follow-up) |
| Formatter (backend) | PASS | `uv run ruff format --check .` |
| Linter (backend) | PASS | `uv run ruff check .` |
| Type Checker (backend) | PASS | `uv run mypy .` (191 files) |
| Backend tests (unit/integration/API) | PASS | 1916 passed, 6 skipped (after the follow-up). One warning: Starlette's `httpx` deprecation, pre-existing |
| Contract | PASS | OpenAPI snapshot + frontend contract test |
| Security Checks | N/A | No dedicated scan was run |
| Accessibility | N/A | No automated audit was run |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Status wird korrekt dargestellt. | PASS | The UI renders the backend `status` field through a fixed per-status label map; no client states exist. Test "shows every backend status by its label" covers all nine. The full-stack test shows the real backend's Queued and then Cancelled. |
| Statusfarben sind nicht die einzige Informationsquelle. | PASS | `StatusBadge` always renders the text label beside the tone dot. Tests assert the labels as text. |
| Execution Details sind erreichbar. | PASS | `/executions/:executionId`, linked from the list, the dashboard and the version page. Tested ("is reached from the list…") and full-stack. |
| Cancel wird nur angeboten, wenn Backend dies erlaubt. | PASS | `CancelExecution` returns null unless `cancellable`, and the backend computes the flag from `EXECUTION_ACTIONS`. Tested in the frontend ("offers cancel only where the backend allows it") and the backend (`cancellable` assertions). |

Scope items checked against the roadmap list (not acceptance criteria):
- **Kosten and Tokens:** PARTIAL. Shown on the detail page, not as list columns (Finding 3). This does not block phase completion; the roadmap's acceptance criteria do not mention them.
- **Run:** implemented on the version page.
- **Cancel and Open Details:** implemented.

---

## 15. Security Considerations

- **Output encoding:**
  - Error messages, cancellation reasons and ids are rendered as React text nodes.
  - There is no `dangerouslySetInnerHTML`.
  - Error messages arrive already redacted by the backend.
- **Input validation:** run input is parsed with `JSON.parse` and validated as a JSON object with Zod before sending. The backend remains authoritative.
- **Authorization:** unchanged. The listings apply the backend owner filter; detail and cancel use `authorize_path`. The frontend adds no permission logic.
- **Secrets:** none are handled. Run input is held in component state only while the dialog is open, and the mutation is not persisted.
- **URL state:** only `status` and `offset`, no user data.
- **Workflow names:** names are exposed only in authenticated listings, which already apply the owner filter, so a member sees only their own workflows' names. They are deliberately not added to the public webhook response (§5, Decision 3).

---

## 16. Performance Considerations

- **List:** one `GET /executions` per page (25 rows). Since the follow-up there is no extra workflows request: names come from a join in the same SQL query, on `workflows.id`, the primary key.
- **Join cost:** the listing query now always joins `workflows`. Before, it did so only for non-admin callers. Not measured.
- **Detail:** two requests, the execution and the report. `FailureDetails` shares the report query key, so the report is fetched once.
- **Report payload:** the report is unpaginated and carries all nodes and events (`events_truncated` bounds the events). For very large executions, the usage card loads a larger payload than it needs.
- **No polling** was added.
- No measurements were taken.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §22 | New contract fields | `cancellable` semantics; cancel of a live-worker run answers 202 while `RUNNING`; `ExecutionListItem.workflow_name` (current name, listings only, why single responses and the webhook omit it) |
| `docs/reports/frontend/phase-13-execution-ui.md` | Phase report | This document |

**Future documentation:**
- When Phase 14 adds live updates, §22's note "clients poll `/executions/{id}/events`" should be revisited.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded (frontend `package.json` and backend `pyproject.toml` unchanged).
- **Configuration and environment:** no changes, including Docker.
- **Test infrastructure:** one new full-stack test using the existing harness.
- **Generated files:** `frontend/src/api/openapi.json`, regenerated by its documented command.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion? |
|---|---|---|---|
| Tokens/cost as list columns | Needs a persisted per-execution aggregate (or an N+1 report fetch); not available in `ExecutionSummary` | Unassigned; candidate for Phase 16 (Monitoring) or a backend read-model change | No (not an acceptance criterion) |
| Live status updates | WebSocket/live updates are Phase 14 | 14 | No |
| Node-level inspector, events, logs | Phase 15 scope | 15 | No |
| Starting runs from the executions list (workflow/version picker) | Run is available on the version page; a picker was not required | Unassigned | No |
| Trigger invocation UI (`useInvokeTrigger` exists) | Trigger UI is Phase 18 | 18 | No |

---

## 20. Known Limitations

- **Tokens and cost are only on the detail page**, not in list rows.
- **No live refresh.** State changes appear on manual Refresh, on remount, or after this client's own mutations. After cancelling a run owned by a live worker, the page keeps showing Running until refreshed.
- **Workflow names are the current names**, not the names at execution time.
- **The detail page has no name of its own:** it shows the workflow id if the execution report fails to load.
- **The status filter selects one status at a time.** The backend accepts several.
- **Run input is a raw JSON textarea.** There is no form generated from the version's input contract.
- **The detail page's Refresh buttons** are separate for the execution and for the usage card.
- **Browser tests against the real backend cover only QUEUED → CANCELLED.** Running/completed states are covered in the browser only with a mocked backend, because the full-stack harness runs no worker.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Listing has no usage/cost aggregate | List cannot show Tokens/Kosten | Medium | Backend read model | Persist per-execution totals at finish, or add an aggregated query; extend `ExecutionSummary` | Unassigned |
| `VersionDetailPage.tsx` size | 714 lines (715 before this phase) | Low | Frontend structure | Split, as already noted in the Phase 12 report | Unassigned |

---

## 22. Architectural / Product Decisions

- **Cancellability is delivered by the backend** (`cancellable`), never derived in the client. The frontend shows Cancel only on that flag.
- **Cancel feedback follows the response:** "cancelled" only when the backend says `cancelled`; otherwise "requested". The UI never assumes a cancel has finished.
- **Running is a per-version action.** It lives on the published-version page because the backend's manual execute endpoint is per version, and executions always pin a version (architecture §3.7).
- **List filter and page live in the URL**, so a user returning from a detail page keeps their view.
- **Workflow names come with listings** (`ExecutionListItem.workflow_name`), not with single-execution responses. Single responses are built from the domain aggregate, and the public webhook must not disclose names. Clients must not reintroduce a client-side workflow-name lookup for execution tables.
- **One execution table component** (`features/executions/shared.tsx`) serves the dashboard and the list page. The version page keeps its compact column set but uses the same `ExecutionLink` and `StatusBadge`.

---

## 23. Repository / Git State

- **Branch:** `main`. HEAD: `b83b75e frontend phase 12 - workflow versions`.
- **Working tree:** uncommitted.
  - Modified: 17 tracked files (§4; 4 of them changed only in the follow-up).
  - Untracked: `frontend/src/features/executions/` (7 files) and this report.
- **Index:** nothing staged. During the phase, `git add -N .` was used once to produce a diff stat and then undone with `git reset -q`. No user changes existed at the start (`git status` was clean), so nothing unrelated was affected.
- **Unrelated changes:** none detected.
- No commit has been made.

---

## 24. Phase Completion Assessment

### Implemented
- Backend `cancellable` field.
- `workflow_name` in execution listings (follow-up).
- Executions list page and execution detail page.
- Run dialog on the version page.
- Cancel with confirmation.
- Execution links from the dashboard, list and version page.
- Shared execution table.

### Verified
- All four acceptance criteria (§14).
- Frontend: 341 unit/component tests, 23 mocked E2E tests, 11 full-stack E2E tests.
- Backend: 1916 tests.
- Lint, format, typecheck and build on both sides.

### Not Implemented
- Tokens/cost list columns (shown on detail instead).

### Deferred
- Live updates (14).
- Inspector and logs (15).
- Trigger invocation UI (18).
- Usage aggregate in listings (unassigned).

### Open
- §21.

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (14 — Live Execution + WebSocket)

**The next phase can assume:**
- Routes `/executions` and `/executions/:executionId` exist (`features/executions/`).
- `executionQueries.list` / `.detail` / `.report` and `queryKeys.executions.*` are the cache that live events should update or invalidate.
- `ExecutionDetailPage` is the natural place for a connection-status indicator and live status.
- `StatusBadge`, `ExecutionTable`, `ExecutionLink` and `CancelExecution` are reusable.

**Contracts:**
- `ExecutionDetail.cancellable` is part of the OpenAPI snapshot and the frontend schema.
- Listing items are `ExecutionListItem` with `workflow_name`. Single-execution responses have no `workflow_name`, so code updating list caches from a single-execution response (e.g. after a live event) must keep the name from the cached list item.
- Event types are defined by the backend (`architecture.md` §18): `execution.created`, `execution.started`, `execution.resumed`, `execution.cancelled`, etc.

**Must not change casually:**
- Cancel must stay gated on the backend flag. After an execution event, refetch the execution rather than recomputing `cancellable` from the event.
- No client-side execution states.

**Known constraints:**
- A cancel of a run owned by a live worker answers 202 while still `running`. Live updates are what will make the final `cancelled` visible without a refresh.
- The full-stack harness runs no worker (`AKASHA_WORKER_ENABLED=false`). Live-update tests against the real backend need a worker, or `runtime.run` driven another way.

**Prerequisites:**
- The backend has no WebSocket endpoint yet (`architecture.md` §22: "Not over HTTP in V1: … live updates (WebSocket, §23; clients poll `/executions/{id}/events?after=`)"). Phase 14 must either add one in the backend or poll the events endpoint.
