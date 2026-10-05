# Phase 22 — Developer Experience

Report date: 2026-10-05. Branch `main`. Base commit `94d6bfb` (frontend phase 21 — settings and authentication). All work described here is **uncommitted** (§23).

## 1. Executive Summary

**Supposed to achieve.** There are two sources, and they disagree:

- **Roadmap** (`docs/roadmap-frontend.md` §28, "Phase 22 — Developer Experience"): editor power-user features.
  - Keyboard shortcuts, command palette, better graph navigation, node search, quick add, copy/paste.
  - Debug information, API/backend diagnostics, shortcut help, editor performance tools.
  - Acceptance criterion: "Der Editor lässt sich effizient mit Maus und Tastatur bedienen" (the editor can be used efficiently with mouse and keyboard).
- **Session prompt:** developer experience for engineers working on the frontend.
  - Reusable development utilities, consistent feature structure, test helpers, API helpers, mock/test fixtures.
  - Error-handling conventions, local development documentation, component conventions.
  - Storybook only if justified.
  - Verify that a new developer can install, start, test, lint, typecheck, build, run E2E and understand the feature structure.
  - "Without creating a framework inside the application."

`frontend/AGENTS.md` §1 ranks an explicit project requirement above the roadmap, so the session prompt's scope was implemented. The roadmap's feature list was not worked on in this phase (§19). Some of its items already existed from earlier phases (§14).

**Actually implemented.** Only tooling, tests and documentation changed; no application code.

- **`npm run dev:backend`:** a one-command local backend for `npm run dev`, built as a `--dev` mode of the existing full-stack test script `tests/fullstack/backend.ts`.
- **Port guard:** the same script now refuses to start on a port that already answers.
- **`npm run check`:** one command for format check, lint, typecheck, unit tests and build.
- **Flaky unit tests (pre-existing):** fixed in the shared test harness (`src/test/setup.ts`).
- **Flaky full-stack test (pre-existing):** fixed (`tests/fullstack/projects.spec.ts`).
- **`frontend/README.md`:**
  - new sections: Local development, Adding a feature, Conventions, Testing, Monitoring, Plugins;
  - two stale claims corrected.

**Resulting state.** Every workflow in the session prompt ran in this session against this checkout, with the results in §12. The roadmap's acceptance criterion is PARTIAL (§14): earlier phases provide keyboard editing, but the roadmap's new items (command palette, shortcut help, quick add from the keyboard) do not exist.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 2. Phase Objective

**Roadmap objective** (§28): "Das Frontend für Entwickler und Power User verbessern" (improve the frontend for developers and power users). The roadmap prompt prioritizes:
- keyboard shortcuts, command palette, quick node search, quick add;
- graph navigation, copy/paste, shortcut help, diagnostics.

It forbids new backend domain functions.

**Session objective:** improve developer ergonomics (utilities, structure, test and API helpers, fixtures, conventions, documentation) and verify the onboarding workflows. Implement and test any missing backend function.

**Not responsible for:**
- new product features for end users;
- backend domain changes (none were needed);
- Phase 23 (security hardening) onward.

## 3. Scope Implemented

### Development tooling
- `npm run dev:backend` → `node tests/fullstack/backend.ts --dev`:
  - PostgreSQL 17.2 (tmpfs) and Redis 8.2.1 containers, labelled `akasha-e2e=dev`;
  - `uv run akasha migrate`, then `uv run akasha serve` with the in-process worker;
  - port 8000 (Vite's default proxy target), overridable through `AKASHA_E2E_API_PORT`;
  - `AKASHA_ENVIRONMENT=development`;
  - waits for `/ready`, creates the first admin with `POST /api/v1/auth/setup` (username `admin`, 24-hex-character random password), and prints the URL and credentials once;
  - removes its containers when the server process exits.
- Port guard in `backend.ts` (dev and full-stack mode): a TCP connect to `127.0.0.1:<port>` before any container starts. If something answers, the script throws: "Port N is already in use. Stop that program or set AKASHA_E2E_API_PORT (and, for `npm run dev`, AKASHA_API_PROXY_TARGET)."
- `npm run check` → `format:check && lint && typecheck && test && build`.

### Test infrastructure
- `src/test/setup.ts`: `configure({ asyncUtilTimeout: 5000 })` from `@testing-library/react`, with a comment giving the reason.
- `src/test/app.tsx`: the doc comment of `routerReady` (`renderRoute().ready`) now says when awaiting it is still needed. Its old text described it as the fix for the cold-import budget.
- `tests/fullstack/projects.spec.ts`: removed the `expect(getByText('Cancellation requested')).toBeVisible()` assertion in "a run started in the UI is listed, opened and cancelled", with a comment explaining why.

### Documentation
- `frontend/README.md`, detailed in §17.

### Not implemented (decided against, §5)
- a shared test-fixture module;
- Storybook;
- a separate `scripts/` directory;
- new API helpers: the existing API layer (`ApiClient`, `ApiError`, `queries.ts`, `mutations.ts`, contract test) already covers them, and the README now documents how to use it.

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/package.json` | Modified | Added `dev:backend` and `check` scripts |
| `frontend/tests/fullstack/backend.ts` | Modified (+68/−9) | `--dev` mode, port guard, async `until()`, probe timeout, `removeContainers()` |
| `frontend/tests/fullstack/projects.spec.ts` | Modified | Removed racy assertion on a transient state (§10) |
| `frontend/src/test/setup.ts` | Modified | `asyncUtilTimeout: 5000` (§10) |
| `frontend/src/test/app.tsx` | Modified | `ready` doc comment updated |
| `frontend/README.md` | Modified (+175/−10) | Sections listed in §17 |
| `docs/reports/frontend/phase-22-developer-experience.md` | Created | This report |
| `.claude/launch.json` | Created, untracked | Local browser-preview configuration used only for verification. Not part of the phase; must not be committed (§23) |

No directories, migrations, generated files or dependencies were added or changed. `src/api/openapi.json` is unchanged.

## 5. Architecture Changes

No architectural changes were introduced during this phase. The following tooling and process decisions were made.

### Decision: implement the session prompt's scope, not the roadmap's
- **Context:** the roadmap §28 and the session prompt describe different work under the same phase name.
- **Chosen approach:** the session prompt.
- **Alternatives considered:** the roadmap's editor features, or both.
- **Reason:** `frontend/AGENTS.md` §1 ranks an explicit project requirement first. Doing both would have been scope creep without a request.
- **Consequences:** the roadmap's Phase 22 editor features remain unscheduled (§19, §21).

### Decision: the dev backend is a mode of the full-stack test script
- **Context:** `npm run dev` proxies `/api` to `127.0.0.1:8000`, but nothing documented or automated providing a backend there. A backend needs PostgreSQL, Redis, a migration and a first admin.
- **Chosen approach:** a `--dev` flag on `tests/fullstack/backend.ts`.
- **Alternatives considered:**
  - a separate script;
  - the repository's `docker-compose.yml`: a production-style deployment that needs `.env` secrets and an image build, and has no dev admin;
  - documentation only.
- **Reason:** the script already handles Windows-safe container startup (Node, not shell), readiness, isolated `AKASHA_*` settings and stale cleanup. The dev mode adds a handful of branches instead of duplicating about 100 lines.
- **Consequences:** changes to `backend.ts` affect both the full-stack suite and local development, so both must be checked. The separate label keeps their containers apart.

### Decision: refuse an occupied port instead of sharing it
- **Context:** on Windows, uvicorn could bind `127.0.0.1:8000` while another program (Portainer, via `com.docker.backend` and `wslrelay`) listened on `0.0.0.0:8000`. Requests reached whichever listener answered (§9, Finding 3).
- **Chosen approach:** a TCP connect check before starting.
- **Alternatives considered:** detecting the clash after start from readiness failures; picking a free port automatically.
- **Reason:** an early, named error is the simplest correct behavior. An automatic port would also need the Vite proxy target changed.
- **Consequences:** a port in use stops both modes immediately. On a closed port the check costs at most the OS connect-refusal time (on Windows about 2 s per refused connect).

### Decision: fix the async-query timeout in the harness, not per test
- **Context:** §10, Bug 1.
- **Chosen approach:** `asyncUtilTimeout: 5000` globally.
- **Alternatives considered:** `await ready` in every test (more than 200 call sites across 13 files), or preloading all lazy routes in setup (every test file would import every feature).
- **Reason:** one line covers every caller. `findBy*` resolves as soon as the element appears, so only failing waits get slower.
- **Consequences:** a failing `findBy*` takes up to 5 s instead of 1 s to report. Tests that need the route loaded before acting (e.g. with fake timers) still use `ready`.

### Decision: no shared fixture module, no Storybook
- **Fixtures:** the domain builders (`execution()`, `report()`, `project()`, `node()`) differ per test file on purpose: different node types, statuses and outputs, verified by diffing `ReportsPage.test.tsx` against `ExecutionInspector.test.tsx`. The only identical helpers are three-line `page()` functions, and their `limit` differs per endpoint (10/25/50/100). A shared module would add indirection without removing duplication that matters. The README states the fixture rule instead.
- **Storybook:** `/dev/ui` (`src/app/UiCatalogPage.tsx`) already renders every primitive in every state and both themes, and it is the E2E target for primitives. Storybook would add a dependency tree and a second catalog.

## 6. Domain / Data Model Changes

Not applicable. No entities, schemas, DTOs, tables or state transitions changed.

## 7. API / Contract Changes

No contract changes. No backend function was missing for this scope. The dev backend uses two existing endpoints:
- `GET /ready`;
- `POST /api/v1/auth/setup` (body `{username, password}`; works only while no account exists).

`src/api/openapi.json` and `backend/` are unchanged.

## 8. Implementation Details

**`tests/fullstack/backend.ts` control flow:**
1. `dev = argv.includes('--dev')`.
2. `port = AKASHA_E2E_API_PORT ?? (dev ? '8000' : undefined)`. Full-stack mode without the variable still throws.
3. Port guard (`node:net` `connect`).
4. `label = akasha-e2e=dev|fullstack`.
5. `removeContainers()`: stale cleanup for this label only. The former inline code became a function so dev mode can reuse it on exit.
6. Start the PostgreSQL and Redis containers; `until()` waits for `pg_isready` and `redis-cli ping`. `until()` now takes `() => unknown` and awaits it, so one helper serves sync docker checks and async HTTP probes.
7. Migrate, then spawn `serve`. SIGTERM and SIGINT are forwarded. On server exit, dev mode runs `removeContainers()` before `process.exit`.
8. Dev only:
   - `until()` probes `/ready` with `AbortSignal.timeout(2000)` per attempt;
   - `POST /auth/setup` (throws on non-2xx);
   - `console.log` of the URL and credentials.

**Process ownership:** Ctrl+C in an interactive terminal reaches the whole process group: `npm`, `node`, `uv` and uvicorn. The server exits, and the exit handler removes the containers. A hard kill (TerminateProcess on Windows) skips the handlers; see §20.

**Full-stack mode** is unchanged except for the port guard and the shared `removeContainers()` helper. `globalTeardown` (`teardown.ts`) still removes the `akasha-e2e=fullstack` containers.

## 9. Findings

### Finding 1 — Roadmap Phase 22 and the session prompt define different scopes
- **Impact:** MEDIUM (planning)
- **Resolution:** WORKAROUND. The session prompt was followed per AGENTS precedence.
- **Notes:** the roadmap's items remain open (§19, §21).

### Finding 2 — Starting the frontend locally was undocumented
- **Impact:** HIGH (onboarding)
- **Resolution:** FIXED (`dev:backend`, README "Local development").
- **Notes:** every route sits behind `RequireSession`, so `npm run dev` alone shows only the login page with a failing session check.

### Finding 3 — Silent port sharing on Windows
- **Impact:** MEDIUM
- **Resolution:** FIXED (port guard).
- **Notes:**
  - On this machine `127.0.0.1:8000` was bound by uvicorn next to Portainer's `0.0.0.0:8000`.
  - The first readiness probe reached Portainer before uvicorn had bound the port.
  - Node's fetch then reused that keep-alive connection, so every later probe got a non-200, and the first two dev runs never created the admin.
  - Diagnosed with a temporary debug log, which was removed afterwards.

### Finding 4 — Opt-in `ready` left most screen tests exposed to cold imports
- **Impact:** MEDIUM (CI reliability)
- **Resolution:** FIXED (§10, Bug 1).
- **Notes:** before this phase, only `ReportsPage.test.tsx` (3 uses) and `ExecutionInspector.test.tsx` (1 use) awaited `ready`.

### Finding 5 — A full-stack spec asserted a state the backend may skip
- **Impact:** MEDIUM (CI reliability)
- **Resolution:** FIXED (§10, Bug 2).

### Finding 6 — README gaps and stale claims
- **Impact:** LOW
- **Resolution:** FIXED.
- **Notes:**
  - There were no Monitoring or Plugins sections. This was phase 20 Finding 5, still OPEN until now.
  - The README referenced `PlaceholderPage`; no such component exists in `src/`.
  - It promised `hooks/`, `schemas/` and `types/` directories that were never created. Hooks live in features; schemas and inferred types in `src/api/`.

### Finding 7 — No CI configuration in the repository
- **Impact:** MEDIUM
- **Resolution:** OPEN.
- **Notes:** there is no `.github/` directory. Root AGENTS §93 expects CI to run the quality gates.

### Finding 8 — Hard-killing `dev:backend` leaves orphans on Windows
- **Impact:** LOW (developer machine only)
- **Resolution:** WORKAROUND (documented).
- **Notes:**
  - During verification, stopping the background task killed the `npm` process but left uvicorn listening and the containers running.
  - The next start removes the containers; the port guard then names the blocked port.

## 10. Bugs / Problems Found

**Bug 1 — Cold-import timeout in screen tests (pre-existing, test infrastructure)**
- **Problem:** `VersionsPage.test.tsx` › "presents the snapshot as read-only, with its metadata" failed during `npm run check`. The DOM still showed the `Loading…` fallback.
- **Root cause:** the first `findBy*` of a screen test also waits for the route's lazy module import. The 1 s default budget is exceeded under machine load (Docker and uvicorn were running).
- **Fix:** `configure({ asyncUtilTimeout: 5000 })` in `src/test/setup.ts`.
- **Regression test:** the file ran 5 times in a row (25/25 each) plus two full runs (464/464). There is no dedicated test: the condition is load-dependent.
- **Affected area:** every Vitest screen test.

**Bug 2 — Racy full-stack assertion (pre-existing, test)**
- **Problem:** `npm run test:fullstack` failed 1/17: `getByText('Cancellation requested')` was not found within 5 s. The page showed `Cancelled` and "Live updates finished".
- **Root cause:** the cancel mutation stays pending until its invalidation refetch completes (`mutations.ts`). If the worker finishes the cooperative cancellation before that refetch, the backend already reports `cancelled`, so the intermediate state is never rendered. The test asserted a state the system may legitimately skip.
- **Fix:** removed that assertion. The final UI state, the hidden cancel button and the stored `{status: 'cancelled', cancellable: false}` are still asserted.
- **Regression test:** the intermediate state stays covered deterministically by `src/features/executions/ExecutionsPage.test.tsx:647`. The full-stack rerun passed 17/17.
- **Affected area:** `tests/fullstack/projects.spec.ts`.

**Bug 3 — Dev readiness probe hung (introduced and fixed during this phase)**
- **Problem:** the first `--dev` runs never printed credentials.
- **Root cause:** Finding 3 (foreign listener plus keep-alive reuse).
- **Fix:** the port guard, and a 2 s per-probe timeout.
- **Regression test:** manual. Port 8000 (taken) → immediate refusal; port 8001 → admin created. No automated test (§20).
- **Affected area:** `tests/fullstack/backend.ts`.

**Bug 4 — Heredoc editing artifacts (introduced and fixed during this phase)**
- **Problem:** script edits made through a Python heredoc wrote CRLF line endings, and literal newlines inside a template string.
- **Root cause:** Windows text-mode file writes, and shell escaping.
- **Fix:** line endings restored to LF; the template string rewritten with `\n`.
- **Regression test:** Prettier check, ESLint and `tsc -p tsconfig.node.json`.
- **Affected area:** `backend.ts`, `package.json`.

## 11. Fixes Applied

| Symptom | Root cause | Implementation | Verification |
|---|---|---|---|
| Screen test stuck on `Loading…` | 1 s budget includes cold lazy import | `asyncUtilTimeout: 5000` | 5× versions file; `npm run check` 464/464 twice |
| Full-stack 1/17 failure | Assertion on a skippable transient state | Assertion removed, reason commented | `test:fullstack` 17/17 |
| Dev backend never ready | Foreign listener on 8000 plus keep-alive reuse | Port guard, probe timeout | Manual runs on 8000 and 8001 |
| `npm run check` format failure on README | New sections not Prettier-formatted | `prettier --write README.md` | `format:check` PASS |

## 12. Testing & Verification

### Unit and component tests (Vitest)
- Baseline, before any change (`npm ci && … && npm test`): 464/464 PASS.
- During the phase: one failure (Bug 1).
- After the fix: `npm run check` 464/464 PASS (37 files), twice. The final run after all edits took 25.03 s.
- `src/features/versions` ran 5× in a row, 25/25 each.
- No new unit tests: no application logic changed.

### E2E tests (Playwright, stubbed API)
- `npm run test:e2e`: 24/24 PASS against the production build.

### Integration tests (Playwright full stack, real backend)
- `npm run test:fullstack`: first run 16/17 (Bug 2), rerun 17/17 PASS. Real PostgreSQL 17.2 and Redis containers.
- This exercises the modified `backend.ts` in full-stack mode, including the port guard on 8765.
- The trailing `ConnectionError: unexpected connection_lost()` in the log is the documented post-teardown worker noise.

### Infrastructure / workflow verification (manual)
| Workflow | Result |
|---|---|
| `npm ci` | PASS |
| `npm run dev:backend` with port 8000 occupied | Refused immediately with the port message; no containers started |
| `AKASHA_E2E_API_PORT=8001 npm run dev:backend` | Containers started, schema `0013_executions_created_at_index`, admin created, credentials printed |
| `npm run dev` with `AKASHA_API_PROXY_TARGET=http://127.0.0.1:8001` | `/login` rendered in the browser pane |
| Through the Vite proxy (curl) | Cookie login with `X-Akasha-CSRF` → 200; `GET /auth/session` → admin; `GET /nodes` → 200 |
| `lint`, `typecheck`, `format:check`, `build` | PASS (inside `npm run check`) |

### Not performed
- Interactive Ctrl+C shutdown of `dev:backend`: only hard kills were possible here.
- No accessibility, security or performance tests were run beyond the existing suites; no UI changed.
- No automated test of the `--dev` mode itself.

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | Failed once on the new README text, fixed with `prettier --write` |
| Linter | PASS | `eslint .`; `backend.ts` re-linted after the final comment edits |
| Type Checker | PASS | `tsc -b`; `tsc -p tsconfig.node.json` after the final comment edits |
| Unit Tests | PASS | 464/464 (one pre-existing flake fixed, §10) |
| Integration Tests | PASS | Full stack 17/17 (16/17 before the Bug 2 fix) |
| API Tests | N/A | No backend change |
| E2E Tests | PASS | 24/24 |
| Build | PASS | `vite build` |
| Security Checks | N/A | No dedicated run; no application or dependency change |
| Accessibility | N/A | No UI change |
| Other | PASS | `npm ci` |

The final `npm run check` ran after every code change. E2E and full stack ran before the last two comment-only edits to `backend.ts`.

## 14. Acceptance Criteria Verification

**Roadmap §28:**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Der Editor lässt sich effizient mit Maus und Tastatur bedienen | PARTIAL | From earlier phases: editor shortcuts (`EditorPage.tsx:150–188`: undo/redo, copy/paste, duplicate, save, delete, select all, Escape), arrow-key node moves, keyboard connection editing (`ConnectionsPanel.tsx`), node-library search, a node list that selects and centers nodes. Covered by `EditorPage.test.tsx`. Missing: command palette, shortcut help, quick add from the keyboard, editor performance tools, a dedicated diagnostics view. Not addressed in this phase (session scope, §1). Does not block the session scope; does leave the roadmap phase open. |

**Session prompt:**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| New developer can install dependencies | PASS | `npm ci` ran; README Setup |
| …start the frontend | PASS | `dev:backend` + `dev` ran end to end, including login through the proxy (§12) |
| …run tests | PASS | `npm test` 464/464 |
| …run lint | PASS | `npm run lint` |
| …run typecheck | PASS | `npm run typecheck` |
| …build | PASS | `npm run build` |
| …run E2E | PASS | `test:e2e` 24/24, `test:fullstack` 17/17 |
| …understand the feature structure | PASS | README: Structure, Adding a feature, Conventions, Testing. Not verified with an actual new developer |
| Development utilities, test helpers, API helpers, fixtures | PASS | Existing harness documented; `dev:backend`, `check` added; fixture rule documented. No new helper module (§5) |
| Error-handling conventions documented | PASS | README "Conventions" |
| Component conventions documented | PASS | README "Conventions", "Design system" |
| Storybook only if justified | PASS | Not added; reason in README "Testing" and §5 |
| No framework inside the application | PASS | No application code changed |
| Workflows documented, reproducible, consistent | PASS with limitations | §20 |

## 15. Security Considerations

- **Credentials:**
  - The dev admin password comes from `randomBytes(12)` per run.
  - It is printed only to the developer's own terminal and never written to a file.
  - It is valid only for a database on tmpfs that is removed with the run.
  - Password policy: 24 characters, within the backend's 12–256 limit (`domain/identity.py`).
- **Exposure:** the backend binds `127.0.0.1`. Containers publish to `127.0.0.1` on random host ports.
- **Configuration isolation:** the developer's `AKASHA_*` variables still do not reach the spawned backend. `AKASHA_E2E_API_PORT` is read only to choose the port.
- **Session cookie:** stays `Secure` (`session_cookie_secure` default). Browsers accept it on `localhost`, and curl did through the proxy. No security setting was relaxed for development.
- **Application code:** no change. No change to CSRF, auth, credential or rendering paths.

## 16. Performance Considerations

- `asyncUtilTimeout` affects only waits that fail. Suite durations were 21.8 s and 25.0 s, in line with earlier runs. Variance comes from machine load; the change was not separately benchmarked.
- The port guard adds one TCP connect at script start.
- No runtime or bundle changes (build output size unchanged; no dependency change).

## 17. Documentation Changes

| File | What |
|---|---|
| `frontend/README.md` | **Local development** (two terminals, throwaway semantics, port override, persistent alternative, example projects); **Commands** (`dev:backend`, `check`; shortened quality gate); **Structure** (both `components/` entries, `tests/fullstack/`, where hooks and schemas live; stale `hooks/schemas/types` claim removed); **Adding a feature** (API → route → screen → tests → docs, modelled on `features/reports/`); **Conventions** (errors, API layer, components, backend decides); **Testing** (`stubBackend`/`renderRoute` example, fixtures, `FakeWebSocket`, jsdom gaps, locale, E2E with `stubSession`, full stack, no Storybook); **Monitoring**; **Plugins**; stale `PlaceholderPage` sentence removed from Decisions |
| `docs/reports/frontend/phase-22-developer-experience.md` | This report |

**Should be updated later:**
- `docs/roadmap-frontend.md` §28, or a new phase, to schedule the editor power-user items.
- `frontend/AGENTS.md` §6 still shows `hooks/`, `schemas/` and `types/` as example directories. The README now explains the actual layout, but AGENTS.md was not changed: it is a rules file, and the example is marked as adaptable.

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded. `package-lock.json` unchanged.
- **npm scripts:** `dev:backend`, `check`.
- **Environment variables:** none new. `AKASHA_E2E_API_PORT` (existing) also selects the dev port; `AKASHA_API_PROXY_TARGET` (existing) is documented for the override.
- **Docker:** new container label `akasha-e2e=dev`; same images (`postgres:17.2`, `redis:8.2.1-alpine`).
- **Test infrastructure:** Testing Library `asyncUtilTimeout` raised to 5000 ms.

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects this phase |
|---|---|---|---|
| Command palette | Roadmap scope; session prompt took precedence | Unscheduled (roadmap §28) | Leaves the roadmap criterion PARTIAL |
| Shortcut help overlay | Same | Unscheduled | Same |
| Quick add from the keyboard | Same | Unscheduled | Same |
| Extended graph navigation | Same | Unscheduled | Same |
| Editor performance tools | Same; overlaps Phase 24 (Performance + UX Hardening) | Phase 24 or unscheduled | Same |
| Dedicated debug/diagnostics view | Same; system status exists on Dashboard and Settings | Unscheduled | Same |
| CI configuration | Not requested; repo-wide decision (backend too) | Phase 27 (Frontend Production Readiness) or separate | No |
| Automated test of `dev:backend --dev` | Needs Docker and `uv`; behavior verified manually | Unscheduled | No |

## 20. Known Limitations

- `dev:backend` data is lost when it stops. This is intended; persistent setups follow `backend/README.md`.
- If `dev:backend` is hard-killed instead of stopped with Ctrl+C, uvicorn may stay alive holding the port, and the containers stay. The next start removes the containers; the orphan process must be ended by hand. The port guard then reports the port.
- Ctrl+C cleanup was not verified interactively (§12).
- The port guard checks only `127.0.0.1`. A listener bound only to `::1` is not detected; the backend binds IPv4 and Vite proxies to `127.0.0.1`.
- `dev:backend` cannot be pointed at a persistent database; that is by design.
- "New developer understands the structure" was verified by document review only, not with a new developer.

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended action | Target |
|---|---|---|---|---|---|
| Roadmap Phase 22 editor items not implemented | Roadmap criterion PARTIAL | MEDIUM | Editor | Schedule as a separate phase or fold into Phase 24 | Undecided |
| No CI configuration | Quality gates depend on local discipline | MEDIUM | Repository | Add a CI job running `npm run check`, `test:e2e` and the backend gates | Phase 27 or separate |
| Possible verification leftovers on the developer machine | Port 8001 may be held; `akasha-e2e=dev` containers may run | LOW | Local machine | `docker ps -q --filter label=akasha-e2e=dev`, end the uvicorn process on 8001, delete `.claude/` | Now (manual) |

## 22. Architectural / Product Decisions

- **One script, two modes:** the throwaway backend for local development and for the full-stack suite is the same script. Modes are separated by container label, so neither cleans up the other.
- **Clash-free ports are enforced, not assumed:** the backend script refuses an occupied port, because Windows does not.
- **The test harness owns timing tolerance:** async-query budgets are set once in `setup.ts`, not per test. `ready` is for tests that must act before anything is findable.
- **Full-stack specs assert only states the backend guarantees.** Transient intermediate states belong in deterministic component tests.
- **No shared fixture module, no Storybook** (§5).
- **No global `hooks/`, `schemas/` or `types/`:** hooks live in their feature; schemas and inferred types in `src/api/`.

## 23. Repository / Git State

- **Branch:** `main`. HEAD `94d6bfb` (frontend phase 21). Nothing committed in this phase.
- **Modified:**
  - `frontend/README.md`
  - `frontend/package.json`
  - `frontend/src/test/app.tsx`
  - `frontend/src/test/setup.ts`
  - `frontend/tests/fullstack/backend.ts`
  - `frontend/tests/fullstack/projects.spec.ts`
- **Untracked:**
  - `docs/reports/frontend/phase-22-developer-experience.md` (this report)
  - `.claude/launch.json`: created for the browser-pane check of `npm run dev`; not part of the phase, must not be committed.
- **Unrelated changes:** none detected; the working tree was clean at session start.
- **Verification leftovers:** the cleanup of the last dev run (uvicorn on port 8001, `akasha-e2e=dev` containers, `.claude/`) was blocked by the session's permission policy. Their current presence was not re-checked, because that check was also blocked.

## 24. Phase Completion Assessment

### Implemented
- `npm run dev:backend` (`--dev` mode of `tests/fullstack/backend.ts`);
- the port guard;
- `npm run check`;
- the harness `asyncUtilTimeout` fix;
- the racy full-stack assertion removed;
- README sections: Local development, Adding a feature, Conventions, Testing, Monitoring, Plugins, plus corrections.

### Verified
`npm ci`, both `dev:backend` paths, `npm run dev` with login through the proxy, `npm run check` (464/464), `test:e2e` (24/24), `test:fullstack` (17/17).

### Not Implemented
The roadmap §28 feature list.

### Deferred
§19.

### Open
Roadmap editor items, CI, and machine leftovers (§21).

### Blocking Issues
None for the session scope. The roadmap acceptance criterion stays PARTIAL until its editor items are scheduled.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

- **The next phase (23, Frontend Security Hardening) can assume:**
  - `npm run check` is the local gate;
  - `npm run dev:backend` + `npm run dev` give a real, signed-in UI against this checkout's backend in one step;
  - the README "Testing" section describes the harness that security tests should use (`stubBackend`, `renderRoute`, `FakeWebSocket`, Playwright `stubSession`, full stack).
- **Contracts:** unchanged from Phase 21.
- **Must not be changed casually:**
  - **Container labels:** the `akasha-e2e=fullstack` / `akasha-e2e=dev` split in `backend.ts` (cleanup relies on it).
  - **`backend.ts` isolation:** the filtering of `AKASHA_*` variables.
  - **Admin creation:** dev-mode admin creation through `/auth/setup` only.
- **Constraints:**
  - Changes to `backend.ts` must be verified in both modes.
  - Full-stack specs must not assert transient states.
- **Open for planning:**
  - the roadmap Phase 22 editor items;
  - CI.
- **Prerequisite:** clean up the verification leftovers listed in §21.
