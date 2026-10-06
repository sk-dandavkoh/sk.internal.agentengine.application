# Phase 08 — Node Library

## 1. Executive Summary

**Supposed to achieve.** Roadmap §14 (`docs/roadmap-frontend.md`): a node library driven by the backend Node Registry, with:
- search, categories and filters
- node preview, description, version and capabilities
- drag and drop, and "Add to Graph"

There must be no hard-coded node list (also frontend `AGENTS.md` §24, which lists Name, Description, Category, Icon, Version and Capabilities as example card content).

**Actually implemented.**
- A "Node library" panel in the draft editor (`frontend/src/features/editor/NodeLibrary.tsx`), fed only by the existing `GET /api/v1/nodes`. It has:
  - search, plus category and capability filters, with grouping by category
  - per-node metadata, including a category icon
  - an expandable "Preview and details" section showing the node exactly as it will look on the canvas
  - an Add button
- HTML5 drag and drop onto the React Flow canvas.
- A new `add` action in the pure editor reducer, which handles duplicate ids and overlapping positions.
- Adding is refused for node types the catalog does not contain.
- The canvas node rendering was extracted into a shared `NodeCard` component (`NodeCard.tsx`), used by both the canvas and the preview.
- A pre-existing flaky test (`src/app/App.test.tsx`) was found during verification and fixed.
- There are no backend, contract or dependency changes.

**Resulting state.** A user can open `/workflows/:id/editor`, browse the registered nodes, see what each does (preview, inputs, outputs, required settings, capabilities, version, documentation), and add a node by button or drag. The node is saved to the draft through the existing save and autosave path. This is verified at component, E2E and full-stack level (real backend registry and PostgreSQL). The full unit suite passed 21 of 21 consecutive runs after the flaky-test fix.

**Acceptance criteria.** All four roadmap criteria pass with evidence (§14). All roadmap scope items, including Node Preview, are implemented.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap §14, "Ziel": provide a professional node library.

**Scope (roadmap):** search, categories, filters, node preview, description, version, capabilities, drag and drop, add to graph. Node definitions come from the backend. Example categories: Core, AI, Control Flow, HTTP, Web, Email, Data, Files, Triggers, Plugins.

**Acceptance criteria (roadmap):**
1. The Node Library uses the backend Node Registry.
2. There is no hard-coded complete node list.
3. Nodes can be dragged into the editor.
4. Node metadata is displayed.

**Explicitly not responsible for** (roadmap prompt: "Keine Node-spezifischen Konfigurationsformulare implementieren"):
- node configuration forms and the inspector (Phase 09)
- connecting and deleting nodes (Phase 10)
- publishing

The task prompt additionally asked for:
- display of inputs, outputs and configuration requirements
- tests for loading, search, filtering, empty state, API error, adding, and duplicate or invalid additions

---

## 3. Scope Implemented

### Frontend / UI — `NodeLibrary.tsx` (new)
- `NodeLibrary({ query, onAdd })` is a `<section aria-labelledby="node-library">`. It wraps its content in the existing `QuerySection` for loading, error with Retry, and stale-refresh states.
- **Search** (`Input type="search"`, "Search nodes"): case-insensitive substring match over `name`, `node_type`, `description` and `category` (`matches`).
- **Category filter** (`Select`): the options are the distinct catalog categories, sorted.
- **Capability filter** (`Select`): the options are every capability key that is `true` on at least one node.
- **Count and states.**
  - A live count, "n of m nodes" (`role="status"`).
  - "No nodes registered" for an empty registry; "No matching nodes" when the filters exclude everything.
  - Results are grouped per category under an `h3`.
- **`LibraryItem` shows:**
  - a category icon, the name (`h4`), `node_type · v<version>`, the description, and capability badges (`<ul aria-label="Capabilities">`)
  - a native `<details>` "Preview and details" with:
    - the preview: a `NodeCard` rendered without handles, inside `aria-hidden="true"` and `pointer-events-none`
    - each input and output port: name, type, `required`, `multiple`, description
    - "Required settings": the top-level `required` keys of the config schema
    - the documentation link
  - an Add button (`aria-label="Add <name> to graph"`)
  - The `<li>` is `draggable` and puts the node type into `dataTransfer` under `NODE_TYPE_MIME = 'application/x-akasha-node-type'`.
- **Humanizing.** `humanize` turns `control_flow` into "Control flow". `CAPABILITY_LABELS` gives friendlier text for the four capability keys the backend emits today, and falls back to `humanize` for others.
- **Documentation link.** `docsUrl` renders only `http:`/`https:` URLs.
- **Layout.** At `lg` and up the panel is `w-72` with `max-h-[40rem]` and its own scroll.

### Frontend / UI — `NodeCard.tsx` (new)
- `NodeCard({ title, nodeType, category, inputs, outputs, selected, unknown, handles })` is the node card as drawn on the canvas: header with category icon, title and type, an "Unknown node type" text marker, and input/output port lists. React Flow `Handle`s are rendered only when `handles` is true, because they need a ReactFlow context.
- `CategoryIcon({ category })`: inline SVG glyphs in `CATEGORY_GLYPHS` for the backend's built-in categories (`ai`, `web`, `control_flow`, `input`, `output`, `observability`, `transform`, `communication`). Any other category gets `GENERIC_GLYPH`. All icons are decorative (`aria-hidden`) and the category is always also given as text.

### Frontend / Canvas and editor integration
- **`GraphCanvas.tsx`:**
  - `DraftNode` now renders `<NodeCard … handles />`; its former inline markup moved into `NodeCard`, so canvas nodes also show the category icon.
  - New prop `onDropNode(nodeType, position)`.
  - `onDragOver` accepts only `NODE_TYPE_MIME`. `onDrop` converts `clientX/Y` with `screenToFlowPosition`.
  - New exported `GRID = 10`, now also used for `snapGrid`.
- **`EditorPage.tsx`, `DraftEditor`:**
  - New `addNode(nodeType, at?)`. It refuses types missing from the catalog with a warning toast ("Node not added"). Otherwise it dispatches `add` with:
    - `newNodeId()`, `config: {}`, `label: ''`
    - a grid-snapped position: the drop point, or the canvas center via `screenToFlowPosition`
  - The library is rendered as the first editor column.
  - The catalog-failure alert now shows only when there is no catalog data at all, and says that nodes cannot be added.

### Frontend / Editor state — `editorState.ts`
- New action `{ type: 'add'; node: NodeInstance }`. It is a no-op while dragging or when the id already exists. Otherwise it commits one undo step, appends the node and selects it.
- New helper `freeSpot(graph, p)`: moves the position diagonally by `PASTE_OFFSET` (40) until no node sits exactly there.

### Design system
- `components/ui/icons.tsx` now exports the `Icon` SVG frame, and `components/ui/index.ts` re-exports it. Feature glyphs reuse the design system's size and stroke instead of duplicating the frame. No existing icon changed.

### Test fix (pre-existing defect)
- `src/app/App.test.tsx`: the request-count assertion is wrapped in `waitFor` (§10).

### Testing and documentation
- Tests: §12. Documentation: §17.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/src/features/editor/NodeLibrary.tsx` | Created (313 lines) | Library panel, filters, item metadata, preview, drag source, `NODE_TYPE_MIME` |
| `frontend/src/features/editor/NodeCard.tsx` | Created (126 lines) | Shared canvas/preview card; category icons |
| `frontend/src/features/editor/GraphCanvas.tsx` | Modified | `DraftNode` uses `NodeCard`; drop target; `onDropNode`; exported `GRID` |
| `frontend/src/features/editor/EditorPage.tsx` | Modified | `addNode`; library column; catalog alert condition and wording |
| `frontend/src/features/editor/editorState.ts` | Modified | `add` action, `freeSpot` |
| `frontend/src/components/ui/icons.tsx` | Modified | Export the `Icon` frame |
| `frontend/src/components/ui/index.ts` | Modified | Re-export `Icon` |
| `frontend/src/features/editor/editorState.test.ts` | Modified | `describe('add')`: 3 tests |
| `frontend/src/features/editor/EditorPage.test.tsx` | Modified | `describe('Node library')`: 7 tests |
| `frontend/src/app/App.test.tsx` | Modified | Fix the race in "boots into the dashboard inside the shell" |
| `frontend/tests/e2e/editor.spec.ts` | Modified | Test "drags a node from the library onto the canvas" |
| `frontend/tests/fullstack/workflows.spec.ts` | Modified | Test "the node library lists the backend registry and adds to the draft" |
| `frontend/README.md` | Modified | "Node library" section; canvas bullet mentions drops |
| `docs/reports/frontend/phase-08-node-library.md` | Created | This report |

There are no new directories, generated files, migrations, configuration or dependency changes, and no deletions. `frontend/src/api/openapi.json` and `frontend/src/api/nodes.ts` are unchanged.

---

## 5. Architecture Changes

No architectural changes were introduced during this phase. The library consumes the existing node contract and adds nodes through the existing editor reducer and draft persistence (Phase 07). Local design decisions:

### 5.1 Filtering on the client
- **Decision:** search and filters run on the loaded catalog.
- **Context:** `GET /nodes` has no query parameters and is unpaginated; the backend documents that the catalog is bounded by installed code (`backend/src/akasha/api/nodes.py`).
- **Alternatives considered:** backend search parameters, which would be a contract change without a measured need.
- **Reason:** the catalog has tens of entries and is cached for 5 minutes (Phase 04).
- **Consequences:** if plugin catalogs grow large, move search to the backend together with pagination.

### 5.2 The client validates only registration and id uniqueness
- **Decision:** `addNode` refuses unregistered types, and the reducer refuses duplicate ids. Config validity, port compatibility and required settings remain the backend's `/validate` and publish validation (root `AGENTS.md` §4).
- **Context:** drafts accept semantically invalid content (`WorkflowService.update_draft`). The backend rejects only unstorable content, plaintext secrets and duplicate node ids (`Graph.add_node`).
- **Reason:** an unregistered type can never become valid, and a duplicate id would make the whole draft unsavable.
- **Consequences:** Phase 09 owns configuring the added nodes.

### 5.3 Drag payload type
- **Decision:** a custom MIME type, `application/x-akasha-node-type`, carrying only the type string, which is re-checked against the catalog on drop.
- **Reason:** the canvas ignores foreign drags (files, text), and a crafted drag cannot add an unregistered type.

### 5.4 Preview = the canvas card itself
- **Decision:** the library preview renders the same `NodeCard` as the canvas, without handles.
- **Alternatives considered:**
  - a separate preview design, which could drift from the canvas
  - a miniature ReactFlow instance per preview, which is heavy and needs its own provider
- **Reason:** what the user previews is exactly what gets added; one component, no drift.
- **Consequences:** canvas card changes (Phase 09+ may add status or config markers) appear in the preview too. Anything that needs ReactFlow context must stay behind the `handles` flag.

### 5.5 Icons per category, in the frontend
- **Decision:** a category → glyph map in `NodeCard.tsx` with a generic fallback; no icon field in the node contract.
- **Context:** `AGENTS.md` §24 lists Icon as card content; `NodeTypeOut` has no icon field.
- **Alternatives considered:** adding `icon` to `NodeMetadata`/`NodeTypeOut`. That is a backend contract change, and the frontend would still need an icon set to map the keys to, so it adds nothing until a plugin needs a distinct icon.
- **Reason:** smallest change that gives every node an icon while staying contract-driven (the category comes from the registry). Unknown categories, such as a plugin's, degrade to the generic glyph instead of breaking.
- **Consequences:** a new built-in category gets the generic glyph until a glyph is added to `CATEGORY_GLYPHS`. This is display data only, not a node list.

---

## 6. Domain / Data Model Changes

There are no domain, schema, DTO or database changes. A library-added node is an ordinary `NodeInstance`:
- `id`: `node_<32 hex>`
- `node_type`
- `config: {}`
- `label: ''`
- `position: {x, y}` on the 10 px grid

The internal editor state type `EditorAction` gained the `add` variant. It is not persisted.

---

## 7. API / Contract Changes

There were no contract changes. The phase reads `GET /api/v1/nodes` (`NodeCatalog { items: NodeTypeOut[] }`) and writes through the existing `PUT /api/v1/workflows/{id}`, with the draft and `expected_revision`. The OpenAPI snapshot is unchanged. Icons deliberately use no contract field (§5.5).

---

## 8. Implementation Details

- **Data flow.**
  - `EditorPage` runs `useQuery(nodeQueries.catalog(client))` and builds `catalog: Map<node_type, NodeDefinition>`.
  - The query result goes to `NodeLibrary`; the map goes to `DraftEditor.addNode` and `GraphCanvas`.
  - Library UI state (search, category, capability) is component state, following the README state boundaries.
- **Add path.**
  - Button: `onAdd(d)` → `addNode(d.node_type)` → canvas-center position.
  - Drag: `onDragStart` (MIME data) → `GraphCanvas.onDrop` → `onDropNode(type, flowPosition)` → `addNode`.
  - Both continue: `dispatch({type:'add'})` → `commit`, which pushes the previous graph to `past` (max 100) and clears `future` → selection `[newId]`.
  - The draft becomes dirty, and the existing autosave (1.5 s) or explicit save persists it with `expected_revision`. Conflict handling is unchanged.
- **Default position.** The center of the `<section aria-label="Graph canvas">` rectangle, converted to flow coordinates. In jsdom the rectangle is zero, so it resolves to (0, 0) plus the `freeSpot` offset.
- **Overlap handling.** `freeSpot` compares exact coordinates only.
- **Security-sensitive behavior.**
  - `docsUrl` uses `new URL` and allows only `http:`/`https:`.
  - The link has `target="_blank" rel="noreferrer noopener"`.
  - All metadata is rendered as React text. There is no `dangerouslySetInnerHTML`.
- **Accessibility.**
  - Every add is reachable by keyboard via the Add button, so drag and drop is never the only path.
  - The details view is a native `<details>`/`<summary>` with `focus-ring`.
  - The preview is `aria-hidden`, because the text port lists repeat its content; this avoids double announcement.
  - The filters are labelled, the count is a status region, and capabilities are text badges.
  - Headings nest `h2` → `h3` → `h4` → `h5`. Icons are `aria-hidden`.
  - Editor shortcuts keep working after clicking Add; text inputs are excluded by the existing `isTextInput`.

---

## 9. Findings

### Finding 1 — Drafts accept invalid nodes; only duplicate ids break a save
- **Impact:** LOW
- **Resolution:** FIXED (by design: guard in reducer)
- **Notes:** `Workflow.replace_draft` does not validate node types or config; `Graph.add_node` rejects duplicate ids. This defined the client-side checks.

### Finding 2 — The node contract has no icon field
- **Impact:** LOW
- **Resolution:** FIXED (category icons in the frontend, §5.5)
- **Notes:** `AGENTS.md` §24 names Icon. The contract was deliberately left unchanged.

### Finding 3 — Pre-existing flaky test `src/app/App.test.tsx` "boots into the dashboard inside the shell"
- **Impact:** MEDIUM (CI reliability)
- **Resolution:** FIXED
- **Notes:**
  - It failed 2 of 19 full `npm test` runs with `AssertionError: expected 0 to be greater than 0` at `expect(requests.length).toBeGreaterThan(0)`.
  - The test asserted request count immediately after the Dashboard heading appeared, but the dashboard's queries start in effects that can run after that render. The file was last changed in Phase 05 (`6f6ab5b`).
  - Fixed by waiting for the condition (`waitFor`), which matches frontend `AGENTS.md` §75 (no timing-dependent tests).
  - After the fix, 21 of 21 consecutive full runs passed.

### Finding 4 — React Flow's Controls panel covers the bottom-left of the pane
- **Impact:** LOW
- **Resolution:** FIXED (test)
- **Notes:** a Playwright `dragTo` aimed there failed actionability ("rf__controls subtree intercepts pointer events"). The E2E test drops at the top-right. Drops onto overlay panels do not add a node (§20).

### Finding 5 — E2E logs show Vite proxy errors
- **Impact:** LOW
- **Resolution:** OPEN (pre-existing, not a failure)
- **Notes:** `npm run test:e2e` prints `[WebServer] http proxy error: /api/v1/...` from specs that do not stub every route. All tests pass. Seen for dashboard endpoints too, so it predates this phase.

---

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Area | Origin |
|---|---|---|---|---|---|
| Flaky `App.test.tsx` | Asserted request count before the dashboard's query effects ran | `await waitFor(...)` around the assertion | The test itself; 21/21 full runs green | Test infrastructure | Pre-existing (Phase 05), fixed |
| Lint error `non-nullable-type-assertion-style` in the drop test | `querySelector(...) as HTMLElement` | `screen.getByTestId('rf__wrapper')` | Lint gate | Test code | This phase, fixed |
| E2E drop target intercepted | Controls panel over the target point | Drop at `{x: width-120, y: 30}` | The E2E test | Test code | This phase, fixed |
| Library would stretch the editor row | No height cap on the panel | `lg:max-h-[40rem] lg:overflow-y-auto` | None (visual; checked by screenshot) | UI | This phase, fixed |
| "Found multiple elements with the text: prompt" after adding the preview | The preview repeats port names already in the text lists | Test asserts the preview via its hidden `Outputs` list and expects `prompt` twice | Component test | Test code | This phase, fixed |

---

## 11. Fixes Applied

1. **Flaky App test.**
   - Symptom: intermittent `expected 0 to be greater than 0`.
   - Implementation: `waitFor` around the request assertions, with a comment explaining why.
   - Verification: 20 looped full runs plus 1 final run, all 230 tests passing each time.
2. **Missing node preview.**
   - Implementation: `NodeCard` was extracted from `DraftNode` and is rendered in "Preview and details".
   - Verification: a component test asserts the hidden preview contains the output port and is not exposed to assistive tech; the existing canvas tests pass unchanged on the refactored `DraftNode`; a screenshot at 1440×900 matches the canvas card.
3. **Missing icons.**
   - Implementation: `CategoryIcon` in the library item and on canvas cards.
   - Verification: screenshot and full test suites.
4. **Lint error, E2E drop point, panel height, test ambiguity:** see §10.
5. **Review cleanups.** One exported `GRID` instead of a duplicate constant and literal; a merged duplicate `import type`.

---

## 12. Testing & Verification

### Unit Tests (`editorState.test.ts`, `describe('add')`)
- Adds and selects as one undo step. **PASS**
- Refuses a duplicate id, and an add during a drag. **PASS**
- Offsets adds onto an occupied spot: `(40,40)`, then `(80,80)`. **PASS**

### Component Tests (`EditorPage.test.tsx`, `describe('Node library')`, real routes against a stub `fetch` backend)
- **Loading:** a loading label, then "3 of 3 nodes" and category headings `Ai, Core, Web`. **PASS**
- **Metadata** for one item:
  - version line, description, capabilities `['Streams output', 'Cancellable']`
  - the hidden preview, whose `Outputs` list contains `text` and is not exposed without `hidden: true`
  - port `prompt` (preview and text list) with `(string, required)`
  - required settings `model, credential`
  - docs link `href`
  - no link for a `javascript:` URL

  **PASS**
- **Search, category and capability filters**, with counts and the no-match state. **PASS**
- **Empty registry.** **PASS**
- **API error** (500 with message), then Retry recovers. **PASS**
- **Two Adds:** two entries, the last selected, one autosave PUT with 4 unique ids, the new ids matching `^node_[0-9a-f]{32}$`, at different positions. **PASS**
- **Canvas drop** adds the node; a drop of `plugin.removed` shows the "Node not added" toast and leaves 3 nodes. **PASS**
- **The existing editor tests (Phase 07)** pass against the refactored `DraftNode`/`NodeCard`. **PASS**

### Integration Tests (full-stack: real backend, PostgreSQL 17.2 in Docker, real registry)
- "Log" is listed, searched (`core.log`) and added; after Ctrl/⌘+S the server's `draft.graph.nodes` is `['core.log']`. **PASS** (suite: 5 passed)

### E2E Tests (Playwright, production build, stubbed network)
- A real HTML5 drag from the library onto the pane gives 3 canvas nodes, the new one selected and labelled `core.log`. Its top-left is within 15 px of the drop point, and the saved node types are `['core.input','core.log','core.log']`. **PASS** (suite: 21 passed)

### Security Tests
- A non-web documentation URL is not linked; an unregistered type cannot be added via drop. **PASS**

### Accessibility Tests
- No automated accessibility tool (axe) was run. The tests query by role and accessible name (region, searchbox, comboboxes, "Add LLM to graph" buttons), and one test verifies the preview is hidden from the accessibility tree. There was no manual screen-reader check.

### Performance Tests
- None performed.

### Visual checks
- Two screenshots at 1440×900, taken with temporary Playwright specs that were deleted afterwards: the three-column layout, and the expanded preview with category icons on both library items and canvas cards. They are not automated tests.

---

## 13. Quality Gates

All were run on the final working tree from `frontend/`.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`npm run format:check`) | PASS | |
| Linter (`npm run lint`) | PASS | Failed once during implementation (§10), then fixed |
| Type Checker (`npm run typecheck`) | PASS | 0 errors |
| Unit / Component Tests (`npm test`) | PASS | 230 tests, 20 files. Before the App-test fix: 2 of 19 runs failed (Finding 3). After: 21 of 21 runs passed. |
| Integration Tests (`npm run test:fullstack`) | PASS | 5 passed |
| API Tests | N/A | No backend change; backend suite not re-run |
| E2E Tests (`npm run test:e2e`) | PASS | 21 passed; pre-existing proxy-error log lines (Finding 5) |
| Build (`npm run build`) | PASS | `EditorPage` chunk 198.78 kB (64.31 kB gzip) |
| Security Checks | N/A | No dependency or audit change; behavior covered by component tests |
| Accessibility | N/A | No automated audit (§12) |
| Backend gates | N/A | No backend files changed |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| The Node Library uses the backend Node Registry | PASS | `NodeLibrary` renders only `nodeQueries.catalog` (`GET /nodes`). The full-stack test lists and adds the real registry's "Log" node. |
| No hard-coded complete node list | PASS | No node types in `NodeLibrary.tsx` or `NodeCard.tsx`. Categories and capabilities are derived from the catalog. The static maps are display data only: `CAPABILITY_LABELS` (4 labels, generic fallback) and `CATEGORY_GLYPHS` (8 glyphs, generic fallback). |
| Nodes can be dragged into the editor | PASS | E2E drag test (real browser, position and save checked); component drop test |
| Node metadata is displayed | PASS | Component test "shows what a node does, its ports, settings, capabilities and version" |

Roadmap scope items:

| Item | Status |
|---|---|
| Search, categories, filters | Implemented and tested |
| Node preview | Implemented and tested (`NodeCard` in "Preview and details") |
| Description, version, capabilities | Implemented and tested |
| Drag & drop, add to graph | Implemented and tested |

---

## 15. Security Considerations

- **Unsafe URLs / XSS.** `documentation_url` is plugin-supplied, so only `http(s)` URLs are linked (`noopener noreferrer`). All metadata, including the preview's titles and port names, is escaped React text.
- **Untrusted drag payload.** It is accepted only under the library MIME type, and only for types present in the backend catalog.
- **Secrets.** New nodes have `config: {}`. The backend plaintext-secret check on draft save is unchanged.
- **Authentication, authorization, CSRF, SSRF, logging.** No changes.

---

## 16. Performance Considerations

- Filtering is O(n) per keystroke over tens of entries, with no memoization.
- Each item's preview `NodeCard` is rendered into the DOM even while its `<details>` is closed (the browser only hides it), so the library renders one small card per node. With tens of nodes this was not measured as an issue.
- `freeSpot` is O(V) per add.
- `EditorPage` chunk: 197.06 kB before the preview and icon work, 198.78 kB after (gzip 63.75 → 64.31 kB). No other measurements were made.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `frontend/README.md` | New feature | "Node library" section: data source, filters, item content and preview (`NodeCard` shared with the canvas), category icons and their fallback, the `http(s)` link rule, add and drop placement, undo and save, refused additions, config in Phase 09. The "Draft editor" canvas bullet now mentions drops. |
| `docs/reports/frontend/phase-08-node-library.md` | Phase report | This document |

**Future documentation updates:** if the node contract ever gets an icon field, update the README and `docs/architecture.md`.

---

## 18. Dependencies / Infrastructure Changes

None:
- no package changes (`package.json` and `package-lock.json` unchanged)
- no configuration, environment, Docker or database changes
- the test infrastructure is reused unchanged

---

## 19. Deferred Work

| What | Why | Intended phase | Affects completion? |
|---|---|---|---|
| Node configuration forms and inspector | Out of scope (roadmap prompt) | 09 | No |
| Connecting and deleting nodes | Out of scope | 10 | No |
| Per-node icons from the contract | Not needed until a plugin needs its own icon (§5.5) | Unassigned | No |

---

## 20. Known Limitations

- **New nodes start with `config: {}`.** Nodes with required settings are invalid until configured (Phase 09); the backend's validation and publish report it.
- **Search and filter state is component state.** It resets when the editor remounts and is not in the URL.
- **Filtering is client-side**, which suits the current unpaginated catalog only.
- **`freeSpot` only avoids exact coordinate collisions**, so partial overlaps are possible.
- **Below `lg`** the library is not height-capped, and adding works only through the Add button on touch devices (HTML5 drag and drop does not support touch).
- **Drops onto React Flow overlay panels** (controls, minimap) do not add a node.
- **A new built-in backend category** gets the generic glyph until `CATEGORY_GLYPHS` is extended.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Vite proxy-error noise in E2E logs (pre-existing) | Log noise, possibly hiding real errors | LOW | E2E test setup | Stub unmatched `/api/v1/**` routes in the affected specs, or fail on unexpected requests | Unassigned |

---

## 22. Architectural / Product Decisions

- **The library lives in `features/editor/`.** It exists only to add nodes to a draft, and it shares the catalog and the drop contract (`NODE_TYPE_MIME`) with `GraphCanvas`.
- **The client checks only registration and id uniqueness** (§5.2). Do not gate adding on client-side config validation.
- **Every add is one undo step that selects the new node**, consistent with paste. Phase 09 can open the inspector on the selection.
- **Button and drop both go through `addNode`**, so the catalog check cannot be bypassed.
- **`NodeCard` is the single source of how a node looks**, in both the canvas and the preview (§5.4).
- **Capability labels and category glyphs are fallback maps, not whitelists.** New backend keys must render without a frontend change.
- **Feature glyphs use the design system's `Icon` frame**, which is now exported.

---

## 23. Repository / Git State

- **Branch:** `main`. HEAD is `07ff1fb` ("frontend phase 07 - workflow draft and graph editor foundation").
- **Working tree:** uncommitted changes from this phase only.
  - Modified: `frontend/README.md`, `frontend/src/app/App.test.tsx`, `frontend/src/components/ui/{icons.tsx,index.ts}`, `frontend/src/features/editor/{EditorPage.tsx,EditorPage.test.tsx,GraphCanvas.tsx,editorState.ts,editorState.test.ts}`, `frontend/tests/e2e/editor.spec.ts`, `frontend/tests/fullstack/workflows.spec.ts`
  - Untracked: `frontend/src/features/editor/{NodeLibrary.tsx,NodeCard.tsx}`, `docs/reports/frontend/phase-08-node-library.md`
  - Diff of tracked files: 11 files, +467 / −71
- **Unrelated changes:** none detected. `App.test.tsx` lies outside the phase's feature, but it was changed deliberately to fix Finding 3.
- **Not committed.** Nothing was reset, stashed or reverted.

---

## 24. Phase Completion Assessment

### Implemented
- The library: listing by category, search, category and capability filters, category icons, metadata, a preview identical to the canvas card, ports, required settings, capabilities, version and docs link.
- Add by button and by drag and drop.
- Reducer `add` with duplicate-id and overlap handling.
- Refusal of unregistered types.
- Loading, error, empty and no-match states.
- The fix for the pre-existing flaky App test.

### Verified
- 3 unit, 7 component, 1 E2E and 1 full-stack new test, all passing.
- The existing Phase 07 editor tests pass on the refactored canvas card.
- All frontend gates pass, and the unit suite passed 21 consecutive runs.

### Not Implemented
- Nothing in this phase's scope.

### Deferred
- The inspector (Phase 09), connections (Phase 10), contract-level per-node icons (only on demand).

### Open
- Pre-existing E2E proxy-log noise (§21).

### Blocking Issues
- None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase (Phase 09 — Node Configuration Inspector)

- **Can assume:**
  - Added nodes have `config: {}` and become the sole selection (`state.selection = [id]`).
  - `catalog: Map<node_type, NodeDefinition>` with `config_schema` is available in `EditorPage`.
  - `NodeLibrary.requiredSettings` reads only top-level `required`. The inspector needs a real schema-driven form.
- **Contracts available:** `GET /nodes` and `PUT /workflows/{id}` with `expected_revision`, both unchanged.
  - Config edits should be a new reducer action, so undo, dirty tracking and autosave keep working.
  - The backend rejects plaintext secrets in config, so use credential references (`credential:<name>`).
- **Must not be changed casually:**
  - `NODE_TYPE_MIME`, shared by the library and the canvas
  - the catalog check in `addNode`
  - the reducer's duplicate-id guard
  - add → select behavior
  - `NodeCard` as the shared rendering of canvas and preview; keep ReactFlow-only parts behind `handles`
- **Known constraints:** the editor loads the workflow once per session (Phase 07). The catalog may refresh in the background; the library handles that via `QuerySection`.
- **Prerequisites:** none.
