# Phase 07 — Workflow Draft + Graph Editor Foundation

## 1. Executive Summary

**Goal.** Roadmap §13 (`docs/roadmap-frontend.md`): build the technical foundation of the visual graph editor and an explicit draft editing model. The editor state must stay separate from published versions. The node library, node configuration and the publish workflow are out of scope.

**What was built.** The route `/workflows/:workflowId/editor` (until now a placeholder) is a working draft editor: `frontend/src/features/editor/`.

- A controlled React Flow canvas with pan, zoom, grid and minimap.
- Node and edge rendering, with ports taken from the backend node catalog.
- Node selection from the canvas and from an accessible node list.
- Node movement.
- A pure editor-state reducer: undo/redo, internal copy/paste, dirty tracking.
- Explicit save and debounced autosave against the existing `PUT /api/v1/workflows/{id}`, pinned to the draft revision.
- Conflict handling and a guard against leaving with unsaved changes.

**Backend change.** The backend graph model could not store node positions, so a layout could not persist. An optional `position` field was added to `NodeInstance`. It covers the domain, the persistence codec, the API DTOs and the workflow package codec. It is tested and documented, and needs no migration.

**Resulting state.** All six roadmap acceptance criteria are met with test evidence (§14). Every quality gate passes on both backend and frontend.

**Limitations.** A few are documented in §20, for example: no large-graph performance measurement, no persisted viewport, and an internal-only clipboard. None blocks an acceptance criterion.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §13, "Ziel": implement the visual workflow editor as a technical foundation.

**Scope (roadmap):**
- Canvas
- Pan
- Zoom
- Selection
- Node Rendering
- Edge Rendering
- Minimap
- Grid
- Selection State
- Undo/Redo Foundation
- Copy/Paste Foundation
- Auto Save Foundation

The phase prompt for this session additionally listed:
- draft loading
- draft saving
- editor state
- unsaved-change state
- viewport behavior

It also required that any missing backend API be implemented, tested and documented.

**Explicitly not in this phase** (roadmap prompt: "Noch keine vollständige Node Library. Noch keine komplexe Node-Konfiguration. Noch kein Publish Workflow."):
- node library and adding nodes (Phase 08)
- configuration forms (Phase 09)
- connect, delete, duplicate and multi-select editing (Phase 10)
- validate and publish (Phase 11)
- version views (Phase 12)

**Critical rule.** The UI must never imply that editing a draft mutates a published version.

---

## 3. Scope Implemented

### Backend / Domain
- `Position` value object: a frozen dataclass with `x` and `y` as finite floats. A non-finite value raises `DomainValidationError`.
- `NodeInstance.position: Position | None = None`.

### Backend / Persistence and contracts
- `serialize_graph` and `deserialize_graph` write and read `"position": {"x", "y"} | null`. A missing key (legacy rows) loads as `None`.
- API DTOs:
  - New `PositionBody` with `FiniteFloat` coordinates and `extra="forbid"`.
  - `NodeInstanceBody.position: PositionBody | None = None`.
  - `DraftBody.to_domain` and `_graph_body` map the field.
- Workflow package codec: new `_Position` model (strict, `allow_inf_nan=False`) and an optional `_Node.position`. It is mapped in both `write_package` and `_to_draft`.
- OpenAPI snapshot `frontend/src/api/openapi.json` regenerated.

### Frontend / API layer
- `nodePositionSchema` was added to `api/workflows.ts`.
- `nodeInstanceSchema.position` is nullable, defaults to `null`, and is exported as type `NodePosition`.

### Frontend / Editor feature (`src/features/editor/`)

| Module | What it contains |
|---|---|
| `editorState.ts` | The pure state and reducer: `EditorState`, `EditorAction`, `initEditor`, `editorReducer`, `isDirty`, `placeUnpositioned`, `newNodeId`, `HISTORY_LIMIT`, `PASTE_OFFSET`. |
| `useDraftEditor.ts` | The persistence hook: save, autosave, conflict and save errors, the `useBlocker` navigation guard, `beforeunload`, paste id generation. |
| `GraphCanvas.tsx` | The controlled React Flow canvas: the custom `DraftNode` node type, edges, `Background` (dots), `Controls`, `MiniMap`, and a `ZoomLevel` panel. |
| `graphView.ts` | Shared read-only helpers: `edgeKey`, `nodeTitle`, `portsOf`. |
| `EditorPage.tsx` | The route component: query loading and not-found handling, the draft header, toolbar, save status, error and conflict alerts, the node list, keyboard shortcuts, and the leave confirmation. |

### Frontend / App wiring and styling
- `app/routes.tsx`: the editor route lazy-loads `EditorPage` instead of the placeholder.
- `styles/index.css`: a `.draft-canvas` block maps React Flow's `--xy-*` variables to the design tokens, so the canvas follows the light and dark theme.

### Testing
- Backend: serialization, package, persistence (PostgreSQL) and HTTP API tests for positions.
- Frontend: reducer unit tests, route-level component tests against a stub backend, and a Playwright e2e spec.
- Test infrastructure: jsdom stand-ins for `ResizeObserver`, `DOMMatrixReadOnly`, `offsetWidth` and `offsetHeight` in `src/test/setup.ts`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/graph.py` | Modified | `Position` value object; `NodeInstance.position`. |
| `backend/src/akasha/infrastructure/graph_serialization.py` | Modified | Persist and load `position`; legacy rows without it load as `None`. |
| `backend/src/akasha/api/workflow_schemas.py` | Modified | `PositionBody` (`FiniteFloat`); `NodeInstanceBody.position`; mapping in `to_domain` and `_graph_body`. |
| `backend/src/akasha/application/workflow_package.py` | Modified | `_Position`; `_Node.position`; export and import mapping. |
| `backend/tests/test_graph_serialization.py` | Modified | Round trip with a position; legacy documents without positions; non-finite rejection. |
| `backend/tests/application/test_workflow_package.py` | Modified | The `sample()` package has a placed node, so the package round trip covers positions. |
| `backend/tests/support.py` | Modified | `workflow_semantics` includes `n.position`, so every semantic comparison covers it. |
| `backend/tests/integration/test_persistence.py` | Modified | The PostgreSQL round trip asserts positions. |
| `backend/tests/integration/test_workflows_api.py` | Modified | New `test_node_positions_round_trip_and_survive_publish`; three new 422 cases for malformed positions. |
| `frontend/src/api/openapi.json` | Modified (generated) | Regenerated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1`: adds `PositionBody` and `position` (+30 lines). |
| `frontend/src/api/workflows.ts` | Modified | `nodePositionSchema`; `nodeInstanceSchema.position`. |
| `frontend/src/features/editor/` | **Created (new directory)** | The editor feature: 5 source files and 2 test files, 1,777 lines. |
| `frontend/src/features/editor/editorState.ts` | Created | Pure editor state and reducer (246 lines). |
| `frontend/src/features/editor/useDraftEditor.ts` | Created | Persistence, autosave and leave guard (111 lines). |
| `frontend/src/features/editor/GraphCanvas.tsx` | Created | React Flow canvas (218 lines). |
| `frontend/src/features/editor/graphView.ts` | Created | Shared helpers (32 lines). |
| `frontend/src/features/editor/EditorPage.tsx` | Created | Route page (383 lines). |
| `frontend/src/features/editor/editorState.test.ts` | Created | 22 unit tests. |
| `frontend/src/features/editor/EditorPage.test.tsx` | Created | 17 route-level tests. |
| `frontend/tests/e2e/editor.spec.ts` | Created | Playwright spec: drag, pan, zoom, edges, minimap, saved positions. |
| `frontend/src/app/routes.tsx` | Modified | The editor route points at `EditorPage`. |
| `frontend/src/app/routes.test.tsx` | Modified | The route test now expects the real page: "Workflow not found" from the stub backend, instead of the placeholder heading "Editor". |
| `frontend/src/features/workflows/WorkflowsPage.test.tsx` | Modified | The fixture node carries `position: {x: 10, y: 20}`, so Edit details is shown to send it back unchanged. |
| `frontend/src/test/setup.ts` | Modified | jsdom stand-ins required by React Flow (§9, finding 4). |
| `frontend/src/styles/index.css` | Modified | `.draft-canvas` theme variable mapping. |
| `frontend/package.json`, `frontend/package-lock.json` | Modified | `@xyflow/react` 12.12.0 (exact pin) and its transitive dependencies (§18). |
| `frontend/README.md` | Modified | New "Draft editor" section. |
| `docs/architecture.md` | Modified | §7.2: Node Instance `position` semantics. |
| `docs/workflow-format.md` | Modified | `workflow.json` example plus a note: `position` is optional editor layout within workflow schema 1. |
| `docs/reports/frontend/phase-07-workflow-draft-graph-editor.md` | Created | This report. |

There are no migrations, no deleted files, no environment variables, and no Docker or configuration changes.

---

## 5. Architecture Changes

### 5.1 Node position is part of the backend graph

- **Decision.** `NodeInstance` carries an optional `position` (`{x, y}`, finite, or `null`). It is persisted with the draft and snapshotted into published versions and packages.
- **Context.** A visual editor must persist its layout. The backend model (`id`, `node_type`, `config`, `label`) had nowhere to store it. Architecture §40 requires that the editor "must operate on the backend-defined graph model" and "must not invent its own incompatible domain model".
- **Chosen approach.** An optional field on the domain entity, carried by every codec: DB JSON, API, and package. Runtime and validation ignore it.
- **Alternatives considered.**
  - (a) Store the layout in the browser (`localStorage`). Rejected: it would not survive across devices or users, would not be included in versions or exports, and is client state pretending to be domain state.
  - (b) A separate layout resource or endpoint. Rejected: it adds an API and a second revision to keep consistent with the draft, and gives no benefit at this stage.
  - (c) Store positions inside `config`. Rejected: `config` belongs to the node definition and is validated against its schema.
- **Reason.** This was the smallest change that keeps a single source of truth. The draft revision (optimistic concurrency) also covers the layout.
- **Consequences.**
  - A published version renders with the layout it was published with.
  - Nodes created through the API or by import may have `position: null`, so every editor must handle unplaced nodes (`placeUnpositioned`).
  - Future node-creating phases (08 and 10) should set a position.
  - Documented in `docs/architecture.md` §7.2 and `docs/workflow-format.md`. No ADR was written (judged a non-breaking additive field). Reconsider if layout grows, for example into groups or viewport.

### 5.2 Editor state model

- **Decision.** Editor state is a pure reducer (`editorReducer`) over immutable graph values. "Dirty" is identity-based: `graph !== saved`.
- **Context.**
  - frontend/AGENTS §29 and §30: undo/redo, dirty state, atomic operations, and "a save must not destroy the undo stack".
  - §97: no global store.
- **Chosen approach.** `useReducer` local to the editor subtree. History is stored as graph snapshots (structural sharing via spread), capped at 100 steps. The drag in progress is tracked as `dragOrigin`, so one drag is one undo step.
- **Alternatives considered.** A global store such as zustand (rejected: §97). A command/inverse-operation history (rejected for now: snapshot history is simpler and correct for the current operation set).
- **Consequences.**
  - Undoing back to the saved graph makes the draft clean again with no extra bookkeeping.
  - Phase 10 operations (add, delete, connect) can be added as reducer actions that call `commit()`.

### 5.3 Controlled canvas with canvas-local measurement state

- **Decision.** React Flow runs controlled. Positions and selection come from the reducer. Measured node sizes are kept in `GraphCanvas`'s own `useState`.
- **Context.** React Flow v12 reads `measured` from the user-supplied node objects (`adoptUserNodes` in `@xyflow/system`). A controlled canvas that drops dimension changes leaves nodes `visibility: hidden`.
- **Reason.** Measured sizes are rendering detail, not domain or editor state. Keeping them out of the reducer keeps undo history and dirty tracking free of layout noise.

### 5.4 The editor loads the draft once per session

- **Decision.** The editor's workflow query disables `refetchOnWindowFocus` and `refetchOnReconnect`. The reducer is initialized once. Background data changes do not replace local edits. "Reload" remounts the editor (a `key` on `ReactFlowProvider`) after a successful refetch.
- **Reason.** Server state must not silently overwrite local editor state (frontend/AGENTS §5). Concurrent changes are detected by revision at save time instead (§94).

---

## 6. Domain / Data Model Changes

| Item | Change |
|---|---|
| `akasha.domain.graph.Position` | New frozen value object `(x: float, y: float)`. `__post_init__` rejects NaN and ±inf. |
| `akasha.domain.graph.NodeInstance` | New field `position: Position \| None = None`. Identity and equality are unchanged (`eq=False`, id-based). |
| Graph JSON (`draft_graph` and version `graph` JSONB columns) | Each node object gains the key `"position"`, either `{"x": float, "y": float}` or `null`. No schema or migration change: the columns are JSON. |
| API DTO `PositionBody` | New. |
| API DTO `NodeInstanceBody` | Optional `position`. |
| Package `_Node` | Optional `position`. `_Position` is strict and finite. |
| Frontend `NodeInstance` type | Gains `position: NodePosition \| null`. |

**Compatibility.**
- **Backward.** Rows and packages without `position` load with `None`. Clients that omit the field send `None`.
- **Forward.** An Engine build without this change rejects a package containing `position` (`extra="forbid"`). It fails loudly and never reinterprets the data. See finding 7.
- **Domain.** No state transitions changed.

---

## 7. API / Contract Changes

No new endpoints were added; the existing contracts gained one optional field.

- **`GET /api/v1/workflows/{id}`, `PUT /api/v1/workflows/{id}`, `POST /api/v1/workflows`, `POST …/duplicate`.** Every `draft.graph.nodes[]` gains `position: {x: number, y: number} | null`.
  - **Request:** optional, default `null`. `x` and `y` must be finite numbers. Unknown keys inside `position` are rejected.
  - **Errors:** a malformed position returns 422 `VALIDATION_ERROR`, for example `{"x": "inf"}`, a missing `y`, or an extra `z`. These cases are tested.
- **Version endpoints and publish responses** (`GET …/versions/{n}`, `POST …/publish`): `graph.nodes[].position` is returned as snapshotted.
- **Workflow package `workflow.json`:** optional `graph.nodes[].position`, within workflow schema version 1. `format_version` is unchanged.
- **Frontend/backend contract:** `openapi.json` was regenerated. `src/api/contract.node.test.ts` and `backend/tests/test_openapi_contract.py` both pass.
- **Compatibility.** The change is additive. Existing clients that send drafts back unchanged must preserve unknown fields or include `position`. The frontend's Edit details path does, because the schema now includes it (finding 2).

---

## 8. Implementation Details

**Load flow.** `EditorPage` → `useQuery(workflowQueries.detail)` and `useQuery(nodeQueries.catalog)` → `<ReactFlowProvider key={session}>` → `DraftEditor` → `useDraftEditor(workflow)`. This calls `useReducer(editorReducer, …, initEditor(draft.graph, revision))`.

**Initial layout.**
- `initEditor` calls `placeUnpositioned`. Each unplaced node is put in a column by longest-path depth from sources and a row by graph order (gaps of 280 and 140 px), below all placed nodes.
- The relaxation is bounded to V rounds, so cycles terminate.
- The laid-out graph is assigned to both `graph` and `saved`. Opening a draft therefore does not mark it dirty, and the layout is persisted with the next real save.

**Reducer actions.**
- `move {positions, done}`: while `done=false` the graph updates live and `dragOrigin` remembers the start. At `done=true` one history entry is committed. If every position equals the origin, no entry is committed (`samePositions`).
- `select`: never part of history.
- `undo` / `redo`: ignored during a drag. The selection is filtered to nodes that still exist.
- `copy`: stores the selected nodes plus the edges whose endpoints are both selected.
- `paste {ids}`:
  - New ids are generated by the hook (`newNodeId()` = `node_` + 32 hex characters from `crypto.randomUUID`), which keeps the reducer pure.
  - Edges are remapped and positions offset by 40 px.
  - The pasted nodes become the selection, and the clipboard advances so repeated pastes cascade.
- `saved {graph, revision}`: updates `saved` and `revision` only. The undo stack is kept.

**Persistence (`useDraftEditor`).**
- **What is sent.** `save()` sends `PUT /workflows/{id}` with the loaded `name`, `description`, `triggers` and `dependencies` (`{...workflow.draft, graph}`) and `expected_revision: state.revision`.
- **Single flight.** A save runs only one at a time (`inFlight` ref). It is skipped while dragging or when the draft is clean.
- **On success.** `dispatch(saved)` records the graph that was sent. Edits made during the request stay dirty (tested).
- **On error.** The error is stored in `saveError`.
- **Autosave.** A `setTimeout(AUTOSAVE_DELAY_MS = 1500)` is re-armed whenever `graph` changes. It is suppressed while saving, while dragging, or while `saveError` is set. A failed save therefore never causes a retry loop; "Save draft" or "Retry" resumes autosave. The callback is a `useEffectEvent`, so it always sees current state without re-arming on unrelated renders.
- **Conflict.** An `ApiError.kind === 'conflict'` shows "The draft was changed elsewhere" with "Discard my changes and reload". That button calls `query.refetch()` and, only on success, bumps `session`, which remounts the editor from the server draft.
- **Cache.** `useUpdateWorkflow` (existing) writes the response into the `workflows.detail` cache and invalidates lists. The editor itself never writes to the query cache.

**Leave guard.** `useBlocker` blocks pathname changes while the draft is dirty or a save is in flight, and a `ConfirmDialog` offers "Stay in editor" or "Leave without saving". A `beforeunload` listener is attached under the same condition.

**Canvas (`GraphCanvas`).**
- **Nodes.** Built in `useMemo` from graph, selection, catalog and `measured`.
- **`DraftNode`** shows:
  - the title: label, else catalog name, else type
  - the node type
  - "Unknown node type" as text, when the catalog loaded but lacks the type
  - input and output `Handle`s per port, with `isConnectable={false}`
- **Ports.** `portsOf` takes the definition ports plus every port referenced by an edge, so stored edges always have handles.
- **Edges.** Not selectable. Their id is `edgeKey`.
- **Options.** `nodesConnectable={false}` and `deleteKeyCode={null}` (Phase 10). `minZoom` 0.2, `maxZoom` 2. `fitView` with a fit zoom of at most 1. `snapToGrid` at 10 px. A dots `Background` with a 20 px gap. `Controls` without the interactivity toggle. A pannable and zoomable `MiniMap`. The React Flow attribution is left visible.
- **`onNodesChange`** splits React Flow changes: `position` goes to `onMove(positions, !dragging)`, `select` to `onSelect(ids)`, and `dimensions` to local `measured`.

**Draft vs published in the UI.**
- The h1 is "Draft editor: {name}" and the header shows a "Draft" badge.
- The text reads: "You are editing the draft (revision N). Saving changes the draft only; published versions, the latest being vN, stay unchanged. Publishing is a separate step." For a never-published workflow it reads "nothing has been published yet".
- The primary button is "Save draft". The save status reads "All changes saved to draft", "Unsaved changes", "Saving draft…" or "Changes not saved" (`role="status"`).

**Keyboard shortcuts.** Bound with `onKeyDown` on the editor container, never `window`:
- Ctrl/⌘+Z: undo
- Ctrl/⌘+Shift+Z or Ctrl+Y: redo
- Ctrl/⌘+C: copy
- Ctrl/⌘+V: paste
- Ctrl/⌘+S: save

They are ignored in inputs, textareas, selects and contenteditable elements. Ctrl/⌘+C is also ignored while page text is selected, so the browser copies the text. React Flow's own keyboard handling (Tab to a node, Enter to select, arrow keys to move) is left enabled. Arrow-key moves become committed undo steps.

**Accessibility.**
- The node list (`<section aria-labelledby="draft-nodes">`) lists every node with its type and in/out connection counts as `aria-pressed` buttons. A click selects the node and centers it (`setCenter`).
- The canvas is a labelled `<section aria-label="Graph canvas">`.
- Toolbar buttons carry `aria-keyshortcuts`.
- The zoom percentage is an `aria-live="polite"` text.
- A node's "unknown" state is text, never color alone.

---

## 9. Findings

### Finding 1 — The backend graph had no node positions
- **Impact:** HIGH
- **Resolution:** FIXED
- **Notes:** Without positions, no layout can persist and the roadmap criterion "Nodes können verschoben werden" could not be met durably. The field was added in all four codecs (§5.1). The roadmap and architecture did not mention layout persistence. This was an unstated assumption.

### Finding 2 — The frontend Zod schema silently strips unknown fields
- **Impact:** HIGH (data loss, had it shipped)
- **Resolution:** FIXED
- **Notes:**
  - Zod `z.object` drops keys it doesn't know.
  - The Phase 06 "Edit details" dialog sends `workflow.draft` back as parsed.
  - Had `position` been added only to the backend, every rename would have erased the whole layout.
  - Fixed by adding `position` to `nodeInstanceSchema`. `WorkflowsPage.test.tsx` now asserts that the position is sent back unchanged.
  - The general risk remains for any future backend-only draft field. See open issue 1.

### Finding 3 — React Flow controlled mode needs `measured` on user nodes
- **Impact:** MEDIUM
- **Resolution:** FIXED (design)
- **Notes:** `adoptUserNodes` (`@xyflow/system`) builds internal nodes from `userNode.measured`, and unmeasured nodes render `visibility: hidden`. Measured sizes are therefore stored canvas-locally (§5.3).

### Finding 4 — jsdom cannot run React Flow without stand-ins
- **Impact:** MEDIUM (test infrastructure)
- **Resolution:** WORKAROUND
- **Notes:**
  - React Flow needs `ResizeObserver`, which must actually report entries with `contentRect`; otherwise nodes stay hidden and the pan-zoom extent observer throws.
  - It also needs `DOMMatrixReadOnly` and non-zero `offsetWidth`/`offsetHeight`.
  - All four were added to `src/test/setup.ts` with fixed 200 px sizes.
  - Consequence: jsdom tests cannot verify geometry. Drag, pan, edge paths and the minimap are verified in Playwright instead (§12).

### Finding 5 — The query client retries 503s
- **Impact:** LOW
- **Resolution:** FIXED (test)
- **Notes:** `shouldRetryQuery` retries transient statuses, so a catalog-failure test with 503 only reached the error state after the retries. The test uses 500, which is not retried. The behavior is correct; the test choice was adjusted.

### Finding 6 — `QueryClient.fetchQuery` is deprecated in the installed TanStack Query
- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** `@typescript-eslint/no-deprecated` flagged it. Reload uses `query.refetch()` instead.

### Finding 7 — Forward compatibility of packages
- **Impact:** LOW
- **Resolution:** OPEN
- **Notes:** The package models use `extra="forbid"`. A package exported by this build (engine version still `0.1.0`, compatibility `>=0.1.0`) is rejected by an older `0.1.0` build with a validation error. It is not misread. There is no release yet, so this has no practical effect today. When versions are released, a build carrying this change should bump the engine version.

### Finding 8 — Pydantic strict float accepts JSON integers
- **Impact:** LOW
- **Resolution:** Verified, no change
- **Notes:** `_Position` is strict. The package sample uses `y=120` (an int), and the round-trip test passes. Integer coordinates from clients are therefore accepted.

---

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Category |
|---|---|---|---|---|
| A drag ending at its start was recorded as an edit (dirty and an undo entry). | `move` compared the new graph to the origin by identity, but the live drag had already produced new node objects. | `samePositions(graph, origin)` compares coordinates and restores `origin` (clean). | `editorState.test.ts` "a drag back to the start is no edit" | Introduced this phase; caught by its unit test before integration. |
| Ctrl/⌘+C copied nodes even while page text was selected, overriding browser copy (frontend/AGENTS §81). | The shortcut handler did not check the document selection. | Skip the `c` shortcut when `window.getSelection()?.isCollapsed === false`. | `EditorPage.test.tsx` "copy shortcut leaves selected page text to the browser" | Introduced this phase; caught in self-review. |
| `WorkflowsPage.test.tsx` "sends the unchanged draft…" failed. | The fixture lacked `position`, which the schema now defaults to `null`. The real backend always sends the field. | The fixture now carries a real position. | The same test, which now also guards finding 2. | Test data outdated by the contract change. |
| `routes.test.tsx` expected the placeholder heading "Editor". | The route now serves the real page. | Expect "Workflow not found" (the stub backend has no workflow). | The same test. | Test outdated by the phase. |
| `tsc`: `TS1294` in `setup.ts`. | A parameter property is not allowed under `erasableSyntaxOnly`. | Explicit field plus constructor assignment. | `npm run typecheck` | Tooling |
| `eslint`: `no-base-to-string` on `String(init.body)`, plus `prefer-for-of`, `no-unnecessary-condition`, `prefer-nullish-coalescing` and `no-empty-function`. | First-draft code. | Rewritten (for example `while` relaxation; `nodes.some(...)` instead of a closure flag). | `npm run lint` | Tooling |

No pre-existing product bugs were discovered.

---

## 11. Fixes Applied

1. **Drag-to-origin is not an edit.** Symptom: a draft looked dirty after a no-op drag. Root cause and implementation: see §10. Verified by a unit test plus the full suite.
2. **Text copy respected.** See §10. Verified by a route test.
3. **Layout would be lost on Edit details.** Symptom: none shipped; prevented. Root cause: finding 2. Implementation: the schema includes `position`. Verified by `WorkflowsPage.test.tsx` and the contract test.
4. **React Flow nodes invisible in tests.** Symptom: `findByRole('group')` found nothing, and 11 of 16 page tests failed on first run. Root cause: finding 4. Implementation: a reporting `ResizeObserver` stub with `contentRect`. Verified: all page tests pass.
5. **Save status matcher too broad.** Symptom: "multiple elements" errors, because alert text also contained "not saved". Implementation: an anchored regex over the four exact status strings. Verified: tests pass.

---

## 12. Testing & Verification

All commands below were executed in this session after the last code change. The README edit came later and is documentation only; frontend gates were re-run after it.

### Unit Tests
- **Frontend, `editorState.test.ts` (22 tests):**
  - `initEditor`: clean start, layout of unplaced nodes without dirtiness
  - `placeUnpositioned`: identity when nothing is missing, termination on cycles, new nodes below placed ones
  - `move`: drag as one step, drag back to start, unknown ids ignored, keyboard move commits
  - undo/redo: restores to clean, a new edit clears redo, no-op on empty history and during a drag, history cap, selection pruning
  - `select` is not an edit
  - copy/paste: only internal edges, empty copy, fresh ids, offset, remapped edges, cascade, empty clipboard
  - `saved`: keeps undo, undo after save is dirty, edits during an in-flight save stay dirty
  - `newNodeId` shape
  - **Result: PASS.**
- **Backend, `tests/test_graph_serialization.py`:** position round trip, legacy document without `position`, and non-finite rejection (`DomainValidationError`). `tests/application/test_workflow_package.py`: the package round trip includes positions (via `workflow_semantics`). **Result: PASS.**

### Component Tests (route-level, jsdom, real router, QueryClient and ApiClient; `fetch` stubbed)
**`EditorPage.test.tsx`, 17 tests.** The stub backend enforces `expected_revision`, like the real one.

- **Loading and wording:**
  - the draft graph loads, with the draft vs published wording
  - unpublished wording
  - node list content (titles, types, connection counts)
  - ports on the canvas
  - "Unknown node type" text
  - edge-derived ports
- **Interaction:**
  - selection from the canvas and from the list (`aria-pressed`, `.selected`)
  - Zoom In changes the zoom indicator
  - a keyboard move produces "Unsaved changes", and undo/redo toggle it
  - shortcuts are ignored in a text input
  - Ctrl+C with selected text
  - copy/paste produces three distinct backend-shaped ids in the PUT
- **Saving:**
  - the save body: name, description, triggers kept, all positions non-null, `expected_revision` 3 then 4
  - the undo stack survives a save
  - Ctrl+S saves
  - autosave fires exactly once after 1.5 s (fake timers)
- **Failures and navigation:**
  - a 500 on save: alert, "Changes not saved", no autosave retries over 4.5 s, Retry succeeds
  - a 409: conflict alert, one PUT only, reload resets to clean at revision 4
  - leaving while dirty: Stay keeps the editor, Leave navigates
  - leaving while clean does not ask
  - a 500 on load: error state, then Retry works
  - a 404: not-found page
  - a catalog 500: warning, editing still works

**Result: PASS.**

### Integration Tests (real PostgreSQL via Testcontainers)
- `tests/integration/test_persistence.py::test_workflow_roundtrip_preserves_full_draft` now asserts positions survive a DB round trip. **Result: PASS.**

### API Tests (real HTTP stack plus PostgreSQL)
- `test_node_positions_round_trip_and_survive_publish`: PUT with one placed and one unplaced node returns the exact positions and `null`; `POST /publish` returns the position in the version graph.
- `test_update_rejects_malformed_drafts`: three new 422 cases (`"inf"`, missing `y`, extra `z`), and the draft revision is unchanged afterwards.
- **Result: PASS.**

### Contract Tests
- `backend/tests/test_openapi_contract.py` (after regeneration) and `frontend/src/api/contract.node.test.ts`. **Result: PASS.**

### E2E Tests (Playwright, Chromium, production build plus preview)
`tests/e2e/editor.spec.ts`, "renders, zooms, drags and saves a draft graph":
- nodes visible
- the edge has a real SVG `d` path
- the background is rendered and the minimap shows 2 nodes
- the auto-placed node is laid out to the right of its source
- Zoom In changes the indicator, and Fit View works
- panning the pane moves nodes by about 50 px and does not dirty the draft
- dragging a node dirties it and selects it, including in the list
- Ctrl/⌘+S saves, with positions in the PUT body (moved y > 0, unplaced node now placed)
- Undo works after the save
- leaving asks, and Stay keeps the URL

**Result: PASS.** The whole e2e suite was run twice: 20/20 both times, the second run after the CSS and shortcut changes.

### Security Tests
- Rejection of non-finite or malformed positions at the API (422) and in the domain and codec (`DomainValidationError`). **Result: PASS.**
- No other security test categories applied (§15).

### Accessibility Tests
- Role- and name-based assertions in the component tests: `region` names, `toolbar`, `aria-pressed`, `role="status"`, and node `group` names via `ariaLabel`. Keyboard paths tested: arrow-key move of a selected node, and the editor shortcuts. React Flow's Tab/Enter node selection is not tested.
- **No automated accessibility audit (axe) and no screen-reader test was performed.**

### Performance Tests
- **None performed.** No large-graph measurement (§16).

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | Backend `uv run ruff format --check .` (195 files). Frontend `npm run format:check` (Prettier). |
| Linter | PASS | Backend `uv run ruff check .`. Frontend `npm run lint` (ESLint). Initial findings were fixed (§10). |
| Type Checker | PASS | Backend `uv run mypy .`: "no issues found in 190 source files". Frontend `npm run typecheck` (`tsc -b`). An initial TS1294 and an `exactOptionalPropertyTypes` error on `measured` were fixed. |
| Unit Tests | PASS | Frontend `npm test`: 20 files, **220 tests** passed. |
| Integration Tests | PASS | Backend `uv run pytest`: **1881 passed, 6 skipped** (207 s). The skips are opt-in suites: `test_compose_lifecycle` (needs `AKASHA_TESTS_DEPLOYMENT=1`) and 5 OpenRouter external smoke tests (need an API key). Docker was available. |
| API Tests | PASS | Included in the backend run (`tests/integration/test_workflows_api.py`). |
| E2E Tests | PASS | `CI=1 npx playwright test`: **20 passed**. The `vite http proxy error` lines in the output are from pre-existing specs whose unstubbed requests hit the preview proxy, which has no backend. |
| Build | PASS | `npm run build`. `EditorPage` chunk 190.74 kB (61.89 kB gzip) JS plus 15.41 kB CSS. Main chunk unchanged at 335.22 kB. |
| Security Checks | PASS | `npm audit --omit=dev`: 0 vulnerabilities. No dedicated backend security scan was run this phase. |
| Accessibility | N/A (no automated audit) | Role- and keyboard-based tests only (§12). |
| Contract | PASS | OpenAPI snapshot test plus the frontend contract test. |

On the first run, `npm test` failed: one Phase 06 test, because of the contract change (§10). It was fixed, and the final result is PASS.

---

## 14. Acceptance Criteria Verification

Roadmap §13 acceptance criteria:

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Graph kann dargestellt werden. | PASS | Component test "loads the draft graph…" (nodes, ports, list). E2E: nodes visible, edge SVG path, minimap nodes. |
| Nodes können ausgewählt werden. | PASS | Component test "selects nodes from the canvas and from the node list". E2E: a dragged node has `.selected` and is pressed in the list. |
| Nodes können verschoben werden. | PASS | E2E mouse drag with the position persisted in the PUT body. Component test for keyboard move. Reducer `move` tests. |
| Edges können dargestellt werden. | PASS | E2E `.react-flow__edge path` has a non-empty `d`. Edge-referenced ports render even for unknown types (component test). |
| Änderungen können als Draft behandelt werden. | PASS | Changes go to local state ("Unsaved changes") and persist only via `PUT /workflows/{id}` (draft) with `expected_revision`. No publish call exists in the feature. Save, autosave and conflict tests. |
| Editor-State ist vom Published State getrennt. | PASS | The reducer holds its own graph and never writes the query cache. The page states that published versions stay unchanged and labels everything "Draft". The backend API test shows published versions are snapshots. Background refetches do not overwrite local edits (refetch on focus and reconnect disabled; re-init only via explicit reload). |

Roadmap scope items:

| Scope Item | Status | Evidence |
|---|---|---|
| Canvas | PASS | `GraphCanvas` |
| Pan | PASS | E2E pane drag moves nodes by about 50 px |
| Zoom | PASS | Component and E2E zoom indicator; min/max 0.2–2 |
| Selection / Selection State | PASS | `selection` in the reducer; tests |
| Node Rendering | PASS | `DraftNode`; tests |
| Edge Rendering | PASS | E2E SVG path |
| Minimap | PASS | E2E `.react-flow__minimap-node` count is 2 |
| Grid | PASS (render presence only) | `Background` dots plus `snapToGrid` 10 px. E2E checks the background is visible. Snap behavior is not asserted by a test. |
| Undo/Redo Foundation | PASS | Reducer, buttons and shortcuts; tests |
| Copy/Paste Foundation | PASS | Internal clipboard; tests |
| Auto Save Foundation | PASS | Debounced autosave, error pause, single flight; tests |

Session prompt items:
- draft loading, saving, editor state, unsaved-change state, viewport and API failures: covered above.
- missing backend API implemented, tested and documented: §5.1, §7, §17.

---

## 15. Security Considerations

- **Input validation.** `position` must be finite numbers. This is enforced at the API (`FiniteFloat`, `extra="forbid"` → 422), in the domain (`Position.__post_init__`), and at the package boundary (strict, `allow_inf_nan=False`). All are tested at the API and domain levels.
- **Secrets and credentials.**
  - The editor does not display or edit `config`. Copy/paste duplicates node `config` as-is, and configs contain only credential references, because the backend rejects plaintext secrets on save (existing rule).
  - The clipboard is in-memory React state. It is never written to the system clipboard, `localStorage`, `sessionStorage` or the URL.
- **Output encoding / XSS.** Labels, node types and port names render as React text. There is no `dangerouslySetInnerHTML`, `innerHTML` or `eval`.
- **Authorization.** Unchanged. The editor uses existing endpoints guarded by the backend; 404 for inaccessible workflows is handled.
- **Concurrency integrity.** Saves always send `expected_revision`. A 409 is never retried automatically and never overwritten.
- **Error disclosure.** Errors are shown via `errorMessage(ApiError)`, which uses backend envelope messages or fallbacks. No stack traces are shown.
- **Dependencies.** `@xyflow/react` (MIT) and its transitive dependencies; `npm audit` reports 0 vulnerabilities.
- Not applicable this phase: SSRF, path traversal, file handling, plugin trust, expressions, WebSockets.

---

## 16. Performance Considerations

- **Rendering.** `GraphCanvas` rebuilds every React Flow node object whenever the graph, selection, catalog or measured sizes change. During a drag this happens on every pointer move. React Flow re-adopts all nodes because none are reference-equal. Marked `ponytail:` in code, with per-node caching as the upgrade path.
- **Layout.** `placeUnpositioned` is O(V·E) relaxation, capped at V rounds, and runs only on load (marked `ponytail:`).
- **Node list.** Connection counts filter all edges per node, which is O(N·E) per render.
- **History.** Up to 100 graph snapshots. Unchanged nodes are shared by reference, and only changed nodes are copied.
- **Network.** Autosave debounce is 1.5 s with at most one request in flight. Each save sends the whole draft, since the API has PUT semantics.
- **Bundle.** The editor is a separate lazy chunk: 61.89 kB gzip JS plus 2.56 kB gzip CSS. The main chunk did not grow.
- **Measurements.** Only bundle sizes were measured. **No large-graph rendering or interaction measurement was performed.** frontend/AGENTS §60 requires testing with realistic graph sizes; this is deferred (§19).

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §7.2 | New domain field | `position` semantics: optional, finite, `null` = unplaced, layout only, ignored by validation and the runtime, part of the draft, versions and packages; the editor lays out unplaced nodes and saves them with the next save. |
| `docs/workflow-format.md` §3 | Package format | `workflow.json` example shows `position`. A note says it is optional layout within workflow schema 1. |
| `frontend/README.md` | New feature | "Draft editor" section: module responsibilities, save, autosave and conflict semantics, canvas configuration, positions, shortcuts, the accessible node list, and the test layout. |
| `frontend/src/api/openapi.json` | Generated | Contract snapshot. |

**Should be updated in future phases.**
- `backend/README.md` does not mention node positions. Its API section was not reviewed for a field list.
- If layout grows (groups, viewport persistence), consider an ADR under `docs/adr/`. That directory does not exist yet.

---

## 18. Dependencies / Infrastructure Changes

| Change | Detail | Why |
|---|---|---|
| Added `@xyflow/react` `12.12.0` (exact pin, `--save-exact`) | MIT; peer dependencies react/react-dom ≥ 17 | The graph library named in frontend/AGENTS §3. It provides pan, zoom, selection, minimap, background and handles. |
| Transitive (lockfile) | `@xyflow/system`, `zustand`, `use-sync-external-store`, `classcat`, `d3-zoom`, `d3-drag`, `d3-selection`, `d3-transition`, `d3-interpolate`, `d3-color`, `d3-dispatch`, `d3-ease`, `d3-timer`, and `@types/d3-*` (6 packages) | Pulled in by `@xyflow/react`. `zustand` is React Flow's internal store and is not used by the application (frontend/AGENTS §97 still holds). |
| Test infrastructure | `src/test/setup.ts`: `ResizeObserver` (reports one entry with `contentRect` 200×200 per `observe`), `DOMMatrixReadOnly` (parses `scale()`), `offsetWidth` and `offsetHeight` = 200 | React Flow cannot mount or show nodes in jsdom without them (finding 4). They apply to all jsdom tests; existing suites are unaffected (220/220). |

There are no backend dependency, Docker, database, Redis, environment variable or configuration changes.

---

## 19. Deferred Work

| Item | Why deferred | Intended phase | Affects Phase 07 completion? |
|---|---|---|---|
| Adding nodes from a library | Out of scope | 08 | No |
| Node configuration editing | Out of scope | 09 | No |
| Connect, disconnect and delete nodes or edges; duplicate; port compatibility feedback; full multi-selection UX | Out of scope; deliberately disabled (`nodesConnectable={false}`, `deleteKeyCode={null}`, edges not selectable) | 10 | No |
| Validate and publish UI | Out of scope | 11 | No |
| Read-only view of published versions on the canvas | Out of scope; `GraphCanvas` has no read-only mode yet | 12 | No |
| Large-graph performance testing and per-node render caching | Requires realistic fixtures and measurement | 24 | No (not an acceptance criterion; a guideline in frontend/AGENTS §60) |
| Automated accessibility audit (axe) | No tooling in the repo yet | 25 | No |
| System-clipboard copy/paste with validation of foreign data (frontend/AGENTS §82) | The internal clipboard satisfies "Copy/Paste Foundation" | 10 | No |

---

## 20. Known Limitations

- **Viewport not persisted.** Every editor open runs `fitView` (fit zoom capped at 1).
- **Autosave always on.** There is no user setting.
- **Saves send the whole draft.** Triggers and dependencies are sent as loaded. Changes made elsewhere to name, description, triggers or dependencies while the editor is open cause a conflict on the next save; nothing is overwritten, but the editor offers discard-and-reload only. There is no merge.
- **No restore of unsaved edits.** Edits lost by closing the tab after a save failure are not recovered. `beforeunload` warns, but there is no local draft recovery (frontend/AGENTS §86 lists it as optional).
- **`beforeunload` untested.** The handler is not covered by an automated test. Only the router blocker is tested.
- **Snap-to-grid untested.** `snapToGrid` behavior is not asserted.
- **Incidental multi-select.** React Flow's built-in modifier-click and Shift+drag box selection work, since the reducer handles any selection set. They are untested and unstyled beyond the default; formal multi-selection is Phase 10.
- **Ports without a catalog.** When the node catalog fails to load, unconnected ports are not shown, and nodes are not flagged unknown. A warning says so.
- **Paste offset.** Pasted nodes keep the source label and config. The offset is a fixed 40 px, which can land off-screen if the viewport moved.
- **Package forward compatibility.** Finding 7.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Any backend-only draft field is silently stripped by the frontend's Zod object schemas and then erased by the next save (the general form of finding 2) | Data loss risk | MEDIUM | Frontend API schemas / contract | Make draft-related schemas reject or preserve unknown keys (for example `z.looseObject`/`.passthrough()` or `strictObject` plus a contract test that fails on unknown response fields). Decide workflow-wide. | Before the next draft-shape change; candidate Phase 09 or 10 |
| Engine version not bumped for the additive package field | Older builds reject new packages (loudly) | LOW | Backend packaging | Bump the engine version at the next release | Release process |
| No large-graph measurement of the editor | Unknown scaling | LOW | Frontend performance | Add a fixture with several hundred nodes and measure drag frame time | 24 |

---

## 22. Architectural / Product Decisions

1. **Node layout lives in the backend graph** (`NodeInstance.position`), not in client storage. Versions and exports therefore keep the layout, and the draft revision protects it.
2. **`position: null` is a valid persisted state.** It means "never placed". Editors must lay such nodes out. Laying out on load does not dirty the draft; the layout is persisted with the next real save. This keeps "open the editor" from writing to the server.
3. **The editor reducer is pure and local** (`useReducer`), with no global store. Ids for pasted nodes are generated outside the reducer.
4. **Dirty is identity-based** (`graph !== saved`). Undoing back to the saved state is clean, and a save never clears the undo stack.
5. **One drag is one undo step.** Intermediate positions update live but are committed once. A drag back to the start is not an edit.
6. **Saves are pinned to the revision and never retried automatically.** Autosave pauses after any failure. A 409 is never overwritten, and the only resolution offered is discard-and-reload.
7. **The editor loads the draft once per session.** Focus and reconnect refetches are disabled, so server data never replaces local edits implicitly.
8. **Measured node sizes are canvas-local rendering state.** They are not editor state.
9. **Connecting and deleting are disabled at the React Flow level**, not just in the UI, so Phase 07 cannot mutate the graph structure.
10. **Theming maps React Flow CSS variables to the design tokens** instead of using React Flow's `colorMode`. The canvas follows the app's own dark-mode rule, including the `data-theme="light"` opt-out.
11. **React Flow attribution stays visible.** The library is MIT-licensed; hiding the attribution is a product decision not taken here.

---

## 23. Repository / Git State

- **Branch:** `main`. HEAD is `3eb1810` ("Merge branch 'claude/charming-tereshkova-6c6694'"). The previous phase commit is `0fa2767` ("frontend phase 06 - workflow management").
- **Working tree:** uncommitted Phase 07 changes only. Nothing was committed in this phase.
  - **Modified (21):**
    - backend: `workflow_schemas.py`, `workflow_package.py`, `graph.py`, `graph_serialization.py`, plus tests `test_workflow_package.py`, `test_persistence.py`, `test_workflows_api.py`, `support.py`, `test_graph_serialization.py`
    - docs: `docs/architecture.md`, `docs/workflow-format.md`
    - frontend: `README.md`, `package.json`, `package-lock.json`, `src/api/openapi.json`, `src/api/workflows.ts`, `src/app/routes.tsx`, `src/app/routes.test.tsx`, `src/features/workflows/WorkflowsPage.test.tsx`, `src/styles/index.css`, `src/test/setup.ts`
  - **Untracked:** `frontend/src/features/editor/`, `frontend/tests/e2e/editor.spec.ts`, and this report.
  - **Diff size (tracked files):** 581 insertions, 21 deletions, across 21 files.
- **Unrelated changes:** none detected. The tree was clean at the start of the phase.
- **Build output:** `frontend/dist/` and `frontend/test-results/` existed before the phase. They were regenerated by build and test runs and are not reported by `git status`, so they are ignored.

---

## 24. Phase Completion Assessment

### Implemented
- Backend `position` support across the domain, persistence, API and package codecs.
- The frontend draft editor:
  - canvas: pan, zoom, grid, minimap
  - node and edge rendering
  - selection from the canvas and the list
  - movement
  - undo/redo
  - internal copy/paste
  - explicit save and autosave with revision pinning
  - conflict handling
  - the leave guard
  - draft vs published labelling
- Theming and documentation.

### Verified
- Backend: 1881 tests passed, including the new position tests against real PostgreSQL. ruff, ruff format and mypy are clean.
- Frontend: 220 Vitest tests passed (39 new editor tests). Typecheck, lint, format and build are clean.
- Playwright: 20/20, including the new editor spec.

### Not Implemented
- Nothing from the Phase 07 scope.

### Deferred
- See §19: Phases 08–12 features, performance measurement (24), axe audit (25), and the system clipboard (10).

### Open
- See §21: Zod unknown-key stripping (MEDIUM), the engine version bump at release (LOW), and large-graph measurement (LOW).

### Blocking Issues
- None.

All six acceptance criteria are PASS (§14). The status is "with limitations" because of the untested `beforeunload` handler and snap-to-grid behavior, the missing performance measurement, and the open schema-stripping risk (§20, §21).

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (08 — Node Library)

**What the next phase can assume:**
- `/workflows/:workflowId/editor` renders a draft graph from `GET /workflows/{id}` and the node catalog from `GET /nodes` (`nodeQueries.catalog`, already loaded on the page as a `Map` keyed by `node_type`).
- All graph changes go through `editorReducer`. Add new operations as reducer actions that call `commit(state, nextGraph)`, so they become undoable, mark the draft dirty and trigger autosave automatically.
- A new node needs:
  - an id from `newNodeId()`, generated outside the reducer
  - `node_type`, `config: {}`, `label`
  - a `position` in flow coordinates, for example via React Flow's `screenToFlowPosition` for drag-and-drop
- `GraphCanvas` draws ports from the catalog definition (`inputs` and `outputs`), plus any port an edge uses.

**Contracts available:**
- `NodeInstance.position` (`{x, y} | null`)
- `PUT /workflows/{id}` with `expected_revision` (409 on conflict)
- the node catalog schema in `src/api/nodes.ts`

**Must not be changed casually:**
- Saves send `expected_revision` and are never auto-retried after an error. Autosave stays paused after a failure.
- The editor must never call publish implicitly. The draft vs published wording must stay.
- `nodeInstanceSchema` must keep every backend node field (finding 2). Otherwise the editor and "Edit details" erase data.
- Keep the reducer pure, and keep measured sizes and viewport out of it.

**Known constraints:**
- Connecting and deleting are disabled in `GraphCanvas` (`nodesConnectable={false}`, `deleteKeyCode={null}`). Phase 10 enables them.
- jsdom tests cannot verify geometry. Put drag-and-drop assertions in Playwright (`tests/e2e/editor.spec.ts` shows the fake-backend pattern).

**Prerequisites:** none outstanding.
