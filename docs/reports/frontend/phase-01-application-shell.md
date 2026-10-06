# Phase 01 — Application Shell

## 1. Executive Summary

Phase 01 was supposed to establish the global application shell. Per `/docs/roadmap-frontend.md` §7 that means: router, layout, sidebar, header, navigation, content area, error boundary, loading states, global error handling and basic navigation for the eight main areas (Dashboard, Workflows, Executions, Monitoring, Reports, Credentials, Plugins, Settings). The task prompt also asked for:

- route-level code splitting
- a not-found page
- application-level state boundaries
- shell support for a future Editor area

What was implemented:

- **Routing:** React Router `8.4.0` in data-router mode, with the central route table in `frontend/src/app/routes.tsx`.
- **Layout:** one layout route (`AppLayout`) containing:
  - the app name
  - the main navigation (a sidebar from the `md` breakpoint up, a horizontally scrolling nav bar below it)
  - a skip link
  - the `<main>` content area
- **Areas:** routes for all eight areas plus `/workflows/:workflowId/editor`. Each area is a `lazy` route, so it is code-split. All areas currently share one placeholder chunk.
- **Root redirect:** `/` redirects to `/dashboard`.
- **Error boundaries:** two levels. The root boundary catches shell failures. A pathless content boundary keeps the navigation usable when a page fails.
- **Not found:** a 404 page rendered inside the shell.
- **Loading:** an initial-load fallback (`HydrateFallback`), plus a pending-navigation indicator with `aria-busy` on `<main>`.
- **Tests:** 16 Vitest tests in 4 files and 3 Playwright E2E tests.

Resulting state: the app boots into a navigable shell. Every quality gate passes (format, lint, typecheck, unit/component tests, build, E2E). No feature functionality was implemented.

Acceptance criteria (§14): five pass. "Global errors are displayed in a controlled way" is PARTIAL. Render and route-loading errors are handled, but errors outside React rendering (event handlers, unhandled promise rejections) have no global UI handling. This does not block later phases.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap objective (`/docs/roadmap-frontend.md` §7): "Eine stabile globale Application Shell schaffen." ("Create a stable global application shell.")

**In scope:**

- router
- layout, sidebar, header, navigation, content area
- error boundary
- loading states
- global error handling
- basic navigation for Dashboard, Workflows, Executions, Monitoring, Reports, Credentials, Plugins, Settings

**Additional requirements from the task prompt:**

- route-level code splitting
- not-found page
- application-level state boundaries that separate application, server, feature and editor state
- shell support for an Editor area
- no global store "merely for convenience"

**Explicitly NOT in scope:**

- design-system components (Phase 02)
- API client (Phase 03)
- domain types and server state / TanStack Query (Phase 04)
- any feature functionality: dashboard content, workflow management, editor, and so on
- authentication and protected routes

---

## 3. Scope Implemented

### Routing

- `createBrowserRouter(routes)` in `App.tsx`. Tests use `createMemoryRouter(routes)`, so production and tests share one route table.
- Route tree, in order:
  - `/` → `AppLayout`, with the root `errorElement` and `HydrateFallback`
    - pathless content route → content `errorElement`
      - index → `loader: () => redirect('/dashboard')`
      - `/dashboard`, `/workflows`, `/executions`, `/monitoring`, `/reports`, `/credentials`, `/plugins`, `/settings` → `lazy: placeholder(label)`
      - `/workflows/:workflowId/editor` → `lazy: placeholder('Editor')`
      - `*` → `NotFoundPage`
- Navigation entries are defined once in `navigation.ts` (`navItems`). The sidebar and the route table both use that list, so the two cannot drift apart.

### Layout and navigation (`AppLayout.tsx`)

- `<header>` containing the app name and `<nav aria-label="Main">` with a `<ul>` of `NavLink`s. React Router sets `aria-current="page"` on the active link automatically. The active link also gets a bold weight and a background, so its state does not depend on color alone.
- Responsive with CSS only:
  - below `md`: a top bar whose nav scrolls horizontally
  - from `md` up: a fixed `w-56` sidebar
- Skip link "Skip to content" pointing at `#main-content`.
- `<main id="main-content" tabIndex={-1}>` wraps the `<Outlet/>`.
- Visible `focus-visible` outlines on links and buttons.

### Loading

- `AppLoading`: a full-screen `role="status"` element ("Loading…"), used as the root route's `HydrateFallback`. It shows until the lazy modules for the first URL have resolved.
- Pending navigation: when `useNavigation().state === 'loading'`, the layout shows a top bar (`role="status"`, screen-reader text "Loading page…") and sets `aria-busy` on `<main>`. The bar pulses, except under `prefers-reduced-motion` (`motion-reduce:animate-none`).

### Errors

- `RouteErrorBoundary` is used at both levels.
  - A 404 `ErrorResponse` renders `NotFoundPage`.
  - Anything else renders a fixed message ("Something went wrong" / "This page could not be displayed. Try reloading it.") in a `role="alert"` section with a Reload button. The error object itself is never rendered.
- `NotFoundPage` is shown inside the shell for `*` and links back to `/dashboard`.

### Testing

- 16 unit/component tests and 3 E2E tests (§12).

---

## 4. Files Changed

| File                                                  | Change   | Reason                                                                                           |
| ----------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------ |
| `frontend/src/app/App.tsx`                            | Modified | Replaced the static heading with `RouterProvider`; documents where each kind of state belongs    |
| `frontend/src/app/routes.tsx`                         | Created  | Central route table, lazy `placeholder()` helper, both error boundaries, redirect, 404           |
| `frontend/src/app/navigation.ts`                      | Created  | Single list of top-level areas (`NavItem`, `navItems`)                                           |
| `frontend/src/app/AppLayout.tsx`                      | Created  | Shell layout: header/sidebar navigation, skip link, content area, pending indicator              |
| `frontend/src/app/AppLoading.tsx`                     | Created  | Initial-load fallback                                                                            |
| `frontend/src/app/RouteErrorBoundary.tsx`             | Created  | Route error UI that does not disclose error details                                              |
| `frontend/src/app/NotFoundPage.tsx`                   | Created  | 404 page                                                                                         |
| `frontend/src/app/PlaceholderPage.tsx`                | Created  | Shared lazy-loaded placeholder for areas whose phase has not landed yet                          |
| `frontend/src/app/App.test.tsx`                       | Modified | Boot test now asserts the dashboard inside the shell, plus the redirect                          |
| `frontend/src/app/routes.test.tsx`                    | Created  | Routing, navigation, editor route, 404, initial loading fallback                                 |
| `frontend/src/app/RouteErrorBoundary.test.tsx`        | Created  | Page crash is contained inside the shell without leaking details                                 |
| `frontend/src/app/AppLayout.test.tsx`                 | Created  | Pending-navigation indicator and `aria-busy`                                                     |
| `frontend/tests/e2e/smoke.spec.ts`                    | Modified | E2E on the production build: boot, sidebar navigation, direct links to nested and unknown routes |
| `frontend/package.json`                               | Modified | Added `react-router@8.4.0`; raised `engines.node` to `^22.22.0 \|\| >=24.0.0`                    |
| `frontend/package-lock.json`                          | Modified | Lock entries for `react-router`, `@remix-run/route-pattern`, `cookie-es`; root `engines` synced  |
| `frontend/README.md`                                  | Modified | Node requirement, `app/` description, decisions on routing, boundaries and state                 |
| `docs/reports/frontend/phase-01-application-shell.md` | Created  | This report                                                                                      |

There are no new directories (everything is in the existing `src/app/`), no migrations and no generated files. Nothing was deleted.

---

## 5. Architecture Changes

### 5.1 Router: React Router in data-router mode

- **Decision:** use `react-router@8.4.0` with `createBrowserRouter` and a `RouteObject[]` table.
- **Context:** the roadmap and `frontend/AGENTS.md` §3 prescribe React Router. The phase requires route-level error boundaries, code splitting and loading states.
- **Chosen approach:** data-router mode. It provides:
  - per-route `errorElement`
  - `lazy` route modules
  - `HydrateFallback`
  - `useNavigation()` pending state
  - `loader` redirects

  `react-router-dom` no longer exists in v8, so everything is imported from `react-router`.

- **Alternatives considered:**
  - Declarative `<BrowserRouter><Routes>` with `React.lazy` + `<Suspense>` plus a hand-written class `ErrorBoundary`. Rejected: it reimplements what the data router already provides, and it would need its own boundary code.
  - TanStack Router. Rejected: it is not the documented choice.
- **Reason:** fewest lines of our own code, and it matches the documented stack.
- **Consequences:**
  - Later phases can attach `loader`s or keep fetching in TanStack Query. That is a Phase 03/04 decision; this phase uses no loaders except the index redirect.
  - Features replace `lazy: placeholder(...)` with `lazy: () => import('../features/<x>/...')`.

### 5.2 Two-level error boundary

- **Decision:** a root `errorElement` on the layout route, plus a second `errorElement` on a pathless route that wraps all content routes.
- **Context:** a failing page should not take the navigation down with it, but a failing layout still needs a boundary.
- **Consequences:** page errors render inside `<main>` with the sidebar intact. Errors in `AppLayout` itself render the same message without the shell.

### 5.3 State boundaries without new code

- **Decision:**
  - The URL (router) is the application/navigation state.
  - Layout UI state would be local `useState`; none is needed yet, because the responsive nav is CSS-only.
  - Server state goes to TanStack Query, added at the app root in Phase 03/04.
  - Feature and editor state stay inside their feature subtrees.
  - There is no global client store.
- **Alternatives considered:** adding `QueryClientProvider` or an `AppProviders` component now. Rejected: nothing would consume it in this phase, so it would be scaffolding without behavior.
- **Consequences:** Phase 03/04 adds the provider in `App.tsx` around `RouterProvider`. The rule is documented in a comment in `App.tsx` and in `frontend/README.md` → Decisions.

### 5.4 Shared placeholder chunk

- **Decision:** all areas lazy-load the same `PlaceholderPage` module. The `placeholder(title)` helper returns a `lazy` function that renders `<PlaceholderPage title={title} />`.
- **Alternatives considered:** one `features/<area>/<Area>Page.tsx` file per area (9 near-identical files). Rejected: those would be empty feature directories created ahead of their phases.
- **Consequences:** the build already produces a separate chunk (`PlaceholderPage-*.js`), so the lazy-loading path is exercised in production. Each feature phase creates its own module and swaps the `lazy` function.

No ADR was written. These decisions implement the documented architecture (`architecture.md` §38–39, `frontend/AGENTS.md` §5, §13–15); they do not change it.

---

## 6. Domain / Data Model Changes

Not applicable. No domain types, schemas, DTOs or persisted data were introduced.

`NavItem` (`{ label, path }`) is a UI-only type.

---

## 7. API / Contract Changes

No frontend/backend contract changes. No backend calls are made.

Client-side URL contract introduced (bookmarkable routes):

- `/` (redirects to `/dashboard`)
- `/dashboard`, `/workflows`, `/executions`, `/monitoring`, `/reports`, `/credentials`, `/plugins`, `/settings`
- `/workflows/:workflowId/editor`
- any other path → 404 page, rendered client-side

These paths follow the example in `frontend/AGENTS.md` §14. The production reverse proxy must serve `index.html` for these paths (SPA fallback). `vite preview` already does this, as the E2E tests show.

---

## 8. Implementation Details

- **Boot:** `main.tsx` (unchanged) renders `<App/>` in `StrictMode`. `App.tsx` creates the browser router once at module scope.
- **First paint:** the initial URL matches lazy routes, so the router is not initialized until their modules have loaded. Until then React Router renders the root `HydrateFallback` (`AppLoading`). The test "shows the loading fallback until the first page chunk resolves" verifies this.
- **Navigation:** clicking a `NavLink` starts a navigation. While the lazy module (or, later, a loader) is pending, the previous page stays visible and the layout shows the pending bar. When the navigation commits, the bar disappears and `aria-busy` returns to `false`.
- **Index redirect:** a `loader` on the index route throws `redirect('/dashboard')`. This happens before render, so there is no flash of an empty page.
- **Error disclosure:** `RouteErrorBoundary` never reads `error.message`. In development React Router logs the error to the console; production logging and telemetry are not part of this phase.
- **Reload:** the error page's Reload button calls `window.location.reload()`. This also recovers from a failed chunk load after a redeploy, where stale chunk hashes are a common cause of lazy-import failures.
- **TypeScript under `exactOptionalPropertyTypes`:** `placeholder()` returns `NonNullable<RouteObject['lazy']>`. Returning `RouteObject['lazy']` would include `undefined`, which cannot be assigned to an optional property under this compiler flag.

---

## 9. Findings

### F1 — React Router 8.4.0 requires Node ≥ 22.22.0

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** `npm view react-router@8.4.0 engines` reports `node >=22.22.0`, but the repository allowed `^22.13.0`. `engines.node` is now `^22.22.0 || >=24.0.0` in `package.json` and in the README. `.nvmrc` pins 24 and the local Node is v24.13.0, so nothing else changed. `.npmrc` sets `engine-strict=true`, so installs on Node 22.13–22.21 now fail instead of producing a mismatched install.

### F2 — npm copies `package.json`'s 4-space indentation into `package-lock.json`

- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:**
  - `npm install` rewrote the whole lockfile with 4-space indentation (a diff of about 9,000 lines, of which 39 lines were real changes).
  - npm detects indentation from `package.json`, which Prettier formats with `tabWidth: 4`.
  - The committed lockfile uses 2 spaces, and `.prettierignore` excludes it.
  - Workaround: the lockfile was re-serialized with 2-space indentation. `npm ci` was verified afterwards.
  - The problem will come back on every `npm install` until one indentation convention is chosen (§21, O2).

### F3 — Lockfile root `engines` was out of sync with `package.json` (pre-existing)

- **Impact:** LOW
- **Resolution:** FIXED (as a side effect)
- **Notes:** the committed lockfile root recorded `"node": ">=22.12.0"` while `package.json` declared `^22.13.0 || >=24.0.0`. Regenerating the lockfile synced it. There was no functional effect, because npm reads `engines` from `package.json`.

### F4 — `Promise.withResolvers` is not available under the configured `lib`

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** `tsconfig.app.json` uses `lib: ["ES2023", ...]`, and `Promise.withResolvers` is ES2024. A test that used it caused typecheck and lint errors (`no-unsafe-*`). The test now builds the deferred promise by hand. `lib` was deliberately left unchanged.

### F5 — `@testing-library/user-event` is not installed

- **Impact:** LOW
- **Resolution:** WORKAROUND (deliberate)
- **Notes:** the tests use `fireEvent.click` instead. Adding `user-event` was not justified for click-only interactions. Keyboard-interaction tests in Phase 02 (dialogs, menus) may justify adding it.

### F6 — The `navigation` landmark reads well, but the shell has no separate top header bar

- **Impact:** LOW
- **Resolution:** OPEN (design decision)
- **Notes:** the roadmap scope lists "Header" and "Sidebar" separately. The implementation uses one `<header>` element that holds the app name and the navigation: a sidebar on desktop and a top bar on mobile. There is no separate header for page-level actions such as user menu, session and notifications. Those depend on auth (session) and toasts (Phase 02), so the slot was not added speculatively.

---

## 10. Bugs / Problems Found

All of these were introduced and fixed during this phase. No pre-existing product bugs were found.

| Problem                                             | Root cause                                                                                    | Fix                                            | Regression test                 | Area    |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------- | ------- |
| `tsc` error TS2375 in `routes.tsx`                  | `RouteObject['lazy']` includes `undefined`; not assignable under `exactOptionalPropertyTypes` | Return type `NonNullable<RouteObject['lazy']>` | Covered by typecheck gate       | Routing |
| `tsc` and lint errors in `AppLayout.test.tsx`       | `Promise.withResolvers` is ES2024; `lib` is ES2023 (F4)                                       | Hand-built deferred promise                    | Covered by typecheck/lint gates | Tests   |
| Test files imported a package that is not installed | `@testing-library/user-event` is not a dependency (F5)                                        | Switched to `fireEvent`                        | The test suite itself           | Tests   |
| Full-file lockfile diff                             | npm indentation detection (F2)                                                                | Re-serialized with 2-space indentation         | `npm ci` verified               | Tooling |

**Environmental (not a product issue):** a manual screenshot script could not connect to `vite preview` on `127.0.0.1:4174` when the server was started without `--host`. The server bound to a different loopback address. Passing `--host 127.0.0.1` fixed it; `playwright.config.ts` already passes `--host 127.0.0.1`.

---

## 11. Fixes Applied

1. **`lazy` typing:** symptom TS2375. Root cause and fix as in §10. Verified with `npm run typecheck` (PASS).
2. **Deferred promise in a test:** symptom: four `@typescript-eslint/no-unsafe-*` errors and a typecheck failure. Fixed by building the promise manually. Verified with lint and typecheck (PASS).
3. **Lockfile formatting:** symptom: an 8,973-line diff. Fixed by re-serializing as 2-space JSON. Verified: `git diff --stat` shows 39 lines (37+/2−), and `npm ci` succeeds.

---

## 12. Testing & Verification

### Unit / Component Tests (Vitest + React Testing Library, jsdom)

4 files, 16 tests, all passing. The run printed no warnings or console errors.

- **`App.test.tsx`** — "boots into the dashboard inside the shell": renders the real `<App/>` (browser router on jsdom) and asserts:
  - the Dashboard `h1`
  - the `navigation` landmark named "Main"
  - `window.location.pathname === '/dashboard'`, which verifies the root redirect
- **`routes.test.tsx`** (12 tests, real route table on a memory router):
  - initial loading fallback (`role=status` "Loading…") is visible at first and gone once the page renders
  - `/` redirects to `/dashboard`
  - each of the 8 areas renders its heading when opened directly (`it.each(navItems)`)
  - clicking through all 8 sidebar links in sequence: the heading, the router pathname and `aria-current="page"` are correct for each
  - `/workflows/p_1/editor` renders the Editor placeholder
  - an unknown path shows "Page not found" with the navigation still present, and "Go to Dashboard" navigates back
- **`RouteErrorBoundary.test.tsx`**:
  - `vi.mock('./PlaceholderPage')` makes the Reports page throw `'TypeError: secret internal detail'` while rendering, using the real route tree
  - asserts: `role=alert` shows "Something went wrong", the navigation is still rendered (content-level boundary), the secret text does not appear, and a Reload button is present
  - **Mutation check (manual):** after deleting the content-level `errorElement` from `routes.tsx`, this test failed. With the line restored it passes, so the test does detect a missing content boundary.
- **`AppLayout.test.tsx`**: uses `AppLayout` with a test route whose `loader` waits on a manually resolved promise. Asserts:
  - the pending `role=status` "Loading page…" appears and `aria-busy="true"` is set on `<main>`
  - after the promise resolves, the page renders, the status element is removed and `aria-busy="false"`

### E2E Tests (Playwright, Chromium, against `npm run build` + `vite preview`)

3 tests, all passing (`CI=1 npm run test:e2e`):

1. Production build: `/` → URL `/dashboard`, title "Akasha Agent Engine", Dashboard `h1` visible.
2. Clicking "Executions" in the Main navigation → URL `/executions` and the Executions heading.
3. Loading `/workflows/p_1/editor` directly shows the Editor heading, and loading `/does/not/exist` shows "Page not found". This verifies the preview server's SPA fallback and client-side 404 handling.

### Accessibility checks (partial, no automated audit)

- Tests use role- and name-based queries (`navigation` "Main", `heading` level 1, `alert`, `status`, `aria-current`, `aria-busy`), so they confirm those semantics exist.
- **Not performed:** an automated axe or contrast audit, a manual screen-reader test, or a keyboard walkthrough of the skip link.

### Visual check (manual)

Production-build screenshots of `/workflows` at 1200×700 and 375×700 were taken with Playwright and inspected:

- the desktop sidebar shows the active "Workflows" item
- the mobile top bar has a horizontally scrolling nav

These screenshots are not committed and are not part of any automated test.

### Not applicable / not performed

Integration, API, infrastructure, performance and security test suites do not apply, because there is no backend interaction. The only security-related assertion is the error-detail check above.

---

## 13. Quality Gates

All results come from a final run on the working tree in this report's state.

| Quality Gate      | Result | Notes                                                                                                |
| ----------------- | ------ | ---------------------------------------------------------------------------------------------------- |
| Formatter         | PASS   | `npm run format:check`                                                                               |
| Linter            | PASS   | `npm run lint` (type-checked strict rule set). Failed once during development (F4), then fixed       |
| Type Checker      | PASS   | `npm run typecheck` (`tsc -b`). Failed twice during development (TS2375, F4), then fixed             |
| Unit Tests        | PASS   | `npm test`: 4 files, 16 tests                                                                        |
| Integration Tests | N/A    | No backend interaction in this phase                                                                 |
| API Tests         | N/A    | —                                                                                                    |
| E2E Tests         | PASS   | `CI=1 npm run test:e2e`: 3/3 against the production build                                            |
| Build             | PASS   | `npm run build`: `index-*.js` 316.01 kB (99.81 kB gzip), `PlaceholderPage-*.js` 0.29 kB, CSS 8.88 kB |
| Security Checks   | PASS   | `npm audit`: 0 vulnerabilities. No dedicated security test suite                                     |
| Accessibility     | N/A    | No automated audit. Semantic roles are asserted by tests (§12)                                       |
| CI                | N/A    | The repository has no CI pipeline; gates were run manually                                           |

---

## 14. Acceptance Criteria Verification

The criteria come from `/docs/roadmap-frontend.md` §7.

| Acceptance Criterion                                      | Status  | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| All main areas are navigable                              | PASS    | `routes.test.tsx` "navigates through every main area via the sidebar" (all 8 links, URL plus `aria-current`); E2E test 2                                                                                                                                                                                                                                                                                                                                                                                        |
| URL routing works                                         | PASS    | `it.each(navItems)` opens each URL directly; root redirect tests; E2E tests 1 and 3 (direct load of nested routes on the production build)                                                                                                                                                                                                                                                                                                                                                                      |
| Layout is consistent                                      | PASS    | All content routes are children of one layout route (`routes.tsx`). The navigation landmark is asserted on area pages, on the 404 page and in the error state. Desktop and mobile screenshots were inspected manually                                                                                                                                                                                                                                                                                           |
| Non-existent routes show a meaningful 404 page            | PASS    | `*` → `NotFoundPage` inside the shell, with a link back; `RouteErrorBoundary` also maps 404 responses to it; unit test and E2E test 3                                                                                                                                                                                                                                                                                                                                                                           |
| Global errors are displayed in a controlled way           | PARTIAL | Render errors and lazy-route/loader errors are caught at two levels with a fixed message that does not disclose details (`RouteErrorBoundary.test.tsx`). **Missing:** errors outside React rendering (event handlers, unhandled promise rejections, `window.onerror`) are not surfaced in the UI. They only reach the console. Reason: these errors will mostly come from API calls, which get normalized error handling with the API client (Phase 03) and toasts (Phase 02). Does not block phase completion. |
| No business features are implemented ahead of their phase | PASS    | Areas render only a "This area is not available yet." placeholder. No API calls, no data, no feature directories                                                                                                                                                                                                                                                                                                                                                                                                |

---

## 15. Security Considerations

- **Error disclosure:** `RouteErrorBoundary` never renders the error object or its message. A test asserts that error text does not appear in the DOM.
- **XSS / output encoding:** no `dangerouslySetInnerHTML`, `innerHTML`, `eval` or `Function`. The route param `:workflowId` is not rendered at all. The editor placeholder shows a static title.
- **Secrets:** none are handled. No `localStorage` or `sessionStorage` use. Nothing is put in the URL beyond route paths.
- **Open redirects:** the only redirect target is the hard-coded `/dashboard`.
- **Authentication / authorization:** not implemented. All routes are public on the client. Protected routes must be added centrally (`frontend/AGENTS.md` §14, §21) when auth arrives. Backend enforcement (backend Phase 20, authentication and authorization) is unaffected.
- **Dependency:** `react-router@8.4.0` (MIT) brings `@remix-run/route-pattern@0.22.1` and `cookie-es@3.1.1` (both MIT). `npm audit` reports 0 vulnerabilities.

---

## 16. Performance Considerations

- **Bundle:** the main chunk is 316.01 kB (99.81 kB gzip), up from 219.85 kB (68.70 kB gzip) in Phase 00. That is +96.16 kB (+31.11 kB gzip), almost entirely `react-router`. CSS grew from 5.13 kB to 8.88 kB because of the shell's Tailwind utilities. Area routes are split into a separate chunk, so feature code added later will not grow the initial bundle unless it is imported from `src/app/`.
- **Initial load:** the first URL needs an extra request for its lazy chunk before content renders. `HydrateFallback` covers that gap.
- **Measurements:** none beyond the bundle sizes reported by Vite. No runtime profiling was done; nothing in this phase is performance-sensitive.

---

## 17. Documentation Changes

| File                                                  | Reason                                                 | Content                                                                                                                                                                                                                                                                                                     |
| ----------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/README.md`                                  | New behavior and requirement                           | Node requirement `^22.22.0 \|\| >=24`; `app/` described as "router, shell layout, route boundaries"; new Decisions entries: React Router data mode and lazy placeholders, boundary layering and loading behavior, state boundaries (URL, TanStack Query later, feature/editor state local, no global store) |
| `frontend/src/app/App.tsx` (comment)                  | Record state boundaries where they will be implemented | Where server, feature and editor state belong                                                                                                                                                                                                                                                               |
| `docs/reports/frontend/phase-01-application-shell.md` | Phase report                                           | This document                                                                                                                                                                                                                                                                                               |

Documentation to update in later phases:

- `docs/deployment.md` / `docs/architecture.md`: once a frontend deployment exists, the production reverse proxy must serve SPA fallback (`index.html`) for client routes. `docs/architecture.md` currently states there is "No frontend or reverse proxy service yet."
- `frontend/README.md` → Structure: once `features/` and `components/` directories exist.

---

## 18. Dependencies / Infrastructure Changes

| Change                                                                  | Why                                                        |
| ----------------------------------------------------------------------- | ---------------------------------------------------------- |
| Added `react-router@8.4.0` (runtime, exact pin per `.npmrc save-exact`) | Router required by the roadmap and `frontend/AGENTS.md` §3 |
| Transitive: `@remix-run/route-pattern@0.22.1`, `cookie-es@3.1.1`        | Dependencies of `react-router`                             |
| `engines.node`: `^22.13.0 \|\| >=24.0.0` → `^22.22.0 \|\| >=24.0.0`     | Required by `react-router@8.4.0` (F1)                      |

No environment variables, Docker, database, Redis or test-infrastructure configuration changed. `vite.config.ts`, `playwright.config.ts`, `eslint.config.js` and the tsconfigs are unchanged.

---

## 19. Deferred Work

| What                                                                              | Why deferred                                                | Intended phase                           | Affects completion          |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------------------- | --------------------------- |
| `QueryClientProvider` / server-state boundary                                     | Nothing consumes it before the API client exists            | Phase 03/04                              | No                          |
| Global handling of non-render errors (unhandled rejections, event-handler errors) | Mostly API-originated; needs error normalization and toasts | Phase 02 (toasts), Phase 03 (API errors) | No (criterion PARTIAL, §14) |
| Header actions area (user menu, session, notifications)                           | Depends on auth and notifications                           | Auth phase / Phase 02                    | No                          |
| Protected routes / login route                                                    | Auth not in scope                                           | Roadmap auth phase                       | No                          |
| Focus management after navigation (move focus to `<main>` or `h1`)                | Fits with the accessibility and design-system work          | Phase 02                                 | No                          |
| Per-route `document.title`                                                        | Not required by the phase                                   | When feature pages exist                 | No                          |
| Feature modules for each area                                                     | Feature scope                                               | Phases 05+                               | No                          |
| Full-bleed layout for the Editor                                                  | Editor needs are unknown until Phase 07                     | Phase 07                                 | No                          |

---

## 20. Known Limitations

- Errors outside rendering and route loading show nothing in the UI. They only reach the console.
- Focus is not moved after client-side navigation, so screen-reader users are not told the page changed beyond the new content. The skip link is available.
- `document.title` stays "Akasha Agent Engine" on every route.
- The mobile navigation is a horizontally scrolling bar. With 8 entries, some links are off-screen at 375 px until scrolled. There is no hamburger or drawer.
- The editor route is rendered inside the padded content area like any other page.
- All routes are reachable without authentication on the client.
- The root-level error state (layout failure) has no navigation, by design. It only offers Reload.

---

## 21. Open Issues

| #   | Issue                                             | Impact                                            | Severity | Area           | Recommended action                                                                                                                                                                 | Target                      |
| --- | ------------------------------------------------- | ------------------------------------------------- | -------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| O1  | Non-render errors are not surfaced in the UI      | Users may get no feedback on failed async actions | MEDIUM   | Error handling | Normalize API errors in the API client and surface them through query/mutation error states and toasts. Consider a global `unhandledrejection` listener that shows a generic toast | Phase 02/03                 |
| O2  | `npm install` re-indents the lockfile to 4 spaces | Noisy diffs on every dependency change            | LOW      | Tooling        | Either set Prettier `overrides` for `package.json` to 2-space, or accept a 4-space lockfile in one formatting-only commit                                                          | Any phase; separate commit  |
| O3  | No CI pipeline runs the gates                     | Regressions are only caught by manual runs        | MEDIUM   | Tooling        | Add a CI workflow running format, lint, typecheck, test, build and E2E                                                                                                             | Not assigned in the roadmap |
| O4  | No focus management on route change               | Accessibility gap for screen-reader users         | LOW      | Shell a11y     | Focus `<main>` or the page `h1` after navigation commits                                                                                                                           | Phase 02                    |

---

## 22. Architectural / Product Decisions

- **The route table is the single source for navigation.** The sidebar renders from `navItems`, and the route table maps over the same list. Adding an area means one entry in `navigation.ts` plus its `lazy` module.
- **Errors are caught at two levels.** The content-level boundary keeps the navigation available when a page fails. The root-level boundary handles failures of the layout itself.
- **Error UI never shows error text.** Detailed or debug output belongs in dedicated inspector areas (`frontend/AGENTS.md` §20), not in the boundary.
- **No global store, and no providers without consumers.** The URL is navigation state. Server state comes with TanStack Query (Phase 03/04). Editor state stays inside the editor feature (Phase 07).
- **One shared placeholder module** instead of per-feature stub files, so `features/` directories appear only when they have content.
- **`fireEvent` instead of `user-event`**, to avoid adding a dependency for click-only tests.
- **Responsive navigation in CSS only**: no JavaScript state for opening or closing the navigation.

---

## 23. Repository / Git State

- **Branch:** `main`
- **HEAD:** `60b2dcf frontend phase 00 - frontend repository foundation`
- **Working tree:** uncommitted Phase 01 changes only. Nothing has been committed for this phase.
  - Modified: `frontend/README.md`, `frontend/package-lock.json`, `frontend/package.json`, `frontend/src/app/App.test.tsx`, `frontend/src/app/App.tsx`, `frontend/tests/e2e/smoke.spec.ts`
  - Untracked: `frontend/src/app/{AppLayout,AppLoading,NotFoundPage,PlaceholderPage,RouteErrorBoundary}.tsx`, `frontend/src/app/navigation.ts`, `frontend/src/app/routes.tsx`, `frontend/src/app/{AppLayout,RouteErrorBoundary,routes}.test.tsx`, and this report
- **Diff size of tracked files:** 6 files, 100 insertions, 18 deletions. The new source and test files total 372 lines in `src/app/`.
- **Unrelated changes:** none. The working tree was clean at the start of the phase.
- `frontend/dist/` and `frontend/test-results/` are build and test output and are git-ignored.

---

## 24. Phase Completion Assessment

### Implemented

- central data router
- layout with responsive navigation, skip link and content area
- 8 area routes plus the editor route, all lazy and code-split
- root redirect
- 404 page inside the shell
- two-level error boundaries
- initial and pending loading states
- documented state boundaries

### Verified

- format, lint, typecheck, build and `npm audit` pass
- 16/16 unit/component tests and 3/3 E2E tests on the production build
- a manual mutation check of the error-boundary test
- manual desktop and mobile screenshots

### Not Implemented

- UI handling of non-render global errors
- a separate header actions area
- focus management on route change

### Deferred

See §19: server-state provider, auth and protected routes, feature modules, and the editor layout.

### Open

O1–O4 (§21).

### Blocking Issues

None.

**Phase Status:** COMPLETE WITH LIMITATIONS

This status follows from §14: five criteria PASS and one is PARTIAL (global error display), and the PARTIAL criterion does not block later phases.

---

## 25. Handoff to Next Phase (Phase 02 — Design System + UI Foundation)

**Phase 02 can assume:**

- a working shell
- `AppLayout` with `<main id="main-content">` as the content container
- the route table in `src/app/routes.tsx`
- the area list in `src/app/navigation.ts`
- Tailwind v4 with no custom theme tokens yet

**Candidates for migration to Phase 02 components:**

- Styling in the shell is inline Tailwind utility strings: NavLink classes, the Reload button, the "Go to Dashboard" link, and the loading and status visuals.
- When Phase 02 introduces `Button`, `Spinner`, `Alert`, `EmptyState` and theme tokens, `RouteErrorBoundary`, `NotFoundPage`, `AppLoading`, `PlaceholderPage` and the pending bar should be migrated to them.
- Phase 02 is also the natural place for:
  - toasts, which is the prerequisite for O1
  - focus management (O4)
  - dark/light theme tokens

**Must not be changed casually:**

- The two-level `errorElement` structure in `routes.tsx`. `RouteErrorBoundary.test.tsx` fails if the content-level boundary is removed.
- The rule that boundary UI never renders error messages.
- `navItems` as the single source for both the navigation and the routes.

**Constraints:**

- Node `^22.22.0 || >=24` (F1).
- After `npm install`, check the lockfile diff for re-indentation (F2/O2).

**For Phase 03/04:**

- Add `QueryClientProvider` in `App.tsx` around `RouterProvider`.
- Decide whether to use route `loader`s or only TanStack Query hooks.
- Feature routes replace `lazy: placeholder('…')` with their own `lazy` imports.
