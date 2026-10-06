# Phase 25 — Responsive + Accessibility

Report date: 2026-10-05. Branch `main`. Base commit `a46dfad` (frontend phase 24 — performance and UX hardening). All work described here is **uncommitted**.

## 1. Summary

**Objective** (`docs/roadmap-frontend.md` §31): professional usability across screen sizes and for different users. Main functions keyboard-reachable, dialogs accessible, forms labelled, errors communicated accessibly, layout does not break. The graph editor may be desktop-first but must react sensibly on smaller viewports.

**Method.** The audit ran against the production bundle and the real backend (`npm run dev:backend`), seeded with a published and executed workflow, a draft-only workflow and a credential. It had three parts:

- axe-core (WCAG 2.2 A/AA plus best practices) and a page-overflow check on all 17 routes, at 1440 / 1024 / 768 / 390 px, in light and dark;
- a Tab walk of every page, recording each stop's name, focus ring and visibility;
- scripted keyboard-only editor work, form error probes and screenshots of the dense pages.

**Result.** The base was already solid. Earlier phases had built the skip link, focus moving to `<main>` on navigation, native modal dialogs, menus, tooltips, tabs, labelled fields, table regions, color-independent status, reduced-motion handling on the navigation bar, and the accessible node list. The audit found **nine concrete defects**, all fixed (§3). No page scrolls horizontally at any tested width.

**Phase Status:** COMPLETE WITH LIMITATIONS (§8)

## 2. Supported range (V1)

Chromium-based desktop browsers (the configured Playwright project) at widths of 1024 px and up, fully supported. 768 px (tablet) and 390 px (phone) are supported for every screen. The graph editor at those widths is usable but desktop-first (§8).

## 3. Findings and fixes

| # | Finding | Where | Fix |
|---|---------|-------|-----|
| 1 | **Graph editor layout broke below desktop.** At 1024 px the canvas between the library (18 rem) and the panels (24 rem) shrank to ~40 px. Below `lg` it had **zero height**: `flex-1` (basis 0) in a column overrode `h-[32rem]`. At 1280 px it was ~300 px. | `EditorPage.tsx`, `NodeLibrary.tsx` | Grid layout: stacked below `xl`, library beside the canvas from `xl`, three columns from `2xl`. DOM order equals visual order, so Tab order still follows the layout. The library scrolls inside a bounded height so the canvas stays reachable. |
| 2 | **Dark primary failed contrast:** indigo-500 links reached 3.9–4.4:1 on dark surfaces and 2.9:1 inside info alerts; text on primary buttons reached 4.4:1. Hit almost every screen. | `styles/index.css` | Dark `--color-primary` → indigo-300 (≥ 6.7:1 on every dark surface and alert tone; on-primary text 10:1), hover → indigo-200. Light theme unchanged. |
| 3 | **Canvas nodes and edges had no visible focus.** React Flow sets `outline: none` on them, yet Tab reaches them. | `styles/index.css` | Focus ring restored on nodes; focused edges get the focus color at a wider stroke. |
| 4 | **Server-side field errors were silent.** A duplicate credential name or a wrong current password showed inline while focus stayed on the submit button, and nothing was announced. | `components/ui/Field.tsx` | The field error is `role="alert"`. Fixed once in the shared `Field`, so every form gets it. |
| 5 | **Graph changes were not announced.** Adding a node keeps focus on the library button; deleting, connecting and undo also change the graph away from focus. | `EditorPage.tsx` | A polite status region: "N nodes, M connections". |
| 6 | **Thirteen identical "Preview and details" summaries** in the node library. | `NodeLibrary.tsx` | Visually hidden suffix "of {node name}". |
| 7 | **Read-only snapshot edges were Tab stops** with no action whose name was raw node ids ("Edge from node_0100…"), and they were zero-size. | `SnapshotCanvas.tsx` | `edgesFocusable={false}`. Nodes stay focusable (React Flow pans to them). |
| 8 | Invalid description-list structure (`dl > div > div > dt`) on Workflow and Version details; a heading skip (h2 → h4) in the version comparison; the React Flow attribution link at 2.8:1. | `WorkflowDetailPage.tsx`, `VersionDetailPage.tsx`, `styles/index.css` | The wide item takes the column span itself; `h3`; attribution uses `--color-muted`. |
| 9 | **Motion ignored `prefers-reduced-motion`:** the animated pan to a node selected in the node list, and the button spinner. | `EditorPage.tsx`, `icons.tsx` | Pan duration 0 and a static spinner under reduced motion. |

A `target-size` finding (Fit View control partly covered at 1024 px) disappeared with fix 1.

### Reviewed, no change needed

- **Semantic HTML / ARIA:** native `<dialog>`, `<details>`, `<button>` and `<table>` with caption and `scope`. ARIA is used only where HTML has nothing (menu, tabs, tooltip, the graph's `group`s); no new ARIA was added where HTML suffices.
- **Dialogs / menus / tooltips:** focus containment, Escape and focus return are already tested in `ui.spec.ts`.
- **Tables:** the focusable, named scroll region keeps wide tables from widening the page.
- **Loading:** `role="status"` and `aria-busy`.
- **Toasts:** a notification region with alert/status.
- **Color:** never the only cue (icons and screen-reader prefixes, dashed invalid edges).
- **Keyboard:** Tab order is logical on every page with no traps, and every stop shows a focus ring (after fix 3).
- **Graph alternative:** the node list, inspector and connection form (AGENTS §100) cover what the canvas shows.

## 4. Tests

### New dev dependency

`@axe-core/playwright` 4.13.0 (MPL-2.0, sole dependency `axe-core`, pinned exactly). It is the standard engine for automated WCAG checks; reimplementing its rules is not reasonable.

### E2E (stubbed backend, `npm run test:e2e`): 34 passed

- `tests/e2e/a11y.ts`: `expectAccessible` (axe, WCAG 2.2 AA + best practice) and `expectNoHorizontalScroll`.
- `ui.spec.ts`: the design-system catalog passes axe in light and dark (guards the tokens).
- `editor.spec.ts`:
  - **keyboard-only graph editing.** Add from the library (announced, focus kept) → Tab into the canvas to a node with a visible ring → Enter selects it → connect through the form → Delete removes the node and its connections → Ctrl+S saves.
  - **canvas at 390 / 768 / 1024 / 1280 / 1600 px:** the canvas keeps at least 400 px of height and usable width, with no page scroll and no axe violations.

### Full-stack (real backend, `npm run test:fullstack`): 19 passed

`tests/fullstack/a11y.spec.ts` seeds a published, executed workflow and a credential. It then checks all 15 screens plus Not Found at 390 / 768 / 1024 / 1440 px, in light and dark, against axe and the overflow check.

### Testing the tests

With the old CSS and editor layout restored, the dark axe test, the keyboard test and all five layout tests failed. With the fixes they pass.

### Unit

469 passed. No unit test was changed; jsdom has no layout, contrast or real focus rendering.

## 5. Quality gates

| Gate | Command | Result |
|------|---------|--------|
| Format | `npm run format:check` | PASS |
| Lint | `npm run lint` | PASS |
| Typecheck | `npm run typecheck` | PASS |
| Unit | `npm test` | PASS (469) |
| Build | `npm run build` | PASS |
| E2E | `npm run test:e2e` | PASS (34) |
| Full-stack E2E | `npm run test:fullstack` | PASS (19; needs Docker) |

## 6. Architectural decisions

None architecturally significant, so no ADR. The editor's breakpoints (stacked < 1280 px, two columns < 1536 px, three columns above) are documented in a comment at the layout.

## 7. Files

- **Source:**
  - `components/ui/Field.tsx`, `components/ui/icons.tsx`;
  - `features/editor/EditorPage.tsx`, `features/editor/NodeLibrary.tsx`;
  - `features/workflows/WorkflowDetailPage.tsx`;
  - `features/versions/SnapshotCanvas.tsx`, `features/versions/VersionDetailPage.tsx`;
  - `styles/index.css`.
- **Tests:** `tests/e2e/a11y.ts` (new), `tests/e2e/editor.spec.ts`, `tests/e2e/ui.spec.ts`, `tests/fullstack/a11y.spec.ts` (new).
- **Dependencies:** `package.json`, `package-lock.json`. The lockfile also drops some `"peer": true` markers: npm-version metadata, not a dependency change.

## 8. Known limitations

- **No manual screen-reader pass** (NVDA, VoiceOver). Announcements were verified through the DOM (roles, live regions, names). axe detects only part of real issues.
- **Chromium only.** Firefox and WebKit are not configured in Playwright; browser compatibility is Phase 27.
- **Some graph editing stays pointer-only, with keyboard alternatives:**
  - drawing an edge by dragging (alternative: the connection form);
  - drag from the library (alternative: Add);
  - box selection (alternative: Ctrl+A on the canvas).

  Arrow keys move a focused node (React Flow).
- **Phones:** a touch drag on the canvas pans the graph, not the page; scroll beside it. The main navigation scrolls horizontally without a visible hint.
- **Server field errors** are announced, but focus does not move to the first invalid field.
- **Three-column editor** (≥ 1536 px): the canvas is about 450 px wide because `<main>` is capped at `max-w-6xl` for all pages.
- **Narrow duration tables:** a value like "194 ms" can wrap between number and unit.

## 9. Technical debt

- The full-stack sweep takes about 2 minutes per color scheme. If it grows, run axe at 390 / 1440 px only and keep the overflow check at every width.

## 10. Not implemented (future phases)

- Broader E2E workflows (Phase 26).
- Browser matrix and production readiness (Phase 27).
- Optional editor features from AGENTS §81 (e.g. keyboard edge creation on the canvas itself) were not added. The form alternative meets the acceptance criteria.
