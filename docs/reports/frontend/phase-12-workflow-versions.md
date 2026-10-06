# Phase 12 — Workflow Versions

Report date: 2026-10-02. Branch `main`. Base commit: `0adbc2c` (frontend phase 11). Everything described here is uncommitted in the working tree (§23).

The phase was done in two passes on the same day:
1. **First pass:** the versions UI, plus two backend additions: version authorship and a per-version execution filter.
2. **Follow-up pass:** at the user's request, four limitations reported after the first pass were resolved:
   - usernames instead of user ids;
   - a read-only graph canvas;
   - comparison with the draft;
   - restore/clone of a version.

This report describes the final state. Where a first-pass decision was later revised, it says so.

## 1. Executive Summary

**Supposed to achieve.** Roadmap §18 (`docs/roadmap-frontend.md`): manage versions transparently. Scope:
- version list, version details, published version, version metadata;
- created at, author;
- node/graph snapshot;
- a version-comparison foundation;
- restore/clone as a new draft "sofern Backend unterstützt".

The session prompt added:
- version selection;
- version-aware execution navigation;
- explicit immutability;
- tests for loading, empty versions, selection, navigation, API failure and immutable presentation;
- implementing, testing and documenting any backend API that was missing.

**Actually implemented.**
- **Backend:** five capabilities.
  - Version authorship: `published_by`, migration `0011_version_author`, returned as `{id, username}`.
  - `GET /workflows/{id}/executions?version=n`.
  - `POST /workflows/{id}/versions/{n}/restore`.
  - `POST /workflows/{id}/duplicate` with an optional `version`.
  - `WorkflowVersion.to_draft()`, the domain copy used by restore and clone.
- **Frontend** (new feature module `frontend/src/features/versions/`):
  - a versions list page with the draft shown separately;
  - a version detail page with:
    - immutability badges and a read-only notice;
    - metadata;
    - a frozen React Flow canvas of the snapshot, plus text tables;
    - comparison with any other version or the current draft;
    - restore-to-draft and clone-as-workflow actions;
    - the paginated executions of that version.
  - version links from the workflow page and from the dashboard's execution tables.

**Resulting state.**
- A user can see every published version of a workflow: number, publish time, author, description, Latest marker.
- They can open a version's frozen snapshot, compare it, copy it into the draft or into a new workflow, and see which executions ran it.
- Every execution shown on the dashboard links to the version it used.
- All quality gates pass on the current tree (§13). That includes the full-stack Playwright suite against the real backend.

**Acceptance criteria.** All three roadmap criteria pass (§14). Some limitations remain:
- versions are listed oldest first;
- files are compared by path and size only;
- there is no execution detail page to link to yet (Phase 13);
- `VersionDetailPage.tsx` is 715 lines and should be split (§9, finding 4).

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §18:**
- **Ziel:** "Versionen transparent verwalten."
- **Scope:** Version List, Version Details, Published Version, Version Metadata, Created At, Author, Node/Graph Snapshot, Version Comparison Foundation, Restore/Clone als neuer Draft (sofern Backend unterstützt).
- **Acceptance criteria:**
  - Versionen sind unveränderlich dargestellt.
  - Aktive Version ist erkennbar.
  - Draft und Published Version sind klar getrennt.
- **Prompt constraints:** "Eine veröffentlichte Version darf im Frontend nicht als editierbarer Draft behandelt werden." "Implementiere Restore/Clone nur, wenn das Backend dies unterstützt." "STOP nach Phase 12."

**Session prompt additions:**
- "version-aware execution navigation";
- "Users can understand which Workflow Version exists and which version an Execution uses";
- "If any function or API is missing on the backend, implement it. It must then be properly tested and documented."

**Not responsible for:**
- the execution UI: lists, detail, starting or cancelling runs (Phase 13);
- live updates (Phase 14);
- authentication UI (Phase 21);
- any change to publish semantics (Phase 11).

---

## 3. Scope Implemented

### Domain (backend)
- `WorkflowVersion.published_by: str | None`. It defaults to `None` and is set by `WorkflowDraft.publish(published_by=)` and `Workflow.publish_draft(published_by=)`.
- `WorkflowVersion.to_draft() -> WorkflowDraft`: an independent, editable copy (deep-copied graph and triggers, copied lists and file dict).

### Application (backend)
- `WorkflowService.publish_workflow(..., published_by=)` records the author.
- `WorkflowService.restore_version(workflow_id, version, *, expected_revision)` does the following in one transaction:
  1. checks the revision;
  2. loads the version;
  3. calls `workflow.replace_draft(snapshot.to_draft(), name=workflow.name)`;
  4. saves.
- `WorkflowService.duplicate_workflow(..., version=None)` copies `to_draft()` of the given version instead of the current draft.
- `WorkflowService.usernames(user_ids) -> dict[str, str]` resolves display names for version authors.
- The module-level helper `load_version(uow, workflow_id, version)` raises `WorkflowVersionNotFoundError`. `get_version` now uses it.
- `ExecutionService.list_executions(workflow_id, *, version=None, ...)`.

### Persistence (backend)
- New column `workflow_versions.published_by` (nullable FK → `users.id`, `RESTRICT`), via migration `0011_version_author`.
- `SqlAlchemyWorkflowVersionRepository.save/_to_domain` write and read `published_by`.
- `ExecutionRepository.list_summaries(workflow_version=)`: the protocol in `application/persistence.py` and `SqlAlchemyExecutionRepository`.

### API (backend)
- New DTOs: `AuthorOut`, `RestoreVersionRequest`. `DuplicateWorkflowRequest.version`.
- `WorkflowVersionSummary/Detail.published_by: AuthorOut | None`.
- The routes changed or added are listed in §7.

### Frontend / UI
- `features/versions/VersionsPage.tsx`: route `/workflows/:workflowId/versions`.
- `features/versions/VersionDetailPage.tsx`: route `/workflows/:workflowId/versions/:version`.
- `features/versions/SnapshotCanvas.tsx`: the read-only canvas.
- `features/versions/compare.ts`: the pure snapshot diff.
- `features/versions/shared.tsx`: `ImmutableBadge`, `LatestBadge`, `Time`, `Author`, `VersionLink`.
- `features/versions/paths.ts`: `versionsPath`, `versionPath`. They are kept separate so fast refresh keeps working.
- `WorkflowDetailPage.tsx`: a "Versions" link; the latest version number links to its page.
- `dashboard/sections.tsx`: the Version column of execution tables now renders `VersionLink`.
- `components/ui/icons.tsx`: `LockIcon`, exported from `components/ui/index.ts`.
- `app/routes.tsx`: two lazy routes.

### API layer (frontend)
- `api/workflows.ts`:
  - `published_by` schema `{id, username|null} | null`;
  - `duplicateWorkflow(..., version?)`;
  - new `restoreWorkflowVersion`.
- `api/executions.ts`: `WorkflowExecutionListParams { version? }` for `listWorkflowExecutions`.
- `api/queries.ts`: `workflows.executionList` key and `workflowQueries.executions` take those params.
- `api/mutations.ts`: `useDuplicateWorkflow` accepts `version`; new `useRestoreWorkflowVersion`, which sets the workflow detail cache from the response and invalidates workflow lists.
- `api/openapi.json`: regenerated from the backend.

### Testing
See §12.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/migrations/versions/0011_version_author.py` | Created | `published_by` column + FK |
| `backend/src/akasha/domain/workflow.py` | Modified | `published_by`, `to_draft()` |
| `backend/src/akasha/application/workflows.py` | Modified | author on publish, `restore_version`, `duplicate_workflow(version=)`, `usernames`, `load_version` |
| `backend/src/akasha/application/executions.py` | Modified | `list_executions(version=)` |
| `backend/src/akasha/application/persistence.py` | Modified | `list_summaries(workflow_version=)` protocol |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | `published_by` column |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | read/write `published_by`; version filter in SQL |
| `backend/src/akasha/api/workflow_schemas.py` | Modified | `AuthorOut`, `RestoreVersionRequest`, `DuplicateWorkflowRequest.version`, author in version DTOs |
| `backend/src/akasha/api/workflows.py` | Modified | author on publish/get/list; restore route; duplicate with version |
| `backend/src/akasha/api/executions.py` | Modified | `version` query param |
| `backend/tests/integration/test_workflows_api.py` | Modified | author, restore, restore errors, clone-from-version |
| `backend/tests/integration/test_executions_api.py` | Modified | `test_list_executions_filter_by_version` |
| `backend/tests/integration/test_persistence.py` | Modified | `_HEAD` → 0011; `test_0011_upgrade_leaves_existing_versions_without_author` |
| `backend/tests/integration/test_execution_persistence.py` | Modified | 0009 upgrade test writes fixture rows with raw SQL (§10, problem 1) |
| `backend/tests/application/test_trigger_service.py` | Modified | fake repository matches the extended protocol |
| `frontend/src/features/versions/` | Created (dir) | 8 files, see §3 |
| `frontend/src/features/versions/VersionsPage.test.tsx` | Created | 21 component tests |
| `frontend/src/features/versions/compare.test.ts` | Created | 2 unit tests |
| `frontend/src/api/workflows.ts`, `executions.ts`, `queries.ts`, `mutations.ts` | Modified | contracts and hooks (§3) |
| `frontend/src/api/openapi.json` | Modified (generated) | regenerated via `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py` |
| `frontend/src/api/contract.node.test.ts` | Modified | calls `restoreWorkflowVersion`, `duplicateWorkflow(..., 1)`, `listWorkflowExecutions(..., {version})` |
| `frontend/src/api/serverState.test.tsx`, `features/editor/EditorPage.test.tsx` | Modified | version fixtures carry `published_by: null` (new required wire field) |
| `frontend/src/app/routes.tsx` | Modified | two routes |
| `frontend/src/features/workflows/WorkflowDetailPage.tsx` | Modified | Versions link, latest version link |
| `frontend/src/features/dashboard/sections.tsx` | Modified | version cell links |
| `frontend/src/components/ui/icons.tsx`, `index.ts` | Modified | `LockIcon` |
| `frontend/tests/fullstack/workflows.spec.ts` | Modified | `publishHelloWorld` helper (extracted from an existing test); new versions test |
| `docs/architecture.md` | Modified | API list; authorship; restore/clone |
| `frontend/README.md` | Modified | new "Workflow versions" section |
| `docs/reports/frontend/phase-12-workflow-versions.md` | Created | this report |

No files deleted. No configuration or dependency files changed.

---

## 5. Architecture Changes

### 5.1 Version authorship is recorded at publish time

- **Decision:** a version stores the publishing user's id (`published_by`). The API resolves it to `{id, username}`.
- **Context:**
  - The roadmap scope lists "Author".
  - Nothing recorded who published.
  - The first pass returned only the id, which the follow-up was asked to fix.
- **Chosen approach:**
  - nullable FK column with `RESTRICT`;
  - the domain field is set from the authenticated identity in the API handler;
  - `WorkflowService.usernames` resolves names for display.
- **Alternatives considered:**
  - infer the author from `workflows.owner_id` (rejected: owner ≠ publisher, and history would be invented);
  - store the username on the version row (rejected: it would duplicate user data);
  - join `users` inside the version repository (rejected: it couples the version aggregate's persistence to identity).
- **Reason:** users are disabled, never deleted (`application/auth.py` has no delete), so an id reference stays valid. Resolution at read time stays correct.
- **Consequences:**
  - Versions published before 0011, and CLI publishes, have `published_by = null`. They are not backfilled.
  - Any future user-deletion feature must handle this FK.

### 5.2 Versions are copied, never edited (restore / clone)

- **Decision:** `WorkflowVersion.to_draft()` is the only path from a version back to editable state. Both restore and clone go through `Workflow.replace_draft`.
- **Context:** the roadmap item "Restore/Clone als neuer Draft" had no backend support. The prompt required implementing missing backend APIs.
- **Chosen approach:**
  - **Restore:** `POST .../versions/{n}/restore` with a required `expected_revision`. It replaces the draft and keeps the workflow name; publishing it creates the next version number.
  - **Clone:** `POST .../duplicate` with `{"version": n}` creates a new, unpublished workflow.
- **Alternatives considered:**
  - make restore a frontend-only "load version into editor, then save" (rejected: it makes the frontend construct domain state, and it races with other writers);
  - a separate `/versions/{n}/clone` endpoint (rejected: `duplicate` already has the exact semantics apart from the source).
- **Reason:**
  - one domain copy function;
  - all draft rules (secret detection, storability, file validation) apply because `replace_draft` is reused;
  - optimistic concurrency is consistent with `PUT /workflows/{id}`.
- **Consequences:**
  - Restore is a draft write and advances `revision`.
  - Clients must send the revision they show.
  - Versions remain insert-only (`WorkflowVersionRepository` still has no update or delete).

### 5.3 A separate read-only canvas instead of a read-only mode in `GraphCanvas`

- **Decision:** `features/versions/SnapshotCanvas.tsx` renders the snapshot with React Flow. Interaction is off: `nodesDraggable`, `nodesConnectable`, `elementsSelectable` and `edgesReconnectable` are all false, and `deleteKeyCode` is null.
- **Context:** `GraphCanvas` is controlled by editor state. Selection, move, connect, drop and reconnect callbacks are required props.
- **Chosen approach:** reuse the presentation pieces:
  - `NodeCard`;
  - `portsOf`, `nodeTitle`, `edgeKey` (`graphView.ts`);
  - `placeUnpositioned` (`editorState.ts`);
  - `MIN_ZOOM`/`MAX_ZOOM` and the `draft-canvas` CSS.

  The editor's interaction logic is not reused.
- **Alternatives considered:** adding a `readOnly` prop to `GraphCanvas`. Rejected: every editor callback would become optional, which weakens the editor's types for one consumer.
- **Consequences:** two canvases share card rendering. Visual changes to `NodeCard` apply to both, but interaction changes don't.

### 5.4 Version comparison is presentation, not domain logic

- **Decision:** `compare.ts` diffs two snapshots (version ↔ version, or version ↔ the current draft) in the browser.
- **Reason:** both inputs come from the backend (`GET /versions/{n}`, `GET /workflows/{id}`). The diff decides nothing (AGENTS §4). A backend diff endpoint would add an API without a consumer that needs server-side computation.
- **Consequences:**
  - The diff ignores node positions.
  - Files compare by path and size, because file contents are not served by the API.

Architecture documentation was updated accordingly (§17). No ADR was created. These are local decisions inside the documented Draft → Publish → immutable Version model, not changes to it.

---

## 6. Domain / Data Model Changes

- **`WorkflowVersion`** (frozen dataclass):
  - new field `published_by: str | None = None`;
  - new method `to_draft()`;
  - existing fields unchanged.
- **`WorkflowDraft.publish` / `Workflow.publish_draft`:** new keyword `published_by`.
- **Database:** `workflow_versions.published_by VARCHAR NULL`, FK `fk_workflow_versions_published_by` → `users.id ON DELETE RESTRICT`. No index: lookups by author don't exist.
- **Migration:** `0011_version_author` (revises `0010_execution_heartbeat`). Downgrade drops the constraint and then the column.
- **Compatibility:**
  - Existing rows keep NULL, verified by `test_0011_upgrade_leaves_existing_versions_without_author`.
  - Backup/restore (`infrastructure/backup.py`) iterates `metadata.sorted_tables`, so it includes the column automatically. The backup tests are part of the passing suite.
- **Unchanged:** no new entities, enums or state transitions. Execution data is unchanged; only the listing query gained a filter.

---

## 7. API / Contract Changes

| Endpoint | Change | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/v1/workflows/{id}/publish` | Response `published_by` is `{id, username}` | unchanged | `WorkflowVersionDetail` | unchanged |
| `GET /api/v1/workflows/{id}/versions` | Items carry `published_by` | unchanged | `WorkflowVersionPage` | unchanged |
| `GET /api/v1/workflows/{id}/versions/{n}` | Carries `published_by` | unchanged | `WorkflowVersionDetail` | unchanged |
| `POST /api/v1/workflows/{id}/versions/{n}/restore` | **New** | `{"expected_revision": int ≥ 1}`, extra fields forbidden | `WorkflowDetail` (200) | 404 `WORKFLOW_NOT_FOUND` / `WORKFLOW_VERSION_NOT_FOUND`, 409 `CONCURRENT_MODIFICATION`, 422 body validation |
| `POST /api/v1/workflows/{id}/duplicate` | Optional `version` | `{"name", "version"?: int ≥ 1}` | `WorkflowDetail` (201) | +404 `WORKFLOW_VERSION_NOT_FOUND`, 422 for `version: 0` |
| `GET /api/v1/workflows/{id}/executions` | Optional `version` query (`ge=1`) | `?version=n&offset&limit` | `ExecutionPage`, newest first | 422 for `version=0`; an unpublished version gives an empty page |

**`published_by` shape:** `null | {"id": str, "username": str | null}`. `username` is null only if the account cannot be found.

**Compatibility:**
- `published_by` is a new field on version responses. In the first pass it was a string; it is now an object. Both shapes existed only in this uncommitted working tree, so no released contract changed.
- All other additions are additive.
- The OpenAPI snapshot is regenerated. `backend/tests/test_openapi_contract.py` and `frontend/src/api/contract.node.test.ts` both pass.

**Unchanged contracts:** no WebSocket, event, node, plugin or SDK contract changed. Restore and clone emit no domain events. This is consistent with `update_draft` and `duplicate_workflow`, which emit none either.

---

## 8. Implementation Details

### Backend
- **Restore transaction** (`WorkflowService.restore_version`): one unit of work.
  1. `load_workflow`;
  2. `_check_revision` (409 before any read of the version);
  3. `load_version`;
  4. `replace_draft`;
  5. `workflows.save`, which is the CAS on `revision`, so a concurrent write in between still fails with 409;
  6. commit.

  It logs `workflow_version_restored`.
- **Clone** (`duplicate_workflow`):
  - the source workflow is loaded first (404 for an unknown workflow);
  - with `version`, the version is then loaded (404 for an unknown version);
  - it logs `workflow_duplicated` with `source_version`.
- **Author resolution:**
  - `usernames()` performs one `users.get` per distinct non-null id, in sorted order;
  - `list_versions` resolves at most one id per distinct author on the page (≤ 100 rows);
  - `publish` uses the caller's identity directly, with no lookup.
- **Version filter:** `WHERE executions.workflow_version = :v`, added only when given. Ordering is unchanged (`created_at DESC, id DESC`).

### Frontend
- **Server state:** all data comes through TanStack Query (`workflowQueries.detail`, `.versions`, `.version`, `.executions`). Versions use `staleTime: Infinity` (immutable, set in phase 04). No server state is copied into local state.
- **Local UI state:**
  - list and execution page offsets (`useState`);
  - comparison target (`number | 'draft' | null`; it defaults to the previous version, or the draft for v1);
  - the open dialog.

  `VersionDetail` is keyed by `workflowId/version`, so navigating between versions resets this state.
- **Route parameter:** only canonical positive integers are versions (`String(Number(p)) === p`). `0`, `02` and `abc` render "Version not found" without a request.
- **Restore UX:**
  - the confirmation names the replaced draft revision (`workflow.revision`);
  - the mutation sends that revision;
  - on 409 the workflow query is invalidated, so the dialog shows the new revision for a retry;
  - on success the workflow cache is set from the response, a toast is shown, and the app navigates to the editor.
- **Clone UX:** `WorkflowFormDialog` with the default name `"<workflow> (vN)"`. On success the app navigates to the new workflow.
- **Canvas:**
  - `SnapshotCanvas` names cards with the current node catalog (`nodeQueries.catalog`);
  - unknown types are marked "Unknown node type" only after the catalog has loaded;
  - versions without layout are positioned by `placeUnpositioned`;
  - the wrapper is `role="figure"`, `aria-label="Published graph (read-only)"`.
- **Snapshot tables:** they show node ids, labels, types and the *names* of configured fields. They never show config values (credential references or other user data stay off this page).
- **Error states:**
  - every query region uses `QuerySection` (loading / error + retry / stale data warning);
  - a 404 workflow or version renders a dedicated `h1` error state.

---

## 9. Findings

### Finding 1 — A migration test depended on today's repository code
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** `test_upgrade_from_0009_makes_orphaned_running_rows_recoverable` wrote its workflow/version fixture with current repositories against a 0009 schema. Any new column on those tables breaks it. It now writes them with raw SQL in 0009's columns (§10).

### Finding 2 — Authorship was never recorded
- **Impact:** MEDIUM
- **Resolution:** FIXED, prospectively.
- **Notes:** versions published before migration 0011 have no author and show "Not recorded". The value is not reconstructible.

### Finding 3 — The version list query loads full snapshot rows
- **Impact:** LOW
- **Resolution:** OPEN (pre-existing)
- **Notes:** `SqlAlchemyWorkflowVersionRepository.list` selects whole rows (graph, files JSONB) to build summaries. This is harmless at current sizes but scales with snapshot size × page size.

### Finding 4 — `VersionDetailPage.tsx` is 715 lines
- **Impact:** LOW (maintainability)
- **Resolution:** OPEN
- **Notes:** it holds the metadata, snapshot tables, comparison, executions, actions and page components. It is cohesive but large; AGENTS §121 asks to split by responsibility. Recommended split: `VersionComparison.tsx`, `VersionActions.tsx`, `VersionExecutions.tsx`.

### Finding 5 — Sharing React Flow between two routes changed the bundle split
- **Impact:** LOW (positive)
- **Resolution:** n/a
- **Notes:** Vite now emits React Flow and `GraphCanvas` as a shared chunk `GraphCanvas-*.js` (197.36 kB). `EditorPage-*.js` shrank to 32.65 kB; it was 229.65 kB at phase 11's build. Total download for the editor route is unchanged.

---

## 10. Bugs / Problems Found

**1. Fragile migration test**
- **Category:** pre-existing defect, surfaced by this phase.
- **Problem:** after the 0011 column was added, `tests/integration/test_execution_persistence.py::test_upgrade_from_0009_makes_orphaned_running_rows_recoverable` failed with `UndefinedColumnError: column "published_by" of relation "workflow_versions" does not exist`.
- **Root cause:** the fixture used `SqlAlchemyWorkflowVersionRepository.save` (current schema) on a database migrated only to 0009.
- **Fix:** the workflow and version rows are inserted with raw SQL (`serialize_graph` for the graph), like the execution rows the test already wrote that way.
- **Regression test:** the test itself. It passes and stays independent of future columns on `workflows` and `workflow_versions`.
- **Affected area:** backend migration tests.

**2. Swapped stubs in a new component test**
- **Category:** introduced and fixed during this phase.
- **Problem:** "lists only the executions of this version" stubbed the wrong version as pending.
- **Fix:** v1 is pending (the comparison target); v2 is loaded (the page).

**3. Over-broad full-stack selector**
- **Category:** introduced and fixed during this phase.
- **Problem:** `graph.getByRole('group')` counted 5. React Flow's controls are also groups, and hello-world has 3 nodes.
- **Fix:** the selector now matches node labels: `/\(core\.\w+\)$/`.

**4. Test fixtures missing a new required field**
- **Category:** introduced and fixed during this phase.
- **Problem:** version fixtures in `serverState.test.tsx` and `EditorPage.test.tsx` failed zod parsing after `published_by` became part of the contract.
- **Fix:** the fixtures now send `published_by: null`, mirroring the backend wire format. Assertions are unchanged.

**5. Double padding in cards**
- **Category:** introduced and fixed during this phase.
- **Problem:** first-pass markup double-padded cards (`Card` already pads its body).
- **Fix:** removed the extra padding.

No bugs in previously shipped product code were found.

---

## 11. Fixes Applied

| Symptom | Root cause | Implementation | Verification |
|---|---|---|---|
| 0009 upgrade test failed after 0011 | fixture written with current repositories | raw SQL fixture rows | test passes; full backend suite passes |
| 3 frontend tests failed on schema parse | fixtures predate `published_by` | fixtures updated to the wire contract | `vitest run`: 323 passed |
| ESLint `react-refresh/only-export-components` in `shared.tsx` | path helpers exported next to components | moved to `paths.ts` | `eslint .` passes |
| ESLint `no-non-null-assertion` in test | `first!`/`second!` row access | explicit guard | `eslint .` passes |
| Restore/clone tests saw the old path | navigation after a lazy route load is async | `waitFor` on `router.state.location` | 3 consecutive full runs green |

---

## 12. Testing & Verification

### Unit tests
- `features/versions/compare.test.ts` (2 tests):
  - added, removed and changed nodes, connections, triggers, dependencies;
  - a move without other changes is not a change;
  - unchanged files;
  - identical snapshots.
- **Result:** pass.

### Component tests (Vitest + React Testing Library, real router/QueryClient/ApiClient, stubbed `fetch`)
`features/versions/VersionsPage.test.tsx`, 21 tests:
- **List:**
  - metadata, Latest marker, author username with id tooltip, "Not recorded";
  - draft card separated, with its editor link and revision;
  - Immutable badge and no edit controls;
  - empty state;
  - pagination (`offset`/`limit` requests);
  - failed load + retry;
  - workflow 404.
- **Detail:**
  - read-only presentation: heading, badges, notice, metadata;
  - frozen canvas: a node card is present, and its `.react-flow__node` lacks `draggable`/`selectable`;
  - tables hide config values;
  - no text inputs or save/publish/delete buttons;
  - comparison with versions: the previous one by default, switching to a later one;
  - comparison with the draft (the default for v1), including files;
  - executions filtered by version: asserts the `version=2` request param;
  - "Not run yet";
  - version not found for `9`, `0`, `abc`, `02`;
  - failed version load.
- **Restore and clone:**
  - restore sends `{expected_revision: 5}`, toasts, and navigates to the editor;
  - restore conflict shows the message, then the reloaded revision 7;
  - clone sends `{name: "Lead scoring (v2)", version: 2}` and navigates to the new workflow.
- **Navigation:** workflow → Versions → v1 (and the latest link); dashboard execution → version page.
- **Result:** 23 passed in `src/features/versions` (21 + 2). The whole suite has 323 tests in 26 files, and it passed 3 consecutive times after the final change.

### Integration / API tests (real PostgreSQL via Testcontainers, real HTTP stack)
- **`test_workflows_api.py`:**
  - `published_by == {"id": owner_id, "username": "admin"}` on publish and in the list;
  - `test_duplicate_a_published_version`: copy equals the v1 graph, not the emptied draft; 404 unknown version; 422 for `version: 0`;
  - `test_restore_version_replaces_only_the_draft`:
    - the draft equals v1 and the revision advances;
    - a later edit doesn't touch v1;
    - restoring again and publishing gives version 2;
  - `test_restore_version_errors`: 409 stale revision (draft unchanged), 404 version, 404 workflow, 422 for three malformed bodies.
- **`test_executions_api.py::test_list_executions_filter_by_version`:**
  - v1 vs v2 runs;
  - pagination with `has_more`;
  - unknown version → empty page;
  - `version=0` → 422.
- **Result:** pass.

### Infrastructure / migration tests
- `test_persistence.py::test_0011_upgrade_leaves_existing_versions_without_author`:
  - a 0010 schema with an existing version is upgraded to head;
  - `published_by` is None;
  - saving a version with an unknown author raises `IntegrityError` (the FK).
- The head check was moved to `0011_version_author`.
- The repaired 0009 upgrade test passes.
- **Result:** pass.

### E2E tests
- **Stubbed** (`npm run test:e2e`): 23 passed. No new specs; this is a regression run.
- **Full-stack** (`npm run test:fullstack`: production bundle + real backend on a fresh database): 10 passed. The new test "versions are listed, inspected, compared and show their runs" does the following:
  1. publishes hello-world twice and runs v1;
  2. checks the list (3 rows, Latest, author `e2e-admin`);
  3. opens v1: read-only notice, its execution id, 3 canvas node cards;
  4. compares with v2 (1 changed node);
  5. confirms v2 shows "Not run yet";
  6. clones v1, then verifies via the API that the clone is unpublished and lacks the v2 label;
  7. compares v1 with the draft;
  8. restores v1 and lands in the editor, then verifies via the API that `current_version` is still 2 and the draft is v1.

### Security tests
- **Restore:** the stale-revision refusal is tested (409, draft untouched).
- **Restore and clone:** both reuse `replace_draft`. Secret detection and storability rules are already covered by existing draft tests; this phase adds no new test for them on the restore path, because a published version already passed those rules.
- **Snapshot page:** the test that config values are not rendered.

### Accessibility
- **Covered implicitly by role and name queries:**
  - tables have captions;
  - the canvas is a named figure;
  - the comparison result is a named region;
  - dialogs are named;
  - badges carry text, not only color.
- **Not run:** no automated accessibility audit (axe) and no screen-reader testing.

### Performance tests
None performed.

---

## 13. Quality Gates

All re-run on the current tree for this report.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | backend `ruff format --check` (196 files); frontend `prettier --check .` |
| Linter | PASS | backend `ruff check`; frontend `eslint .` (earlier failures in §11 fixed) |
| Type Checker | PASS | backend `mypy` (191 files); frontend `tsc -b` |
| Unit + Component Tests | PASS | `vitest run`: 26 files, 323 tests |
| Integration / API / Migration Tests | PASS | backend `pytest`: 1915 passed, 6 skipped (1 pre-existing `StarletteDeprecationWarning`) |
| E2E (stubbed) | PASS | 23 passed |
| E2E (full-stack) | PASS | 10 passed |
| Build | PASS | `npm run build` |
| OpenAPI contract | PASS | snapshot regenerated; both contract tests pass |
| Security Checks | N/A | no separate security scanner run |
| Accessibility | N/A | no automated audit run |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Versionen sind unveränderlich dargestellt | PASS | "Immutable" badge with lock icon and text on the list and on every version page; "Published versions are read-only" notice; canvas with interaction disabled (test asserts nodes not `draggable`/`selectable`); no inputs or save/publish/delete controls (component test). Restore and clone copy the version and never change it (API test: v1 unchanged after restore, a later publish is v2). Backend has no version update/delete route (`test_published_versions_cannot_be_modified`, pre-existing). |
| Aktive Version ist erkennbar | PASS | "Latest" badge on `current_version` in the list and on the version page; "v1 (latest is v2)" in metadata; the workflow page links its latest version (component and full-stack tests). Note: "active" is interpreted as the latest published version, the one triggers resolve against (architecture §125). There is no separate "active version" concept in the domain. |
| Draft und Published Version sind klar getrennt | PASS | Separate "Draft" card (revision, editor link) above "Published versions"; restore is an explicit, confirmed copy into the draft; comparison labels "the current draft (revision n)" distinctly; the version page never opens the editor on the version. Component and full-stack tests. |

**Additional scope items:**

| Item | Status | Evidence |
|---|---|---|
| Version List / Details / Metadata / Created At | PASS | list and detail pages, tests |
| Author | PASS | username via `published_by` (API + UI tests); "Not recorded" for pre-0011 versions |
| Node/Graph Snapshot | PASS | `SnapshotCanvas` + tables |
| Version Comparison Foundation | PASS | `compare.ts`, version ↔ version and ↔ draft |
| Restore/Clone als neuer Draft | PASS | backend endpoints + UI; API, component and full-stack tests |
| Version-aware execution navigation | PASS, with limitation | executions per version on the version page; dashboard version links. Execution rows are not links: there is no execution detail page until Phase 13. |

---

## 15. Security Considerations

- **Authorization:** the new and changed routes all have `{workflow_id}` in the path:
  - `/versions/{n}/restore`;
  - `/duplicate`;
  - `/executions?version=`.

  The v1 router's existing path authorization (`akasha.api.auth`) applies unchanged. No new authorization code, and no new authorization tests.
- **Username exposure:** anyone who can see a version can see its publisher's username. For members (who see only workflows they own), the author is themselves or an admin. This is documented in `docs/architecture.md`. Usernames are not secret; no other user data is exposed.
- **Secrets:** restore and clone go through `Workflow.replace_draft`, so the plaintext-secret check, storability check and file validation apply to the copied content. The version page shows config *field names* only, never values.
- **Input validation:** request bodies forbid extra fields; `expected_revision ≥ 1`; `version ≥ 1` (path, body and query). Route parameters on the frontend are checked before any request.
- **Concurrency:** restore requires `expected_revision`, so it cannot overwrite unseen draft work.
- **Not applicable:** no new HTML rendering (descriptions render as plain text), no new external calls, no new logging of payloads. The log events carry ids and version numbers only.

---

## 16. Performance Considerations

- **Version list:** loads full version rows server-side (§9, finding 3). Author resolution adds ≤ 1 query per distinct author on a page.
- **Version detail:** fetches the workflow, the version, the node catalog (cached for 5 min, `INSTALLATION_STALE_TIME_MS`), one execution page (20 rows) and, for comparison, one other version. Versions are cached forever (`staleTime: Infinity`).
- **Executions by version:** filters on `workflow_id` and `workflow_version`. No new index; existing workflow-level ordering applies. No measurements were taken.
- **Canvas:** React Flow renders all nodes; there is no virtualization. Node objects are memoized per graph and catalog.
- **Bundle:** see §9, finding 5. `VersionDetailPage` is 13.19 kB and `VersionsPage` 3.33 kB; React Flow sits in a shared chunk.
- **Not done:** no performance tests or measurements were run.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` | new and changed API, persistence | API list: `executions?version=`, `versions/{n}/restore`; a paragraph on authorship (0011, null semantics, `{id, username}`, visibility) and on version copying (`to_draft`, restore, clone) |
| `frontend/README.md` | new feature | "Workflow versions" section: routes, draft/published separation, canvas, comparison, restore/clone, author display, tests |
| `docs/reports/frontend/phase-12-workflow-versions.md` | phase report | this file |

**Should be updated later:**
- the frontend README "Executions" area, once Phase 13 links execution rows;
- `docs/architecture.md` §6.3, which lists `WorkflowVersion` fields but not `published_by`. The authorship paragraph is under §22 (API) instead.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded (`package.json`, `pyproject.toml` and lockfiles unchanged).
- **Database:** migration `0011_version_author` (§6).
- **Configuration and environment:** no changes.
- **Docker, Redis:** no changes.
- **Test infrastructure:**
  - the fullstack spec gained a `publishHelloWorld` helper, extracted from the existing "a published workflow cannot be deleted" test, which now uses it;
  - the backend fake `ExecutionRepository` in `test_trigger_service.py` gained the new parameter.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion |
|---|---|---|---|
| Execution detail page; linking execution rows on the version page | Execution UI is Phase 13 scope | 13 | No |
| Starting a run from a version page | Execution UI | 13 | No |
| Splitting `VersionDetailPage.tsx` | Maintainability, not behavior | next touch of the module | No |
| Newest-first version listing | Needs a backend sort parameter; current order is the backend's | when the list grows long, or Phase 24 | No |

---

## 20. Known Limitations

- **List order:** versions are listed oldest first. With more than 50 versions, the newest are on later pages.
- **File comparison:** by path and size only. Same-size content changes are not detected, because the API does not serve file contents.
- **Comparison skips descriptions:** version descriptions are not part of the comparison (shown in metadata only).
- **Canvas uses today's catalog:** names and ports come from the current node catalog, not from the catalog at publish time. A node type removed since publish shows as "Unknown node type".
- **Version page executions are not links:** no detail page exists yet.
- **Old versions have no author:** pre-0011 versions show "Not recorded". CLI publishes record no author.
- **Restore and clone emit no domain event:** consistent with draft updates and duplicates today.
- **No automated accessibility audit** was run.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Next action | Target |
|---|---|---|---|---|---|
| Version list loads full snapshot rows | payload/DB cost grows with graph size | LOW | backend persistence | select summary columns only in `list` | Phase 24 (performance) |
| `VersionDetailPage.tsx` at 715 lines | maintainability (AGENTS §121) | LOW | frontend | split into comparison, actions and executions components | next change to the module |
| Execution rows not navigable from version page | users can't drill into a run from its version | LOW | frontend | link rows when `/executions/:id` exists | 13 |

---

## 22. Architectural / Product Decisions

- **Author source:** the author is the authenticated session at publish time. It is never inferred from the workflow owner.
- **Username resolution:** usernames are resolved at read time from `users`, not stored on versions.
- **Restore semantics:**
  - restore needs `expected_revision`;
  - it keeps the workflow name and copies the description and files from the version;
  - it does not publish.
- **Clone path:** clone is `duplicate` with a `version`, not a new endpoint.
- **Read-only canvas:** a separate component, not a mode of the editor canvas.
- **"Active version":** shown as the latest published version (`current_version`). The domain has no separate activation concept.
- **No backend diff:** comparison is a display-only client computation over backend snapshots.

---

## 23. Repository / Git State

- **Branch:** `main`. HEAD `0adbc2c` ("frontend phase 11 - graph validation and publish workflow").
- **Working tree:** 30 modified files (+903 / −75 lines per `git diff --stat`), plus untracked files:
  - `backend/migrations/versions/0011_version_author.py`;
  - `frontend/src/features/versions/` (8 files, 1,900 lines);
  - `docs/reports/frontend/phase-12-workflow-versions.md`.
- **Unrelated changes:** none. All changes belong to this phase.
- **Commits:** none created. No stash, reset or revert.
- **Artifacts:** `frontend/dist/` and `frontend/test-results/` from the gate runs are ignored by git.

---

## 24. Phase Completion Assessment

### Implemented
- Versions list and detail pages.
- Immutability presentation.
- Draft/published separation.
- Frozen snapshot canvas and tables.
- Version ↔ version and version ↔ draft comparison.
- Restore to draft; clone as workflow.
- Executions per version.
- Version links from the workflow and the dashboard.
- Backend: authorship (`{id, username}`), version filter, restore, clone-from-version, migration 0011.

### Verified
- Every gate in §13, re-run for this report.
- Three consecutive green Vitest runs after the final change.
- Full-stack E2E against the real backend.

### Not Implemented
Nothing in the phase scope.

### Deferred
Execution drill-down and runs from a version page (Phase 13); newest-first listing.

### Open
§21: version-list query weight, `VersionDetailPage` size, execution row links.

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (13 — Execution UI)

**The next phase can assume:**
- `GET /workflows/{id}/executions?version=n` exists and is tested.
- `executionSchema.version` is the version an execution ran.
- `VersionLink` (`features/versions/shared.tsx`) and `versionPath` (`features/versions/paths.ts`) render and build version links. Reuse them for any version reference in execution views.
- `workflowQueries.executions(client, workflowId, { version, offset, limit })` exists, with a stable query key.

**Next steps:**
- When `/executions/:executionId` exists, make the execution id cell in `VersionDetailPage`'s `executionColumns` a link.
- "Run this version" belongs on the version page. `useExecuteWorkflowVersion` already exists in `api/mutations.ts`.

**Must not change casually:**
- versions are insert-only (no update/delete in `WorkflowVersionRepository`);
- restore and clone go through `WorkflowVersion.to_draft()` + `Workflow.replace_draft`;
- restore requires `expected_revision`;
- the `published_by` wire shape `{id, username} | null` is a contract (OpenAPI snapshot + `contract.node.test.ts`).

**Known constraints:**
- authors before 0011 are null;
- the snapshot canvas reads the current node catalog.
