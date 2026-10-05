# Phase 21 — Settings + Authentication

Report date: 2026-10-05. Branch `main`. Base commit `9fdbae7` (frontend phase 20 — plugin management ui). All work described here is **uncommitted** (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap `docs/roadmap-frontend.md` §27, "Phase 21 — Settings + Authentication": global settings and user access. Scope: login, logout, session state, current user, settings, theme, API/backend status, account information and security-relevant UI. No enterprise SSO. The session prompt added:
- protected routes, redirect behaviour and authentication errors;
- not exposing tokens to application code;
- a security review of token storage, XSS, CSRF, logout, 401 handling, expiry and protected routes;
- implementing and testing any missing backend function.

**Discovered.** The backend (Phase 20) issued bearer tokens only (architecture §59: "No cookies, so there is no CSRF surface"). It had no endpoint that reports a session's expiry. With bearer-only sessions, a session that survives a reload requires the token in web storage. AGENTS §35/§62 and frontend AGENTS §21/§62 forbid that.

**Actually implemented:**
- **Backend.** An opt-in HttpOnly cookie session, CSRF-protected by a required custom header (ADR 0002). Also `GET /api/v1/auth/session`, cookie authentication on the live WebSocket with an Origin check, and the setting `AKASHA_SESSION_COOKIE_SECURE`.
- **Frontend.** A central session query, a route guard over the whole shell, `/login`, sign-out, global 401 handling and `/settings` (account, password change, theme, engine status).
- **Theme.** Tokens converted to CSS `light-dark()`.
- **Follow-up fixes.** Three failures that also occurred on the base commit: locale-dependent frontend tests, a Windows-only ZIP path discrepancy, and a full-stack harness that could not start on Windows.

**Resulting state.** Every application area requires a session. The browser never holds the session token. Sessions survive a reload and end cleanly on logout, expiry or revocation. All quality gates pass on Windows/de-DE:
- backend: 2048 passed, 10 skipped;
- Vitest: 464/464;
- Playwright: 24/24 stubbed and 17/17 full-stack.

**Phase Status:** COMPLETE

## 2. Phase Objective

Roadmap §27: "Globale Einstellungen und Benutzerzugang" (global settings and user access).

Acceptance criteria:
- unauthenticated users cannot open protected areas;
- session loss is handled cleanly;
- logout works;
- auth state is central.

**Responsible for:** the login/logout UX, session state and restoration, protected routes, authentication errors, current user and account information, settings (theme, backend status, password) and the backend functions these need.

**Not responsible for:**
- enterprise SSO (explicitly excluded);
- the developer experience and diagnostics features of Phase 22;
- CSP and security headers (Phase 23);
- responsive and accessibility hardening (Phase 25);
- user administration (not in the Phase 21 scope list).

## 3. Scope Implemented

### Architecture
- ADR 0002 ([docs/adr/0002-browser-session-cookie.md](../../adr/0002-browser-session-cookie.md)): browser sessions use an HttpOnly cookie; CSRF is closed by a required header; the WebSocket uses an Origin check.

### Backend/API
- `POST /api/v1/auth/login` accepts `cookie: bool = false`. With `true`, the token goes into the `akasha_session` cookie and the response has `token: null`.
- A cookie login without `X-Akasha-CSRF` answers 403 `CSRF_HEADER_REQUIRED`.
- `POST /api/v1/auth/logout` also expires the cookie.
- New: `GET /api/v1/auth/session` returns `SessionDetail {expires_at, user}`.
- `current_identity` resolves the bearer token first, else the cookie, but the cookie only counts when `X-Akasha-CSRF` is present.
- Live WebSocket: if the subscribe frame has no token, the handshake cookie authenticates, but only when `Origin` matches `Host` or `X-Forwarded-Host`.
- Setting `session_cookie_secure` (`AKASHA_SESSION_COOKIE_SECURE`, default `true`).

### Frontend/UI
- `features/auth`:
  - session query and hooks (`session.ts`);
  - route guard (`RequireSession.tsx`);
  - `/login` (`LoginPage.tsx`);
  - validation of the history state handed to the login page (`loginState.ts`);
  - sidebar user and sign-out (`SessionMenu.tsx`).
- `features/settings`: `/settings` (`SettingsPage.tsx`) and theme persistence (`theme.ts`).
- `ApiClient`:
  - `getToken` removed;
  - every request sends `X-Akasha-CSRF: 1`;
  - new `onSessionEnded` hook.
- Live client: the subscribe frame is `{type, after}`, with no token.
- Routing:
  - `/login` added outside the shell;
  - the shell wrapped in `RequireSession`;
  - `/settings` is a real page;
  - the placeholder-route machinery is removed because no nav area uses it any more.
- Theme: tokens rewritten as `light-dark(light, dark)`. `data-theme` pins `color-scheme`, and `main.tsx` applies the stored theme before the first render.

### Testing / tooling
- New unit, component, integration and full-stack tests (§12).
- Stubbed E2E specs get a session stub (`tests/e2e/session.ts`).
- The full-stack harness is ported from bash to Node (`tests/fullstack/backend.ts`).
- The test locale is pinned to en-US (`src/test/setup.ts`).

### Fixes to code from earlier phases
- `akasha/plugins/archive.py` now validates the raw ZIP entry name (§11).

## 4. Files Changed

Working tree: 48 entries changed against `9fdbae7` (41 tracked files with +1018/−279 lines, plus untracked new files).

**Backend**

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/api/auth.py` | Modified | `SESSION_COOKIE`, `CSRF_HEADER`, `CsrfHeaderRequiredError`, `session_token()`, `SessionLoginRequest`, `SessionDetail`, cookie handling in login/logout, `GET /auth/session` |
| `backend/src/akasha/api/live.py` | Modified | `_cookie_token()`: cookie authentication with the Origin check; the docstring is updated |
| `backend/src/akasha/api/routes.py` | Modified | Access-policy docstring |
| `backend/src/akasha/config.py` | Modified | `session_cookie_secure` |
| `backend/src/akasha/plugins/archive.py` | Modified | Validate `orig_filename` and reject NUL (fix, §11) |
| `backend/tests/integration/test_auth_api.py` | Modified | 10 new test functions (11 cases) for cookie sessions |
| `backend/tests/integration/test_executions_api.py` | Modified | 3 new test functions (4 cases) for the WebSocket cookie |
| `backend/tests/test_plugin_archive.py` | Modified | `make_zip` stores hostile names verbatim; new NUL case |
| `backend/README.md` | Modified | `AKASHA_SESSION_COOKIE_SECURE` |

**Frontend: API layer and app shell**

| File | Change | Reason |
|---|---|---|
| `frontend/src/api/auth.ts` | Modified | `sessionSchema`, nullable `token`; `login` sends `cookie: true` and strips the token; `fetchSession` and `changePassword` added; `fetchCurrentUser` removed (no longer used) |
| `frontend/src/api/client.ts` | Modified | `getToken` removed; `CSRF_HEADER`; `onSessionEnded` |
| `frontend/src/api/live.ts` | Modified | No token in the subscribe frame |
| `frontend/src/api/queries.ts` | Modified | `queryKeys.session()` |
| `frontend/src/api/openapi.json` | Modified (generated) | Backend contract snapshot regenerated |
| `frontend/src/app/App.tsx` | Modified | Wires `onSessionEnded` to `expireSession` |
| `frontend/src/app/routes.tsx` | Modified | `/login`, `RequireSession`, `/settings`; placeholder mapping removed |
| `frontend/src/app/AppLayout.tsx` | Modified | Renders `SessionMenu` |
| `frontend/src/app/PlaceholderPage.tsx` | Deleted | No route uses it any more |
| `frontend/src/main.tsx` | Modified | `applyTheme(readTheme())` before render |
| `frontend/src/styles/index.css` | Modified | `light-dark()` tokens; `color-scheme` per `data-theme` |

**Frontend: new features**

| File | Change | Reason |
|---|---|---|
| `frontend/src/features/auth/*` | Created (new directory) | Session state, guard, login, session menu, and the auth tests (`auth.test.tsx`) |
| `frontend/src/features/settings/*` | Created (new directory) | Settings page, theme, and the settings tests |

**Frontend: test infrastructure and updated tests**

| File | Change | Reason |
|---|---|---|
| `frontend/src/test/app.tsx` | Modified | Default session stub; `testSession`, `noSession`; `onSessionEnded` wiring |
| `frontend/src/test/setup.ts` | Modified | Pin the default locale to en-US (fix, §11) |
| `frontend/src/api/{client.node,live,contract.node}.test.ts` | Modified | CSRF header and no token; new service functions |
| `frontend/src/app/{AppLayout,RouteErrorBoundary}.test.tsx` | Modified | Providers for `SessionMenu`; mock the real pages instead of the deleted placeholder |
| `frontend/src/features/{credentials,dashboard,executions}/*.test.tsx` | Modified | A 401 now means session end (§9 F4); frames carry no token |
| `frontend/tests/e2e/session.ts` | Created | Session stub for the stubbed E2E suite |
| `frontend/tests/e2e/{dashboard,editor,projects,smoke,ui}.spec.ts` | Modified | Use the session stub; new theme-pin check |
| `frontend/tests/fullstack/projects.spec.ts` | Modified | Sign in by cookie instead of an injected bearer header; 3 new auth specs |
| `frontend/tests/fullstack/backend.ts` | Created | Node port of `backend.sh` |
| `frontend/tests/fullstack/backend.sh` | Deleted (staged) | Could not run on Windows |
| `frontend/tests/fullstack/teardown.ts` | Modified | Comment reference |
| `frontend/playwright.fullstack.config.ts` | Modified | `command: 'node tests/fullstack/backend.ts'` |

**Docs**

| File | Change | Reason |
|---|---|---|
| `docs/adr/0002-browser-session-cookie.md` | Created | Architecture decision |
| `docs/architecture.md` | Modified | §23 (live auth), §59 (cookie sessions, error code) |
| `docs/deployment.md` | Modified | The proxy must forward `Host`; HTTPS and cookie note |
| `frontend/README.md` | Modified | New "Authentication" section; API layer, live, tokens and full-stack text |

No migrations. No dependency changes.

## 5. Architecture Changes

### Decision 1: browser sessions in an HttpOnly cookie (ADR 0002)

**Decision.** Opt-in cookie login. The token is never returned to page scripts.

**Context.** The requirements need reload survival and session restoration, and the token must not be exposed to application code. Bearer-only would force the token into web storage, which is forbidden, or into memory, which loses the session on reload.

**Chosen approach.**
- The `akasha_session` cookie is `HttpOnly`, `SameSite=Strict`, `Secure` (configurable) and `Path=/api/v1`, and expires with the session.
- The response carries `token: null`.
- Bearer clients are unchanged.

**Alternatives considered.**
- Token in sessionStorage: rejected (XSS exfiltration, repository rules).
- Double-submit CSRF token: rejected; it adds a second secret the UI must read.
- Always setting the cookie: rejected; bearer clients would get an ambient credential.

**Reason.** This is the only design that meets both reload survival and no token in JavaScript. Frontend AGENTS §21 already anticipated HttpOnly cookies.

**Consequences.**
- Cookie requests have a CSRF surface; Decision 2 closes it.
- The UI must stay same-origin with `/api/v1`.
- XSS can act as the user while the page is open, but cannot steal the session for later use.

### Decision 2: CSRF protection by a required custom header

**Decision.** The cookie counts only on requests that carry `X-Akasha-CSRF`.

**Context.** Cookies are ambient credentials.

**Chosen approach.**
- `session_token()` ignores the cookie when the header is missing, so the request is unauthenticated (401).
- A cookie login without the header is 403 `CSRF_HEADER_REQUIRED` (login CSRF).

**Alternatives considered.** Origin/Referer checks on HTTP, which are less robust behind proxies.

**Reason.** Forms and `<img>` cannot set headers. Cross-origin `fetch` with the header needs a CORS preflight, and the API grants none.

**Consequences.** Every UI request must go through `ApiClient`, which ESLint already enforces.

### Decision 3: WebSocket cookie authentication with an Origin check

**Decision.** With an empty frame token, the cookie authenticates only if the `Origin` netloc is in {`Host`, `X-Forwarded-Host`}.

**Context.** A WebSocket can carry neither the CSRF header nor be protected by CORS, and the UI no longer has a token.

**Alternatives considered.** A one-time WebSocket ticket endpoint: rejected, because it needs shared state across processes.

**Consequences.** Reverse proxies must forward the browser's host. `docs/deployment.md` now sets `proxy_set_header Host $host`.

### Decision 4: theme tokens as `light-dark()`

**Decision.** Each semantic colour is defined once as `light-dark(light, dark)`. The theme is chosen by `color-scheme` (OS default, or pinned by `data-theme`).

**Context.** A manual dark theme on a light OS needs the dark tokens outside the media query.

**Alternatives considered.**
- Duplicating the dark block: rejected (token drift).
- JS-only theme resolution: rejected (first-paint flash for OS-dark users).

**Consequences.** Opacity modifiers on semantic tokens were not in use. If added later, they rely on `color-mix()` with `light-dark()`, which current browsers support.

### Decision 5: full-stack harness in Node instead of bash

**Decision.** `tests/fullstack/backend.ts` replaces `backend.sh`.

**Context.** Playwright starts web servers via `cmd.exe` on Windows.

**Alternatives considered.** Hard-coding a Git Bash path plus `MSYS_NO_PATHCONV`: rejected as fragile; it was used only once, as a temporary local config, and then deleted.

**Consequences.**
- Requires Node with type stripping (≥ 22.18; `engines` already requires ^22.22 or ≥ 24).
- It drops the inherited `AKASHA_*` variables instead of using `env -i`, so OS variables Python and uv need on Windows are kept.

## 6. Domain / Data Model Changes

No domain entities, tables, migrations or state transitions changed. The `sessions` table and `UserSession` are unchanged; cookie sessions are ordinary sessions.

New API models in `api/auth.py`:
- `SessionLoginRequest(LoginRequest)` with `cookie: bool = False`;
- `SessionDetail {expires_at, user}`;
- `LoginResponse(SessionDetail)` with `token: str | None`.

## 7. API / Contract Changes

| Contract | Change | Request/input | Response/output | Errors | Compatibility |
|---|---|---|---|---|---|
| `POST /api/v1/auth/login` | Changed | Optional `cookie: bool` (default `false`) | `{token, expires_at, user}`; `token` is now `string \| null` (null for cookie logins); `Set-Cookie: akasha_session=…` for cookie logins | New 403 `CSRF_HEADER_REQUIRED` | Bearer clients unaffected at runtime; typed clients see a nullable `token` |
| `POST /api/v1/auth/logout` | Changed | — | 204, now also `Set-Cookie: akasha_session=""; Max-Age=0` | unchanged (401) | Compatible |
| `GET /api/v1/auth/session` | New | Session (bearer, or cookie plus header) | `{expires_at, user}` | 401 | Additive |
| All session-protected routes | Changed | Also accept the cookie plus `X-Akasha-CSRF` | — | 401 when neither credential is valid | Bearer takes precedence |
| WS `/api/v1/executions/{id}/live` | Changed | `token` in the subscribe frame is optional when a same-origin cookie is present | unchanged | 4401 for a foreign or missing Origin with a cookie only | Token clients unaffected |
| Frontend `api/auth.ts` | Changed | `login()` returns `Session` (no token); `fetchSession`, `changePassword` added | — | — | `fetchCurrentUser` removed |
| `ApiClient` options | Changed | `getToken` removed; `onSessionEnded` added | — | — | Internal |

`frontend/src/api/openapi.json` was regenerated, and both contract tests pass:
- backend `tests/test_openapi_contract.py`;
- frontend `contract.node.test.ts`.

## 8. Implementation Details

### Session state (`features/auth/session.ts`)
- One TanStack query `['auth','session']` of type `SessionState`:
  - `{status:'active', session}`;
  - `{status:'anonymous', expired}`.
- `staleTime` is 60 s.
- A 401 from `/auth/session` resolves to `anonymous`, with `expired` true if the previous state was active or expired. Other errors (5xx, network) stay query errors; the guard shows "Could not check your session" with Try again instead of redirecting.
- `expireSession(queryClient)` switches `active` to `anonymous{expired:true}`. It is a no-op otherwise.
- `useLogin` uses `gcTime: 0`, because the mutation variables hold the password.
- `useLogout`:
  - success: anonymous;
  - error of kind `authentication`: anonymous;
  - any other error: stays signed in, and the menu shows a toast. The cookie would still be valid.

### Global session end (`api/client.ts`)
- Any error response with kind `authentication` and code other than `INVALID_CREDENTIALS` calls `onSessionEnded`.
- It is wired in `App.tsx` and the test harness to `expireSession`.
- This catches expiry and revocation from whichever request notices first: queries, mutations and direct calls. It also avoids treating a wrong password (login, password change) as a session end.

### Guard (`RequireSession.tsx`)
- active: renders the shell;
- anonymous: `<Navigate to="/login" state={{from, expired}}/>`;
- error: error state;
- pending: `AppLoading`.
- An effect calls `forgetServerState` (removes every query except `auth`) once anonymous. Running it after the commit that unmounted the pages prevents mounted observers from refetching the removed queries (§9 F5).

### Login (`LoginPage.tsx`, `loginState.ts`)
- An uncontrolled form (`formText`).
- `readLoginState` validates history state with Zod. It returns only paths starting with `/`, not `//`, and not `/login`; otherwise `/`.
- When the session becomes active, the page renders `<Navigate to={returnTo}>`.

### Settings (`SettingsPage.tsx`)
- `AccountCard`: username, role, created, session expiry.
- `PasswordCard`:
  - mismatched confirmation is caught client-side as UX only;
  - `INVALID_CREDENTIALS` becomes a field error on the current password;
  - other errors appear as an alert;
  - `gcTime: 0`.
- `AppearanceCard` uses `theme.ts`: localStorage key `akasha.theme`, every storage access in try/catch.
- `SystemStatusCard` is reused from the dashboard.

### Backend details
- `session_token()`:
  - `bearer_token()` first (≤ 256 characters);
  - otherwise the cookie, only if `x-akasha-csrf` is present, also bounded to 256.
- `_cookie_secure(request)` reads `container.settings.session_cookie_secure`.
- `live._cookie_token()` compares `urlsplit(Origin).netloc` with `Host` and `X-Forwarded-Host`.
- The resolved token is re-authenticated on every poll, unchanged from before, so logout ends a cookie stream too.

## 9. Findings

### F1: the backend had no browser-safe session model
- **Impact:** HIGH. **Resolution:** FIXED (ADR 0002).
- Bearer-only sessions made reload survival incompatible with the rule against storing tokens in web storage.

### F2: test-only Playwright request contexts do not send `Secure` cookies over http
- **Impact:** LOW. **Resolution:** WORKAROUND in the test.
- `page.request.post('/api/v1/auth/logout')` in a full-stack spec silently did not authenticate, and its status was not asserted, so the session was never revoked.
- The spec now logs out with an in-page `fetch` and asserts 204.
- Chromium itself accepts `Secure` cookies on `http://127.0.0.1`, so the backend default stays `true` in the harness.

### F3: Python `zipfile` normalizes names per platform
- **Impact:** MEDIUM (consistency; no security bypass). **Resolution:** FIXED (§11).
- `ZipInfo.filename` replaces `\` with `/` on Windows only and truncates at NUL everywhere, both on read and on write.

### F4: existing tests used 401 as a generic "failure"
- **Impact:** LOW. **Resolution:** FIXED.
- With central session handling, a 401 now means signed out:
  - `DashboardPage.test` "every section fails" now uses a 500;
  - the `CredentialsPage.test` expired-session test now asserts the redirect to login.

### F5: removing queries while pages are mounted leaves data in the cache
- **Impact:** MEDIUM (privacy across a user switch). **Resolution:** FIXED.
- The first implementation removed queries in the logout `onSuccess`. The logout test showed a `projects` query surviving.
- The removal moved into the guard's post-commit effect.

### F6: the placeholder route machinery was dead
- **Impact:** LOW. **Resolution:** FIXED (deleted).
- With `/settings` implemented, every nav item has a page.
- `RouteErrorBoundary.test` had mocked `PlaceholderPage` for `/reports` and `/settings`; it now mocks the real page modules.

### F7: one timing flake under full parallel load
- **Impact:** LOW. **Resolution:** OPEN.
- `VersionsPage.test` "presents the snapshot as read-only, with its metadata" failed once with a 1 s `findBy` budget, then passed in 5 consecutive runs (3 isolated, 2 full-suite).
- It cannot be attributed to this phase's changes with the evidence available.

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Area | Origin |
|---|---|---|---|---|---|
| Previous user's queries survived logout | `removeQueries` ran while pages were mounted | Removal in the `RequireSession` effect | `auth.test.tsx` "signs out on the server and forgets the previous user's data" | Frontend auth | Introduced in this phase, fixed before completion |
| Full-stack "session ended elsewhere" spec passed through with a valid session | Playwright request context dropped the `Secure` cookie (F2) | In-page `fetch`, status asserted | The spec itself | Test | Introduced in this phase, fixed |
| 6 Vitest failures on a de-DE machine | Tests asserted en-US formatting; Node uses the OS locale on Windows | Test setup pins the default locale | The 6 tests themselves | Test infrastructure | Pre-existing, environmental |
| `test_rejects_unsafe_paths[pkg\..\evil.py]` failed on Windows | `zipfile` name normalization on both write and read (F3) | Unpacker uses `orig_filename`; helper writes verbatim | Existing case plus a new NUL case | Backend plugins | Pre-existing |
| `npm run test:fullstack` could not start on Windows | `cmd.exe` cannot execute `.sh` | Node harness | The 17 full-stack specs | Test infrastructure | Pre-existing, tooling |
| A literal NUL byte written into source files by an edit script | Shell/Python escaping during editing | Replaced with `\x00` escapes | Python import and ruff passed | Tooling (this session) | Introduced and fixed in-session |

## 11. Fixes Applied

1. **Locale.**
   - Symptom: 6 frontend tests failed (`0,0042` instead of `0.0042`).
   - Root cause: test assumptions depended on the machine.
   - Implementation: `src/test/setup.ts` wraps `Intl.NumberFormat`, `Intl.DateTimeFormat` and `Number.prototype.toLocaleString` so a missing locale means `en-US`. `Date#toLocale*` is deliberately not wrapped because the app does not use it. Application code is unchanged.
   - Verification: Vitest 464/464.
2. **ZIP names.**
   - Symptom: a Windows-only test failure.
   - Root cause: F3.
   - Implementation: `_extract` uses `info.orig_filename` and rejects `"\x00"`. `make_zip` sets `info.filename = name` for names containing `\` or NUL.
   - Verification: `tests/test_plugin_archive.py` and `tests/integration/test_plugins_api.py` 35/35; full backend suite 2048 passed.
3. **Full-stack harness.**
   - Symptom: "Der Befehl "." ist entweder falsch geschrieben…" ('"." is not recognized'), then a Docker `--tmpfs` path mangled by MSYS.
   - Root cause: the shell dependency.
   - Implementation: `tests/fullstack/backend.ts`:
     - Docker CLI via `execFileSync`;
     - readiness polling;
     - base64url key with padding via `node:crypto`;
     - migrate, then serve with signal forwarding.
   - Verification: `npm run test:fullstack` from PowerShell 17/17; no leftover containers (`docker ps --filter label=akasha-e2e=fullstack` empty).
4. **Session clearing** (F5) and **full-stack logout spec** (F2): see §10.

## 12. Testing & Verification

### Unit tests
- `readLoginState`: in-app paths kept; `//evil.example`, `https://…`, `/login` and garbage give `/`. PASS.
- Backend `test_plugin_archive.py`: 16 cases including the new NUL case. PASS.

### Component tests (Vitest + RTL, real router, ApiClient and QueryClient, stubbed `fetch`)

`features/auth/auth.test.tsx` (11 tests, PASS):
- a visitor without a session goes to `/login` and no page data is requested;
- session restoration on reload;
- backend 500 on the session check: error state, retry, then the dashboard;
- cookie login returns to `/projects?q=x`; the body has `cookie: true`; no token and, after unmount, no password in the caches;
- invalid credentials message;
- throttled message;
- `/login` while signed in redirects to the dashboard;
- a 401 mid-use leads to `/login` with "session has ended", and re-login returns to the page;
- logout calls the backend and clears every non-auth query;
- a failed logout keeps the session and shows a toast;
- `readLoginState`.

`features/settings/SettingsPage.test.tsx` (6 tests, PASS):
- account, role, expiry, engine status;
- password change success clears the form;
- a wrong current password is a field error with no sign-out;
- a 422 message;
- mismatch: no request;
- theme pin and persistence.

Updated:
- `client.node.test.ts`: CSRF header, no Authorization, login body;
- `live.test.ts`: frame without a token;
- `AppLayout`, `RouteErrorBoundary`, `CredentialsPage`, `DashboardPage` and `ExecutionsPage` tests.

### Integration / API tests (backend, real PostgreSQL via Testcontainers, httpx ASGI)

`test_auth_api.py`, new (PASS):
- cookie attributes (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/v1`, `Expires`) and `token: null`, then `/auth/session`;
- bearer login sets no cookie;
- the cookie without the header gives 401 for reads and logout;
- a cookie login without the header gives 403 with no cookie;
- a failed cookie login sets no cookie;
- invalid and oversized cookies give 401;
- the cookie session expires (injected clock);
- logout revokes and clears (`Max-Age=0`), and a replay gives 401;
- `/auth/session` without a session gives 401;
- `session_cookie_secure=False` omits `Secure`.

`test_executions_api.py`, new (real uvicorn socket, PASS):
- a same-origin cookie subscribes and streams to the end;
- a foreign or missing Origin gives 4401;
- `X-Forwarded-Host` is honoured.

The existing test that walks OpenAPI and asserts 401 on every non-public route passes, so `/auth/session` is covered. Full backend suite: 2048 passed, 10 skipped.

### Contract tests
- Backend OpenAPI snapshot test and frontend `contract.node.test.ts`: PASS.

### E2E tests
- Stubbed Playwright: 24/24, including the new "a theme pinned in Settings overrides the OS color scheme" and the existing light/dark token checks.
- Full stack: 17/17 against the real backend, PostgreSQL and Redis. New specs:
  - without a session every area leads to the login page, and the API itself answers 401;
  - sign in, survive a reload, sign out:
    - a wrong password is refused;
    - the cookie is `HttpOnly` and `SameSite=Strict`;
    - `document.cookie` is empty and the token is absent from web storage;
    - a reload restores the session;
    - after sign-out the cookie is removed and a replay gives 401;
  - a session ended elsewhere sends the user to sign in again.
- The existing live spec now authenticates by the real cookie through the preview proxy.

### Security tests
The token-storage, CSRF, CSWSH, replay, open-redirect and 401 cases above. All PASS.

### Accessibility
No dedicated audit was run. New forms use the shared `Input`/`Select` components with labels, and the tests address controls by label and role. Errors use `role="alert"` and notices `role="status"`. Keyboard and screen-reader review is not separately verified (Phase 25).

### Performance
Not measured.

## 13. Quality Gates

Final results; all were run in this session.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `prettier --check` (frontend); `ruff format --check` (backend: 210 files) |
| Linter | PASS | `eslint .`; `ruff check .`. Intermediate failures (`FormEvent` deprecated, unbound-method in the locale pin, void-arrow returns, base-to-string) were fixed |
| Type Checker | PASS | `tsc -b`; `mypy .` (205 files) |
| Unit/Component Tests | PASS | Vitest 464/464 (before the follow-up fixes: 458/464, the same 6 failing on the base commit) |
| Integration/API Tests | PASS | Backend 2048 passed, 10 skipped (before the fix: 1 Windows failure, also on the base commit) |
| E2E Tests | PASS | Stubbed 24/24; full-stack 17/17 via plain `npm run test:fullstack` on Windows |
| Build | PASS | `vite build` |
| Contract | PASS | OpenAPI snapshot plus frontend contract test |
| Security Checks | PASS | Security tests above; no dependency audit run (no dependency changes) |
| Accessibility | N/A | No audit (see §12) |

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Unauthenticated users cannot open protected areas | PASS | `RequireSession` wraps every shell route. `auth.test.tsx` "send a visitor without a session to the login page, loading nothing". Full-stack "without a session every area leads to the login page", which also asserts the backend's 401 |
| Session loss is handled cleanly | PASS | Central `onSessionEnded`. `auth.test.tsx` "sends the user to sign in again when the backend rejects the session". Full-stack "a session ended elsewhere…". Backend cookie expiry test. Live stream closes with 4401 on logout (existing test) |
| Logout works | PASS | `auth.test.tsx` logout and failed-logout tests. Backend "logout revokes the cookie session and clears the cookie". Full-stack sign-out with replay 401 |
| Auth state is central | PASS | A single query `['auth','session']` (`session.ts`) consumed by the guard, login, session menu and settings. A single 401 path in `ApiClient` |
| Scope: login, logout, session, current user, settings, theme, API status, account info, security UI | PASS | `/login`, `SessionMenu`, `/settings` (Account, Change password, Appearance, System) |
| Prompt: no tokens exposed to application code | PASS | `token: null` for cookie logins; `getToken` removed; full-stack asserts `document.cookie` is empty and storage holds no token |

## 15. Security Considerations

- **Authentication.** Cookie sessions reuse the Phase 20 session store, expiry, revocation and throttling unchanged.
- **Token storage / XSS.** The token is never in JavaScript (HttpOnly; response `token: null`). The login password is not stored in React state (uncontrolled form). The mutation cache drops it once the form unmounts (`gcTime: 0`, asserted). XSS can still issue requests while the page is open; that remains for CSP in Phase 23.
- **CSRF.** `SameSite=Strict` plus the required `X-Akasha-CSRF` header. Login CSRF is blocked (403). The webhook route is unaffected (public, HMAC).
- **WebSocket.** Origin check against `Host`/`X-Forwarded-Host`. Browsers cannot forge either header in a handshake.
- **Open redirect.** The return path travels in history state, not the URL, and is validated.
- **Logout semantics.** Revocation happens server-side and the cookie is expired. A failed logout does not fake a sign-out.
- **Authorization.** Unchanged. The frontend guard is UX only. The full-stack test asserts the backend 401 independently.
- **Path traversal.** The archive fix makes unsafe-name rejection platform-independent and adds NUL rejection.
- **Error disclosure.** New errors use the existing envelope. `CSRF_HEADER_REQUIRED` reveals nothing secret.
- **Transport.** `Secure` is on by default; `AKASHA_SESSION_COOKIE_SECURE=false` is documented only for plain-HTTP rigs outside localhost.

## 16. Performance Considerations

- **One extra request per page load** (`GET /auth/session`). It is re-checked on window focus only when older than 60 s.
- **Cache cleared on session end.** Every non-auth query is dropped, so the next sign-in refetches.
- **WebSocket cost unchanged.** The cookie path adds one header comparison per subscribe; polling and re-authentication per tick are unchanged.
- **No measurements were taken.**

## 17. Documentation Changes

| File | What was documented |
|---|---|
| `docs/adr/0002-browser-session-cookie.md` | Context, decision, alternatives and consequences of cookie sessions, CSRF and the WebSocket Origin check |
| `docs/architecture.md` | §59 browser sessions and `CSRF_HEADER_REQUIRED`; §23 live authentication by cookie and Origin (replaces "No Origin check… no cookies") |
| `docs/deployment.md` | `proxy_set_header Host $host` and why; HTTPS requirement for the `Secure` cookie; `AKASHA_SESSION_COOKIE_SECURE` |
| `backend/README.md` | `AKASHA_SESSION_COOKIE_SECURE` in the configuration table |
| `frontend/README.md` | New "Authentication" section; API-layer description (CSRF header instead of `getToken`); live-frame note; full-stack harness (`backend.ts`); theme tokens |
| This report | Phase record |

Suggested for a future phase: the roadmap section for Phase 23 could reference ADR 0002 for the CSP work.

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded. `npm ci` was run from the existing lockfile to install modules locally.
- **Environment variables:** new `AKASHA_SESSION_COOKIE_SECURE` (default `true`).
- **Test infrastructure:**
  - `backend.sh` is replaced by `backend.ts`; `openssl` and bash are no longer needed;
  - `playwright.fullstack.config.ts` now uses `command: 'node tests/fullstack/backend.ts'`;
  - Docker images are unchanged (`postgres:17.2`, `redis:8.2.1-alpine`);
  - test locale pinned in `src/test/setup.ts`.
- **Database, Redis, Docker, migrations:** no changes.

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion |
|---|---|---|---|
| Admin user-management UI (backend `/users` exists) | Not in the Phase 21 scope list | Unassigned | No |
| First-admin setup UI | First-run setup is an operator step per `docs/deployment.md` (before the API is reachable) | Unassigned | No |
| Enterprise SSO | Excluded by the roadmap | Not in V1 | No |
| CSP and security headers (limits what XSS can do in-page) | Phase scope | Phase 23 | No |
| Accessibility audit of the new screens | Phase scope | Phase 25 | No |
| Hiding admin-only nav items for members | UX nicety; the backend already answers 403 | Unassigned | No |

## 20. Known Limitations

- **User switch in another tab.** If another user signs in in a different tab, this tab shows its cached data until the next session re-check (on focus after 60 s) or the next 401. The session query then reports the new user, but this tab's cache is not cleared on a user change.
- **No expiry timer.** The UI learns of expiry on the next request or session re-check, not at `expires_at`.
- **Stale cookie after an already-expired logout.** Logout with an expired session answers 401 and the cookie is not expired by that response. It is inert (rejected by the backend) and disappears at its own `Expires`.
- **The test locale pin covers** `Intl.NumberFormat`, `Intl.DateTimeFormat` and `Number#toLocaleString` only. A future `Date#toLocale*` call in app code would again depend on the machine locale in tests.
- **The full-stack harness needs** Node ≥ 22.18 (type stripping), which the `engines` field already requires.

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| `VersionsPage.test` timed out once under full parallel load (F7) | Possible CI flake | LOW | Frontend tests | Watch CI; if it recurs, check that the test awaits `ready` before `findBy` | Phase 24 (performance/UX hardening) or as found |
| Cache not cleared on a user change detected by re-check (§20) | Previous user's data visible in a second tab until reload | LOW | Frontend auth | Compare user ids in the session query and drop server state on change | Phase 23 |

## 22. Architectural / Product Decisions

- **Cookie login is opt-in per request** (`cookie: true`), so bearer API clients never receive an ambient credential.
- **A bearer token always wins over the cookie.** A request with an invalid bearer token is 401 even with a valid cookie; ambiguity resolves to the explicit credential.
- **The cookie without the CSRF header is ignored (401), not rejected (403).** Only a cookie *login* without the header is 403: there the client explicitly asked for cookie mode.
- **`/auth/session` was added instead of changing `/auth/me`,** to avoid a breaking change for existing `/me` clients.
- **The return path after login travels in history state,** never as `?next=`, removing the open-redirect class.
- **A 401 with `INVALID_CREDENTIALS` is not a session end.** Wrong passwords at login or password change must not sign the user out.
- **A failed logout keeps the UI signed in,** because the cookie would still be valid.
- **Server state is dropped in the guard after unmount,** not in mutation callbacks (F5).
- **Tokens use `light-dark()`;** themes only set `color-scheme`.
- **The test locale is pinned in the test setup,** not in the app. Users keep their own locale.

## 23. Repository / Git State

- Branch `main`; HEAD `9fdbae7` (frontend phase 20 — plugin management ui). The working tree was clean at the start of the session.
- Uncommitted: 41 tracked files modified or deleted (+1018/−279). `frontend/tests/fullstack/backend.sh` is deleted and staged (`D `). `frontend/src/app/PlaceholderPage.tsx` is deleted but unstaged (` D`): the `git stash` round-trip used for baselines unstaged its `git rm`. All other changes are unstaged.
- Untracked new files:
  - `docs/adr/0002-browser-session-cookie.md`;
  - this report;
  - `frontend/src/features/auth/`;
  - `frontend/src/features/settings/`;
  - `frontend/tests/e2e/session.ts`;
  - `frontend/tests/fullstack/backend.ts`.
- No unrelated changes detected.
- The temporary `frontend/playwright.win-local.config.ts` and `frontend/test-results/` were deleted.
- `git stash` was used twice to take base-commit baselines and popped immediately; the working tree was verified afterwards.

## 24. Phase Completion Assessment

### Implemented
Cookie-based browser sessions with CSRF and WebSocket protection; `/auth/session`; central session state; route guard; login; logout; global 401 handling; Settings (account, password, theme, engine status); `light-dark()` theming; ADR and docs; fixes for locale tests, ZIP name validation and the Windows full-stack harness.

### Verified
Every acceptance criterion (§14) by component, integration and real-browser full-stack tests. All quality gates PASS (§13).

### Not Implemented
Nothing within the Phase 21 scope.

### Deferred
User-management UI, setup UI, SSO, CSP (Phase 23) and the accessibility audit (Phase 25); see §19.

### Open
The F7 flake observation; cache clearing on a cross-tab user change (§21).

### Blocking Issues
None.

**Phase Status:** COMPLETE

## 25. Handoff to Next Phase

**What Phase 22 can assume:**
- Every route under `/` renders inside `RequireSession`, so a signed-in user exists.
- `useSession()` (`features/auth/session.ts`) gives `data.session.user` (`id`, `username`, `role`) and `expires_at`.
- The API client handles authentication. Never add an `Authorization` header or read a token: there is none in the browser.

**Contracts now available:**
- `GET /api/v1/auth/session`;
- `POST /api/v1/auth/login` with `cookie: true`;
- `POST /api/v1/auth/password`;
- `X-Akasha-CSRF` on every UI request, added by `ApiClient`.

**Must not be changed casually:**
- the `X-Akasha-CSRF` requirement and the cookie attributes (ADR 0002);
- the WebSocket Origin check;
- the rule that only non-`INVALID_CREDENTIALS` 401s end the session;
- `forgetServerState` running after unmount;
- the UI and API staying same-origin.

**Constraints:**
- New HTTP calls must go through `ApiClient`, which ESLint already enforces; otherwise they lack the CSRF header and get a 401.
- New tests that render routes get a signed-in session from `stubBackend` by default. Override `GET /auth/session` with `noSession` for anonymous cases.
- Stubbed E2E specs must call `stubSession(page)` after their own catch-all route.
- Reverse proxies must forward `Host` (`docs/deployment.md`).

**Open items to be aware of:** §21.

**Prerequisites for Phase 22:** none beyond the above.
