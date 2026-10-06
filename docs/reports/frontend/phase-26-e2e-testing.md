# Phase 26 — E2E Testing

## 1. Executive Summary

**Goal.** Roadmap §32, "Phase 26 — E2E Testing", asks for automated end-to-end coverage of the critical V1 user journeys against the real application boundaries. The required flows are:

- Workflow flow: login → create → open → add node → configure → save → validate → publish.
- Execution flow: open → run → open execution → live status → logs → result.
- Import/export flow: export → import → open → validate → run.
- Credential flow: create → a node references it → the secret stays hidden.

**What was implemented.**

| Spec | Coverage |
|---|---|
| `tests/fullstack/journey.spec.ts` (new) | All 22 journeys of the phase brief, as one test of named steps, driven only through the UI against the real backend and worker |
| `tests/fullstack/credentials.spec.ts` (new) | The credential workflow end to end, including export and import; refusal of a corrupt package |
| `tests/fullstack/session.ts` (new) | Shared helpers: admin API context, cookie sign-in, download capture, a minimal zip reader |

The existing full-stack specs (`workflows.spec.ts`, `a11y.spec.ts`) and the stubbed E2E suite (`tests/e2e`) still pass unchanged.

**Defects found by the new tests and fixed at their layer:**

| # | Defect | Layer | Fix |
|---|---|---|---|
| D1 | Workflow import/export had no UI, although the backend endpoints existed | Frontend (product gap) | Export button, Import dialog, binary download in `ApiClient` |
| D2 | Monitoring hid runs created in the current minute | Frontend (product) | The preset window ends at the end of the current minute |
| D3 | A served backend with `AKASHA_ENVIRONMENT=test` never configured logging, so `AKASHA_LOG_LEVEL` was ignored and INFO node log lines were never stored | Backend (product) | Logging is configured by the serving process, in every environment |
| D4 | The dedicated `akasha worker` logged like a one-off CLI command (WARNING unless `-v`, text on stderr), so production runs on the worker never stored INFO node log lines | Backend (product) | The worker configures logging like the API |
| D5 | Ambiguous locators ("Connect" vs "Remove connection…", "Report" vs "Reports") | Test | Exact role-name matches |

**Result.** Full-stack suite: 22 passed. Stubbed E2E suite: 34 passed. The two new specs passed 3 runs in a row (9/9). Every quality gate passes (§13).

## 2. Phase Objective

Roadmap acceptance criterion: "Die wichtigsten V1 Workflows funktionieren automatisiert" (the most important V1 workflows work, automated).

The phase brief adds these requirements:

- **Real boundaries:** E2E tests exercise the real application; complete journeys are not replaced by component mocks.
- **Determinism:** deterministic infrastructure; no arbitrary sleeps; waits on observable conditions.
- **Failure handling:** reproduce, find the root cause, fix at the correct layer, add regression coverage, then rerun the targeted test and the relevant suite. Assertions are not weakened.
- **Journeys:** at least the 22 listed, from Login to "credential workflow where safe".

## 3. Scope Implemented

### Frontend / UI

- **Export:** on `/workflows/:id`, downloads the draft's package (`<workflow id>-draft.akasha.zip`). A toast states that credential values are not included, only their references. A failure shows a toast with the backend's message.
- **Import workflow:** on `/workflows`, opens a dialog to pick a `.zip` (size-checked in the browser for UX only) and upload it. The dialog then shows the backend's answer:
  - success: the new workflow's name, its validation result (valid, or the list of issues), the referenced credentials this installation lacks, and an "Open workflow" button;
  - refusal: the backend's error message and, for `WORKFLOW_PACKAGE_INCOMPATIBLE`, the requirement issues.

  Nothing is published or run by an import.

### Frontend / API layer

- **`ApiClient.download`:** returns a `Blob`, byte for byte. It shares one request path (`exchange`) with `requestWithBody`, so headers, CSRF, timeout and cancellation behave the same, and failures are mapped to the same `ApiError`s.
- **Service functions:** `exportWorkflow`, `importWorkflow`, `importResultSchema` and `MAX_WORKFLOW_PACKAGE_BYTES` in `api/workflows.ts`; the `useImportWorkflow` mutation invalidates the workflow lists.
- **`lib/saveFile.ts`:** the blob-download helper, moved out of `ReportDetailPage` so both downloads share it.
- **Monitoring:** `presetWindow` ends at the end of the current minute (D2).

### Backend

- **Served process (`akasha/main.py`):** `create_served_app()` is the uvicorn factory. It configures logging from `AKASHA_LOG_LEVEL` and then builds the app. `create_app` no longer configures logging at all.
- **CLI (`akasha/cli.py`):** `main(argv, *, configure_logs=True)`. The console script always configures logging; the in-process test harnesses pass `configure_logs=False`. `akasha worker` configures service logging (JSON on stdout, `AKASHA_LOG_LEVEL`).
- **Environment setting:** `AKASHA_ENVIRONMENT` no longer changes any behavior.

### Testing

- Two new full-stack specs (4 journey-level scenarios counting the tests in `credentials.spec.ts` and the single journey test).
- New unit, contract and backend regression tests (§12).

## 4. Files Changed

**Frontend:**

| File | Change |
|---|---|
| `src/api/client.ts` | `exchange` (shared request path) and `download` |
| `src/api/workflows.ts` | Export/import service functions and schema |
| `src/api/mutations.ts` | `useImportWorkflow` |
| `src/api/client.node.test.ts` | Binary download test |
| `src/api/contract.node.test.ts` | Covers export (binary response) and import; parses `application/zip` content |
| `src/features/workflows/ImportWorkflowDialog.tsx` | New |
| `src/features/workflows/WorkflowsPage.tsx` | Import workflow button |
| `src/features/workflows/WorkflowDetailPage.tsx` | Export button |
| `src/features/reports/ReportDetailPage.tsx` | Uses `lib/saveFile` |
| `src/lib/saveFile.ts` | New (moved) |
| `src/features/monitoring/model.ts`, `model.test.ts` | Window end and its regression test |
| `tests/fullstack/journey.spec.ts`, `credentials.spec.ts`, `session.ts` | New |
| `README.md` | Testing, workflows, monitoring, API layer and security notes |

**Backend:**

| File | Change |
|---|---|
| `src/akasha/main.py` | `create_served_app` |
| `src/akasha/api/app.py` | No logging configuration in the factory |
| `src/akasha/cli.py` | `configure_logs`; worker service logging |
| `tests/test_lifecycle.py` | Factory and served-app logging tests |
| `tests/test_cli.py` | Console-script and worker logging tests; harness passes `configure_logs=False` |
| `tests/integration/test_cli.py` | Harness passes `configure_logs=False` |

**Docs:** `docs/architecture.md` (node log history: which process sets the level), and this report.

## 5. Architecture Changes

### 5.1 Logging belongs to the process, not to the app factory

**Before.** `create_app` configured logging unless `environment == "test"`, and the CLI did the same. "test" therefore meant two things: a deployment environment, and "pytest owns the handlers".

A served backend started as `test` kept Python's default WARNING root level. Because node log capture (`akasha.runtime.node_logs`) is gated by the logger level, INFO lines were silently not stored.

**After.** The entry points own logging:

- `akasha serve` configures it through `create_served_app`, which uvicorn calls after it set up its own loggers, so the timing is unchanged;
- the console script configures it in `cli.main`.

In-process callers either never configure logging (`create_app`) or opt out explicitly (`configure_logs=False`). The environment name no longer changes behavior. Documented in `docs/architecture.md` (node log history, ceilings).

### 5.2 Binary responses in the API client

The client read every body as text, which corrupts a zip. `download` reuses the request path and error mapping and reads the success body with `response.blob()`. JSON validation stays where it was: `requestWithBody` parses and validates as before.

## 6. Domain / Data Model Changes

None.

## 7. API / Contract Changes

- **HTTP API:** no endpoint changed.
- **Frontend contract coverage:**
  - `GET /workflows/{id}/export`: the success response must be exactly `application/zip`, with no body sent.
  - `POST /workflows/import`: the response is checked against `ImportWorkflowResponse`.
- **Backend Python API:**
  - `cli.main` gained a keyword-only `configure_logs` (default `True`, the old behavior for real processes);
  - `akasha.main.create_served_app` is the uvicorn factory.

## 8. Implementation Details

### The journey test

`journey.spec.ts` is one test with 14 `test.step`s, because each step builds on the last. A fresh browser context signs in through the login form. Each step checks what the backend stored, read with a bearer-token API context:

| Step | Checked against the backend |
|---|---|
| Save | Edges `core.input>core.log>core.output` and the Log `message` |
| Trigger | The draft has a manual trigger |
| Publish | `current_version` is 1 |
| Run | Execution `completed`, trigger type `manual`, version 1 |
| Export | The package contains the configured message |
| Import | The imported draft equals the source draft; `current_version` is 0 |
| Re-execution | Completed, belongs to the imported workflow |

Steps checked through what the page received:

- **Live view:** the WebSocket frames the page received name the execution, and the activity list shows "Execution completed".
- **Monitoring:** the test captures the `GET /monitoring/summary` response the page itself received. It asserts that the `core.log` completion count in that response is at least 1 and that the page renders exactly that number. No figure is computed in the test.
- **Report:** the downloaded JSON has the execution id and status `completed`.

### Credential spec

The secret is a unique `sk-e2e-<uuid>`. It must never appear:

- in the page HTML (also after a reload), in `localStorage`/`sessionStorage`, or in any `/api/v1/` response body the page received during the test;
- in the credential's API detail;
- in the stored draft, which holds `credential:<name>`;
- in any entry of the exported package (inflated with `node:zlib`).

The node picks the credential from the datalist the backend's credential list fills. After the credential is deleted, importing the package lists its name under "Credentials to create".

A second test uploads a non-zip. The request returns 422; the dialog shows the backend's message and stays on the upload form.

### Determinism

- **No sleeps:** waits are web-first assertions, `waitForResponse`, `waitForEvent('download')` and `expect.poll` on the API.
- **Unique data:** every test makes unique names, so tests sharing the run's database do not interfere.
- **Monitoring:** the only cross-test aggregate is asserted only through the response the page received.

## 9. Findings

### F1 — Import/export journey had no UI

Phases 06–25 never added it; the frontend README listed it as "not covered" because the client only spoke JSON. The phase brief's journeys 19–21 cannot be tested without it, so the minimum UI was added (§3) rather than testing the API in the browser's place.

### F2 — Monitoring window excluded the current minute

`presetWindow` ended at `floor(now)` to the minute. The backend counts `created_at < until`, so the "Last 24 hours" view right after a run showed "No executions in this time range".

### F3 — `AKASHA_ENVIRONMENT=test` disabled logging in served processes

The full-stack backend ran as `test`, so the first journey run found "No log lines were recorded" for a Log node at its default level, INFO. The existing failed-run spec had passed only because it logged at WARNING.

### F4 — The production worker stored no INFO node log lines

The same root cause, found while fixing F3. Compose runs executions in `akasha worker`, which logged at WARNING unless `-v` was given. `docs/deployment.md` already described worker logs as JSON on stdout, which is now true.

## 10. Bugs / Problems Found

D1–D5 in §1. Tooling note: `--repeat-each=3` of the new specs was used to check stability (9/9 passed).

## 11. Fixes Applied

| Defect | Fix | Regression coverage |
|---|---|---|
| D1 | Export/Import UI and `ApiClient.download` | `client.node.test.ts` (binary bytes intact, error mapping); contract test; both new E2E specs |
| D2 | Window ends at the end of the current minute (still stable within the minute, so the query key does not churn) | `model.test.ts`: a run created now (also exactly on a minute boundary) is inside the window. Journey monitoring step |
| D3 | `create_served_app`; `create_app` leaves logging alone; CLI `configure_logs` | `test_lifecycle.py`: served app configures `AKASHA_LOG_LEVEL` with `environment` test and production; factory leaves the root logger untouched. `test_cli.py`: console script configures with `environment=test`. The full-stack backend runs as `test` again, so the journey's INFO log assertion checks it end to end |
| D4 | Worker uses service logging | `test_cli.py::test_worker_logs_like_a_service`: INFO level, a single stdout handler |
| D5 | Exact locators | n/a (test code) |

The new backend tests were run against the old `app.py`/`cli.py`: the factory and console-script tests failed there, as intended.

## 12. Testing & Verification

### Unit / component tests (Vitest)

472 passed (38 files), including:

- the new download test;
- the contract tests for export and import;
- the monitoring window regression test.

### Backend tests (pytest)

| Run | Result |
|---|---|
| Targeted (`test_lifecycle.py`, `test_cli.py`, `test_foundation.py`) | 47 passed, 1 skipped |
| Full suite (Docker/Testcontainers available) | 2052 passed, 10 skipped |
| `test_cli.py`, `integration/test_cli.py`, `test_lifecycle.py`, rerun after the worker change (D4) | 61 passed, 3 skipped |

The full suite ran before D4; the files D4 touches were rerun afterwards. Skips are POSIX-only tests (file modes, signals) on this Windows host.

### E2E (Playwright)

| Suite | Result |
|---|---|
| `npm run test:fullstack` (production bundle, real backend, real worker, PostgreSQL 17.2, Redis 8.2.1 in throwaway containers) | 22 passed |
| New specs with `--repeat-each=3` | 9 passed |
| `npm run test:e2e` (production bundle, API stubbed) | 34 passed |

The full-stack suite was run twice:

1. with the interim `AKASHA_ENVIRONMENT=e2e` workaround;
2. after the backend fix, with the E2E backend back on `test`.

### Journey coverage (phase brief)

| # | Journey | Where |
|---|---|---|
| 1 | Login | journey (login form); `workflows.spec.ts` (wrong password, reload, sign out, expired session) |
| 2 | Dashboard | journey |
| 3 | Create Workflow | journey |
| 4 | Open Workflow | journey |
| 5 | Create/Edit Draft | journey |
| 6 | Add Node | journey |
| 7 | Configure Node | journey; credentials spec |
| 8 | Connect Nodes | journey (connections panel); `workflows.spec.ts` (drag) |
| 9 | Save Graph | journey |
| 10 | Validate | journey; `workflows.spec.ts` (invalid → fix) |
| 11 | Publish | journey |
| 12 | Configure Trigger | journey; `workflows.spec.ts` (cron, webhook, event) |
| 13 | Trigger Execution | journey (manual trigger row) |
| 14 | Observe Live Execution | journey; `workflows.spec.ts` (dropped connection) |
| 15 | Open Execution Inspector | journey; `workflows.spec.ts` (failed run) |
| 16 | Inspect Logs | journey |
| 17 | View Metrics | journey |
| 18 | View Report | journey (view and download) |
| 19 | Export Workflow | journey; credentials spec |
| 20 | Import Workflow | journey; credentials spec (missing credential, corrupt package) |
| 21 | Re-execute Imported Workflow | journey |
| 22 | Credential workflow | credentials spec |

### Security tests

- **Secret non-disclosure:** across UI, storage, API responses, draft and export (credentials spec).
- **Corrupt upload:** refused by the backend without importing anything.
- **Untrusted text:** the existing specs keep covering it as text, not markup.

### Accessibility tests

The `a11y.spec.ts` sweep covers `/workflows` and `/workflows/:id` (new buttons) at four widths in both color schemes; it passed. The Import dialog uses the shared `Dialog` and a labelled file input. It is not part of the automated sweep, which visits screens, not dialogs.

## 13. Quality Gates

| Gate | Command | Result |
|---|---|---|
| Frontend format | `npm run format:check` | PASS |
| Frontend lint | `npm run lint` | PASS |
| Frontend typecheck | `npm run typecheck` | PASS |
| Frontend unit | `npm test` | PASS (472) |
| Frontend build | `npm run build` (run by both Playwright suites) | PASS |
| Stubbed E2E | `npm run test:e2e` | PASS (34) |
| Full-stack E2E | `npm run test:fullstack` | PASS (22) |
| Backend lint | `uv run ruff check .` | PASS |
| Backend format | `uv run ruff format --check .` | PASS |
| Backend typecheck | `uv run mypy .` | PASS |
| Backend tests | `uv run pytest` | PASS (2052 passed, 10 POSIX-only skipped) |

## 14. Acceptance Criteria Verification

| Criterion | Status |
|---|---|
| The most important V1 workflows run automated | Met: all 22 journeys (§12) |
| Real backend test environment, not fake assertions | Met: production bundle, real backend and worker; every step asserts stored or returned backend state |
| No arbitrary sleeps; waits on observable conditions | Met |
| Failures handled at their root cause with regression coverage, assertions not weakened | Met (§11); no assertion was relaxed. The only test edits after failures were ambiguous locators made exact |

## 15. Security Considerations

- **Export content:** the package contains credential references only (backend write gate). The E2E proves it on the actual bytes.
- **Download file names:** derived from the workflow id, sanitized to `[\w-]`; user text never reaches a file name.
- **Import:** the size check in the browser is UX only. Type, size, archive structure and requirements are validated by the backend, and its messages render as plain text.
- **Logging scope:** served processes now honor `AKASHA_LOG_LEVEL` in every environment. Node log lines are still redacted and bounded before storage (unchanged).

## 16. Performance Considerations

- **Bundle:** the Import dialog is part of the workflows route chunk; no new dependency.
- **Downloads:** go through a Blob, as the report export did.
- **Suite duration:** the full-stack suite takes about 5 minutes; the journey test about 13 seconds.

## 17. Documentation Changes

- **`frontend/README.md`:**
  - what each full-stack spec covers and the shared helpers;
  - import/export in the Workflows section;
  - binary responses in the API layer (the "not covered" note removed);
  - the monitoring window anchor;
  - downloads in Security.
- **`docs/architecture.md`:** which processes set the log level that gates stored node log lines, and that `create_app` never configures logging.

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added (the zip reader in `session.ts` uses `node:zlib`).
- **Full-stack backend:** runs as before (`AKASHA_ENVIRONMENT=test`, `AKASHA_LOG_LEVEL=INFO`); the level now takes effect.

## 19. Deferred Work

- Export of a published version from the version page (the API supports `?version=`).
- Automated accessibility sweep of dialogs (Import, Publish, Trigger).

## 20. Known Limitations

- **One worker:** the full-stack suite uses fixed ports and a single Playwright worker; it cannot run in parallel with itself.
- **Not separate journeys here:** plugin installation and settings are not E2E journeys in this phase; they are covered by the accessibility sweep and the stubbed suite.
- **`akasha workflow run`:** an in-process run from the CLI stores node log lines at the CLI's level (WARNING, or INFO with `-v`), not `AKASHA_LOG_LEVEL`. This is documented.
- **Chromium only:** the browser matrix is defined in Phase 27.

## 21. Open Issues

None blocking.

## 22. Architectural / Product Decisions

- **Process-owned logging (§5.1)** rather than a new setting: it removes the overloaded meaning of `environment` without adding configuration.
- **Minimum import/export UI:** added in this phase because the required journeys need it; scoped to the draft package and the backend's existing answer, with no dry-run or conflict editor.
- **One stepwise journey test** rather than many independent tests: the steps share state by design (each consumes the previous result). Independent edge cases stay in `workflows.spec.ts` and `credentials.spec.ts`.

## 23. Repository / Git State

Branch `main`; changes are uncommitted in the working tree (22 files changed plus this report and `docs/architecture.md`). No generated files were edited by hand (`openapi.json` unchanged).

## 24. Phase Completion Assessment

### Implemented

- All 22 journeys of the phase brief, end to end against the real stack.
- Import/export UI.
- Fixes D1–D5.

### Verified

All quality gates in §13.

### Not Implemented

Nothing in scope.

### Deferred

§19.

### Open

None.

### Blocking Issues

None.

## 25. Handoff to Next Phase

Phase 27 (Frontend Production Readiness) can rely on:

- `npm run test:fullstack` as the regression net for the V1 journey;
- served backends honoring `AKASHA_LOG_LEVEL` in every environment.

Items for Phase 27:

- the browser support matrix (Playwright runs Chromium only);
- whether to offer version export in the UI.
