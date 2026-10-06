# Phase 00 — Frontend Repository Foundation

## 1. Executive Summary

Phase 00 was supposed to establish the frontend tooling foundation. That means React, TypeScript (strict), Vite, Tailwind CSS, ESLint, Prettier, Vitest, React Testing Library, a Playwright foundation, environment configuration, npm package structure and the basic directory structure. No product features were in scope.

When the phase started, the previously committed frontend (the third-party "Ecme" admin template, package name `ecme`, about 70 runtime dependencies including `firebase` and `axios`) and `examples/frontend-demo/` had already been deleted in the working tree. Those deletions were uncommitted and were not made during this session. Only `frontend/AGENTS.md`, `.gitignore`, `.prettierrc`, `.prettierignore` and a git-ignored `.env` remained. The foundation was therefore built from scratch in `frontend/`.

What was implemented:

- An npm project (`akasha-frontend`) with a committed lockfile and 2 runtime dependencies (`react`, `react-dom`).
- 21 dev dependencies, all pinned exactly.
- Separate strict TypeScript configs for app code and tooling code.
- ESLint flat config using the type-checked `strictTypeChecked` + `stylisticTypeChecked` rule sets.
- Prettier, kept with the repository's existing style.
- Vitest with jsdom and React Testing Library.
- A Playwright smoke test that runs against the production build.
- Tailwind v4 through its Vite plugin.
- A dev/preview `/api` proxy configured through an environment variable.
- A minimal `App` component.
- A rewritten `frontend/README.md`.

Resulting state:

- `npm ci` installs reproducibly with 0 vulnerabilities.
- Format, lint, typecheck, unit test, production build and E2E smoke test all pass from a clean install.
- The dev server starts and serves the app.

Every acceptance criterion in the roadmap is satisfied (§14). Two limitations remain:

- The repository has no CI pipeline, so the gates are only run manually.
- The deleted template files are still uncommitted in the working tree, outside this phase's control.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Per `/docs/roadmap-frontend.md` §6, Phase 00:

> Eine saubere, professionelle Frontend-Grundlage schaffen.

("Create a clean, professional frontend foundation.")

The scope is to set up React, TypeScript, Vite, Tailwind CSS, ESLint, Prettier, Vitest, React Testing Library, a Playwright foundation, TypeScript strict mode, environment configuration, npm/package-manager structure and the basic directory structure. The roadmap requires these checks: TypeScript, lint, unit test and build.

Explicitly not in scope:

- Routing, layout, sidebar and error boundaries (Phase 01).
- The design system (Phase 02).
- The API client (Phase 03).
- Domain types, TanStack Query and Zod (Phase 04).
- All feature areas: dashboard, workflows, editor, nodes, executions, authentication and so on (Phases 05–21).

The session prompt also excluded the dashboard, workflow management, graph editor, node library, authentication UI and execution UI.

---

## 3. Scope Implemented

### Tooling / Package Management

- `package.json` scripts: `dev`, `build` (`tsc -b && vite build`), `preview`, `typecheck` (`tsc -b`), `lint` (`eslint .`), `format`, `format:check`, `test` (`vitest run`), `test:watch`, `test:e2e` (`playwright test`).
- `engines.node`: `^22.13.0 || >=24.0.0`. This is the intersection of the engine ranges declared by Vite 8, Vitest 5, ESLint 10 and jsdom 29.
- `.npmrc`: `save-exact=true` and `engine-strict=true`.
- `.nvmrc`: `24`.
- `package-lock.json`: regenerated and committed-ready.

### TypeScript

- `tsconfig.json`: a solution file that references `tsconfig.app.json` and `tsconfig.node.json`.
- `tsconfig.app.json`, for `src/`:
  - DOM libs and `types: ["vite/client"]`.
  - `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noPropertyAccessFromIndexSignature`, `noImplicitOverride`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`, `isolatedModules`, `noEmit`.
- `tsconfig.node.json`, for `vite.config.ts`, `playwright.config.ts` and `tests/`:
  - Node types and the same strict flags.
  - The one exception is `noPropertyAccessFromIndexSignature`, which is omitted so `process.env.CI` stays idiomatic.

### Linting / Formatting

- `eslint.config.js` (ESLint 10 flat config, `defineConfig`):
  - `@eslint/js` recommended.
  - `typescript-eslint` `strictTypeChecked` + `stylisticTypeChecked`, using `projectService`.
  - `disableTypeChecked` for `**/*.js`, since the config file is outside every tsconfig.
  - `eslint-plugin-react-hooks` `flat.recommended` and `eslint-plugin-react-refresh` `vite` for `src/**`.
  - Ignores `dist`, `coverage`, `playwright-report` and `test-results`.
- Prettier: the existing `.prettierrc` is kept (`singleQuote`, no `semi`, `tabWidth: 4`).
- `.prettierignore` was rewritten for the new layout. It ignores build/test output, `package-lock.json` and `AGENTS.md`.

### Build / Styling

- `vite.config.ts`: `@vitejs/plugin-react`, `@tailwindcss/vite`, the `/api` proxy for `server` and `preview`, and the Vitest `test` block.
- `src/styles/index.css`: `@import 'tailwindcss';`. There is no `tailwind.config.*` or PostCSS config; Tailwind v4 doesn't need them.

### Application

- `index.html`: `lang="en"`, viewport meta, title "Akasha Agent Engine", `#root`, module script.
- `src/main.tsx`: finds `#root`, throws an explicit `Error` if it is missing (no non-null assertion), and renders `<App />` inside `StrictMode`.
- `src/app/App.tsx`: the named export `App`. It renders a `<main>` containing one `<h1>` with Tailwind classes. This is a placeholder only; there is no shell or routing.

### Environment Configuration

- `.env.example` documents `AKASHA_API_PROXY_TARGET`, which defaults to `http://127.0.0.1:8000`.
- `vite.config.ts` reads it with `loadEnv(mode, cwd, 'AKASHA_')`. It is used only by the Node-side Vite config.
- The file warns that only `VITE_*` variables are exposed to the bundle.

### Testing

- `src/test/setup.ts`: imports `@testing-library/jest-dom/vitest` and calls RTL `cleanup()` in `afterEach`.
- `src/app/App.test.tsx`: an RTL test that checks the level-1 heading by accessible role and name.
- `playwright.config.ts`:
  - Chromium project, `testDir: tests/e2e`.
  - `webServer` builds the app and serves it with `vite preview` on `127.0.0.1:4173 --strictPort`.
  - `forbidOnly` and no server reuse when `CI` is set; trace kept on failure.
- `tests/e2e/smoke.spec.ts`: checks the document title and that the heading is visible in the production bundle.

### Documentation

- `frontend/README.md` was rewritten. It covers requirements, setup, the command table, the quality-gate command, configuration, directory structure and decisions.

---

## 4. Files Changed

All paths are relative to `frontend/` unless noted otherwise.

| File | Change | Reason |
|---|---|---|
| `package.json` | Modified (fully replaced) | The Ecme template manifest was replaced with the foundation manifest, scripts and engines. |
| `package-lock.json` | Modified (regenerated) | Reproducible install of the new dependency set. |
| `.npmrc` | Created | `save-exact`, `engine-strict` (AGENTS.md §54). |
| `.nvmrc` | Created | Pins the development Node major (24). |
| `tsconfig.json` | Modified (replaced) | Solution file with project references. |
| `tsconfig.app.json` | Created | Strict config for browser code. |
| `tsconfig.node.json` | Modified (replaced) | Strict config for tooling and E2E code. |
| `vite.config.ts` | Modified (replaced) | React and Tailwind plugins, `/api` proxy, Vitest config. |
| `eslint.config.js` | Created | Type-checked flat config. Replaces the deleted template `eslint.config.mjs`. |
| `playwright.config.ts` | Created | Playwright foundation. |
| `index.html` | Modified (replaced) | Minimal entry document. |
| `src/main.tsx` | Modified (replaced) | Entry point. |
| `src/app/App.tsx` | Created | Root component placeholder. |
| `src/app/App.test.tsx` | Created | Baseline component test. |
| `src/styles/index.css` | Created | Tailwind v4 entry. |
| `src/test/setup.ts` | Created | Vitest/RTL setup. |
| `tests/e2e/smoke.spec.ts` | Created | E2E smoke test. |
| `.env.example` | Created | Documents environment variables. |
| `.gitignore` | Modified | Added `coverage`, `playwright-report`, `test-results`. |
| `.prettierignore` | Modified | Template-specific entries removed (`/public`, `/src/assets/styles`, `/src/index.css`, `package.json`, `package.lock.json` [sic]). New-layout entries added. |
| `.prettierrc` | Modified (whitespace only) | Prettier rewrote line endings and added the final newline. Content is unchanged. |
| `README.md` | Modified (replaced) | Frontend development documentation. |
| `docs/reports/frontend/phase-00-repository-foundation.md` (repo root) | Created | This report. |

New directories: `src/app/`, `src/styles/`, `src/test/`, `tests/e2e/`, and `docs/reports/frontend/` at the repo root.

Generated and git-ignored: `node_modules/`, `dist/`, `node_modules/.tmp/*.tsbuildinfo`, and Playwright `test-results/` when tests fail.

No migrations. No backend files changed.

Pre-existing deletions that were not made in this phase are listed in §23.

---

## 5. Architecture Changes

### 5.1 Same-origin API access via proxy

**Decision:** Browser code calls the backend on its own origin under `/api`. The Vite dev and preview servers proxy `/api` to `AKASHA_API_PROXY_TARGET`.

**Context:**

- The backend listens on `127.0.0.1:8000` (`backend/src/akasha/config.py`, `docker-compose.yml`).
- `grep -i cors backend/src` finds no CORS middleware.
- A browser app on `:5173` calling `:8000` directly would fail cross-origin.

**Chosen approach:** `server.proxy` and `preview.proxy` with `{'/api': apiTarget}`. The target is read server-side with `loadEnv(..., 'AKASHA_')`, so it never reaches the bundle.

**Alternatives considered:**

- Add CORS to the backend. This would mean a backend change outside a frontend phase, and it widens the attack surface.
- A `VITE_API_BASE_URL` absolute URL. This needs CORS too, and it bakes the URL into the bundle.

**Reason:** This needs no backend change. It also keeps cookies and CSRF semantics same-origin, which is relevant for Phase 21 authentication.

**Consequences:**

- Phase 03's API client should use relative `/api/v1/...` paths.
- Production deployment needs a reverse proxy that serves the bundle and `/api` from one origin. `docs/architecture.md` §68 (Deployment Architecture) notes "No frontend or reverse proxy service yet", so this is still open for Phase 27.

### 5.2 E2E against the production bundle

**Decision:** Playwright's `webServer` runs `npm run build && npm run preview`, not `npm run dev`.

**Reason:** E2E should exercise what ships. A single smoke test costs about 1 s of build time.

**Consequence:** E2E runs include a full build. If later suites outgrow this, they can switch to a prebuilt `dist/`.

### 5.3 No empty scaffold directories

**Decision:** Only directories with content were created. The roadmap example (`components/`, `features/`, `pages/`, `api/`, `domain/`, `hooks/`, `state/`, `utils/`) is documented in `frontend/README.md` as the target layout rather than created empty.

**Reason:** Git doesn't track empty directories. `frontend/AGENTS.md` §112 forbids "halbfertige Vorbereitungen" (half-finished preparations). See also Finding 9.3.

**Consequence:** Each phase creates its directories when it adds code to them.

---

## 6. Domain / Data Model Changes

Not applicable. No domain types, schemas or persistence were introduced.

---

## 7. API / Contract Changes

No API or contract changes. No endpoint is called by frontend code. The only API-adjacent artifact is the `/api` dev/preview proxy (§5.1), which doesn't define a contract.

---

## 8. Implementation Details

- **Entry flow:** `index.html` → `/src/main.tsx` → `createRoot(#root).render(<StrictMode><App/></StrictMode>)`. If `#root` is missing, `main.tsx` throws `Error('Missing #root element in index.html')` instead of using a `!` assertion. This follows the rule against hiding uncertainty with type assertions (`frontend/AGENTS.md` §4).
- **Type checking:** `tsc -b` checks both projects. `noEmit` is set and `tsBuildInfoFile` is placed under `node_modules/.tmp/`, so incremental build info never lands in the source tree.
- **Linting:**
  - Type-aware linting uses `projectService: true`. It resolves `.ts`/`.tsx` files through the referenced tsconfigs.
  - `eslint.config.js` itself is linted without type information (`tseslint.configs.disableTypeChecked`), because no tsconfig includes it.
  - Including it would need `allowJs` plus Node types in a third config.
- **Vitest:**
  - Configured inside `vite.config.ts` through `vitest/config`'s `defineConfig`, so there is a single config file.
  - `include: ['src/**/*.test.{ts,tsx}']` keeps Playwright specs out of Vitest.
  - `restoreMocks: true` resets spies between tests.
- **Tailwind:** Tailwind v4 scans sources automatically. There are no theme tokens yet; those belong to Phase 02.
- **Environment:**
  - Vite exposes only `VITE_*` variables to the client. The proxy target uses the `AKASHA_` prefix and is loaded explicitly in Node, so it cannot leak into the bundle.
  - No `import.meta.env` usage exists in `src/`.
- No state management, data fetching, error handling or concurrency exists yet. That is intended for this phase.

---

## 9. Findings

### 9.1 Previous frontend template and demo were already deleted, uncommitted

- **Finding:** The working tree had 3,460 unstaged deletions:
  - 2,679 files under `examples/frontend-demo/`.
  - 781 files under `frontend/`: the Ecme admin template with `src/`, `public/img/countries/*`, `tailwind.config.cjs`, `postcss.config.cjs`, `eslint.config.mjs` and others.
  - These deletions were present before any change in this session. The session-start git snapshot reported "clean", so they happened shortly before the session.
- **Impact:** MEDIUM. The phase diff mixes these deletions with the new foundation unless they are committed separately.
- **Resolution:** OPEN. Left untouched as user work, per instructions.
- **Notes:** The new foundation shares no code with the template. Recommendation: commit the deletion separately from the Phase 00 commit (AGENTS.md §65, focused commits).

### 9.2 Stale Firebase `.env` from the template

- **Finding:** `frontend/.env` (git-ignored, untracked, dated Jan 30 2026) contains eight `VITE_FIREBASE_*` variables left over from the template. Vite loads `.env` automatically.
- **Impact:** LOW. Variables are only inlined when referenced. Verified: there is no `import.meta.env` usage in `src/`, and `grep -ri firebase dist/` finds nothing.
- **Resolution:** OPEN. Not deleted, because it is the user's local file.
- **Notes:** Recommend deleting it. Any later `VITE_*` usage would pick up unrelated variables from it.

### 9.3 Inconsistent target directory structures across documents

- **Finding:** Three documents propose different `src/` layouts:
  - `docs/roadmap-frontend.md` §6: `app, components, features, pages, api, domain, hooks, state, utils`.
  - `docs/architecture.md` §38: `app, features, components, api, hooks, stores, types`.
  - `frontend/AGENTS.md` §6: `app, components, features, api, hooks, lib, schemas, types, styles`.
- **Impact:** LOW. All three say the structure can be adapted.
- **Resolution:** DEFERRED. The README lists the union that `frontend/AGENTS.md` §6 prefers (it outranks the roadmap per its §1). Concrete directories appear with their phases.
- **Notes:** Phases 01–04 should settle on one structure and update `docs/architecture.md` §38 if needed.

### 9.4 Backend has no CORS configuration

- **Finding:** No CORS middleware exists in `backend/src`.
- **Impact:** MEDIUM, architectural.
- **Resolution:** FIXED by design: the same-origin proxy described in §5.1.
- **Notes:** Production still needs a reverse proxy (Phase 27).

### 9.5 Current dependency majors are newer than the architecture docs imply

- **Finding:** The resolved latest versions are TypeScript 6.0, Vite 8.3, Vitest 5.0, ESLint 10.11, React 19.3, Tailwind 4.3 and jsdom 29. Their declared engine ranges differ; for example, Vitest excludes Node 23 and 25.
- **Impact:** LOW.
- **Resolution:** FIXED. `engines.node` is set to the intersection `^22.13.0 || >=24.0.0`, and `engine-strict=true` is enforced.

### 9.6 Playwright browsers are not installed by `npm ci`

- **Finding:** `@playwright/test` doesn't download browsers. `npx playwright install chromium` downloaded Chrome Headless Shell 153 (about 99 MiB) into `~/Library/Caches/ms-playwright`.
- **Impact:** LOW.
- **Resolution:** WORKAROUND. This is documented as a setup step in `frontend/README.md`.

### 9.7 No CI pipeline exists in the repository

- **Finding:** There is no `.github/` directory or any other CI configuration, for the frontend or the backend.
- **Impact:** MEDIUM. AGENTS.md §93 requires CI, so the quality gates are currently enforced only manually.
- **Resolution:** OPEN. Out of Phase 00 scope; the roadmap doesn't list CI for this phase.

---

## 10. Bugs / Problems Found

The first three items were bugs introduced during this phase and fixed before completion. The last item was a tooling problem.

| # | Problem | Root cause | Fix | Regression check | Area |
|---|---|---|---|---|---|
| 1 | ESLint: `eslint.config.js was not found by the project service` | The type-aware parser needs a tsconfig for every linted file, and the JS config file was in none. | Removed it from the `tsconfig.node.json` include and applied `tseslint.configs.disableTypeChecked` to `**/*.js`. | `npm run lint` passes. | Tooling |
| 2 | ESLint `@typescript-eslint/no-unsafe-assignment` on `import.meta.dirname` in `eslint.config.js` | An intermediate attempt (`allowDefaultProject`) type-checked the file without Node types. | Superseded by fix #1. | `npm run lint` passes. | Tooling |
| 3 | ESLint `@typescript-eslint/dot-notation` on `process.env['CI']` and `env['AKASHA_API_PROXY_TARGET']` | Bracket access was written for `noPropertyAccessFromIndexSignature`, but that flag is not enabled in `tsconfig.node.json`. | Switched to dot access. | `npm run lint` and `npm run typecheck` pass. | Tooling |
| 4 | `@types/node` resolved to 26.x while the runtime is Node 24 | `npm install` picks the latest version. | Pinned to `@types/node@24` (24.19.0). | `npm ls` | Dependencies |

No pre-existing bugs in project code were discovered. The previous frontend code was already deleted.

---

## 11. Fixes Applied

These are the same as the fixes in §10:

1. Type-aware ESLint configuration for the JS config file: disable typed rules for `**/*.js`. Verified with `npm run lint`.
2. Dot-notation consistency in Node-side configs. Verified with lint and typecheck.
3. `@types/node` aligned with the Node 24 runtime. Verified with `npm ls`.
4. `.prettierrc` normalized by Prettier (line endings and final newline). Verified with `npm run format:check`.

---

## 12. Testing & Verification

### Component Tests (Vitest + RTL, jsdom)

- `src/app/App.test.tsx`, "renders the application heading": renders `<App/>` and asserts `getByRole('heading', { level: 1, name: 'Akasha Agent Engine' })` with `toBeInTheDocument()`. This also verifies that the jest-dom matchers from `src/test/setup.ts` are registered.
- Result: 1 passed.

### E2E Tests (Playwright, Chromium headless shell 153)

- `tests/e2e/smoke.spec.ts`, "production build serves the application": builds, serves with `vite preview`, and asserts the title and that the heading is visible.
- Result: 1 passed (run with `CI=1`, so no existing server is reused).

### Manual / Infrastructure Verification

- **Dev server:** `npx vite --port 5179 --strictPort`.
  - `GET /` returned HTML with `<title>Akasha Agent Engine</title>`.
  - `GET /src/main.tsx` returned 200.
  - `GET /api/v1/health` returned 502 with Vite log `http proxy error ... ECONNREFUSED 127.0.0.1:8000`. This shows the proxy forwards to the configured target; the backend was not running.
- **Reproducible install:** `rm -rf node_modules dist && npm ci` succeeded with 0 vulnerabilities, followed by all gates.
- **Bundle secret check:** `grep -ri firebase dist/` found no matches.

### Not performed

There are no unit tests of logic, because no logic exists. No integration, API, contract, accessibility, security or performance tests were run, because there is nothing to test in those categories yet.

---

## 13. Quality Gates

All gates were run after a clean `rm -rf node_modules dist && npm ci`.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `npm run format:check`: "All matched files use Prettier code style!" |
| Linter | PASS | `npm run lint`: no output. Initially failed (§10 #1–3), then fixed. |
| Type Checker | PASS | `npm run typecheck` (`tsc -b`, both projects). |
| Unit/Component Tests | PASS | `npm test`: 1 file, 1 test. |
| Integration Tests | N/A | No integration surface yet. |
| API Tests | N/A | No API usage yet. |
| E2E Tests | PASS | `CI=1 npm run test:e2e`: 1 passed. |
| Build | PASS | `npm run build`: JS 219.85 kB (68.70 kB gzip), CSS 5.13 kB (1.79 kB gzip), no warnings. |
| Security Checks | PASS (limited) | `npm audit`: 0 vulnerabilities. No other security tooling. |
| Accessibility | N/A | No interactive UI. The page uses `lang`, `<main>` and `<h1>`; no automated audit was run. |
| Dependency install | PASS | `npm ci` is reproducible from `package-lock.json`. |

---

## 14. Acceptance Criteria Verification

The criteria are from `docs/roadmap-frontend.md` §6.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Frontend startet lokal (frontend starts locally) | PASS | Dev server served `/` with the correct title and transformed `src/main.tsx` (200). `vite preview` served the bundle during the E2E run. |
| Production Build funktioniert (production build works) | PASS | `npm run build` succeeded after a clean install; output in `dist/`. |
| TypeScript strict ist aktiv (TypeScript strict is active) | PASS | `"strict": true` plus additional strict flags in `tsconfig.app.json` and `tsconfig.node.json`; `tsc -b` passes. |
| Tests funktionieren (tests work) | PASS | Vitest component test and Playwright E2E test both pass. |
| Lint funktioniert (lint works) | PASS | `npm run lint` passes with type-aware strict rules. It demonstrably reports errors (§10). |
| Formatter funktioniert (formatter works) | PASS | `npm run format` / `format:check` pass. Prettier changed `.prettierrc` and `App.test.tsx` on its first run, which shows it is active. |
| Keine unnötigen globalen Abhängigkeiten (no unnecessary global dependencies) | PASS | Runtime dependencies are only `react` and `react-dom`. No globally installed tools are needed; everything runs through `npm` scripts and `npx` from local `node_modules`. Router, TanStack Query and Zod were not added because they belong to Phases 01 and 04. |

Additional criteria from the session prompt:

| Criterion | Status | Evidence |
|---|---|---|
| Dependency installation reproducible | PASS | `npm ci` from the lockfile; exact versions; `engine-strict`. |
| Repository structure documented | PASS | `frontend/README.md` "Structure" section. |
| No future-phase functionality | PASS | `src/` contains only `main.tsx`, `app/App.tsx`, its test, the CSS entry and the test setup. |

---

## 15. Security Considerations

- **Secrets in the bundle:**
  - Only `VITE_*` variables reach client code, and none are used.
  - The proxy target uses a non-`VITE_` prefix.
  - `.env.example` warns against secrets in `VITE_*`.
  - `.env` and `*.local` are git-ignored.
- **Stale secrets on disk:** `frontend/.env` holds Firebase configuration values from the old template (Finding 9.2). They are not bundled and not committed, but the file should be deleted.
- **Unsafe rendering:** No `dangerouslySetInnerHTML`, `innerHTML`, `eval` or `Function`.
- **Dependencies:** `npm audit` reports 0 vulnerabilities. The runtime dependency surface was reduced from about 70 template packages (including `firebase`, `axios`, `axios-mock-adapter`) to 2.
- **CSRF / auth:** No authentication code yet. The same-origin proxy decision keeps cookie-based authentication viable later.
- The proxy binds nothing new. The dev server defaults to localhost, and the preview in E2E binds to `127.0.0.1`.

---

## 16. Performance Considerations

- Measured production bundle: JS 219.85 kB (68.70 kB gzip), almost entirely React and ReactDOM. CSS 5.13 kB (1.79 kB gzip).
- Build time is about 0.65 s. A full E2E run takes about 5.4 s including the build.
- No code-splitting decisions have been made yet.
- No other performance claims are made.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `frontend/README.md` | Replaced the template README | Requirements, setup, commands, quality gate, configuration (`AKASHA_API_PROXY_TARGET`, `VITE_*` rule, same-origin model), structure, decisions. |
| `frontend/.env.example` | New | Environment variable documentation. |
| `docs/reports/frontend/phase-00-repository-foundation.md` | New | This report. |

Documentation that should be updated later:

- `docs/architecture.md` §38: align the `src/` layout once settled (Finding 9.3).
- `docs/deployment.md` / architecture: same-origin reverse proxy for the frontend (Phase 27).
- Root `README.md` mentions `package-lock.json / pnpm-lock.yaml`. npm is now chosen and could be stated definitively.

---

## 18. Dependencies / Infrastructure Changes

Runtime dependencies, both exact:

- `react@19.3.0`
- `react-dom@19.3.0`

Dev dependencies, all exact:

| Package | Version | Purpose |
|---|---|---|
| `typescript` | 6.0.3 | Type checking |
| `vite` | 8.3.2 | Dev server / bundler |
| `@vitejs/plugin-react` | 6.1.1 | React transform / Fast Refresh |
| `tailwindcss`, `@tailwindcss/vite` | 4.3.3 | Styling (Tailwind v4 Vite plugin) |
| `@types/react`, `@types/react-dom` | 19.3.0 | React types |
| `@types/node` | 24.19.0 | Types for Node-side configs (pinned to the runtime major) |
| `eslint`, `@eslint/js` | 10.11.0, 10.0.1 | Linting |
| `typescript-eslint` | 8.71.0 | Type-aware TS linting |
| `eslint-plugin-react-hooks` | 7.1.1 | Hooks rules |
| `eslint-plugin-react-refresh` | 0.5.7 | Fast Refresh export rules |
| `globals` | 17.13.0 | Browser globals for ESLint |
| `prettier` | 3.9.9 | Formatting |
| `vitest` | 5.0.3 | Unit/component tests |
| `jsdom` | 29.1.1 | DOM environment for Vitest |
| `@testing-library/react`, `@testing-library/dom` | 16.3.3, 10.4.2 | Component testing (`dom` is a peer dependency) |
| `@testing-library/jest-dom` | 7.0.1 | DOM matchers |
| `@playwright/test` | 1.63.0 | E2E |

`@testing-library/user-event` was not added; no test needs interaction yet.

Removed: the entire Ecme template dependency set (the deletion pre-dated this session; this phase replaced the manifest).

Environment variables: `AKASHA_API_PROXY_TARGET`, dev and preview only.

Infrastructure:

- No Docker, database or Redis changes.
- Playwright Chromium headless shell is installed in the user cache and is not versioned in the repo.

---

## 19. Deferred Work

| Item | Why deferred | Target phase | Affects Phase 00? |
|---|---|---|---|
| Router, layout, error boundary | Out of scope | 01 | No |
| Design tokens / UI components | Out of scope | 02 | No |
| API client, OpenAPI-generated types (`architecture.md` §42) | Out of scope | 03 | No |
| TanStack Query, Zod | Out of scope | 04 | No |
| `@testing-library/user-event` | No interaction to test yet | First phase with interactive components (01/02) | No |
| Accessibility test tooling (e.g. axe) | No UI to audit yet | 25 (earlier if useful) | No |
| CI pipeline | Not in the Phase 00 roadmap scope | Unassigned (22 or 27 likely) | No |
| Production reverse proxy for same-origin `/api` | Deployment concern | 27 | No |

---

## 20. Known Limitations

- The quality gates are not automated in CI.
- E2E covers Chromium only; browser support is not yet defined (`frontend/AGENTS.md` §78).
- Playwright browsers need a separate manual install step.
- The `/api` proxy only exists in the Vite dev and preview servers. There is no production serving setup.
- The UI is a single static heading. There is no theme or dark mode.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended action | Target |
|---|---|---|---|---|---|
| Uncommitted deletion of the Ecme template and `examples/frontend-demo/` (3,460 files) | Mixed diff, unclear history | MEDIUM | Repository | Commit the deletion separately, before or alongside the Phase 00 commit. | Now (user) |
| Stale `frontend/.env` with Firebase values | Possible future confusion or leak via `VITE_*` | LOW | Frontend config | Delete the file. | Now (user) |
| No CI | Regressions not caught automatically | MEDIUM | Repo / infra | Add a CI workflow running the frontend and backend gates. | Unassigned |
| Divergent `src/` layout across docs | Structural drift | LOW | Docs | Settle and update `architecture.md` §38. | 01–04 |

---

## 22. Architectural / Product Decisions

- **npm is the package manager.** `package-lock.json` is authoritative. pnpm is not installed on the development machine; the root README allowed either.
- **The browser talks same-origin to `/api`.** The dev/preview proxy target is server-side only (`AKASHA_` prefix); see §5.1.
- **Tailwind v4 is used with the Vite plugin and no JS config.** Theme tokens will be CSS-first in Phase 02.
- **Vitest config lives in `vite.config.ts`.** This avoids drift between the two configs.
- **Unit and component tests are co-located as `src/**/*.test.ts(x)`.** E2E specs live in `tests/e2e/`.
- **Strictness beyond `strict` is deliberate:** `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, `noPropertyAccessFromIndexSignature` and type-aware `strictTypeChecked` lint. Relaxing them later should require a documented reason.
- **The existing Prettier style is kept:** 4 spaces, single quotes, no semicolons.
- **The template was not reused.** The Ecme template was already deleted and conflicts with the architecture (Firebase, axios-mock-adapter, template-specific structure).

---

## 23. Repository / Git State

- Branch: `main`. HEAD is `b783fd5` ("backend phase 27 - backend v1 integration"). Nothing has been committed for this phase.
- Phase 00 changes are uncommitted.
  - Modified: `frontend/{.gitignore,.prettierignore,.prettierrc,README.md,index.html,package-lock.json,package.json,src/main.tsx,tsconfig.json,tsconfig.node.json,vite.config.ts}`
  - Untracked: `frontend/{.env.example,.npmrc,.nvmrc,eslint.config.js,playwright.config.ts,tsconfig.app.json,src/app/,src/styles/,src/test/,tests/}` and `docs/reports/frontend/`
- Unrelated or pre-existing changes: 3,460 unstaged deletions (`examples/frontend-demo/**`: 2,679; old template files under `frontend/**`: 781). These were not made by this phase and were left untouched.
- Ignored local files: `frontend/.env` (stale Firebase values), `frontend/node_modules/`, `frontend/dist/`, `.DS_Store` files.
- No reset, revert or stash was performed.

---

## 24. Phase Completion Assessment

### Implemented

React 19 + TypeScript 6 strict + Vite 8 app skeleton; Tailwind 4; ESLint 10 with type-aware strict rules; Prettier; Vitest 5 + RTL + jest-dom; Playwright foundation with a smoke test; env-configured `/api` proxy; npm with an exact-pinned lockfile, `engines` and `.nvmrc`; README.

### Verified

Clean `npm ci`; format, lint, typecheck, component test, build and E2E all pass; dev server serves the app and proxies `/api`; no Firebase strings in the bundle.

### Not Implemented

Any product functionality. This is intended.

### Deferred

See §19.

### Open

Uncommitted template deletion, stale `.env`, missing CI, docs layout divergence (§21).

### Blocking Issues

None. Every roadmap acceptance criterion is PASS. The status is "with limitations" because CI does not exist and the working tree contains unrelated uncommitted deletions.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (Phase 01 — Application Shell)

What Phase 01 can assume:

- `npm ci` followed by the quality-gate command in `frontend/README.md` works.
- `src/main.tsx` renders `App` from `src/app/App.tsx` inside `StrictMode`. Phase 01 replaces the `App` body with the router and shell.
- Tailwind v4 is active through `src/styles/index.css`.
- Tests live next to code as `*.test.tsx` and use jsdom with jest-dom matchers. E2E specs go in `tests/e2e/` and run against the production build.

What Phase 01 will need:

- `react-router` is not installed. Add it, exactly pinned, with `save-exact` applied automatically.
- `@testing-library/user-event` is likely needed for navigation tests.
- When client-side routes are added, check that `vite preview` and any future production server fall back to `index.html` for deep links. The current E2E test only loads `/`.

Must not be changed casually:

- The TypeScript strict flags.
- The `strictTypeChecked` lint baseline.
- The same-origin `/api` convention: future API code must use relative `/api/v1` paths.
- The rule that secrets never go in `VITE_*` variables.

Open items to be aware of:

- Commit the pre-existing template deletion separately.
- Delete `frontend/.env`.
- There is no CI.
