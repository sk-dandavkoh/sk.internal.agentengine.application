# Phase 23 — Frontend Security Hardening

Report date: 2026-10-05. Branch `main`. Base commit `64c11d3` (frontend phase 22 — developer experience). All work described here is **uncommitted** (§23).

## 1. Executive Summary

**Supposed to achieve.** `docs/roadmap-frontend.md` §29 asks for a systematic hardening pass over the frontend. The areas are XSS, unsafe HTML, token handling, secret handling, URL handling, open redirects, WebSocket authentication, error disclosure, sensitive browser storage, CSP "soweit sinnvoll" (as far as sensible), dependency security, file upload UI and download handling.

The session prompt adds more areas: LLM output, report and log rendering, plugin-provided content, credential UI, CSRF/CORS assumptions and API authorization assumptions. It required the method threat model → attack → fix → regression test → review. It also allowed backend additions if the backend lacked something. The prompt asked for best practice without paranoia, because the app is self-hosted.

**Actually implemented.**

- **Path traversal through route ids (pre-existing defect, fixed):** `ApiClient.requestWithBody` (`frontend/src/api/client.ts`) now refuses any request path with a `.` or `..` segment. Before the fix, a crafted URL such as `/projects/../editor` let the route param `..` turn `POST /api/v1/projects/../publish` into `POST /api/v1/publish`. A test against a real local HTTP server reproduced this before the fix.
- **Content Security Policy:** a Vite plugin in `frontend/vite.config.ts` injects a strict CSP `<meta>` into the production `index.html` only. ADR 0002 had explicitly deferred CSP to this phase.
- **Regression guards in ESLint** (`frontend/eslint.config.js`), applied to production code. They forbid:
  - `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML` and `document.write`;
  - `eval` and `new Function`;
  - `localStorage` and `sessionStorage`, except in `src/features/settings/theme.ts`.
- **E2E CSP enforcement:** `frontend/tests/e2e/fixtures.ts` is an auto fixture that fails a test on any CSP violation. All E2E specs and the full-stack spec now use it. The new `frontend/tests/e2e/security.spec.ts` proves that an injected inline script and an inline event handler do not run.
- **Referrer policy:** `<meta name="referrer" content="same-origin">` in `frontend/index.html`.
- **Documentation:**
  - `docs/deployment.md`: proxy headers for serving the UI (`frame-ancestors`, `nosniff`, `Referrer-Policy`, `Cache-Control` for `index.html`).
  - `frontend/README.md`: a new "Security" section.

The review found most surfaces already hardened by earlier phases (§9). No backend change was necessary: every backend control the frontend relies on already exists and has tests (§9, Finding 7).

**Resulting state.** The frontend has no known high-risk security defect in V1 scope.

- Every quality gate passes (§13).
- One **pre-existing** smoke E2E test is flaky on this developer machine. Another local service listens on port 8000, which is the preview proxy target. The cause is environmental and the test predates this phase (§9 Finding 6, §21). It is not a security defect and does not affect the acceptance criteria.

**Phase Status:** COMPLETE WITH LIMITATIONS

The limitations: framing protection depends on operator proxy headers (§20), and the flaky smoke test is still open (§21).

## 2. Phase Objective

Roadmap §29 goal: "Das Frontend sicher gegen typische Web-Anwendungsprobleme machen" (make the frontend secure against typical web application problems).

Acceptance criteria (roadmap §29):

1. Keine unnötige Speicherung sensibler Daten (no unnecessary storage of sensitive data).
2. Keine unsichere HTML-Ausgabe (no unsafe HTML output).
3. Security relevante Fehler werden nicht unnötig offengelegt (security-relevant errors are not disclosed unnecessarily).
4. Dependency Audit durchgeführt (dependency audit performed).

**Responsible for:** reviewing and hardening the frontend attack surface, adding regression tests, and filling backend gaps if any existed.

**Not responsible for:**

- New features ("Keine zukünftigen Features implementieren").
- Packaging and serving the UI in production, including server-sent headers, reverse proxy compatibility and caching. That is Phase 27 (roadmap §33).
- Performance work (Phase 24).

## 3. Scope Implemented

### API layer

- `ApiClient.requestWithBody` rejects any path with a `.` or `..` segment before it builds the URL or calls `fetch`. The error is an `ApiError` of kind `not_found` with the standard fallback message. Every service module routes through this method, so this single guard covers every endpoint (`projects`, `executions`, `credentials`, `plugins`, reports, ...).

### Build / browser policy

- The Vite plugin `akasha-csp` (`apply: 'build'`, `transformIndexHtml`, `injectTo: 'head-prepend'`) emits:
  `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'`.
- `index.html` gets `<meta name="referrer" content="same-origin">`. This applies in dev and in production.

### Static analysis

- A new ESLint config block for `src/**/*.{ts,tsx}`, excluding `src/**/*.test.{ts,tsx}` and `src/test/**`:
  - `no-eval`, `no-new-func`;
  - `no-restricted-syntax` for the `dangerouslySetInnerHTML` JSX attribute and the `localStorage`/`sessionStorage` identifiers;
  - `no-restricted-properties` for `innerHTML`, `outerHTML`, `insertAdjacentHTML` and `document.write`.
- An override for `src/features/settings/theme.ts` keeps only the HTML-sink selector. The theme preference is the one documented use of `localStorage`.

### Testing

- `src/api/client.node.test.ts`: new `describe('path segments from untrusted input')` with three cases (§12).
- `tests/e2e/fixtures.ts` (new): a `test` extended with the auto fixture `cspViolations`, re-exporting `expect`.
- `tests/e2e/security.spec.ts` (new): proves CSP enforcement in the production bundle.
- `tests/e2e/{dashboard,editor,projects,smoke,ui}.spec.ts` and `tests/fullstack/projects.spec.ts`: import `test`/`expect` from `fixtures.ts` instead of `@playwright/test`. Type imports are unchanged.

### Documentation

- `docs/deployment.md` and `frontend/README.md` (§17).

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/src/api/client.ts` | Modified (+9) | Dot-segment guard in `requestWithBody` (§10). |
| `frontend/src/api/client.node.test.ts` | Modified (+29) | Regression tests for the guard; imports `getProject`, `publishProject`. |
| `frontend/vite.config.ts` | Modified (+35/−2) | `CONTENT_SECURITY_POLICY` constant and the build-only `akasha-csp` plugin. |
| `frontend/index.html` | Modified (+1) | Referrer policy meta. |
| `frontend/eslint.config.js` | Modified (+39) | XSS-sink, dynamic-code and browser-storage lint guards. |
| `frontend/tests/e2e/fixtures.ts` | Created | Auto fixture that fails any test on a CSP violation. |
| `frontend/tests/e2e/security.spec.ts` | Created | Proves the CSP blocks injected inline scripts and handlers. |
| `frontend/tests/e2e/dashboard.spec.ts`, `editor.spec.ts`, `projects.spec.ts`, `smoke.spec.ts`, `ui.spec.ts` | Modified (import lines only) | Run under the CSP fixture. |
| `frontend/tests/fullstack/projects.spec.ts` | Modified (import lines only) | Runs under the CSP fixture against the real backend, including the live WebSocket. |
| `frontend/README.md` | Modified (+31) | New "Security" section. |
| `docs/deployment.md` | Modified (+16) | Proxy headers for the UI. |

- No directories were deleted, and the only new files are the two above.
- No generated files changed, and `src/api/openapi.json` is untouched.
- No migrations, no `package.json` or `package-lock.json` changes, no backend files.

## 5. Architecture Changes

### Decision 1: CSP delivered as a build-time `<meta>`, framing protection by proxy header

- **Context:** ADR 0002 left a known consequence open: "XSS can still act as the user while the page is open … Content Security Policy hardening is frontend Phase 23." The UI is static files that an operator's reverse proxy serves (`docs/deployment.md`; `frontend/README.md` "Configuration"). The repository has no UI server or container yet (Phase 27).
- **Chosen approach:** a Vite plugin adds the CSP `<meta>` to the built `index.html`. Any server serving `dist/` therefore enforces the policy with no configuration. `frame-ancestors` is ignored in `<meta>` per the CSP spec, so `docs/deployment.md` documents it as a proxy header together with `nosniff`, `Referrer-Policy` and `Cache-Control: no-cache`.
- **Alternatives considered:**
  - **CSP only as a proxy header.** Rejected as the sole mechanism: it protects nothing until every operator configures it.
  - **CSP also in dev.** Rejected: `@vitejs/plugin-react` injects an inline refresh preamble into the dev server's HTML, which `script-src 'self'` blocks.
  - **Nonces or hashes.** Unnecessary: the bundle has no inline code (verified, §9 Finding 3).
- **Reason:** strongest policy at zero runtime cost, effective by default.
- **Consequences:**
  - Future code must not add inline scripts, inline `<style>` elements, `style=""` attributes from HTML strings, `data:`/`blob:` images, cross-origin fetches or third-party assets without changing the policy. The E2E fixture fails on any such violation.
  - Report downloads use a `blob:` URL on an `<a download>`, which is navigation and not governed by `img-src`/`connect-src`. They are unaffected.
  - Phase 27 may move the full policy into a server header. The meta and the header can coexist, and browsers enforce both.

### Decision 2: The dot-segment guard lives in `ApiClient`, not in route handling or per service

- **Context:** route params are attacker-influenced (crafted links), and `encodeURIComponent` does not encode `.`.
- **Chosen approach:** one check in `requestWithBody`, the method every HTTP call goes through.
- **Alternatives:** validating ids per route, or per service `path()` helper (`projects.ts`, `executions.ts`, `credentials.ts`, `plugins.ts`). Rejected: four places plus every future one, and a sibling would be missed.
- **Consequences:** no request path may legitimately contain a `.` or `..` segment. No current endpoint does.

### Decision 3: Security regression guards as lint rules

- **Chosen approach:** ESLint core rules (`no-restricted-syntax`, `no-restricted-properties`, `no-eval`, `no-new-func`) instead of a new plugin such as `eslint-plugin-react` `no-danger` or `eslint-plugin-security`.
- **Reason:** no new dependency (AGENTS §53), and the rules run in `npm run lint`/CI already.
- **Consequences:** an exception now needs a config change, which reviewers will see, rather than an inline disable comment (AGENTS §55, §64). Tests are exempt because they read storage and markup to prove nothing leaks there.

## 6. Domain / Data Model Changes

Not applicable. No domain types, Zod schemas, DTOs or persisted data changed.

## 7. API / Contract Changes

- **No backend API, WebSocket or event contract changes.**
- **Client-side behavior change:** `ApiClient.request`/`requestWithBody` now throw `ApiError{kind: 'not_found'}` without a network request when the path has a `.` or `..` segment.
  - Callers already handle `not_found`; for example, the editor shows "Project not found".
  - This only affects malformed or crafted ids, which no real backend id matches. Backend ids are prefixed tokens such as `p_…` and `exec_…`, and credential names match `[a-z0-9][a-z0-9._-]{0,63}` (a name can contain dots but cannot be exactly `.` or `..`).

## 8. Implementation Details

- **Guard placement:** the check runs after `requestId` is generated, so the thrown error carries a request id like every other `ApiError`. It runs before the timeout signal and `fetch`, so nothing is sent. It splits the *unencoded-dot* path, which is `baseUrl`-relative and built by the service `path()` helpers. Encoded slashes (`%2F`) stay inside one segment, so `../a.b` becomes `/projects/..%2Fa.b` and is allowed. The test verifies this.
- **Live WebSocket** (`src/api/live.ts`): it builds its URL itself and does not use the guard. Its only caller, `useLiveExecution(execution.id, …)` in `ExecutionDetailPage.tsx`, passes the id the backend returned for an execution that loaded successfully, not a route param. A crafted id therefore fails at `GET /executions/{id}` (now guarded) before any WebSocket opens.
- **CSP meta placement:** `head-prepend` puts the CSP before `<meta charset>`. The charset declaration is still within the first 1024 bytes, which is what the HTML spec requires. The CSP precedes every `<script>` and `<link rel="stylesheet">` it governs.
- **Why `style-src 'self'` works with React and React Flow:** React applies `style` props through the CSSOM (`element.style.x = …`), which CSP does not restrict. React Flow 12 ships its CSS as a file (`GraphCanvas-*.css`) and injects no `<style>` elements. The E2E editor specs (drag, zoom, connect, minimap) run under the fixture with zero violations.
- **CSP violation detection:** Chromium reports violations as console messages containing "Content Security Policy". The fixture collects every console message with that text and asserts the list is empty after the test. `security.spec.ts` asserts that the same text *does* appear after an injection, so the matcher itself is tested (AGENTS §110).
- **`security.spec.ts` uses plain `@playwright/test`**, not the fixture, because it triggers violations on purpose. It passes the injection as a string expression to `page.evaluate`, because the E2E tsconfig has no DOM lib.

## 9. Findings

### Finding 1: Dot segments in route ids redirect API requests
- **Impact:** MEDIUM. A crafted link could make the UI send a same-origin request carrying the session cookie and CSRF header to a different endpoint. All mutations need explicit user action (buttons, confirm dialogs), and the user would also need the matching role. For example, the publish button on a `..` project id would `POST /api/v1/publish`, which does not exist today. No concrete exploitable chain was found, but the primitive is real.
- **Resolution:** FIXED (§10).
- **Notes:** pre-existing since the API layer (Phase 01/02).

### Finding 2: No Content Security Policy
- **Impact:** MEDIUM as defence-in-depth. No XSS sink exists today (Finding 4).
- **Resolution:** FIXED (build-time meta, §5 Decision 1).
- **Notes:** explicitly deferred to this phase by ADR 0002.

### Finding 3: The production bundle needs no inline code
- **Impact:** informational.
- **Resolution:** n/a.
- **Notes:** checked in `dist/`: no inline `<script>`; no `data:` URIs in CSS or JS; no `createElement('style')`, `insertRule` or `adoptedStyleSheets` in JS. This made a nonce-free strict policy possible.

### Finding 4: Most surfaces were already hardened by earlier phases
- **Impact:** informational. Verified by reading the code; nothing changed here.
- **Resolution:** n/a.
- **Notes:**
  - **Rendering:** no `dangerouslySetInnerHTML`, `innerHTML`, `eval` or Markdown/HTML renderer anywhere in `src/`. LLM output, node outputs, logs, reports, plugin metadata and errors are rendered as React text (`JsonBlock`, `LogList`, `PluginError`, `FailureDetails`).
  - **Session:** HttpOnly, `SameSite=Strict`, `Path=/api/v1` cookie; no token in JS, state, storage or URL (ADR 0002, `features/auth/session.ts`).
  - **CSRF:** every request sends `X-Akasha-CSRF` (`client.ts`), and the backend has no CORS.
  - **Open redirect:** login return target comes from validated history state and must be an in-app path (`features/auth/loginState.ts`); no `?next=`.
  - **External links:** plugin `documentation_url` must be `http(s)` (`NodeLibrary.tsx` `docsUrl`) and opens with `rel="noreferrer noopener"`.
  - **Credentials UI:** the secret is held only in an uncontrolled `type=password` input (`autoComplete="new-password"`, spellcheck off). Credential mutations use `gcTime: 0` (`api/mutations.ts` `secretOptions`). `CredentialInput` hides non-reference values.
  - **Logout:** `forgetServerState` drops every cached query except the session after a session ends.
  - **Error disclosure:** `errorMessage()` shows only `ApiError` messages, which are backend-redacted envelope messages or fixed fallbacks. `RouteErrorBoundary` never shows stack traces. `malformed()` keeps the cause off the UI.
  - **Downloads:** `ReportDetailPage` `saveFile` writes an `application/json` blob, sanitizes the file name, and revokes the URL after 40 s.
  - **Uploads:** the plugin `.zip` is size-checked client-side (UX only) and needs an explicit trust checkbox; the backend validates it.
  - **Storage:** only `theme.ts` uses `localStorage` (a UI preference). `sessionStorage` is unused.
  - **Logging:** no `console.*` calls in production code.
  - **Config:** only `VITE_*` variables reach the bundle; `AKASHA_*` is read only by `vite.config.ts`.

### Finding 5: Static plugin content is text only
- **Impact:** LOW.
- **Resolution:** n/a.
- **Notes:** plugin-provided config schemas drive form fields. No plugin-supplied regex is compiled with `new RegExp` in `ConfigForm.tsx`/`configSchema.ts`; a grep for `RegExp`/`pattern` found none, so there is no ReDoS from plugin metadata in the UI.

### Finding 6: Flaky smoke E2E test caused by a service on port 8000
- **Impact:** LOW. Test reliability, not security.
- **Resolution:** OPEN (§21).
- **Notes:**
  - `tests/e2e/smoke.spec.ts` "deep links to nested and unknown routes resolve client-side" failed in 2 of about 11 local runs. It was never failing during implementation runs.
  - **Root cause:** the test stubs only `/api/v1/auth/session`. The editor's `GET /api/v1/projects/p_1` passes through the Vite preview proxy to `AKASHA_API_PROXY_TARGET` (default `127.0.0.1:8000`).
  - On this machine a Portainer container (`0.0.0.0:8000`) is listening there and answers `404`. The assertion `getByRole('heading', { level: 1, name: 'Editor' })` matches the transient loading header "Draft editor" (substring match). When the 404 arrives first, the page shows "Project not found" instead and the assertion times out.
  - The test is pre-existing, and its only change in this phase is the import line. It depends on developer-machine state (AGENTS §16).

### Finding 7: No backend gap
- **Impact:** informational.
- **Resolution:** n/a.
- **Notes:** the backend already provides:
  - the cookie session plus CSRF header check (`api/auth.py`);
  - the WebSocket Origin check against `Host`/`X-Forwarded-Host` (`api/live.py` `_cookie_token`);
  - login throttling (`application/auth.py` `LoginThrottle`) and a non-enumerating `INVALID_CREDENTIALS`;
  - error redaction (`api/app.py` `_error`);
  - `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on API responses.

  The session prompt's "implement missing backend APIs" clause therefore produced no backend work.

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Affected area | Origin |
|---|---|---|---|---|---|
| A route id `.`/`..` sends the API request to another endpoint | `encodeURIComponent('..') === '..'`; WHATWG URL parsing collapses dot segments in `fetch` URLs | Guard in `ApiClient.requestWithBody` | `client.node.test.ts` › "path segments from untrusted input" (3 cases); failed before the fix (`malformed_response`, request reached the server), passes after | `src/api` (all services) | Pre-existing |
| Smoke E2E flaky on this machine | Unstubbed API call reaches a foreign service on port 8000; transient-heading assertion | Not fixed (report phase, pre-existing) | — | `tests/e2e/smoke.spec.ts` | Pre-existing / environmental |

Problems introduced *during* this phase and fixed before completion:

- **Type imports:** the first rewrite of E2E imports produced `import { type X }` forms that failed typecheck in the Playwright `webServer` build step (exit code 2). Corrected to `import type { … }`.
- **Missing DOM types:** `security.spec.ts` first used DOM globals inside a function passed to `page.evaluate`. The E2E tsconfig has no DOM lib (TS2584). Replaced with a string expression.
- **Lint scope:** the first version of the storage/`innerHTML` lint rules flagged 14 usages, all in test files that read or clear storage or markup to check what the UI stored or rendered. Scoped the rules to production code.

## 11. Fixes Applied

1. **Dot-segment request redirection.**
   - **Symptom:** `publishProject(client, '..')` hit the server at another path.
   - **Root cause:** see §10.
   - **Implementation:** a 9-line guard in `client.ts`.
   - **Verification:** new tests, full unit suite (467/467), E2E and full-stack suites.
2. **No CSP.**
   - **Symptom:** an XSS bug would have no second line of defense.
   - **Implementation:** the `akasha-csp` Vite plugin.
   - **Verification:** `dist/index.html` contains the meta. `security.spec.ts` shows that injected inline script and `onerror` handlers do not run. All 24 other E2E tests and 17 full-stack tests (including the live WebSocket under `connect-src 'self'`) pass with zero violations.
3. **No guard against future unsafe sinks or storage.**
   - **Implementation:** the ESLint block.
   - **Verification:** a temporary probe file using `localStorage.setItem`, `document.body.innerHTML` and `dangerouslySetInnerHTML` produced exactly those three lint errors (plus the React Compiler's own immutability error), and was deleted afterwards. `npm run lint` is clean on the real code.

## 12. Testing & Verification

### Unit / component tests (Vitest)

- **New tests** (`src/api/client.node.test.ts`, against a real `node:http` server on 127.0.0.1):
  - "never sends a request whose id is \".\"" — `publishProject(client(), '.')` → `ApiError.kind === 'not_found'`, server hit count 0.
  - The same for `".."`.
  - "still encodes ids that merely contain dots or slashes" — `getProject(client(), '../a.b')` reaches `/api/v1/projects/..%2Fa.b`.
- **Result:** 37 files, 467 tests passed (`npm run check`, final run).

### E2E tests (Playwright, production bundle via `vite preview`, API stubbed per spec)

- All specs run under the CSP-violation fixture.
- `security.spec.ts`: the dashboard renders under CSP. Injecting `<script>window.__xss = true</script>` and `<img src=/missing.png onerror="window.__xss = true">` leaves `window.__xss` unset, and CSP violations are reported.
- **Result:** 25 of 25 passed on 5 of 7 final runs. On 2 runs, 1 failed: the pre-existing smoke test (Finding 6). `npx playwright test --list` reports 25 tests in 6 files.

### Integration / full-stack tests

- `npm run test:fullstack`: the real backend (PostgreSQL and Redis via Docker, worker, migrations) behind the production bundle, under the CSP fixture. This includes "a run is followed live, across a dropped connection, to its end", which exercises the cookie-authenticated WebSocket under `connect-src 'self'`.
- **Result:** 17 passed (twice: during implementation and in the final verification).
- Backend logs printed during the run include `ConnectionError: unexpected connection_lost()` from asyncpg at teardown and expected `node.failed`/`execution.failed`/`worker_dequeue_failed` lines from failure-path tests. No test failed.

### Security tests

- The path-segment unit tests, `security.spec.ts`, and the CSP fixture across every E2E and full-stack test.
- The lint guards were verified with a probe file (§11).
- `npm audit` and `npm audit --omit=dev`: 0 vulnerabilities.

### Backend tests

- Not run. No backend file changed.

### Accessibility and performance tests

- Not performed in this phase. The existing keyboard and mobile E2E tests ran as part of the suite and passed.

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `npm run format:check`: "All matched files use Prettier code style!" One intermediate failure on `tests/fullstack/projects.spec.ts` after the import edit; fixed with `prettier --write`. |
| Linter | PASS | `npm run lint` (ESLint, type-checked strict set plus new security rules). |
| Type Checker | PASS | `npm run typecheck` (`tsc -b`). Intermediate failures in E2E files fixed (§10). |
| Unit Tests | PASS | 467/467. |
| Integration Tests | PASS | Full-stack Playwright, 17/17, real backend, PostgreSQL and Redis in Docker. |
| API Tests | N/A | No backend change; client HTTP behavior is covered by `client.node.test.ts`. |
| E2E Tests | PASS (flaky pre-existing test) | 25/25 on 5 of 7 final runs; `smoke.spec.ts` deep-link test failed on 2 runs for environmental reasons (§9 Finding 6). |
| Build | PASS | `npm run build`; `dist/index.html` contains the CSP meta. |
| Security Checks | PASS | `npm audit`: 0 vulnerabilities; security E2E and unit tests pass. |
| Accessibility | N/A | Not in scope; existing a11y E2E checks pass. |

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Keine unnötige Speicherung sensibler Daten | PASS | Session token is an HttpOnly cookie and never in JS (ADR 0002). The only `localStorage` key is the theme preference (`theme.ts`), and `sessionStorage` is unused. ESLint now rejects storage elsewhere in production code. Secrets stay in uncontrolled inputs and `gcTime: 0` mutations. `ExecutionInspector.test.tsx` ("renders redacted values as the backend sent them and stores nothing") asserts that nothing is stored. `SettingsPage.test.tsx` covers the theme key (`akasha.theme`). |
| Keine unsichere HTML-Ausgabe | PASS | No HTML sink in `src/` (grep). Lint rules now forbid `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` and `new Function`. External data URLs are restricted to `http(s)`. A strict CSP blocks inline script, proven by `security.spec.ts`. |
| Security relevante Fehler werden nicht unnötig offengelegt | PASS | `errorMessage()` and `RouteErrorBoundary` show only redacted backend messages or fixed fallbacks, never stack traces or raw bodies. Login errors do not distinguish unknown users. The guarded path returns the generic `not_found` message. |
| Dependency Audit durchgeführt | PASS | `npm audit` and `npm audit --omit=dev` on 2026-10-05: 0 vulnerabilities. |

All four criteria pass. The status is COMPLETE WITH LIMITATIONS because of the deployment-dependent framing protection and the open flaky test (§20, §21), neither of which is an acceptance criterion.

## 15. Security Considerations

- **XSS / output encoding:** React text rendering throughout, plus a strict CSP (scripts and styles from self only, `object-src 'none'`, `base-uri 'none'`, which blocks `<base>` injection) and lint guards.
- **Clickjacking:** not enforceable from `<meta>`. Operators must send `frame-ancestors 'none'` (documented). Until Phase 27 ships a UI server, this depends on the deployment.
- **CSRF:** unchanged and sound (`SameSite=Strict` plus the required custom header; no CORS). `form-action 'self'` additionally limits form targets.
- **WebSocket:** cookie authentication only when Origin matches the host (backend). `connect-src 'self'` restricts the page to its own origin, including `ws(s):` (CSP Level 3 `'self'` semantics).
- **Path traversal (client-side request routing):** fixed (§10).
- **Unsafe URLs:** plugin documentation links are limited to `http(s)`. Login redirect is in-app only. No `window.open` or `location` assignment from data.
- **Referrer leakage:** `same-origin` policy; external doc links also use `noreferrer`.
- **Secrets / credentials:** unchanged and sound (Finding 4).
- **Plugin trust:** the upload needs an explicit trust checkbox; backend validation remains authoritative.
- **Error disclosure:** unchanged and sound.
- **Remaining residual risk:** an XSS that bypasses CSP could still act as the user while the page is open (ADR 0002), but cannot read the session token.

## 16. Performance Considerations

- The CSP meta and the referrer meta add about 230 bytes to `index.html`. There is no runtime cost.
- The dot-segment guard is an O(segments) split per request, which is negligible.
- No measurements were performed; none were needed.

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `frontend/README.md` | Make the security model discoverable | New "Security" section: threat model, rendering rule and lint guards, CSP and its tests, URL handling including the dot-segment guard, storage, downloads and uploads, errors, `npm audit`. |
| `docs/deployment.md` | `frame-ancestors` cannot be set in `<meta>` | nginx `location /` example for the UI with `Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options`, `Referrer-Policy`, `Cache-Control: no-cache`. |

**Should be updated later:**

- `docs/adr/0002-browser-session-cookie.md` still says "Content Security Policy hardening is frontend Phase 23". A one-line pointer to this report would close that loop. It was not edited, because ADRs record the decision at the time.
- Phase 27 should fold the UI header guidance into the production packaging.

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded. `package.json` and `package-lock.json` are unchanged.
- **Configuration:** `vite.config.ts` (new build plugin) and `eslint.config.js` (new rules).
- **Environment variables:** none.
- **Docker / DB / Redis:** none.
- **Test infrastructure:** new Playwright fixture `tests/e2e/fixtures.ts`, used by both Playwright configs' specs.

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion? |
|---|---|---|---|
| Server-sent security headers for the UI (full CSP header, `frame-ancestors`) | The repository has no UI server or container; serving is Phase 27 scope ("Reverse Proxy Compatibility", "Deployment Documentation") | 27 | No |
| Fixing the flaky smoke test | Pre-existing, not security-related; the report phase must not change code | 24 or 26 (E2E Testing) | No |
| CSP in development | Blocked by Vite's inline React refresh preamble; E2E always tests the production bundle | — (not planned) | No |
| Subresource Integrity | Same-origin assets only; SRI protects against compromised third-party hosts, which this app does not use | — (not needed) | No |
| CSP violation reporting (`report-to`) | Needs a collection endpoint; low value for self-hosted | — | No |

## 20. Known Limitations

- **Framing:** clickjacking protection exists only when the operator's proxy sends `frame-ancestors 'none'` (or `X-Frame-Options`). The bundle alone cannot enforce it.
- **No CSP under `npm run dev`:** only production builds carry the policy.
- **Chromium-only check:** the CSP fixture detects violations by Chromium's console wording ("Content Security Policy"). The E2E project is Chromium only. Other browsers would need a different detection method, such as listening to `securitypolicyviolation` events.
- **Narrow `img-src 'self'`:** future features showing `data:`/`blob:` images or remote images, such as plugin icons, need a deliberate policy change.
- **Unguarded WebSocket URL:** the live WebSocket URL (`live.ts`) does not pass through the dot-segment guard. It only ever receives a backend-confirmed execution id (§8).
- **Residual XSS impact:** described in §15 and ADR 0002.

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended action | Target |
|---|---|---|---|---|---|
| `smoke.spec.ts` "deep links…" flaky when something listens on `127.0.0.1:8000` | False E2E failures on developer machines with a service on port 8000 (here: Portainer) | LOW | `frontend/tests/e2e/smoke.spec.ts` | Stub `/api/v1/**` in the smoke spec (e.g. a catch-all `route.abort()` or 404 before `stubSession`), and assert a stable element instead of the transient "Draft editor" header. Optionally point the preview proxy at an unused port in `playwright.config.ts`. | Phase 26 (E2E Testing) or earlier |

## 22. Architectural / Product Decisions

- **CSP lives in the build, not the server:** the built `index.html` carries its own CSP so any static server enforces it by default. Framing protection must come from a header (§5 D1).
- **One guard for untrusted ids:** request-path safety is enforced once in `ApiClient`. Service `path()` helpers must keep using `encodeURIComponent`; they do not need their own dot checks (§5 D2).
- **Lint config, not comments:** security sink bans are lint rules. An exception is a reviewed config change, never an inline disable (§5 D3). `theme.ts` is the single sanctioned `localStorage` user.
- **E2E fails on CSP violations:** every E2E and full-stack test fails on a violation, so a feature that needs a looser policy surfaces immediately, and the policy change must be deliberate.
- **No backend changes:** the backend's existing controls were judged sufficient for the self-hosted threat model the session prompt set ("no need to be too paranoid").

## 23. Repository / Git State

- Branch `main`, HEAD `64c11d3` (frontend phase 22 — developer experience).
- Working tree before this report:
  - **Modified (13):** `docs/deployment.md`, `frontend/README.md`, `frontend/eslint.config.js`, `frontend/index.html`, `frontend/src/api/client.node.test.ts`, `frontend/src/api/client.ts`, `frontend/tests/e2e/{dashboard,editor,projects,smoke,ui}.spec.ts`, `frontend/tests/fullstack/projects.spec.ts`, `frontend/vite.config.ts`.
  - **Untracked:** `frontend/tests/e2e/fixtures.ts`, `frontend/tests/e2e/security.spec.ts`.
- Plus this report: `docs/reports/frontend/phase-23-frontend-security-hardening.md` (untracked).
- `git diff --stat`: 13 files, +169/−9.
- No unrelated changes detected. Nothing committed, stashed or reverted.

## 24. Phase Completion Assessment

### Implemented
- Dot-segment guard in `ApiClient`.
- Build-time strict CSP.
- Referrer policy.
- ESLint security guards.
- E2E CSP fixture applied to all E2E and full-stack specs.
- CSP enforcement E2E test.
- Deployment header documentation.
- README "Security" section.

### Verified
- Unit 467/467.
- E2E 25/25 (with the pre-existing environmental flake noted).
- Full-stack 17/17 including the live WebSocket under CSP.
- Lint, typecheck, format and build pass.
- `npm audit`: 0 vulnerabilities.
- Lint guards fire on a probe file.
- The attack reproduced before the fix and is blocked after it.

### Not Implemented
- Server-sent UI headers (no UI server exists).

### Deferred
- See §19.

### Open
- Flaky smoke test (§21).

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

**The next phase can assume:**

- `ApiClient` refuses dot-segment paths. Keep all HTTP inside `src/api`, which the existing ESLint `fetch` ban enforces.
- Production builds enforce `script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'`.
  - **Phase 24 (performance):** code splitting and lazy loading are fine (same-origin chunks). Inlining assets as `data:` URIs (Vite `assetsInlineLimit` for images and fonts), injecting runtime `<style>` tags, or adding web workers from `blob:` URLs will break under CSP. The E2E fixture will fail such changes. Adjust the policy in `vite.config.ts` deliberately and document why.
  - **Phase 27 (production readiness):** serve `dist/` with the headers from `docs/deployment.md`. A CSP response header may duplicate the meta policy and must add `frame-ancestors 'none'`. Never cache `index.html` (`no-cache`); hashed assets can be cached long-term.
- New E2E specs should import `test`/`expect` from `tests/e2e/fixtures.ts`, unless they provoke violations on purpose.

**Must not be changed casually:**

- the CSP directives;
- the `ApiClient` dot-segment guard;
- the ESLint sink and storage bans;
- the HttpOnly-cookie and CSRF-header session model (ADR 0002).

**Known constraint:** E2E specs that leave API calls unstubbed reach whatever listens on `127.0.0.1:8000` (§21).
