# Phase 11 — Graph Validation + Publish Workflow

Report date: 2026-10-02. Base commit: `5ed516e` (frontend phase 10). All changes described here are uncommitted in the working tree of `main`.

## 1. Executive Summary

**Supposed to achieve.** Roadmap §17 (`docs/roadmap-frontend.md`): show the full Draft → Save → Validate → Validation Results → Fix → Validate → Publish → Immutable Project Version workflow in the UI, with the backend as the authoritative validator. The session prompt added: map errors to nodes and ports, a validation summary, publish confirmation, published-version information, publish failure handling, unsaved-changes handling, and tests for a stale draft. It also asked to implement, test and document any missing backend API.

**Actually implemented.**
- **Backend:**
  - `POST /api/v1/projects/{id}/validate` now accepts an optional body `{"expected_revision": n}`. A different stored revision answers 409 `CONCURRENT_MODIFICATION`.
  - Before this, a validation result could describe a draft other than the one the user sees, and nothing on the wire said which revision was checked.
- **Frontend** (`frontend/src/features/editor/`):
  - a new `ValidationPanel` with `usePublishWorkflow`. Results are tied to the revision they checked and can become "Outdated".
  - Issues are grouped by scope with a count summary. Node and port issues are named, marked on the canvas and in the node list, and are clickable to navigate.
  - Publish requires a confirmation dialog, then shows a success notice. The draft reloads after publish.
  - Stale-draft handling for both validate and publish.
- **Test infrastructure, fixed:**
  - *Pre-existing:* backend integration and node fixtures moved from per-directory `conftest.py` to plugin modules. Fixtures went missing when pytest paths interleaved directories.
  - *Introduced this phase:* a flaky first render in two frontend component tests. The cold import of the editor route took longer than the 1 s `findBy` wait.
  - *Pre-existing:* a race in the mocked e2e test "an unreachable backend shows one error, not one per section".

**Resulting state.**
- A user can validate the saved draft, see every backend issue, jump to the affected node, fix it, validate again and publish an immutable version.
- A full-stack Playwright test verifies this loop against the real backend.
- Every quality gate passes on the final tree, and repeated runs stay green (§13):
  - frontend unit/component suite: 13 consecutive full runs;
  - mocked e2e: 2 full runs, plus the formerly racy test 12/12 under deliberate CPU load;
  - full-stack e2e;
  - backend suite;
  - typecheck, lint, format and build.

**History.** An earlier version of this report marked the phase INCOMPLETE because `npm test` had become flaky (8 of 9 full runs failed). That defect is fixed (§10, problem 3), and so is a pre-existing e2e race found while re-verifying (§10, problem 4).

**Acceptance criteria.** All five roadmap criteria pass (§14). Two scope details remain:
- the roadmap's "Warnings": the backend has no warning severity (§9, finding 3);
- navigation to trigger and project-wide issues: no editor surface exists yet.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §17.**
- **Ziel:** "Den vollständigen Draft → Validate → Publish Workflow abbilden."
- **Scope:**
  - Draft → Save → Validate → Validation Results → Fix Errors → Validate → Publish → Immutable Project Version.
  - Validation UI: Fehler, Warnings, Node, Port, Configuration, Graph, Navigation zum betroffenen Node.
- **Acceptance criteria:**
  - Backend Validation wird dargestellt.
  - Fehler können angeklickt werden.
  - Editor navigiert zum betroffenen Node.
  - Publish ist nur bei gültigem Zustand möglich.
  - Published Version wird klar von Draft unterschieden.
- **Prompt constraint:** "Publish darf nicht als rein lokale Frontend-Entscheidung implementiert werden."

**Session prompt additions:**
- the explicit UX chain Draft → Validate → Publish → Immutable Project Version;
- "Never silently publish. Never hide backend validation errors.";
- tests for valid graph, invalid graph, multiple errors, node-specific errors, publish success, publish failure, unsaved changes and stale draft;
- implement, test and document missing backend APIs.

**Not responsible for:**
- Phase 12: version list, version details, version comparison.
- Phase 13: executing versions.
- Phase 18: trigger editing.
- Any client-side graph validation that replaces the backend.

---

## 3. Scope Implemented

### Backend/API
- `ProjectService.validate_project(project_id, *, expected_revision=None)` (`backend/src/akasha/application/projects.py`) reuses the existing `_check_revision`, the same check publish uses.
- New request model `ValidateProjectRequest` (`backend/src/akasha/api/project_schemas.py`).
- The `/validate` route (`backend/src/akasha/api/projects.py`) takes an optional body. Existing callers (CLI `project validate`, tests, import) are unchanged.

### Frontend/UI
- **Workflow panel** `ValidationPanel.tsx`, at the top of the editor's right column:
  - **Step strip:** "Draft (revision N) → Validate → Publish → Immutable version N+1".
  - **Status line** (`role="status"`) states exactly one of:
    - unsaved changes;
    - draft changed elsewhere;
    - not validated yet;
    - results for an older revision;
    - accepted, publishable as version N+1;
    - rejected with N errors.
  - **Badge:** Valid / "N errors" / Outdated, always as text.
  - **Buttons:** Validate and "Publish…", both `aria-describedby` the status line.
  - **Summary line:** "Nodes: 2 · Connections: 1 · Triggers: 1 · Project: 1".
  - **Issue lists** grouped by scope. Each issue shows its location ("Node "Log" → port "value"", trigger id, or connection endpoints), the backend message and the stable code.
  - **Locatable issues are buttons:** a node issue whose node still exists, or an issue with an edge. Other issues are plain items.
  - **Publish confirmation:** `ConfirmDialog` titled "Publish version N?", `tone="primary"`, `irreversible`. It explains that the backend re-validates and freezes the draft and that versions are immutable.
- **Workflow state** `usePublishWorkflow.ts`:
  - holds the last result with the revision it was requested for;
  - tracks `stale` and the dialog;
  - wraps `useValidateProject` / `usePublishProject`.
- **Mapping** `validation.ts`:
  - `issueScope`: node > connection > trigger > project;
  - `issueCounts`: errors per `node_id`;
  - `publishIssues`: parses `details.issues` of a 422 `PROJECT_VALIDATION_FAILED` with zod, `null` otherwise;
  - `isConflict`.
- **Canvas marker:** `NodeCard` has a new `issues` prop that gives a danger border and the text "N validation error(s)". It is fed through `GraphCanvas` prop `issueCounts` → `CanvasNodeData.issues`.
- **Node list:** each node shows "N validation error(s)" in the side list.
- **`EditorPage.tsx`:**
  - wires the workflow;
  - `locate(issue)` focuses the node (existing `focusNode`) or selects the edge;
  - the existing conflict alert also covers `workflow.stale`. Its text and button ("Reload the draft" vs "Discard my changes and reload") now depend on whether there are unsaved changes;
  - after publish: a success toast, a persistent success `Alert` ("Version N is published", timestamp, immutability note, link to project), and a draft reload.

### API layer
- `validateProject(client, projectId, expectedRevision?)` in `frontend/src/api/projects.ts`.
- `useValidateProject` now takes `{ projectId, expectedRevision? }` (`frontend/src/api/mutations.ts`). It had no callers before this phase.

### Testing
- See §12.
- Test-infrastructure fixes: see §10/§11.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/application/projects.py` | Modified | `validate_project` accepts `expected_revision`, checked with `_check_revision` |
| `backend/src/akasha/api/project_schemas.py` | Modified | New `ValidateProjectRequest` |
| `backend/src/akasha/api/projects.py` | Modified | `/validate` reads the optional body |
| `backend/tests/integration/test_projects_api.py` | Modified | New `test_validate_pinned_to_revision`; import path `tests.integration.fixtures` |
| `backend/tests/conftest.py` | Modified | `pytest_plugins = ["tests.integration.fixtures", "tests.nodes.fixtures"]` plus a comment explaining why |
| `backend/tests/integration/conftest.py` → `fixtures.py` | Renamed (content unchanged) | Fixture visibility fix (§10) |
| `backend/tests/nodes/conftest.py` → `fixtures.py` | Renamed (content unchanged) | Same latent problem |
| `backend/tests/integration/test_{auth_api,cli,credentials,executions_api,plugins_api,project_packages,project_packages_api,reports,triggers_api,workers}.py` | Modified (one import line each) | `tests.integration.conftest` → `tests.integration.fixtures` |
| `frontend/src/api/openapi.json` | Modified (generated) | Regenerated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1`; adds `ValidateProjectRequest` and the `/validate` request body |
| `frontend/src/api/projects.ts` | Modified | `validateProject` with `expectedRevision` |
| `frontend/src/api/mutations.ts` | Modified | `useValidateProject` variables |
| `frontend/src/api/contract.node.test.ts` | Modified | Calls `validateProject(recorder, 'p', 1)`, so the request body field name is contract-checked |
| `frontend/src/features/editor/validation.ts` | Created | Issue scope, per-node counts, publish-422 parsing |
| `frontend/src/features/editor/validation.test.ts` | Created | Unit tests for the above |
| `frontend/src/features/editor/usePublishWorkflow.ts` | Created | Workflow state and mutations |
| `frontend/src/features/editor/ValidationPanel.tsx` | Created | Panel, issue list, confirm dialog |
| `frontend/src/features/editor/EditorPage.tsx` | Modified | Wiring, `locate`, stale alert, published notice, draft reload |
| `frontend/src/features/editor/GraphCanvas.tsx` | Modified | `issueCounts` prop → node data |
| `frontend/src/features/editor/NodeCard.tsx` | Modified | `issues` marker |
| `frontend/src/features/editor/EditorPage.test.tsx` | Modified | 7 new tests (including edge-issue navigation); `backend()` stub gains `current()`/`patch()`; two Phase 10 assertions narrowed; `beforeAll` preload of the editor module (§10, problem 3) |
| `frontend/src/features/editor/NodeInspector.test.tsx` | Modified | `beforeAll` preload of the editor module (§10, problem 3) |
| `frontend/src/app/routes.test.tsx` | Modified | `beforeAll` preload of the editor module: the same latent cold-import race (§10, problem 3) |
| `frontend/tests/e2e/dashboard.spec.ts` | Modified | Wait for the page-level error heading before counting alerts (§10, problem 4) |
| `frontend/tests/fullstack/projects.spec.ts` | Modified | New full-stack test "validate, fix and publish a draft as an immutable version" |
| `docs/architecture.md` | Modified | `/validate` and `/publish` `expected_revision` semantics; publish advances the draft revision |
| `frontend/README.md` | Modified | New section "Validate and publish" |

- No new directories.
- No migrations.
- No configuration or dependency changes.

---

## 5. Architecture Changes

### Decision 1 — Validation is pinned to a draft revision

**Context.**
- `/validate` validated whatever draft was stored and returned only `{valid, issues}`.
- The editor could not tell whether the result described its own revision or a newer one saved elsewhere.
- In that case "Publish is only possible in a valid state" would rest on a result about a different draft. `/publish` was already pinnable.

**Chosen approach.**
- Optional `expected_revision` on `/validate`, with the same semantics and error (409 `CONCURRENT_MODIFICATION`, `details.current_revision`) as `/publish`.
- The frontend always sends it for both calls.

**Alternatives considered.**
- *Return the validated `revision` in `ValidationResponse`.* Rejected: the service returns the domain `ValidationResult`, so this would change the service return type and all its callers (CLI, import response, tests). It would also still leave the client to compare revisions itself.
- *Two calls (GET project, then validate).* Rejected: racy.

**Reason.** It is the smallest additive change, it reuses the existing check, and it has no breaking effect.

**Consequences.**
- Clients that omit the body behave as before.
- Future UIs that validate should pin, as the editor does.

### Decision 2 — The editor reloads the draft after publishing

**Context.** Discovered during implementation: `ProjectService.publish_project` saves the project row, which advances `revision`. The existing API test asserts revision 2 → 3 after publish. The editor keeps the revision it last saw and pins saves to it. Without a reload, the first save after publishing would be refused as a conflict.

**Chosen approach.** On publish success, `EditorPage` calls its existing `reload()`, which refetches and remounts `DraftEditor` with a new session key.

**Alternatives considered.**
- *Assume `revision + 1`.* Rejected: it depends on a storage detail and would silently adopt a concurrent writer's revision.
- *Return the new project revision from `/publish`.* Rejected: it is a contract change to `ProjectVersionDetail`, which describes a version, not the project.

**Reason.**
- Publish is only reachable with a clean editor, so the reload loses no edits.
- If someone else saved in between, the reload shows their draft, which is the correct behaviour.

**Consequences.**
- Undo/redo history is cleared by a publish.
- Documented in `docs/architecture.md` and `frontend/README.md`.

### Decision 3 — Package fixtures are loaded as plugins

See §10, problem 2. Test infrastructure only. The comment in `backend/tests/conftest.py` records the reason.

---

## 6. Domain / Data Model Changes

- No domain entities, enums, database tables or migrations changed.
- One new API request model: `ValidateProjectRequest { expected_revision: int | null (ge=1) }`, a separate class with the same shape as `PublishProjectRequest`.
- Frontend: `CanvasNodeData` gained `issues: number`, and `NodeCardProps` gained `issues?: number`. These are UI types only.

---

## 7. API / Contract Changes

**`POST /api/v1/projects/{project_id}/validate`** (changed, additive)

| | Before | After |
|---|---|---|
| Request | no body | optional JSON `{"expected_revision": int ≥ 1 \| null}` |
| 200 | `{valid, issues[]}` | unchanged |
| 409 | not possible | `CONCURRENT_MODIFICATION` when the stored revision ≠ `expected_revision`; `details`: `project_id`, `expected_revision`, `current_revision` |
| 422 | — | `VALIDATION_ERROR` for a malformed body (e.g. `expected_revision: 0`) |

- **Error documentation:** 404/409/413/415/422/500 are already declared router-wide as `ErrorResponse` (`projects.py`, `responses=errors`).
- **Compatibility:** backward compatible. No body means the old behaviour. The CLI (`akasha project validate`) and the import flow do not send a revision.
- **Contract snapshot:** `frontend/src/api/openapi.json` was regenerated. `backend/tests/test_openapi_contract.py` and `frontend/src/api/contract.node.test.ts` pass against it.
- **Other endpoints:** `/publish` is unchanged and was already pinnable. The frontend now always pins it.

No WebSocket, event, node, plugin or SDK contract changes.

---

## 8. Implementation Details

### Result validity
`usePublishWorkflow` stores `checked = { revision, result }`, where `revision` is the editor revision at request time. The result counts as current only if all of these hold:
- `checked.revision === state.revision`, and
- the editor is clean: not dirty, not saving, no save error.

`canPublish = current && result.valid && !stale`. Any edit therefore makes the result "Outdated", even before autosave. A later autosave produces a new revision, so the old result stays outdated.

### Only the saved draft is validated
- Validate and Publish are disabled while the editor is dirty, saving or has a save error. The status line says why.
- Validation does not trigger a save. Autosave runs after 1.5 s (`AUTOSAVE_DELAY_MS`), or the user saves with Save draft / Ctrl+S.

### Publish flow
1. `requestPublish` resets the previous mutation error and opens the dialog. Nothing is sent before the user confirms.
2. On confirm, `publish` runs `usePublishProject` with `expectedRevision`.
3. The outcome is handled by type:
   - **Success:** `usePublishProject` first invalidates `projects.detail(id)` (covering versions and executions) and `projects.lists()`. Then `onPublished(version)` sets the success notice and calls `reload()`.
   - **409:** the dialog closes and `stale` is set, which shows the conflict alert. Validate and Publish are disabled until the user reloads.
   - **422 `PROJECT_VALIDATION_FAILED`:** the dialog closes and the issues from `details.issues` replace the shown result. This covers a backend that changed between validate and publish, e.g. a node catalog change.
   - **Any other error** (network, 500, 403): the dialog stays open with `errorMessage(error)` for a retry.

### Validate errors
- 409 sets `stale`.
- Other errors show an inline `Alert` "Could not validate the draft".
- The previous result, if any, stays and is labelled by its revision.

### Navigation
`locate(issue)`:
- If `node_id` refers to an existing node: `focusNode` (Phase 07/10), which selects the node and centres the canvas on it.
- Otherwise, if the issue has an `edge`: selects that edge. The backend sets `node_id` on almost all graph issues; only `duplicate_edge` and `edge_unknown_node` are edge-only.

Issues for deleted nodes show "deleted node <id>" and are not clickable.

### Server state
- No new query keys. Validation results are mutation results held in component state.
- Validation is per editor session and never cached, which follows the rule that the backend's answer is the source of truth.

### Security-relevant behaviour
- Issues carry locations and messages only. The backend's `issue_payload` never includes config values.
- The frontend renders them as React text, never as HTML.

---

## 9. Findings

### Finding 1 — `/validate` could describe a different draft than the editor shows
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** §5 Decision 1. A concurrent save between loading and validating would have produced a result for an unseen draft.

### Finding 2 — Publishing advances the draft revision
- **Impact:** MEDIUM
- **Resolution:** FIXED (handled in frontend), documented
- **Notes:**
  - Not mentioned anywhere in the docs before this phase. Only `test_full_lifecycle_create_edit_validate_publish` asserted it.
  - Without handling it, every first save after a publish from the editor would have hit a revision conflict.

### Finding 3 — The backend has no warnings
- **Impact:** LOW
- **Resolution:** DEFERRED
- **Notes:**
  - Roadmap §17 lists "Warnings".
  - `ValidationResult.ok` is `not issues`, so every issue blocks publish, and there is no severity field.
  - The UI calls all issues "errors". Inventing warnings in the frontend would contradict "backend is authoritative".

### Finding 4 — Fixtures disappear when pytest paths interleave directories
- **Impact:** MEDIUM (test infrastructure)
- **Resolution:** FIXED
- **Notes:** Pre-existing. §10, problem 2.

### Finding 5 — The frontend unit suite became flaky
- **Impact:** HIGH (it made the required CI gate unreliable)
- **Resolution:** FIXED
- **Notes:**
  - §10, problem 3.
  - Measured: a cold `import('./EditorPage')` in jsdom takes 601 ms alone and 1085 ms during a full parallel run, which is more than Testing Library's whole 1000 ms `findBy*` wait.

### Finding 6 — A strict Playwright locator fails at once on a transient state
- **Impact:** MEDIUM (an intermittent red e2e gate)
- **Resolution:** FIXED
- **Notes:**
  - Pre-existing; §10, problem 4.
  - Verified with a throwaway spec: `expect(getByRole('alert')).toHaveText(...)` failed after 41 ms while two alerts were briefly present, instead of retrying until the 5 s timeout.

---

## 10. Bugs / Problems Found

### Problem 1 — Phase 10 assertions matched new text (introduced this phase, fixed)
- **Problem:** two existing tests used `getByText(/revision 4/)` and found multiple elements once the panel also printed the revision.
- **Root cause:** the assertion was too broad, not a product bug.
- **Fix:** narrowed to `/editing the draft \(revision 4\)/`, the header sentence they meant.
- **Affected area:** `EditorPage.test.tsx` ("never overwrites a draft changed elsewhere…", "a saved graph comes back the same…").

### Problem 2 — Integration fixtures missing for interleaved paths (pre-existing, fixed)
- **Problem:** `uv run pytest tests/integration/test_projects_api.py tests/test_cli.py tests/integration/test_project_service.py` gave 20 errors: `fixture 'clean_db' not found`. Reproduced identically on a clean tree (stash).
- **Root cause:**
  - With interleaved paths (`tests/integration/…`, `tests/…`, `tests/integration/…`), pytest 9.1.1 collects two separate `<Package integration>` nodes (confirmed with `--co`).
  - The fixtures from `tests/integration/conftest.py` were visible to the first only.
  - The full suite and single-directory runs were not affected.
- **Fix:**
  - Renamed `tests/integration/conftest.py` and `tests/nodes/conftest.py` to `fixtures.py` and loaded both via `pytest_plugins` in `tests/conftest.py`. Plugin fixtures are not tied to a collection node.
  - Checked beforehand that neither file defines hooks or autouse fixtures, and that no fixture names collide with the root conftest.
- **Regression test:** none automated. Verified by re-running the failing command and an interleaved `tests/nodes` command (§12).
- **Affected area:** backend test infrastructure only.

### Problem 3 — Flaky first render in frontend component tests (introduced this phase, fixed)
- **Problem:** `npm test` failed intermittently:
  - `EditorPage.test.tsx › loads the draft graph and says it is the draft, not a version`: "Unable to find role="heading" and name "Draft editor: Leads"", at ≈1.1 s;
  - sometimes `NodeInspector.test.tsx › renders every field kind from the node schema alone`: "Unable to find role="region" and name `/^Nodes/`".
- **Observed rate before the fix:**
  - Phase 11 tree: 8 of 9 full runs failed at least one test.
  - Phase 10 commit (temporary worktree, same `node_modules`): 8 of 8 passed.
- **Root cause (measured):**
  - The editor route is code-split (`src/app/routes.tsx`, `lazy: () => import('../features/editor/EditorPage')`). The first test in a file that renders it pays the cold module import (React Flow and the editor) inside its `findBy*` wait, which defaults to 1000 ms.
  - A throwaway test measured that import at **601 ms alone** and **1085 ms during a full parallel run**. Phase 11 adds three modules to the editor chunk, which pushed it over the limit.
  - `src/app/routes.test.tsx` ("serves the per-project editor route") had the same latent exposure.
- **Fix:**
  - `beforeAll(() => import('./EditorPage'))` in `EditorPage.test.tsx` and `NodeInspector.test.tsx`, and `beforeAll(() => import('../features/editor/EditorPage'))` in `routes.test.tsx`, each with a comment.
  - The import then happens once per file under the 10 s hook timeout, and the route's `lazy()` resolves from the module cache.
  - No `findBy` timeout was raised and no sleeps or retries were added (`/AGENTS.md` §95).
- **Verification:**
  - 10 consecutive full runs right after the fix, then 3 more on the final tree: 299/299 each time (13/13).
- **Affected area:** frontend component tests only; no product code.

### Problem 4 — Race in the dashboard outage e2e test (pre-existing, fixed)
- **Problem:** `tests/e2e/dashboard.spec.ts › an unreachable backend shows one error, not one per section` failed twice while re-verifying this phase. The error was a strict-mode violation: `getByRole('alert')` resolved to several section alerts ("Could not load system status", "Could not load active executions", …).
- **Root cause:**
  - With the API aborted, each of the six dashboard queries fails after its own retries (`shouldRetryQuery`, 2 retries). Sections render their own error as soon as their query fails. `DashboardPage`'s `useOutage` collapses them into one page-level error only once every query has failed (by design).
  - Under CPU load the final failures do not land in the same tick, so for a moment several section alerts are visible.
  - The test's `expect(getByRole('alert')).toHaveText(/Could not load the dashboard/)` throws immediately on that transient strict-mode violation instead of retrying. Confirmed with a throwaway spec: it failed after 41 ms on a page with two alerts that collapsed after 1.5 s.
- **Pre-existing:** under the same deliberate load (two parallel `vitest run`s, `--repeat-each 12 --workers 6`), the unchanged test on the Phase 10 commit failed 2 of 12.
- **Fix:** the test first waits for the page-level heading "Could not load the dashboard" (`toBeVisible`, 15 s), then asserts `toHaveCount(1)` and the message. Both assertions retry. The product behaviour is unchanged.
- **Verification:** the fixed test passed 12/12 under the same load; full mocked e2e 23/23 twice on the final tree.
- **Affected area:** `frontend/tests/e2e/dashboard.spec.ts` only.

---

## 11. Fixes Applied

| Symptom | Root cause | Implementation | Verification |
|---|---|---|---|
| Validation result could belong to another revision | `/validate` was not pinnable | `expected_revision` on `/validate` | `test_validate_pinned_to_revision` (409 stale, 200 current, 422 malformed); stale-draft component test |
| First save after publish would conflict | Publish advances the revision | Draft reload after publish | Component test saves after publish and asserts `expected_revision: 4`; full-stack test sees "Revision N has not been validated yet" after publish |
| "Discard my changes and reload" shown with no changes | Phase 07 alert assumed a failed save | Label and text depend on `editor.dirty` | Stale-draft component test clicks "Reload the draft" |
| Ambiguous `/revision 4/` assertions | Too broad | Narrowed to header sentence | Editor tests pass |
| 20 fixture errors for interleaved pytest paths | Duplicate `Package` nodes | Fixtures as plugins | Failing command: 124 passed; full suite: 1910 passed |
| `clean` flag computed twice in `DraftEditor` | Duplication from wiring | Single `const clean` | `tsc -b`, editor tests |
| Flaky first render in editor component tests | Cold lazy-route import (0.6–1.1 s) charged to the 1 s `findBy` wait | `beforeAll` preload of the editor module in the three files that render the route | 13/13 full `npm test` runs green (before: 1/9) |
| No test for edge-only issue navigation | Missing coverage | Component test 2 clicks the `duplicate_edge` issue and asserts the canvas edge has class `selected` and the node is no longer selected | Passes; it fails if `locate` stops selecting edges |
| Intermittent dashboard outage e2e failure | Strict locator on a transient multi-alert state | Wait for the page-level heading, then count | 12/12 under load (unfixed on the Phase 10 commit: 10/12); full e2e 23/23 twice |

---

## 12. Testing & Verification

### Unit Tests
- **`frontend/src/features/editor/validation.test.ts`** (3 tests):
  - `publishIssues` reads valid 422 issues;
  - `publishIssues` returns `null` for off-contract details, missing issues, another error code, and a non-`ApiError`;
  - `issueScope` precedence.
- **Result:** pass.

### Component Tests
`EditorPage.test.tsx`, new block "Validate and publish" (7 tests, against `stubBackend`; validate and publish enforce the pinned revision like the real backend):
1. Valid draft: "not validated yet", Publish disabled → validate → "accepted … version 3" → dialog "Publish version 3?" with immutability and "cannot be undone" text. Asserts no publish request before confirm. After publish: notice, `expected_revision: 3` on both requests, header shows revision 4, the panel resets, and a following save sends `expected_revision: 4`.
2. Five issues of every scope:
   - "5 errors" and the summary line;
   - every message shown;
   - node/port location text;
   - "2 validation errors" on the canvas card and in the node list;
   - clicking the port issue selects the node (`aria-pressed`);
   - clicking the connection-only `duplicate_edge` issue selects that edge on the canvas (class `selected`) and deselects the node;
   - the trigger issue is shown but not a button.
3. Unsaved changes: after an edit, "Outdated" and "Save the draft first"; Validate and Publish disabled. After autosave, "results are for revision 3"; Publish still disabled. No extra validate request.
4. Stale at validate (409): conflict alert, buttons disabled, "Reload the draft" → revision 4, buttons enabled.
5. Stale at publish (409): dialog closes, conflict alert, no success notice.
6. Publish refused with 422 `PROJECT_VALIDATION_FAILED`: dialog closes, "1 error", issue shown, Publish disabled.
7. Publish 500: dialog stays open with the message; no notice; revision unchanged.

- **Result:** pass.
- **Stability:** after the preload fix (§10, problem 3), the full suite (299 tests, 24 files) passed in 13 consecutive runs.

### API Tests
- `backend/tests/integration/test_projects_api.py::test_validate_pinned_to_revision` (real PostgreSQL via Testcontainers): stale pin → 409 with `current_revision: 2`; current pin → 200 `{valid: true, issues: []}`; `expected_revision: 0` → 422.
- **Result:** pass.

### Contract Tests
- `backend/tests/test_openapi_contract.py`: failed after the API change as intended, then passed once the snapshot was regenerated.
- `frontend/src/api/contract.node.test.ts`: passes with the pinned `validateProject` call.

### Integration Tests
- Full backend suite (`uv run pytest`, Testcontainers PostgreSQL/Redis etc.): **1910 passed, 6 skipped**, run after the fixture change.
- Previously failing interleaved command: 124 passed.
- Interleaved `tests/nodes` command: 70 passed. Not confirmed to have failed before the fix.

### E2E Tests
- **Mocked Playwright** (`npx playwright test`): 23 passed, twice on the final tree.
  - "an unreachable backend shows one error, not one per section" also ran 12× under deliberate CPU load: 12/12 (§10, problem 4).
- **Full-stack Playwright** (`npx playwright test -c playwright.fullstack.config.ts`, production bundle + real backend + fresh database): 9 passed, including the new test "validate, fix and publish a draft as an immutable version":
  - seeded draft with Log's input unconnected → Validate → "Node "Log" → port "value"" → click navigates → connect Input → Log in the Connections panel → save → Validate → "Valid" → confirm "Publish version 1" → notice;
  - the API returns version 1 with 2 edges;
  - the panel shows the next revision not yet validated.
- Re-run on the final tree: 9 passed.

### Security Tests
- No new security tests.
- The relevant existing guarantee (issues never contain config values) is enforced by `issue_payload` and covered by earlier backend tests.

### Accessibility Tests
- No automated accessibility audit was run.
- Implemented accessibility properties:
  - status via `role="status"`; errors via `Alert` `role="alert"`;
  - state never shown by colour alone (badges and markers are text);
  - issue entries are native `<button>`s;
  - the dialog uses the existing accessible `ConfirmDialog`, with initial focus on Cancel;
  - action buttons are `aria-describedby` the status line.

### Performance Tests
- None as a gate.
- One diagnostic measurement: cold import of `EditorPage` in jsdom, 601 ms alone and 1085 ms in a full parallel run (§10, problem 3).

---

## 13. Quality Gates

All results are from the final tree.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (prettier `format:check`) | PASS | |
| Formatter (`ruff format --check`) | PASS | 195 files |
| Linter (eslint) | PASS | |
| Linter (`ruff check`) | PASS | |
| Type Checker (`tsc -b`) | PASS | |
| Type Checker (mypy) | PASS | 190 source files |
| Unit/Component Tests (`npm test`) | PASS | 299 tests, 13 consecutive green full runs. Before the fix: 8 of 9 runs failed (§10, problem 3) |
| Backend Tests incl. Integration/API (`uv run pytest`) | PASS | 1910 passed, 6 skipped; the formerly failing interleaved command: 124 passed |
| Contract Tests | PASS | OpenAPI snapshot regenerated |
| E2E (mocked Playwright) | PASS | 23, two runs. Before the fix: one dashboard test failed intermittently (§10, problem 4) |
| E2E (full-stack Playwright) | PASS | 9 |
| Build (`npm run build`) | PASS | |
| Security Checks | N/A | No dedicated scan run |
| Accessibility | N/A | No automated audit run |

**Failures during the phase, then fixed:**
- `test_openapi_contract.py`: stale snapshot after the intended API change; regenerated.
- Two Phase 10 editor tests: ambiguous text; narrowed.
- First draft of the publish component test: the test double kept a separate revision counter; the stub was changed to share state.
- `npm test` flaky (introduced this phase): fixed (§10, problem 3).
- Mocked e2e dashboard outage test, intermittent (pre-existing): fixed (§10, problem 4).

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Backend Validation wird dargestellt | PASS | `ValidationPanel` lists every issue from `POST /validate` (message, code, location), plus summary and count badge. Component test 2 asserts all 5 messages; full-stack test against the real backend |
| Fehler können angeklickt werden | PASS | Node and connection issues are `<button>`s. Component test 2 clicks a node issue and a connection issue; the full-stack test clicks a node issue. Trigger/project issues are not clickable: the editor has no trigger or dependency surface yet (§20) |
| Editor navigiert zum betroffenen Node | PASS | `locate` → `focusNode` selects and centres the node. Asserted via `aria-pressed` in component test 2 and the full-stack test |
| Publish ist nur bei gültigem Zustand möglich | PASS | Frontend: Publish enabled only for a current, valid, non-stale result (tests 1, 2, 3, 6). Backend: `/publish` re-validates and is pinned to the revision (422/409 handled, tests 5, 6). Publish is never a local decision |
| Published Version wird klar von Draft unterschieden | PASS | Header keeps the "Draft" badge and "editing the draft (revision N)"; the confirmation explains that the version is immutable; after publish, "Version N is published" with timestamp and immutability text; the step strip names "Immutable version N". Tests 1 and full-stack |
| Scope item "Warnings" | NOT IMPLEMENTED | The backend has no warning severity (Finding 3). Does not block the acceptance criteria |

All acceptance criteria pass, and all quality gates pass (§13). "COMPLETE WITH LIMITATIONS" reflects the roadmap scope item "Warnings", which has no backend counterpart, and the deferred trigger and project navigation.

---

## 15. Security Considerations

- **Authorization:** `/validate` and `/publish` keep their existing project authorization. No new endpoint.
- **Input validation:** `expected_revision` is checked as `int ≥ 1` or `null`; malformed bodies get 422 `VALIDATION_ERROR` (tested).
- **Error disclosure:** 409 details expose only project id and revision numbers, the same as the existing publish and save conflicts.
- **Sensitive data:** validation issues contain no config values (backend `issue_payload`). Nothing is logged or stored client-side, and the result is not cached in TanStack Query.
- **XSS:** issue messages, codes and ids are rendered as React text.
- **Safety of publish:**
  - always explicit, requiring a confirmation dialog;
  - pinned to the reviewed revision;
  - re-validated by the backend.

---

## 16. Performance Considerations

- **Per-node counts:** `issueCounts` is computed once per result (`useMemo` on `checked`).
- **Canvas:** the node memo in `GraphCanvas` gained `issueCounts` as a dependency. A new result rebuilds all node objects, which is the existing behaviour already marked with a `ponytail:` comment for Phase 24.
- **Node list:** `nodeName` does a linear search per issue item, O(issues × nodes). Acceptable for graph sizes so far; not measured.
- **Payloads:** validation responses are unbounded in the number of issues (backend behaviour, unchanged).
- **Bundle:** `EditorPage` chunk is 229.65 kB (72.93 kB gzip) in the final build. The Phase 10 size was not recorded in this session.
- **Measured (test environment only):** cold import of the editor module in jsdom takes 601 ms alone and 1085 ms under a full parallel Vitest run. This is not a browser measurement; production load time was not measured.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` (API section after the V1 endpoint list) | New contract behaviour | `expected_revision` on `/validate` and `/publish`; 409 semantics; publish advances the draft revision, so clients reload |
| `frontend/README.md` (new section "Validate and publish") | New feature | Workflow rules, outdated results, grouping and navigation, what is not locatable, no backend warnings, publish confirmation, failure handling, reload after publish, test locations |
| `backend/tests/conftest.py` (comment) | Non-obvious test setup | Why fixtures load as `pytest_plugins` |
| Comments in `EditorPage.test.tsx`, `NodeInspector.test.tsx`, `routes.test.tsx` and `tests/e2e/dashboard.spec.ts` | Non-obvious test timing | Why the editor module is preloaded; why the outage test waits for the heading |

Checked: no other document (`backend/README.md`, `backend/AGENTS.md`, `docs/*.md`, `README.md`) refers to the renamed conftest files.

**Should be updated later:**
- Roadmap §17 "Warnings", once the backend decides on a warning severity.

---

## 18. Dependencies / Infrastructure Changes

- No dependency, version, environment-variable, Docker, database or Redis changes.
- **Test infrastructure:** backend package fixtures moved to plugin modules (`tests/integration/fixtures.py`, `tests/nodes/fixtures.py`), loaded from `tests/conftest.py`. This makes all integration fixtures visible to every backend test.
  - They are not autouse, so tests that don't request them are unaffected.
  - `testcontainers` was already imported by the root conftest through `tests.support`.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion? |
|---|---|---|---|
| Validation warnings | The backend has no severity; all issues block publish | Needs a backend decision; no phase assigned | No |
| Navigating to trigger / dependency issues | The editor has no trigger or dependency UI | Phase 18 (Triggers UI); dependencies unassigned | No |
| Version list and details after publish | Out of scope | Phase 12 | No |
| Config-field-level location for `invalid_config` | The backend puts the field path in the message text, not in a structured field; the inspector already shows per-field results from `/nodes/{type}/config/validate` (Phase 09) | Needs a backend contract change; not assigned | No |

---

## 20. Known Limitations

- Validation runs only on the saved draft. Validate does not save first, so the user must save (or wait 1.5 s for autosave) before validating.
- Undo/redo history is cleared by publishing, because the draft reloads.
- Trigger and project-wide issues are listed but cannot be navigated to.
- The success notice links to the project page, not to the version (no version page until Phase 12).
- The version number in the confirmation (`current_version + 1`) comes from the loaded project. If another user publishes concurrently, the backend's revision pin turns that into a 409, so a wrong number cannot be frozen. The dialog text itself is not re-checked.
- All issues are called "errors"; there are no warnings.

---

## 21. Open Issues

1. **Revision bump on publish** is now documented, but the backend still models publish as a project save.
   - **Impact:** every client must reload after publish.
   - **Severity:** LOW.
   - **Recommended action:** none required; revisit only if the reload becomes a UX problem.

---

## 22. Architectural / Product Decisions

- **Results belong to a revision.** A validation result is only meaningful together with the draft revision it checked. The UI never presents an older result as current; it labels it "Outdated".
- **Validate and publish are pinned.** The frontend always sends `expected_revision`. A mismatch is a stale draft, shown with a reload, never silently re-run.
- **No client-side publishability.** The frontend only gates the Publish button on the backend's answer; the backend re-validates on publish. A 422 at publish replaces the shown result with the backend's issues.
- **Publish → reload.** Because publishing advances the draft revision, the editor restarts from the server's draft. This was preferred to guessing the new revision.
- **No warnings in the UI** until the backend defines them.
- **Validation result is not server-state cache.** It lives in component state for the editor session, so it is never shown for a different session or revision.
- **Fixtures as `pytest_plugins`.** Package fixtures in the backend tests must be plugin modules, not directory conftests, so any combination of command-line paths works.
- **Preload code-split routes in component tests.** A test file that renders a lazily loaded route preloads its module in `beforeAll`, so module import time is not charged to a test's 1 s `findBy*` wait. Raising timeouts was rejected because it hides where the time goes.
- **Assert the end state, not a transient one, in e2e.** Where the UI legitimately passes through intermediate states (per-section errors before the outage collapse), wait for the final element first, then make strict assertions.

---

## 23. Repository / Git State

- **Branch:** `main`, HEAD `5ed516e` (frontend phase 10). Nothing committed for Phase 11.
- **Modified:** 4 backend source/test-infrastructure files (`application/projects.py`, `api/projects.py`, `api/project_schemas.py`, `tests/conftest.py`), 11 backend test files (one changed import line in 10 of them, plus the new test in `test_projects_api.py`), `docs/architecture.md`, `frontend/README.md`, and 12 frontend files:
  - `src/api/contract.node.test.ts`, `mutations.ts`, `openapi.json`, `projects.ts`;
  - `src/app/routes.test.tsx`;
  - `src/features/editor/EditorPage.tsx`, `EditorPage.test.tsx`, `GraphCanvas.tsx`, `NodeCard.tsx`, `NodeInspector.test.tsx`;
  - `tests/e2e/dashboard.spec.ts`, `tests/fullstack/projects.spec.ts`.
- **Renamed (staged by `git mv`):** `backend/tests/integration/conftest.py → fixtures.py`, `backend/tests/nodes/conftest.py → fixtures.py`.
- **Untracked:** `frontend/src/features/editor/{ValidationPanel.tsx, usePublishWorkflow.ts, validation.ts, validation.test.ts}`, and this report.
- **Unrelated changes:** none detected.
- **Other worktrees:** an existing worktree `.claude/worktrees/charming-tereshkova-6c6694` (branch `claude/charming-tereshkova-6c6694`) predates this phase and was not touched. Temporary worktrees for the Phase 10 baseline comparisons were created in the session scratchpad and removed (`git worktree list` shows only the two above).

---

## 24. Phase Completion Assessment

### Implemented
- Pinned backend validation.
- Validation panel with summary, grouped issues, node/port mapping, canvas and list markers, and navigation.
- Confirmed publish with failure handling.
- Published-version notice; unsaved-changes and stale-draft handling.
- Documentation.
- Fixes for the pre-existing pytest fixture-visibility defect and the pre-existing dashboard e2e race.
- Fix for the component-test flake this phase introduced.
- Test for edge-only issue navigation.

### Verified
- Backend full suite.
- Contract tests.
- Frontend unit/component suite, 13 consecutive green runs.
- Typecheck, lint, format and build.
- Mocked e2e and full-stack e2e, including the complete validate → fix → publish loop against the real backend.

### Not Implemented
- Warnings (no backend support).

### Deferred
- Trigger/dependency issue navigation (Phase 18 and unassigned).
- Version pages (Phase 12).
- Structured config-field locations.

### Open
- Only the LOW item in §21 (publish modelled as a project save).

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

- **Test conventions to keep:**
  - a component test file that renders a code-split route preloads it in `beforeAll` (see `EditorPage.test.tsx`);
  - e2e tests wait for the final element before strict assertions.
- **Available contracts:**
  - `POST /projects/{id}/validate` and `/publish` accept `{"expected_revision"}` and answer 409 when stale.
  - `/publish` returns `ProjectVersionDetail` (201) and advances the project `revision`.
  - 422 `PROJECT_VALIDATION_FAILED` carries `details.issues` in the `/validate` issue shape.
- **Frontend building blocks:**
  - `usePublishWorkflow`, `ValidationPanel`;
  - `validation.ts` (`issueScope`, `issueCounts`, `publishIssues`, `isConflict`);
  - `NodeCard` `issues` and `GraphCanvas` `issueCounts` props.
- **Cache behaviour for Phase 12:** `usePublishProject` invalidates `['projects', id]`, which includes `['projects', id, 'versions', …]`, so a version list on any page refetches after a publish.
- **Do not change casually:**
  - always pin validate and publish to the shown revision;
  - never enable Publish from local checks;
  - reload the draft after publish (or obtain the new revision from the backend);
  - keep backend package fixtures as `pytest_plugins`.
- **Prerequisites for Phase 12:** none beyond the existing `GET /projects/{id}/versions[/{n}]`. The success notice in the editor should then link to the new version page.
