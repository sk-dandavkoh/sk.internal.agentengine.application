# Phase 19 — Reports UI

Report date: 2026-10-04. Branch `main`. Base commit `362389e` (frontend phase 18 — triggers ui). All work described here is **uncommitted** but **staged** in the index (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap §25, "Phase 19 — Reports UI": "Ausführungsberichte anzeigen und exportieren" (show and export execution reports).

The roadmap scope is:
- report list and report details;
- execution summary;
- duration, nodes, errors, tokens and costs;
- export and download.

The session prompt added:
- execution association;
- report metadata;
- logs/errors/metrics represented by the report;
- safe rendering of untrusted content and preserved redaction;
- implement, test and document any missing backend function.

**Discovered.** The backend side was already complete for this phase:
- **Report endpoint:** `GET /api/v1/reports/executions/{execution_id}` (backend Phase 19, architecture §55) returns the canonical, redacted, deterministic report JSON (`report_json`). Access is checked per execution by `authorize_path`.
- **Frontend contract:** the Zod schema (`frontend/src/api/reports.ts`) and the query (`executionQueries.report`) already existed. The Phase 15 execution inspector already rendered most report sections.
- **Report list:** no report-list endpoint exists. None is needed, because a report exists for every execution and `GET /api/v1/executions?status=` is already a paginated, access-filtered list.

**Actually implemented (frontend only):**
- a `/reports` list page;
- a `/reports/:executionId` report page rendered solely from the backend report;
- a "Download JSON" export that saves the backend response body byte for byte after schema validation;
- an execution-to-report link in the inspector header;
- 12 component tests and one additional contract-test entry;
- README documentation.

**Resulting state.** A user can open Reports from the navigation, filter finished executions by status, page through them, and open a report. The report shows:
- summary;
- usage and cost;
- nodes;
- outputs;
- errors;
- the run's inputs and versions (run snapshot);
- the event timeline.

From the report page the user can download the canonical JSON export or jump to the execution inspector, including its Logs tab.

**Acceptance criteria.** Both roadmap criteria pass (§14).

**Phase Status:** COMPLETE WITH LIMITATIONS

The limitations (§20) are:
- running executions are not listed;
- there is no project filter on the list;
- no full-stack/E2E or real-browser verification of the download was performed.

## 2. Phase Objective

**Roadmap §25:**
- **Goal:** show and export execution reports.
- **Scope:** Report List, Report Details, Execution Summary, Duration, Nodes, Errors, Tokens, Costs, Export, Download.
- **Acceptance:**
  1. "Reports basieren ausschließlich auf Backend-Reports." (Reports are based exclusively on backend reports.)
  2. "Sensitive Daten werden nicht clientseitig aus redigierten Daten rekonstruiert." (Sensitive data is not reconstructed client-side from redacted data.)
- **OpenCode prompt:** "Keine eigene Report-Berechnung als Ersatz für das Backend." (No report computation of its own as a replacement for the backend.)

**The session prompt adds:**
- reports are read-only representations of backend state;
- rendering must not execute HTML or script;
- tests for: successful report, failed execution report, empty state, malformed report, untrusted text, export behavior, large report.

**Not responsible for:**
- report generation or aggregation (backend);
- business/aggregate reports (vision §34);
- PDF/CSV formats;
- Plugin Management (Phase 20) and later phases;
- any change to report semantics or schema.

## 3. Scope Implemented

### Frontend/UI

- **Report list** (`ReportsPage`, route `/reports`; previously a placeholder):
  - lists executions with status `completed | failed | timeout | cancelled`, newest first, 25 per page;
  - status filter limited to those four values;
  - `status` and `offset` are kept in the URL;
  - empty state that links to `/executions`;
  - each row reuses `ExecutionTable` (execution id → inspector, project, version link, status badge, trigger, created, duration) and adds a "View report" link.
- **Report detail** (`ReportDetailPage`, route `/reports/:executionId`). It renders only `ExecutionReport` data:
  - `Summary`: status, project (with a "project no longer exists" note when `project.name` is null), version, trigger, created/started/finished, the backend's `duration_ms`, correlation id;
  - `Usage` (reused from the inspector): tokens in/out/total, per-currency cost estimate, `cost_complete` warning, node status counts, attempts/retries/failed attempts, cancellation;
  - `Nodes`: a table (node, type, status, attempts, duration, tokens, cost) and a native `<details>` disclosure per node that has outputs;
  - `ErrorsPanel` (reused): the execution error and every failed attempt per node. "Inspect" navigates to `/executions/:id?tab=nodes&node=…`;
  - `RunSnapshot` (reused): engine version, version published at, version description, node versions, redacted input;
  - `EventsPanel` (reused): the timeline in steps of 200, with a truncation notice;
  - a link to the inspector's Logs tab, because logs are not part of the report;
  - a "Report not found" state for 404 (unknown or inaccessible execution);
  - loading, error and stale states through the existing `QuerySection`.
- **Export:** a "Download JSON" button (`DownloadReport`):
  - re-fetches the report and saves the raw response body as `execution-report-<id>.json`;
  - shows a busy state on the button while fetching;
  - shows a danger toast on any failure.
- **Execution association:** the inspector header (`ExecutionDetailPage`) now links "Report". The report header links "Open in inspector" and "All reports".

### API layer

- **`ApiClient.requestWithBody`:** returns `{ data, body }`, the schema-validated data plus the unmodified response text. `ApiClient.request` now delegates to it, with unchanged behavior.
- **`exportExecutionReport(client, executionId, signal?)`** in `api/reports.ts`: returns the validated raw body.

### Testing

- **`features/reports/ReportsPage.test.tsx`:** 12 tests (§12).
- **`api/contract.node.test.ts`:**
  - the recorder now overrides `requestWithBody`, which also covers every `request` call;
  - `exportExecutionReport` is registered, so it is checked against the OpenAPI contract.

### Backend

- No changes.

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/src/features/reports/ReportsPage.tsx` | Created (167 lines) | Report list page |
| `frontend/src/features/reports/ReportDetailPage.tsx` | Created (305 lines) | Report page, `DownloadReport`, `saveFile` |
| `frontend/src/features/reports/ReportsPage.test.tsx` | Created (415 lines) | Component tests for list, detail, export |
| `frontend/src/api/client.ts` | Modified (+16/−1) | `requestWithBody`; `request` delegates to it |
| `frontend/src/api/reports.ts` | Modified (+19) | `exportExecutionReport` |
| `frontend/src/api/contract.node.test.ts` | Modified (+4/−3) | Recorder overrides `requestWithBody`; export function covered |
| `frontend/src/app/routes.tsx` | Modified (+17) | Lazy routes `/reports` and `/reports/:executionId`; `/reports` removed from the placeholder set |
| `frontend/src/features/executions/format.ts` | Modified (+3) | `reportPath()` next to `executionPath()` |
| `frontend/src/features/executions/ExecutionDetailPage.tsx` | Modified (+3/−2) | "Report" link in the header |
| `frontend/src/features/executions/inspector/OverviewPanel.tsx` | Modified (2 lines) | `Usage` and `RunSnapshot` exported for reuse |
| `frontend/README.md` | Modified (+23) | New "Reports" section |
| `docs/reports/frontend/phase-19-reports-ui.md` | Created | This report |

Other changes:
- **New directory:** `frontend/src/features/reports/`.
- **Build output:** the build emits the code-split chunks `ReportsPage-*.js` and `ReportDetailPage-*.js`. The build output in `dist/` is not committed.
- **Untouched:** no migrations, no configuration, no dependency changes, and `frontend/src/api/openapi.json` is unchanged.

## 5. Architecture Changes

No architectural changes were introduced during this phase. Two frontend-level decisions are recorded here.

### Decision 1: the report list is the execution list filtered to finished statuses

- **Context:**
  - the roadmap requires a "Report List";
  - the backend exposes reports only per execution;
  - the prompt allows implementing missing backend APIs.
- **Chosen approach:** `ReportsPage` calls the existing `GET /executions?status=completed&status=failed&status=timeout&status=cancelled&offset=&limit=25`.
- **Alternatives considered:** a new `GET /reports` endpoint. Rejected, because:
  - it would duplicate the executions list, its owner filtering and its pagination;
  - reports are derived on request and are not stored entities, so a list of them is exactly the list of executions.
- **Consequences:**
  - no new backend surface;
  - list filters are limited to what `/executions` supports (status only);
  - a future project filter belongs on `/executions`.

### Decision 2: the export uses the backend bytes, not a client re-serialization

- **Context:**
  - the backend defines `report_json` (sorted keys, indent 2) as the export format and the HTTP body, so the same persisted state gives the same bytes;
  - the Zod object schemas strip unknown keys, so a re-serialized client copy would silently drop additive backend fields and would not be the canonical export.
- **Chosen approach:** `ApiClient.requestWithBody` returns the raw text alongside the validated data, and the download saves the raw text.
- **Alternatives considered:**
  - `JSON.stringify` of the cached parsed report (lossy, not canonical);
  - a backend `Content-Disposition` download endpoint (unnecessary: a Blob download needs no server change).
- **Consequences:**
  - the API client now has a second public method;
  - the only callers needing raw bytes should use it;
  - every other caller keeps `request`.

## 6. Domain / Data Model Changes

Not applicable:
- no domain entities, enums, DTOs, tables or state transitions changed;
- the frontend report schema (`executionReportSchema`) is unchanged.

## 7. API / Contract Changes

- **Backend HTTP API:** no changes.
- **OpenAPI snapshot** (`frontend/src/api/openapi.json`): unchanged.
- **Frontend-internal contract additions:**
  - `ApiClient.requestWithBody<S>(method, path, schema, options) → Promise<{ data: z.output<S>; body: string }>`. It has the same errors as `request`: `ApiError` with kinds `network | timeout | cancelled | malformed_response | <http kinds>`. It is additive, and `request` keeps its signature and behavior.
  - `exportExecutionReport(client, executionId, signal?) → Promise<string>`. It calls `GET /reports/executions/{id}`, validated by `executionReportSchema`. The contract test checks it against the OpenAPI response schema.
- **Frontend routes:** `/reports` (previously a placeholder) and `/reports/:executionId` (new).

## 8. Implementation Details

- **Data flow, detail:**
  - `ReportDetailPage` → `useQuery(executionQueries.report(client, id))`, with query key `['executions', id, 'report']`;
  - the same key the inspector uses, so the cache is shared and invalidated with `executions.detail(id)`;
  - all rendering takes the parsed `ExecutionReport` only, with no other request;
  - a 404 `ApiError` with no cached data renders "Report not found".
- **Data flow, list:**
  - `useQuery({ ...executionQueries.list(client, { status, offset, limit }), placeholderData: keepPreviousData })`;
  - URL parameters are parsed defensively: `status` is checked against the finished set, and `offset` is a non-negative integer, otherwise 0.
- **Derived values:**
  - the only values computed in the browser are display formatting (locale numbers, `formatMilliseconds`, joining the cost list);
  - durations come from `duration_ms`. A running execution therefore shows "—", matching the backend's "no duration" rule.
- **Download (`DownloadReport` / `saveFile`):**
  1. `exportExecutionReport` fetches the report. A schema failure throws `malformed_response`, so no file is created.
  2. A `Blob` of type `application/json` is created, then `URL.createObjectURL`, then a detached `<a download>` with `click()`.
  3. The URL is revoked after 40 s (`REVOKE_AFTER_MS`). An immediate revoke can cancel the download in some browsers.
  4. The file name is `execution-report-${id.replace(/[^\w-]/g, '_')}.json`.
  5. Errors raise a toast with `errorMessage(error)`, the user-facing `ApiError` message.
- **The download re-fetches** rather than reusing the cache, so the file reflects current persisted state.
- **Untrusted content:** all node-, LLM- and user-originated strings go through:
  - React text nodes;
  - `UntrustedText` / `JsonBlock` (Phase 15): `<pre>` with text content, truncated at 20,000 characters with a "Show all" toggle;
  - `ErrorText`.

  Node outputs are inside closed `<details>` elements. They are present in the DOM but hidden until opened, which keeps them keyboard-operable without extra state.
- **Accessibility:**
  - the "View report" link has `aria-label="View report of execution <id>"`, which starts with the visible text (WCAG 2.5.3);
  - tables use the existing `Table`, with a hidden caption;
  - status is shown as label plus tone, never color alone;
  - pagination is a labeled `nav`.

## 9. Findings

### Finding: the backend already provides everything Phase 19 needs
- **Impact:** LOW
- **Resolution:** FIXED (no work needed)
- **Notes:**
  - `GET /reports/executions/{id}` already exists, is authorized per execution and is tested (`backend/tests/integration/test_reports.py`, `test_auth_api.py`);
  - no backend change was required, so no backend tests or docs were added.

### Finding: no report-list endpoint exists
- **Impact:** LOW
- **Resolution:** WORKAROUND (by design)
- **Notes:** covered by `GET /executions?status=` (Decision 1, §5).

### Finding: the Zod schemas strip unknown keys, so a client-serialized export would be lossy
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:**
  - this drove `requestWithBody` (Decision 2);
  - a test proves an unknown field survives the download.

### Finding: the list endpoint has no project filter
- **Impact:** LOW
- **Resolution:** DEFERRED
- **Notes:** per-project histories exist on the project page (`/projects/{id}/executions`).

## 10. Bugs / Problems Found

None in product code, either pre-existing or introduced. Problems hit while writing this phase's tests and gates:

| Problem | Root cause | Fix | Category |
|---|---|---|---|
| The paging test expected `offset=25` but got `0` | The test stub returned `has_more` only when `offset` was absent. `ApiClient` always sends `offset`, so "Next" stayed disabled | Stub keys on `offset === '0'` | Introduced this phase, test code |
| `contract.node.test.ts` "every service function is covered" failed | The new `exportExecutionReport` was not registered, and the recorder overrode only `request` | Recorder overrides `requestWithBody`; function registered | Introduced this phase, test code |
| ESLint `no-unused-vars` on `_dropped` in the malformed test | Destructuring used to drop `nodes` | Replaced with `delete broken['nodes']` on a `Record<string, unknown>` | Introduced this phase, test code |
| `tsc` TS2353 and TS4111 in the test file | An input object literal type was too narrow; index-signature access needs brackets | `Object.assign(...)`; bracket access | Introduced this phase, test code |

## 11. Fixes Applied

All four items in §10. Each one was verified by re-running:
- `npx vitest run`;
- `npm run lint`;
- `npm run build`.

All of them pass in the final state (§13).

## 12. Testing & Verification

### Component Tests (Vitest + React Testing Library, jsdom)

These run the real router, `ApiClient` and `QueryClient`, with only `fetch` stubbed (`src/test/app.tsx`). `ReportsPage.test.tsx` contains the following.

**List:**
1. Requests exactly the four finished statuses. The report link goes to `/reports/exec_1`, and the execution id links to `/executions/exec_1`.
2. The status filter sends only `status=failed`, and "Next" sends `offset=25`.
3. Empty state: "No reports yet".

**Detail:**
4. Successful report:
   - project link and backend duration "1 s";
   - cost "0.0021 USD" and the node row with 42 tokens;
   - "No errors were recorded";
   - event timeline;
   - inspector and Logs-tab links.
5. Failed execution report:
   - execution error `NODE_FAILED`;
   - the node's "Upstream failed" as "Attempt 1";
   - no outputs section when outputs are null.
6. Malformed report (missing `nodes`): "Could not load the report", and no summary is rendered.
7. 404: "Report not found".
8. Untrusted text:
   - a node label `<img src=x onerror=…>` and an error message `<script>…</script>` render as literal text;
   - `document.body` contains no `img`, `script` or `iframe`;
   - `window.pwned` is not set;
   - `[REDACTED]` in outputs is shown verbatim.
9. Large report:
   - 300 nodes give 301 table rows;
   - 10,000 events with `events_truncated` show the truncation notice and "Show more events (9800 more)";
   - a 100,000-character output shows "Showing the first 20,000 of …".

**Export:**
10. The saved Blob text equals the exact backend body, including the unknown `future_field`. MIME type is `application/json`, and the file name is `execution-report-exec_1.json`.
11. A malformed export body: the toast "Could not download the report" appears, and no Blob or click occurs.
12. The id `exec/../x y` gives the file name `execution-report-exec____x_y.json`.

**Result:** 12/12 pass.

### Contract Tests

`api/contract.node.test.ts` checks every service function, including `exportExecutionReport`, against `openapi.json`. **Result:** pass.

### Regression

Full frontend suite: **34 files, 429/429 pass.** This includes the Phase 15 inspector tests, which are affected by the new header link and the `OverviewPanel` exports.

### Security Tests

Tests 8, 11 and 12 above, which cover XSS rendering, refusing malformed exports and file-name sanitization.

### Not performed

- Playwright E2E or full-stack tests (`tests/fullstack/`);
- a manual real-browser check of the download;
- automated accessibility audit tooling;
- performance measurement beyond the large-report component test;
- backend tests (no backend change).

## 13. Quality Gates

All frontend gates were executed on 2026-10-04 on the final state.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `npm run format:check`: "All matched files use Prettier code style!" |
| Linter | PASS | `npm run lint` (`eslint .`): no output. An earlier `no-unused-vars` failure in the test was fixed (§10) |
| Type Checker | PASS | `tsc -b`, as part of `npm run build`. Earlier TS2353/TS4111 failures in the test were fixed |
| Unit/Component Tests | PASS | `npx vitest run`: 34 files, 429/429. An earlier contract-coverage failure and a paging-test failure were fixed |
| Integration Tests | N/A | No backend change; the frontend has no integration tier besides full-stack |
| API/Contract Tests | PASS | `contract.node.test.ts`, part of the 429 |
| E2E Tests | N/A | Not run in this phase |
| Build | PASS | `vite build` ✓; report chunks are code-split |
| Security Checks | PASS | Component security tests; `grep` finds no `innerHTML`/`dangerouslySetInnerHTML` in non-test `src` |
| Accessibility | N/A | No automated audit; semantic markup reviewed manually (§8) |
| Backend gates | N/A | Backend unchanged, not re-run |

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Reports are based exclusively on backend reports | PASS | `ReportDetailPage` renders only `executionQueries.report` data. Duration is the backend's `duration_ms`, not computed from timestamps. Usage and cost are `report.metrics`, not summed client-side. The export is the backend body verbatim (test 10) |
| Sensitive data is not reconstructed client-side from redacted data | PASS | No code transforms `[REDACTED]`. Test 8 asserts it is displayed as sent. The export is saved unmodified, and nothing is written to browser storage |
| Scope: Report List | PASS | `/reports`, tests 1–3 |
| Scope: Report Details / Execution Summary / Duration | PASS | `Summary`, test 4 |
| Scope: Nodes / Errors / Tokens / Costs | PASS | `Nodes`, `ErrorsPanel`, `Usage`; tests 4, 5 |
| Scope: Export / Download | PASS | `DownloadReport`; tests 10–12. Verified in jsdom only, not in a real browser (§20) |

None of the open points block completion.

## 15. Security Considerations

- **Authorization:** unchanged and backend-enforced. `authorize_path` → `require_execution` on `/reports/executions/{execution_id}`, and the list uses the owner-filtered `/executions`. The UI shows a 404 as "not found or no access", without distinguishing the two.
- **XSS/output encoding:**
  - all report strings are React text;
  - no HTML, Markdown or auto-linking;
  - no `innerHTML`/`dangerouslySetInnerHTML` in `src` (grep verified);
  - test 8 covers this.
- **Secrets/redaction:**
  - redaction is performed by the backend (`akasha.security.redaction` in `build_report`);
  - the UI neither reconstructs nor stores report data outside the TanStack Query cache;
  - no localStorage/sessionStorage;
  - no report data in URLs. Only the execution id, plus `tab`/`node` for inspector links.
- **Download:**
  - malformed or unexpected bodies are rejected before any file is created (test 11);
  - file name characters are restricted to `[A-Za-z0-9_-]` (test 12);
  - the Blob URL is same-origin and revoked after 40 s.
- **Error disclosure:** toasts show the normalized `ApiError` message, never the raw cause.

## 16. Performance Considerations

- **Event timeline:** rendered in steps of 200 (existing `EVENT_STEP`). The backend caps it at 10,000 events.
- **Large text:** each value is cut at 20,000 rendered characters (`MAX_RENDERED_CHARS`).
- **Node outputs:**
  - each node's outputs are rendered into a closed `<details>`, so they are present in the DOM but not displayed;
  - for very large node counts with big outputs this still creates DOM nodes;
  - not measured (§20).
- **Node table:** renders all nodes without virtualization. The 300-node test passes in jsdom; no browser measurement was taken.
- **List:** paginated (25 per page) with `keepPreviousData`, so the table doesn't blank between pages.
- **Download:** costs one extra request. The cached report is not reused, on purpose (§8).
- **Bundle:** both pages are lazy-loaded chunks.

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `frontend/README.md` | New feature | "Reports" section: list semantics and why `/executions` is used, report sections, link to logs, export byte-fidelity and validation, file-name rule, untrusted-text rules |
| `docs/reports/frontend/phase-19-reports-ui.md` | Phase report | This document |

No change was needed to `docs/architecture.md`, whose §55 already describes the report service and export format.

## 18. Dependencies / Infrastructure Changes

None:
- no new, removed or upgraded packages (`package.json` and `package-lock.json` untouched);
- no environment variables, Docker, database, Redis or test-infrastructure changes.

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion |
|---|---|---|---|
| Full-stack Playwright test of the report page and download | Not required by the phase tests; E2E is its own phase | Phase 26 (E2E Testing) | No |
| Project filter for the report list | Needs a backend `/executions` filter; out of scope | Unassigned | No |
| PDF/CSV export | Not in the roadmap scope; the backend offers JSON only | Unassigned | No |
| Business/aggregate reports (vision §34) | Not in the Phase 19 scope | Unassigned | No |

## 20. Known Limitations

- **Running executions:** `/reports` lists only finished executions (completed, failed, timeout, cancelled). Executions in `created/queued/running/paused/waiting` have a partial report that is reachable only through the inspector "Report" link or a direct URL.
- **No project filter or search** in the report list.
- **Logs:** the report contains no log lines (backend contract), so the report page links to the inspector Logs tab and does not embed them.
- **Download verification:** verified only in jsdom with stubbed `URL.createObjectURL` and `HTMLAnchorElement.click`. There was no real-browser or full-stack check. The detached-anchor technique is standard in current browsers but was not observed here.
- **Very large reports:** no virtualization of the node table, and outputs exist in the DOM even while collapsed. No browser performance measurement was taken.
- **Refresh:** the report page does not refresh live for running executions; there is a manual Refresh button. Live updates remain the inspector's job (Phase 14).

## 21. Open Issues

No known open issues resulting from this phase. The items in §19 and §20 are tracked as deferred work and limitations.

## 22. Architectural / Product Decisions

- **The report list is the list of finished executions**, using `GET /executions?status=`. There is no `/reports` collection endpoint, because reports are derived on demand and are not stored.
- **The export must be the backend's bytes.** Any future download of backend-canonical documents should use `ApiClient.requestWithBody`, not `JSON.stringify` of parsed data. Zod strips unknown fields.
- **Validation before download.** A body failing `executionReportSchema` is never saved, even though the raw bytes are what is saved.
- **The download re-fetches** so the file matches current persisted state rather than possibly stale cache.
- **The report page shares the inspector's query key and panels.** Changes to `ErrorsPanel`, `EventsPanel`, `Usage` or `RunSnapshot` affect both pages.

## 23. Repository / Git State

- **Branch:** `main`. HEAD `362389e` ("frontend phase 18 - triggers ui").
- **Working tree:** all phase changes are **staged** (`git diff --cached --stat`: 12 files, +1061/−8). There are no unstaged or untracked files.
  - Added: `docs/reports/frontend/phase-19-reports-ui.md`, `frontend/src/features/reports/{ReportDetailPage.tsx, ReportsPage.tsx, ReportsPage.test.tsx}`.
  - Modified: `frontend/README.md`, `frontend/src/api/{client.ts, contract.node.test.ts, reports.ts}`, `frontend/src/app/routes.tsx`, `frontend/src/features/executions/{ExecutionDetailPage.tsx, format.ts, inspector/OverviewPanel.tsx}`.
- **Commits:** none yet.
- **Unrelated changes:** none detected.

## 24. Phase Completion Assessment

### Implemented
- `/reports` list;
- `/reports/:executionId` report page;
- canonical JSON download;
- execution ↔ report links;
- `ApiClient.requestWithBody`;
- `exportExecutionReport`;
- README section.

### Verified
- 12 new component tests and the contract test;
- the full suite (429/429);
- lint, format, typecheck and build, all passing.

### Not Implemented
- a backend report-list endpoint, deliberately (Decision 1);
- PDF/CSV export.

### Deferred
- E2E/full-stack report test;
- project filter;
- aggregate reports.

### Open
- None.

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

- **Next phase:** 20, Plugin Management UI. It is independent of reports.
- **Available:**
  - routes `/reports` and `/reports/:executionId`;
  - `reportPath(executionId)` in `features/executions/format.ts`;
  - `exportExecutionReport` in `api/reports.ts`;
  - `ApiClient.requestWithBody` for any endpoint whose exact bytes must be preserved (e.g. a future project-package export).
- **Must not change casually:**
  - **byte fidelity:** the download saves the backend body verbatim (test 10 guards it);
  - **validate-before-save** (test 11);
  - **untrusted text as React text only** (test 8);
  - the shared inspector panels used by both pages.
- **Contract test:** when adding service functions, register them in `contract.node.test.ts`. The recorder hooks `requestWithBody`, which also catches `request`.
- **Constraints:**
  - report semantics (schema_version 1, determinism, redaction) are owned by backend `akasha.application.reports`;
  - the frontend must not compute report figures.
