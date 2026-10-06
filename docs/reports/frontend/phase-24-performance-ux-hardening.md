# Phase 24 — Performance + UX Hardening

Report date: 2026-10-05. Branch `main`. Base commit `7731315` (frontend phase 23 — frontend security hardening). All work described here is **uncommitted** (§23).

## 1. Executive Summary

**What the phase was for.** `docs/roadmap-frontend.md` §30 asks for the frontend to be optimized for real use. The areas to review were:

- bundle size, code splitting and lazy loading;
- graph rendering and large-graph performance;
- execution list pagination and virtualized lists;
- query caching and unnecessary re-renders;
- WebSocket event handling, debouncing and autosave;
- loading states.

Optimizations had to be based on measured or clearly traceable problems.

**What was implemented.** The review found four real defects, all in the graph editor and in server-state caching, and fixed each one:

1. **Canvas derivation was quadratic.** Building the React Flow nodes and edges from a 500-node / 800-edge draft (the backend's default `max_graph_nodes` limit) took 426 ms per editor change, i.e. per drag frame. It now takes about 2–8 ms.
2. **Every node card re-rendered on every change**, twice per drag frame. Now only the changed cards re-render; on a drag that is the moved card.
3. **Layout defect with large drafts (pre-existing).** The editor's accessible node list grew without limit. The canvas stretched to its height (~12 000 px at 500 nodes), so `fitView` centred the graph off-screen.
4. **Redundant requests:**
   - every live refetch reloaded every log page the user had loaded;
   - draft validation was re-POSTed on every window focus.

In a real Chromium browser, 30 drag moves on a 500 / 800 graph took 24.96 s before and 3.27 s after. All other review areas already met the criteria; no change was made there (§9, Finding 6). The roadmap's four acceptance criteria are met (§14). About 100 ms per drag move remains at the node limit, spent inside React Flow (§20).

**Phase Status:** COMPLETE WITH LIMITATIONS

## 2. Phase Objective

Roadmap (`docs/roadmap-frontend.md` §30): "Frontend für reale Nutzung optimieren" (optimize the frontend for real use). The areas to review are listed in §1. Acceptance criteria:

- large graphs stay usable;
- long execution lists stay fast;
- no unnecessary API requests;
- the editor responds smoothly.

The phase prompt adds a required loop (measure → identify the bottleneck → optimize → verify → regression test → review). It also asks for checks of loading feedback, optimistic updates, disabled, error and empty states, navigation, unsaved changes, reconnect behavior and perceived responsiveness. Normal and large datasets were to be tested.

The constraints come from `frontend/AGENTS.md` §59 ("Memoization nicht blind einsetzen. Zuerst messen" — do not memoize blindly, measure first) and `docs/roadmap-frontend.md` ("Keine vorzeitige komplexe Optimierung" — no premature complex optimization).

**Not in scope:**

- responsive design and accessibility (Phase 25);
- broader E2E coverage (Phase 26);
- production readiness (Phase 27);
- any backend change.

## 3. Scope Implemented

### Frontend: graph editor

- Per-graph lookup index (`graphIndex`, `edgesOf` in `features/editor/graphView.ts`).
- `sameEdge` compares fields instead of building two `edgeKey` strings.
- `connectionProblem` (`features/editor/connections.ts`):
  - looks nodes up by id;
  - checks the duplicate and occupied rules only against the target node's incoming edges;
  - no longer copies the whole edge array on each call.
- `GraphCanvas.tsx`:
  - unchanged canvas nodes (`canvasNode`) and edges (`reuseEdge`) keep their object identity;
  - `DraftNode` reads `CheckContext` only while a connection is being dragged.
- `ConnectionsPanel.tsx`, `SnapshotCanvas.tsx` and `EditorPage.tsx` (node list edge counts) use the index.
- The node list in `EditorPage.tsx` scrolls inside `max-h-[32rem]`.

### Frontend: server state

- Log infinite query (`executionQueries.logs` in `api/queries.ts`): a refetch reuses cached full pages and requests only the tail.
- `workflowQueries.validation` gets `staleTime: INSTALLATION_STALE_TIME_MS`.

### Testing

- New `features/editor/GraphCanvas.test.tsx`: re-render scope of the canvas.
- Extended `ExecutionInspector.test.tsx`: a log refetch requests only the tail page.
- New E2E test in `tests/e2e/editor.spec.ts`: a draft at the node limit (500 nodes / 800 edges) is rendered, dragged and saved.

### Documentation

- This report.

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/src/features/editor/graphView.ts` | Modified | Added `GraphIndex`, `graphIndex()` (cached in a `WeakMap` per graph), `edgesOf()`; `sameEdge` compares fields |
| `frontend/src/features/editor/connections.ts` | Modified | `connectionProblem` uses the index; no per-call `others` array |
| `frontend/src/features/editor/GraphCanvas.tsx` | Modified | `canvasNode()` / `reuseEdge()` object reuse; conditional `use(CheckContext)`; indexed edge titles; removed the earlier `ponytail:` note that deferred this work to Phase 24 |
| `frontend/src/features/editor/ConnectionsPanel.tsx` | Modified | Indexed ports, missing inputs and titles |
| `frontend/src/features/editor/EditorPage.tsx` | Modified | Node list scrolls (`max-h-[32rem] overflow-y-auto`); edge counts from `edgesOf` |
| `frontend/src/features/versions/SnapshotCanvas.tsx` | Modified | Ports from `edgesOf` (same O(N·E) pattern as the editor) |
| `frontend/src/api/queries.ts` | Modified | Tail-only log refetch; validation stale time |
| `frontend/src/features/editor/GraphCanvas.test.tsx` | Created | Re-render regression tests |
| `frontend/src/features/executions/ExecutionInspector.test.tsx` | Modified | Refetch requests only the tail log page |
| `frontend/tests/e2e/editor.spec.ts` | Modified | `largeGraph()` fixture and the node-limit test |
| `docs/reports/frontend/phase-24-performance-ux-hardening.md` | Created | This report |

No new directories, migrations, configuration, generated files or dependency changes. A temporary benchmark directory (`src/features/editor/__bench/`) was used for measurement and deleted before completion.

## 5. Architecture Changes

No architectural changes were introduced during this phase. The decisions below are implementation decisions inside the existing editor and server-state layers.

### Decision 1: A per-graph lookup index, cached by object identity

- **Context.** `connectionProblem`, `portsOf`, `missingInputs` and the edge titles each scanned all nodes or edges. They ran once per node or edge on every canvas render, which is O(N·E + E²) per drag frame.
- **Chosen approach.** `graphIndex(graph)` builds `nodes`, `incoming` and `touching` maps once per `Graph` object and stores them in a module-level `WeakMap`.
- **Alternatives.**
  - Memoizing `GraphCanvas` on a structural key: more state, and it does not help `ConnectionsPanel` or the per-port checks during a connection drag.
  - Changing the signatures of `connectionProblem`, `portsOf` and `missingInputs` to take an index: a larger diff for every caller.
- **Reason.** Draft graphs are never mutated: the reducer (`editorState.ts`, e.g. `withPositions`, `commit`) always creates a new graph. Caching by object identity is therefore correct, needs no invalidation, and keeps every existing signature.
- **Consequences.** Code must keep treating `Graph` objects as immutable. Mutating a graph in place would serve a stale index.

### Decision 2: Reuse canvas node and edge objects instead of memoizing components

- **Context.** React Flow re-renders a node or edge whenever its object changes. `GraphCanvas` rebuilt every object on each change.
- **Chosen approach.** `canvasNode()` reuses the last object built for an unchanged `NodeInstance`. "Unchanged" compares the catalog, selection, measured size, issue count and own edges, each by identity. `reuseEdge()` does the same per `Edge`, comparing selection, aria label, label and class.
- **Alternatives.** `React.memo` on `DraftNode` would not help: React Flow's wrapper re-renders on a new node object regardless.
- **Consequences.** Anything new that a card or edge displays must be added to the reuse comparison, or the canvas shows stale data. `GraphCanvas.test.tsx` covers selection, moves and edge removal.

### Decision 3: Treat full log pages as immutable

- **Context.** On refetch, a TanStack infinite query refetches every loaded page in sequence.
- **Chosen approach.** The query function returns a cached page that has `has_more: true` and fetches only the rest.
- **Reason.** The backend's `node_logs.position` is an identity column written "by one CAS-guarded writer per execution" in order (`backend/src/akasha/infrastructure/db_models.py`). A full page with a fixed filter and cursor therefore cannot change.
- **Consequences.** The optimization depends on that backend ordering guarantee. If logs ever gain concurrent writers or in-place edits, this must be revisited.

## 6. Domain / Data Model Changes

Not applicable. No domain, schema, DTO or persistence change.

## 7. API / Contract Changes

No contract changes. The same endpoints are called with the same parameters. Only the number of calls dropped:

- `GET /executions/{id}/logs` on refetch;
- `POST /workflows/{id}/validate` on window focus.

## 8. Implementation Details

- **`graphIndex(graph)`.** Returns:
  - `nodes`: a `Map` from id to node;
  - `incoming`: a `Map` from target id to its edges;
  - `touching`: a `Map` from node id to the edges into or out of it, with a self-loop listed once.

  `edgesOf(graph, id)` returns a shared empty array when a node has no edges. That keeps the result stable and allocation-free.
- **`connectionProblem`.**
  - The duplicate check (`sameEdge`) and the occupied check (same `target_port`) run only over `incoming(target)`, minus the edge being reconnected.
  - This is equivalent to the old full scan, because both checks require the same target node.
  - The existing `connections.test.ts` cases still pass.
- **`canvasNode`.**
  - The cache is keyed by `NodeInstance`. A moved node is a new instance, so it is rebuilt.
  - `measured` defaults to a constant `UNMEASURED` object, so an unmeasured node does not count as changed.
  - The own-edges comparison is element-wise by identity. Edge objects keep their identity across moves because `withPositions` keeps `graph.edges`.
- **`DraftNode`.**
  - `useConnection` returns a string that changes only when a connection drag starts or ends.
  - `use(CheckContext)` is called only while that string is set. React 19 allows `use` to be called conditionally.
  - So a card subscribes to the frequently changing check only during a connection drag.
- **Log `queryFn`.** It reads `client.getQueryData(queryKey)` from the query function context, then finds the page whose `pageParam` matches. A cached full page is returned without a request. "Load more" (a new `pageParam`) and first loads always request.
- **Validation staleness.** The key includes the revision. A save changes the revision and invalidates the `workflows.detail(id)` prefix, so the verdict for a new draft is always fetched.

## 9. Findings

### Finding 1: Canvas derivation was quadratic

- **Impact:** HIGH. **Resolution:** FIXED.
- Measured with a temporary Vitest benchmark of the memo bodies, per editor change:

  | Graph (nodes / edges) | Time per change |
  |---|---|
  | 50 / 60 | not recorded |
  | 200 / 300 | not recorded |
  | 500 / 800 | 426 ms |
  | 500 / 1500 | 1 445 ms |

- After the field-wise `sameEdge` alone: 47.8 ms at 500 / 800.
- After adding the index: 0.32 / 2.2 / 7.8 / 15.5 ms for the four sizes. The split at 500 / 800, warmed up: index 0.67 ms, nodes 0.59 ms, edge checks 0.89 ms.
- An earlier phase had left a `ponytail:` note in `GraphCanvas.tsx` that deferred exactly this to Phase 24.

### Finding 2: Every card re-rendered, twice per drag frame

- **Impact:** HIGH. **Resolution:** FIXED.
- Measured with a React `Profiler` and a counting `NodeCard` (jsdom), per frame:

  | Graph (nodes / edges) | Before | After nodes fix | After nodes + edges fix |
  |---|---|---|---|
  | 20 / 20 | 40 renders | — | — |
  | 100 / 100 | 200 renders, 112 ms commit | 1 render, 10 ms | — |
  | 250 / 400 | 500 renders, 455 ms commit | 1 render, 113 ms | 1 render, 88 ms |

- There were two causes:
  - new node and edge objects on every change;
  - `CheckContext` receiving a new function on every render.

### Finding 3: The node list pushed the canvas off-screen with large drafts

- **Impact:** MEDIUM. **Resolution:** FIXED.
- Discovered by the new E2E test: the dragged node's bounding box was at y ≈ 12 039 px.
- **Root cause.** The canvas uses `lg:h-auto` inside a flex row, so it stretched to the height of the right column, which holds the unbounded 500-row node list.

### Finding 4: Live log refetch reloaded every loaded page

- **Impact:** MEDIUM. **Resolution:** FIXED.
- With *k* pages loaded, each live reconcile (at most one per second, `RECONCILE_INTERVAL_MS`) sent *k* sequential requests.

### Finding 5: Draft validation was re-POSTed on every window focus

- **Impact:** LOW. **Resolution:** FIXED.
- `TriggersPage` uses this query with `staleTime` 0, so it was refetched on every focus.

### Finding 6: These areas already met the criteria

- **Impact:** LOW. **Resolution:** no change needed.
- **Code splitting.** Every route is `lazy` (`app/routes.tsx`). The build output:
  - entry `index` chunk: 237.6 kB (74.2 kB gzip);
  - `GraphCanvas`: 198.4 kB (64.2 kB gzip), loaded only by graph views;
  - `queries`: 130.5 kB (37.7 kB gzip), mostly Zod;
  - `EditorPage`: 32.1 kB.
- **Lists.** Executions, workflows and reports (25 per page), versions and credentials (50 per page) and version executions (20 per page) are paginated on the server.
- **Logs and events.** Logs use cursor paging. The event timeline steps by `EVENT_STEP = 200` and live activity is capped at `MAX_ACTIVITY = 200`.
- **WebSocket.**
  - reconcile is throttled to 1 per second;
  - duplicate and stale events are dropped by position;
  - reconnects use exponential backoff and resync on resubscribe;
  - the final state is applied without delay.
- **Autosave.** Debounced (1.5 s), single-flight, paused while dragging and after a failure, and guarded by the revision.
- **Editor state checks.** Node config validation is debounced and cached per config. `isDirty` is a reference compare.
- **Navigation.** Unsaved-changes blocker plus `beforeunload` (`useDraftEditor`).

### Finding 7: The 500-row node list is not a meaningful cost

- **Impact:** LOW. **Resolution:** no change needed.
- Experiment: not rendering the list changed the real-browser drag time from 3.27 s to 3.17 s, so the list was not memoized.

## 10. Bugs / Problems Found

| Problem | Classification | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|---|
| Large graph centred off-screen | Pre-existing bug | Unbounded node list set the flex row height; the canvas stretched to it | Node list scrolls within `max-h-[32rem]` | E2E "stays usable with a graph at the node limit" (fails without the fix: node unreachable) | Editor layout |
| Quadratic canvas derivation | Pre-existing performance defect | Linear scans and string-building `sameEdge`, per node or edge | `graphIndex`, field-wise `sameEdge` | E2E node-limit test (old code: 24.96 s, over the 6 s bound) | Editor |
| All cards re-rendered per frame | Pre-existing performance defect | New objects per change; unstable context value | `canvasNode` / `reuseEdge`; conditional `use` | `GraphCanvas.test.tsx` (both tests fail on the old `GraphCanvas.tsx`) | Editor |
| Log pages re-requested | Pre-existing inefficiency | Infinite-query refetch reloads every page | Reuse full pages | `ExecutionInspector.test.tsx` (fails on the old `queries.ts`) | Execution inspector |

Problems in this phase's own work, fixed before completion:

- A first E2E fixture produced duplicate edges and "occupied" labels over nodes, so the drag missed its target. The fixture was rewritten to use distinct pairs into a `multiple` input, and now asserts zero `.edge-invalid` elements.
- A debugging line that used `document` broke `tsc -b` and with it the Playwright `webServer` build. The line was removed.
- Lint flagged `no-empty-function` in the new test; `vi.fn()` is used instead.
- The first `canvasNode` version did not typecheck (`measured` type under `exactOptionalPropertyTypes`). `Size` is now `NonNullable<CanvasNode['measured']>`.

No environmental problems beyond §12, Infrastructure.

## 11. Fixes Applied

1. **Slow drags on large graphs.** Root cause: the quadratic derivation and the full re-render (§9, Findings 1–2). Implementation: §8. Verification:
   - benchmark numbers in §9;
   - `GraphCanvas.test.tsx`;
   - the E2E node-limit test: 24.96 s → 3.27 s.
2. **Graph off-screen.** Root cause: §10. Implementation: one class change in `EditorPage.tsx`. Verification: the node's bounding box moved from y ≈ 12 039 to ≈ 297; the E2E test passes.
3. **Redundant log requests.** Implementation: the `queryFn` in `api/queries.ts`. Verification: the extended inspector test asserts requests `['0', '100', '100']` after a focus refetch, and still 101 rows.
4. **Validation re-POST on focus.** Implementation: one `staleTime` line. Verification: the existing `TriggersPage` tests pass. **No dedicated test** for the focus behavior.

## 12. Testing & Verification

### Unit tests

- The existing `connections.test.ts` and `editorState.test.ts` pass unchanged. They cover the `connectionProblem` rules (self, unknown node, duplicate, occupied, incompatible, replacing) on the new index-based implementation.

### Component tests (Vitest + React Testing Library)

- `GraphCanvas.test.tsx`, 30-node chain:
  - over three moves, only "Step 5" re-renders;
  - selecting n7 re-renders only "Step 7";
  - removing the edge into n12 re-renders only "Step 11" and "Step 12".
  - Result: PASS. Both tests FAIL against the base commit's `GraphCanvas.tsx`.
- `ExecutionInspector.test.tsx` ("filters logs through the backend and pages through large logs"): after two pages, a `focusManager` refetch requests only `after=100`. Result: PASS; it FAILS against the base `queries.ts`.
- Full suite: 38 files, 469 tests, PASS. Re-run on the final tree while writing this report.

### E2E tests (Playwright, production bundle, API stubbed at the network boundary)

- New test "stays usable with a graph at the node limit":
  1. 500 nodes and 800 edges render;
  2. there are 0 invalid-edge labels;
  3. "Step 262" is dragged through 30 moves; the duration is recorded as the `drag-ms` annotation and must be under 6 000 ms;
  4. the draft is saved with the moved position and all 800 edges.
  - Result: PASS at 3 269 ms. On the old editor code (with only the layout fix) it FAILS at 24 959 ms.
- Full E2E suite: 26 passed.

### Integration tests (full stack)

- `npm run test:fullstack`: real backend and PostgreSQL started by the suite's own scripts (Docker 29.1.2). 17 passed.
- After the run, the backend web server logged an asyncpg `ConnectionError: unexpected connection_lost()` during shutdown. It did not fail any test; it was not investigated further.

### Performance tests

- The benchmarks and profiler measurements in §9 were temporary and have been deleted. The lasting guards are the component tests above and the E2E time bound.

### Not performed

- No security or accessibility tests specific to this phase (none of the changes touch those surfaces).
- No backend tests: the backend did not change.

## 13. Quality Gates

All gates were run on the final tree.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`npm run format:check`) | PASS | |
| Linter (`npm run lint`) | PASS | Failed once on `no-empty-function` in the new test; fixed |
| Type Checker (`npm run typecheck`) | PASS | Failed once on `measured` typing; fixed |
| Unit / Component Tests (`npm test`) | PASS | 469 tests |
| Integration Tests (`npm run test:fullstack`) | PASS | 17 tests; backend shutdown log noise (§12) |
| API Tests | N/A | No API change |
| E2E Tests (`npx playwright test`) | PASS | 26 tests |
| Build (`npm run build`) | PASS | Chunk sizes in §9, Finding 6 |
| Security Checks | N/A | The phase 23 ESLint security rules are part of `lint` and pass |
| Accessibility | N/A | Not in scope. The node list keeps its semantics; it only scrolls, and its buttons stay keyboard-reachable |

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Große Graphen bleiben nutzbar (large graphs stay usable) | PASS | The E2E test at the backend's node limit (500 / 800) renders, drags and saves. Before, the graph was off-screen and a drag took 25 s. |
| Lange Execution Lists bleiben performant (long execution lists stay fast) | PASS | Already paginated on the server (`ExecutionsPage` `PAGE_SIZE = 25`, `keepPreviousData`); logs are cursor-paged; events and activity are capped. No change needed (§9, Finding 6). |
| Keine unnötigen API Requests (no unnecessary API requests) | PASS | Log refetch requests only the tail (tested). Validation is no longer re-POSTed on focus (untested one-liner). Existing throttling and debouncing reviewed. |
| Editor reagiert flüssig (the editor responds smoothly) | PASS, with limitation | One card render per drag frame (tested); derivation ~2–8 ms. About 109 ms per drag move remains at 500 / 800 under Playwright input, spent in React Flow (§20). Smaller graphs were not timed in a real browser. |

None of the limitations blocks phase completion. The remaining editor cost is at the configured maximum graph size.

## 15. Security Considerations

No security-relevant change.

- The log page reuse only returns data already in the client cache for the same query key: the same execution and filter, from the same authorized session.
- No new storage, URLs, rendering paths or dependencies.

## 16. Performance Considerations

- **Measurements.** See §9. The jsdom commit times are relative indicators only; jsdom DOM work is far slower than a browser's.
- **Memory.** The `WeakMap` caches (`graphIndex`, `built`, `builtEdges`) are keyed by graph, instance and edge objects. They are released with those objects (e.g. when undo history entries beyond `HISTORY_LIMIT` are dropped).
- **Remaining bottleneck.** About 100 ms per drag move at 500 / 800 nodes / edges in Chromium, inside React Flow's store update and its edge and minimap rendering. `onlyRenderVisibleElements` was not adopted: at minimum zoom every element is visible.
- **Request frequency during live runs.** At most one detail refetch per second, as before. Log traffic per refetch is now constant instead of growing with the number of loaded pages.

## 17. Documentation Changes

- `docs/reports/frontend/phase-24-performance-ux-hardening.md` (this report).
- Code comments document the invariants behind the changes:
  - graph immutability (`graphIndex`);
  - object reuse (`GraphCanvas.tsx`);
  - the log ordering guarantee (`api/queries.ts`).
- No architecture or roadmap document needed updating; no behavior described there changed.

## 18. Dependencies / Infrastructure Changes

None. No dependency, configuration, environment, Docker or test-infrastructure change.

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion? |
|---|---|---|---|
| Virtualizing log lines loaded through "Load more" | Loading is driven by the user, 100 lines at a time; no measured problem | none planned | No |
| Further editor drag cost inside React Flow | Library-internal; would need deeper React Flow tuning or level-of-detail rendering, without a measured target | none planned | No |
| Dedicated test for validation focus caching | One-line configuration that matches the existing `configValidation` | — | No |

## 20. Known Limitations

- About 100 ms per drag move at 500 nodes / 800 edges (Chromium, Playwright input).
- The E2E bound (6 s for 30 moves) is deliberately loose. It catches order-of-magnitude regressions, not small ones.
- The node and edge reuse requires any new displayed property to be added to the reuse comparison in `canvasNode` / `reuseEdge`.
- The log reuse assumes append-only, in-order logs per execution (§5, Decision 3).

## 21. Open Issues

| Issue | Impact | Severity | Area | Next action |
|---|---|---|---|---|
| asyncpg `ConnectionError` logged by the full-stack backend at shutdown | Log noise only; tests pass | LOW | Full-stack test harness / backend shutdown | Check whether it predates this phase; if so, track separately |

No other known open issues result from this phase.

## 22. Architectural / Product Decisions

- Draft `Graph` objects are immutable by contract. Indexes and canvas objects are cached by identity on that basis.
- In the canvas, unchanged React Flow node and edge objects keep their identity on purpose; React Flow re-renders by identity.
- Full log pages are treated as immutable because of the backend's single in-order writer per execution.
- The editor's node list scrolls by itself, so the canvas height never depends on the number of nodes.

## 23. Repository / Git State

- Branch `main`, HEAD `7731315`. Nothing committed for this phase.
- Modified:
  - `frontend/src/api/queries.ts`
  - `frontend/src/features/editor/{ConnectionsPanel,EditorPage,GraphCanvas}.tsx`
  - `frontend/src/features/editor/{connections,graphView}.ts`
  - `frontend/src/features/executions/ExecutionInspector.test.tsx`
  - `frontend/src/features/versions/SnapshotCanvas.tsx`
  - `frontend/tests/e2e/editor.spec.ts`
- Untracked:
  - `frontend/src/features/editor/GraphCanvas.test.tsx`
  - this report
- Diff stat: 9 files, +308 / −77, excluding the untracked files.
- No unrelated changes were detected.

## 24. Phase Completion Assessment

- **Implemented:**
  - graph index;
  - field-wise edge compare;
  - canvas object reuse;
  - conditional context read;
  - scrolling node list;
  - tail-only log refetch;
  - validation stale time.
- **Verified:**
  - all quality gates (§13);
  - before/after measurements (§9);
  - regression tests that fail on the old code (§12).
- **Not implemented:** log line virtualization; React-Flow-internal drag tuning.
- **Deferred:** §19.
- **Open:** backend shutdown log noise in the full-stack run (§21).
- **Blocking issues:** none.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

- **Phase 25 can assume:**
  - the editor handles drafts at the node limit;
  - the node list is a scroll container (`max-h-[32rem] overflow-y-auto`) whose rows are buttons, which is relevant for keyboard and responsive review.
- **Do not change casually:**
  - graph immutability (`editorState.ts` reducers must return new graphs);
  - the reuse comparisons in `GraphCanvas.tsx`;
  - the conditional `use(CheckContext)`.

  Breaking any of these shows up as stale cards or re-rendering every card; `GraphCanvas.test.tsx` guards them.
- **Backend dependency.** The log query relies on the backend's ordered, append-only `node_logs` writes. A backend change to log writing must re-check `executionQueries.logs`.
- **E2E fixture.** The `largeGraph()` fixture in `tests/e2e/editor.spec.ts` is available for further large-graph E2E work (Phase 26).
