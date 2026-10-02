# Phase 03 — API Client + Backend Integration Foundation

## 1. Executive Summary

**Goal.** Phase 03 had to create one central, typed and testable layer for all communication with the backend. Feature components must never perform raw HTTP themselves (`docs/roadmap-frontend.md` §9).

**What was implemented.** The new directory `frontend/src/api/` contains:

- `ApiClient`: one `request(method, path, schema, options)` method. It builds the URL from a base URL plus path and query, serializes JSON bodies, attaches a `Bearer` token from an injected `getToken`, and sends an `X-Request-ID` header. It enforces a timeout, supports caller cancellation via `AbortSignal`, and validates every response body at runtime with Zod.
- `ApiError`: one error class with a `kind` discriminant (12 kinds). It maps the backend's error envelope (`code`, `message`, `details`, `request_id`) and supplies user-facing fallback messages when no envelope arrived.
- A TanStack Query `QueryClient` factory with an explicit retry policy: queries retry only transient failures, and mutations never retry.
- React injection of the client through `ApiClientContext` / `useApiClient()`.
- One service module, `auth.ts` (`login`, `logout`, `fetchCurrentUser`), against the real backend contracts.

The route error boundary now displays `ApiError.message`. An ESLint rule forbids `fetch` and `XMLHttpRequest` everywhere in `src/` outside `src/api/`.

**Contract checking (added in a follow-up within this phase).** The first version of this report left two issues open. Both are now fixed:

- **Frontend/backend contract test (F4).** The backend's OpenAPI document is committed as `frontend/src/api/openapi.json`.
  - A backend test fails when that snapshot is stale.
  - A frontend test checks every service call (method, path, response schema, request body fields) and the error envelope against it.
  - The test immediately found a real drift: the frontend required `details` in the error envelope, but the contract marks it optional (F7).
  - A second backend test found one route documenting its 422 as FastAPI's default validation body instead of `ErrorResponse`. That backend documentation bug is fixed (F8).
- **Lockfile re-indentation (F1, open issue O2 since Phase 01).**
  - Root cause: npm copies `package.json`'s indentation (4 spaces) into `package-lock.json`.
  - Fix: `package.json` is now 2-space via a Prettier override, and the lockfile stays 2-space through `npm install`, `npm ci` and `npm install <pkg>`.

**Resulting state.**

- Frontend quality gates pass: format, lint, typecheck, 111/111 Vitest tests, build, and 11/11 Playwright E2E tests.
- Backend quality gates pass: ruff check, ruff format, mypy, and 1849 pytest tests passed with 6 opt-in skips.
- The client was also exercised by hand against the running backend through the Vite dev proxy (§12).
- One backend change: OpenAPI documentation of `GET /api/v1/reports/executions/{execution_id}`, plus the new contract test. Runtime behavior is unchanged.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap (`docs/roadmap-frontend.md` §9): "Eine robuste zentrale Backend-Kommunikationsschicht schaffen" (create a robust, central backend communication layer).

**Scope:**

- HTTP client, base URL and environment configuration
- authentication handling
- request and response handling, error normalization
- API types
- query and mutation infrastructure
- retry behavior only where sensible
- request cancellation

**The roadmap prompt also requires:**

- no feature UI
- no API calls inside React components
- tests for successful requests, API errors, network errors, cancellation and response parsing
- using the actual backend contracts and not inventing future endpoints

**The phase task added:**

- typed errors for network, HTTP, validation, authentication, authorization, malformed responses and server errors
- timeouts
- runtime validation
- preparation of the authentication boundary
- a WebSocket foundation "if required by architecture"
- implementing missing backend functions, if any

**Not responsible for:**

- domain types and query hooks for projects, nodes, executions and other areas (Phase 04)
- any feature screen (Phase 05+)
- session storage, login UI and redirects (Phase 21)
- live updates over WebSocket (Phase 14)

---

## 3. Scope Implemented

### Architecture

- **One HTTP module.** Only `src/api/` speaks HTTP. ESLint `no-restricted-globals` enforces this: `fetch` and `XMLHttpRequest` are errors in `src/**` outside `src/api/**`.
- **Explicit dependency injection.** The `ApiClient` instance is created once in `App.tsx` and provided through `ApiClientContext`. `useApiClient()` reads it and throws if no provider exists. Service functions take the client as their first argument.
- **Server-state infrastructure.** `QueryClientProvider` is mounted at the app root with `createQueryClient()`.

### HTTP client (`src/api/client.ts`)

- **`ApiClient` options:** `baseUrl` (default `/api/v1`), `timeoutMs` (default 30 000), `getToken`.
- **`request(method, path, schema, options)`**, with `options` = `{ body?, query?, signal?, timeoutMs? }`:
  - Headers: always `Accept: application/json` and `X-Request-ID: crypto.randomUUID()`. `Authorization: Bearer <token>` is added only when `getToken()` returns a value. `Content-Type: application/json` is added only when there is a body.
  - Query: entries whose value is `undefined` are dropped; the rest are serialized with `URLSearchParams`.
  - Abort: the request runs on `AbortSignal.any([callerSignal, AbortSignal.timeout(ms)])`, or on the timeout signal alone.
  - Response: the body is read as text. A non-2xx status goes to `httpError()`. An empty body becomes `undefined`; anything else goes through `JSON.parse`. The result is then checked with `schema.safeParse`.

### Errors (`src/api/errors.ts`)

- **`ApiError extends Error`** with readonly fields `kind`, `status`, `code`, `details`, `requestId`, plus the standard `cause`.
- **Kinds:**
  - transport and parsing: `network`, `timeout`, `cancelled`, `malformed_response`
  - by HTTP status: `validation` (422), `authentication` (401), `authorization` (403), `not_found` (404), `conflict` (409), `rate_limited` (429), `server` (≥500), `http` (other non-2xx)
- **`httpError(status, body, requestId)`** validates the body against the backend envelope schema. If it matches, it uses the envelope's `code`, `message`, `details` and `request_id`. Otherwise it uses the kind's fallback message, `code = KIND.toUpperCase()`, and the response's `X-Request-ID`.
- **`fallbackMessage(kind)`** returns the user-facing English text for each kind.

### Query infrastructure (`src/api/queryClient.ts`)

- **`shouldRetryQuery(failureCount, error)`** returns true only if all of these hold:
  - `failureCount < 2`
  - the error is an `ApiError`
  - the kind is `network` or `timeout`, or the status is 502, 503 or 504
- **`createQueryClient()`** sets `queries.retry = shouldRetryQuery` and `mutations.retry = false`.

### Services (`src/api/auth.ts`)

- **Schemas:** `userSchema` and `loginResponseSchema`, with derived types `User` and `LoginResponse`, plus the `Credentials` interface.
- **`login(client, credentials, signal?)`:** `POST /auth/login`
- **`logout(client, signal?)`:** `POST /auth/logout`, which expects 204 and returns `z.undefined()`.
- **`fetchCurrentUser(client, signal?)`:** `GET /auth/me`

### Frontend/UI

- **`RouteErrorBoundary`:** when the thrown value is an `ApiError`, the `ErrorState` description is `error.message`. Otherwise the existing generic text is kept.

### Contract with the backend

- **`frontend/src/api/openapi.json`:** a generated snapshot of the backend's OpenAPI document (`create_app(...).openapi()`, rendered with `json.dumps(indent=2, sort_keys=True)`). It is listed in `.prettierignore` and never edited by hand.
- **`backend/tests/test_openapi_contract.py`:**
  - `test_snapshot_matches_the_api` fails when the snapshot differs from the live document. `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1` rewrites it.
  - `test_every_documented_error_is_the_error_envelope` fails when a documented 4xx/5xx response does not reference `ErrorResponse`.
- **`frontend/src/api/contract.node.test.ts`:**
  - A `RecordingClient` (an `ApiClient` subclass whose `request` records `{method, path, schema, body}` instead of sending) runs every service function once.
  - For each recorded call it checks:
    - the operation exists, by exact or templated path
    - the success response schema matches the Zod schema
    - a no-content response is accepted as `undefined`
    - the request body field names match
  - Separate checks cover the error envelope and the comparison function itself.
- **`errorEnvelopeSchema`** is now exported, and its `details` field is optional, as in the contract (F7).

### Backend

- `backend/src/akasha/api/reports.py`: the reports router documents 422 as `ErrorResponse` (F8).

### Tooling

- `frontend/.prettierrc`: the override `{"files": "package.json", "options": {"tabWidth": 2}}`. `package.json` was reformatted to 2 spaces and `package-lock.json` restored to 2 spaces (F1).

### Testing

- `src/api/client.node.test.ts`: 22 tests against a real `node:http` server.
- `src/api/queryClient.test.ts`: 16 tests.
- `src/api/contract.node.test.ts`: 7 tests.
- `src/app/RouteErrorBoundary.test.tsx`: 1 new test.
- `backend/tests/test_openapi_contract.py`: 2 tests.

Details are in §12.

---

## 4. Files Changed

New directory: `frontend/src/api/`.

| File                                           | Change              | Reason                                                                                                                                                                                   |
| ---------------------------------------------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/api/client.ts`                   | Created             | `ApiClient`, `HttpMethod`, `ApiClientOptions`, `RequestOptions`, `DEFAULT_BASE_URL`, `DEFAULT_TIMEOUT_MS`                                                                                |
| `frontend/src/api/errors.ts`                   | Created             | `ApiError`, `ApiErrorKind`, `httpError`, `fallbackMessage`, exported `errorEnvelopeSchema` (`details` optional, F7)                                                                      |
| `frontend/src/api/queryClient.ts`              | Created             | `createQueryClient`, `shouldRetryQuery`                                                                                                                                                  |
| `frontend/src/api/context.ts`                  | Created             | `ApiClientContext`, `useApiClient`                                                                                                                                                       |
| `frontend/src/api/auth.ts`                     | Created             | Auth service and Zod schemas of `backend/src/akasha/api/auth.py`                                                                                                                         |
| `frontend/src/api/client.node.test.ts`         | Created             | Client tests against a real local HTTP server (Vitest `node` environment)                                                                                                                |
| `frontend/src/api/queryClient.test.ts`         | Created             | Retry policy and `QueryClient` defaults                                                                                                                                                  |
| `frontend/src/api/contract.node.test.ts`       | Created             | Frontend schemas and service calls checked against the OpenAPI snapshot                                                                                                                  |
| `frontend/src/api/openapi.json`                | Created (generated) | Backend OpenAPI snapshot (124 166 bytes, 28 paths, 59 component schemas); regenerated by the backend test, never edited by hand                                                          |
| `backend/tests/test_openapi_contract.py`       | Created             | Snapshot freshness check and error-envelope documentation check                                                                                                                          |
| `backend/src/akasha/api/reports.py`            | Modified            | 422 documented as `ErrorResponse` (F8)                                                                                                                                                   |
| `backend/README.md`                            | Modified            | "Quality gates": the contract test and how to regenerate the snapshot                                                                                                                    |
| `frontend/.prettierrc`                         | Modified            | `package.json` override with `tabWidth: 2` (F1)                                                                                                                                          |
| `frontend/.prettierignore`                     | Modified            | `src/api/openapi.json` (generated)                                                                                                                                                       |
| `frontend/src/app/App.tsx`                     | Modified            | Mounts `ApiClientContext` and `QueryClientProvider` around the existing `ToastProvider` and `RouterProvider`; the state-boundary comment is updated                                      |
| `frontend/src/app/RouteErrorBoundary.tsx`      | Modified            | Shows `ApiError.message` (user-facing by construction)                                                                                                                                   |
| `frontend/src/app/RouteErrorBoundary.test.tsx` | Modified            | The mocked `PlaceholderPage` throws an `ApiError` for "Monitoring"; new test asserts the message is shown                                                                                |
| `frontend/src/test/setup.ts`                   | Modified            | The `HTMLDialogElement` stand-in is guarded with `typeof HTMLDialogElement !== 'undefined'`; the setup file also runs for `node`-environment files                                       |
| `frontend/tsconfig.app.json`                   | Modified            | `exclude: ["src/**/*.node.test.ts"]`, so browser code does not get Node types                                                                                                            |
| `frontend/tsconfig.node.json`                  | Modified            | `include` adds `src/**/*.node.test.ts` (it has `types: ["node"]`)                                                                                                                        |
| `frontend/eslint.config.js`                    | Modified            | `no-restricted-globals` for `fetch` and `XMLHttpRequest` outside `src/api/**`                                                                                                            |
| `frontend/package.json`                        | Modified            | Adds dependencies `@tanstack/react-query@5.104.0` and `zod@4.6.5` (exact, per `.npmrc save-exact`); re-indented from 4 to 2 spaces (F1). `git diff -w`: 3 insertions, 1 deletion         |
| `frontend/package-lock.json`                   | Modified            | 29 insertions, 2 deletions; indentation unchanged (2 spaces)                                                                                                                             |
| `frontend/README.md`                           | Modified            | New "API layer" section, including "Contract with the backend"; the `package.json` indentation note under Requirements; structure tree lists `api/`; "State boundaries" decision updated |
| `docs/reports/frontend/phase-03-api-client.md` | Created             | This report                                                                                                                                                                              |

No migrations changed. One generated file was added (`openapi.json`). The backend changes are limited to API documentation metadata, a test and the README.

---

## 5. Architecture Changes

The documented architecture is unchanged. The decisions below shape how the frontend uses it.

### 5.1 One request method with a mandatory schema

- **Decision:** `ApiClient` has a single `request(method, path, schema, options)` method, and the schema is required.
- **Context:** `frontend/AGENTS.md` §10 forbids `response.json() as T` and requires runtime validation at API boundaries.
- **Chosen approach:**
  - The return type is `z.output<S>`, so the type is derived from the same schema that validated the data.
  - An empty body parses as `undefined`, so 204 endpoints pass `z.undefined()` and need no separate code path.
- **Alternatives considered:**
  - Per-verb helpers (`get`, `post`, …): more surface for no new behavior.
  - An optional schema with an unchecked fallback: this would reintroduce unvalidated casts.
- **Reason:** Callers cannot skip validation by accident. There is one code path to test.
- **Consequences:** Phase 04 must provide a Zod schema for every endpoint it wraps.

### 5.2 One error class with a `kind` discriminant

- **Decision:** Every failure is an `ApiError`, and `kind` is the discriminant callers branch on.
- **Context:** The task requires typed errors for network, HTTP, validation, authentication, authorization, malformed and server failures. `frontend/AGENTS.md` §4 prefers discriminated unions.
- **Chosen approach:**
  - one class with `kind: ApiErrorKind`
  - the backend `code` is kept separately for finer distinctions, e.g. `INVALID_STATE_TRANSITION` vs `CONFLICT`, both 409
- **Alternatives considered:** a subclass per error type, which means more classes and `instanceof` chains for the same information.
- **Reason:** One `instanceof ApiError` check plus a `switch (error.kind)` covers everything, and TypeScript checks the switch for exhaustiveness.
- **Consequences:**
  - `message` is user-facing by contract, so UI may display it directly.
  - Technical detail lives in `cause` (e.g. the `ZodError`) and in `requestId`.

### 5.3 Client injected through React context, not a module singleton

- **Decision:** The instance is created in `App.tsx` and provided via `ApiClientContext`.
- **Context:** `/AGENTS.md` §68 says to avoid hidden singletons, and tests must be able to replace dependencies.
- **Alternatives considered:** `export const api = new ApiClient()` in `src/api`.
- **Reason:** Tests and the Phase 21 auth feature can supply a client with their own `getToken` or base URL.
- **Consequences:** Query hooks in Phase 04 obtain the client with `useApiClient()` and pass it to service functions.

### 5.4 Retry policy split between queries and mutations

- **Decision:** Queries retry at most twice, and only on `network`, `timeout`, 502, 503 or 504. Mutations never retry.
- **Context:** The roadmap says "Retry-Verhalten nur dort, wo sinnvoll" (retries only where sensible). `/AGENTS.md` §46–47 says retries of side effects are not harmless.
- **Alternatives considered:**
  - TanStack's default (3 retries for every error): this retries 401, 404, 422 and malformed responses, which cannot succeed on retry, and it delays the error UI.
  - Retrying `rate_limited`: deferred, because honoring `Retry-After` needs a delay function. Today 429 fails immediately.
- **Reason:** 500 `INTERNAL_ERROR` is treated as deterministic. 502–504 come from proxies while the backend restarts.
- **Consequences:** A feature that wants retries for a specific idempotent mutation must opt in explicitly.

### 5.5 No WebSocket client

- **Decision:** No WebSocket foundation was built.
- **Context:** The task says "WebSocket foundation if required by architecture". `docs/architecture.md` §22 states that live updates over WebSocket are not offered over HTTP in V1; clients poll `/executions/{id}/events?after=`.
- **Reason:** No backend endpoint exists, so a client would have nothing to talk to.
- **Consequences:** Phase 14 builds it once the backend provides an endpoint.

### 5.6a Contract snapshot owned by the backend, checked by both sides

- **Decision:** The backend's OpenAPI document is committed as `frontend/src/api/openapi.json`. The backend checks that the snapshot is fresh; the frontend checks its schemas against the snapshot.
- **Context:** `/AGENTS.md` §21 says frontend types must not silently diverge, and the generated OpenAPI schema should be validated in CI. The backend reports raised the same need: backend Phase 09 OI-6 ("No automated OpenAPI contract check") and the Phase 27 recommendation to "add an OpenAPI snapshot check".
- **Chosen approach:**
  - Python generates the snapshot; Vitest reads it with `fs`.
  - Neither test suite needs the other language's runtime.
  - A stale snapshot fails the backend suite. A frontend schema that does not match the snapshot fails the frontend suite.
- **Alternatives considered:**
  - **Generating TypeScript types from OpenAPI** (e.g. `openapi-typescript`): this adds a dependency and a generation step, and it yields compile-time types only. The Zod schemas would still be written by hand and could still drift from the generated types.
  - **Generating Zod schemas from OpenAPI:** this needs a code generator dependency, and generated code replaces deliberate frontend choices (e.g. which fields a screen relies on).
  - **The frontend test fetching `/openapi.json` from a running backend:** this needs Python and the backend in the frontend test run (`/AGENTS.md` §124).
- **Reason:** No new dependency. Both tests run in their normal suites, and a contract change shows up as a reviewable diff of one JSON file.
- **Consequences:**
  - An intended API change must regenerate and commit the snapshot (backend README, "Quality gates").
  - Every new frontend service function must be called in `contract.node.test.ts`; a coverage test enforces this for the imported service modules.
  - Response field sets must match exactly: a new backend field fails the frontend test until the frontend schema is updated deliberately.

### 5.6b `package.json` uses 2-space indentation

- **Decision:** A Prettier override sets `tabWidth: 2` for `package.json`; all other files keep 4 spaces.
- **Context:** npm 11.6.2 writes `package-lock.json` with `package.json`'s indentation (verified: with a 2-space lockfile and a 4-space `package.json`, a no-op `npm install` rewrote the whole lockfile to 4 spaces). With both files at 2 spaces, `npm install`, `npm ci` and `npm install zod@4.6.5` left the lockfile byte-identical in indentation.
- **Alternatives considered:**
  - A one-time 4-space lockfile commit: this works too, but it rewrites all 4 500 lockfile lines in history for no gain, and the earlier phases had already settled on 2 spaces.
  - Re-indenting by hand after each install (the Phase 01 workaround): this is what failed again in this phase.
- **Consequences:** Closes O2.

### 5.6 Base URL stays a same-origin constant

- **Decision:** No `VITE_API_BASE_URL` variable. The base URL is the constructor option `baseUrl`, which defaults to `/api/v1`.
- **Context:** Since Phase 00 the browser always calls the same origin. Vite (dev and preview) or a reverse proxy (production) forwards `/api`, and `AKASHA_API_PROXY_TARGET` selects the backend. The backend has no CORS configuration.
- **Reason:** A cross-origin base URL would need backend CORS and would weaken the "no ambient credentials" design.
- **Consequences:** Pointing the frontend at another backend is done with `AKASHA_API_PROXY_TARGET` (dev/preview) or the reverse proxy (production).

---

## 6. Domain / Data Model Changes

There are no domain model changes. Domain types are Phase 04.

The only frontend data schemas added are API boundary schemas in `src/api/auth.ts`:

- `userSchema`:
  - `id`, `username`: strings
  - `role`: `'admin' | 'member'`, matching backend `Role`
  - `disabled`: boolean
  - `created_at`, `updated_at`: ISO datetimes with offset, kept as strings
- `loginResponseSchema`: `token` (a non-empty string), `expires_at`, `user`

Datetimes are kept as strings. Whether to convert them to `Date` is a Phase 04 decision. Unknown extra fields are stripped (Zod `object` default), so additive backend changes do not break parsing (`/AGENTS.md` §81).

---

## 7. API / Contract Changes

**Backend runtime behavior:** unchanged. No backend functionality was missing for this phase:

- error envelope: `ErrorResponse` in `backend/src/akasha/api/schemas.py`, applied by every handler in `api/app.py`
- `X-Request-ID` echo
- `Bearer` authentication: `api/auth.py`
- versioned routes: `api/routes.py`

**Backend OpenAPI documentation (fixed, F8):**

- Before: `GET /api/v1/reports/executions/{execution_id}` documented its 422 response as FastAPI's `HTTPValidationError` (`{"detail": [...]}`).
- At runtime it always sent `ErrorResponse`, because the `RequestValidationError` handler in `api/app.py` produces that envelope.
- The reports router now lists 422 among its `ErrorResponse` codes `(404, 422, 500)`.
- Compatibility: a documentation-only correction; clients relying on the envelope were already right.

**New contract artifact:** `frontend/src/api/openapi.json`, the committed OpenAPI 3.1.0 document (28 paths, 59 component schemas).

**Frontend contract consumers (new):**

| Contract                                              | Consumer                   | Request                     | Response                  | Errors (backend) |
| ----------------------------------------------------- | -------------------------- | --------------------------- | ------------------------- | ---------------- |
| Error envelope `{code, message, details, request_id}` | `httpError` in `errors.ts` | —                           | —                         | All non-2xx      |
| `POST /api/v1/auth/login`                             | `login`                    | `{username, password}` JSON | `loginResponseSchema`     | 401, 422, 429    |
| `POST /api/v1/auth/logout`                            | `logout`                   | Bearer token                | 204, empty body           | 401              |
| `GET /api/v1/auth/me`                                 | `fetchCurrentUser`         | Bearer token                | `userSchema`              | 401              |
| Request header `X-Request-ID`                         | `ApiClient.request`        | UUID v4 per request         | Echoed in response header | —                |

**Compatibility.**

- At runtime, schema mismatches surface as `malformed_response`, never as silently wrong data, and additive fields are tolerated (stripped).
- At test time, the contract test requires exact field sets, so an additive backend field is noticed and handled deliberately.
- The envelope's `details` is optional in the frontend schema, because the contract does not require it (F7); `ApiError.details` defaults to `{}`.

---

## 8. Implementation Details

### Control flow of `ApiClient.request`

1. Generate a `requestId` and create `AbortSignal.timeout(options.timeoutMs ?? this.timeoutMs)`. Combine it with the caller signal via `AbortSignal.any`.
2. Build headers and serialize the body (`body: null` when absent, as required by `exactOptionalPropertyTypes`).
3. `await fetch(...)`, then `await response.text()`, **inside one `try`**. An abort or connection drop while the body is streaming is therefore classified exactly like one before the headers arrive.
4. **Catch classification**, in this order:
   - caller `signal.aborted` → `cancelled`
   - `timeout.aborted` → `timeout`
   - anything else → `network`

   Caller cancellation is checked first, so an unmount is never reported as a backend failure. The original exception is preserved as `cause`.

5. `responseId = response.headers.get('X-Request-ID') ?? requestId`.
6. If `!response.ok`, throw `httpError(status, text, responseId)`.
7. Parse the body: `''` → `undefined`; otherwise `JSON.parse`. A parse failure is `malformed_response` with the `SyntaxError` as `cause`.
8. Run `schema.safeParse`. A failure is `malformed_response` with the `ZodError` as `cause`, and the HTTP status is kept.

### Messages

- `ApiError.message` is always user-facing.
- The backend's envelope message is used as is: the backend redacts it (`redact_text` in `api/app.py`) and writes it for humans.
- Without an envelope (e.g. Vite's empty-body 502, or HTML from a reverse proxy), a fixed English fallback per kind is used.
- No technical text (stack, `TypeError`, Zod issues) ever becomes `message`.

### Authentication boundary

- `getToken` is the only auth hook. The client stores no token.
- No `onUnauthorized` callback was added. A 401 surfaces as `kind: 'authentication'`, and Phase 21 can react centrally, e.g. through TanStack's `QueryCache`/`MutationCache` `onError`.

### Cancellation with TanStack Query

Query functions receive TanStack's `signal`. Service functions accept `signal?: AbortSignal` and forward it. A cancelled query yields `kind: 'cancelled'`, and `shouldRetryQuery` never retries it.

### Testing infrastructure

- `client.node.test.ts` starts with `// @vitest-environment node`. It uses Node's real `fetch` and `AbortSignal` against `node:http` on `127.0.0.1:0`.
- `afterAll` destroys any held responses (`pending`), calls `server.closeAllConnections()` and closes the server.
- The `.node.test.ts` suffix routes the file to `tsconfig.node.json`.

### Contract test mechanics (`contract.node.test.ts`)

**Reading the document.** The snapshot is parsed with a Zod schema of the subset that is used: paths → operations → `requestBody`/`responses` → optional `application/json` schema, plus `components.schemas`. A JSON Schema subset is defined recursively with `z.lazy`. There are no `as` casts on the document.

**Normalizing both sides** into a `Shape`, `{ type, props?, items?, members? }`:

- **Zod side:** `z.toJSONSchema(schema, { io: 'input' })`, i.e. what `parse` accepts.
- `$ref`s are resolved against `components.schemas`.
- `anyOf`/`oneOf` and JSON Schema type arrays become one sorted, flattened `union`. Pydantic writes nullables as `anyOf: [X, {type: null}]`, Zod as `type: [X, "null"]`.
- `enum`/`const` become `enum(sorted values)`.
- Objects with `properties` become `object` with per-property `required` flags. Objects without properties become `record`.
- Strings carry their `format` (`string:date-time`). Zod's regex `pattern`, `minLength`, `maxLength` and `title` are ignored, since they do not change the wire format.
- No type means `unknown`.

**Comparison rules (`compare`):**

- Types must be equal.
- Field sets must be equal.
- A field required in the frontend must be required in the backend. A frontend-optional field may be backend-required: being more lenient is safe.
- Recursion covers objects, arrays and union members.

**Endpoints:**

- The operation is found by the exact path `DEFAULT_BASE_URL + path`, else by matching templated paths (`{param}` → `[^/]+`), so Phase 04 paths with ids work.
- The success response is the lowest documented 2xx.
- For request bodies, the recorded sample body's keys must be a subset of the documented properties, and the documented required properties must be present.

**Coverage guard:** `recorder.calls` must have one entry per exported function of the service modules (currently `auth`).

**Known simplification**, marked with a `ponytail:` comment in the file: union members are paired by sort order. Two object members that differ only in optionality could be paired wrongly; no such union exists in the current frontend schemas.

---

## 9. Findings

### F1 — `npm install` re-indented `package-lock.json` again

- **Finding:**
  - After `npm install zod@4.6.5 @tanstack/react-query@5.104.0`, the lockfile went from 2-space to 4-space indentation.
  - `git diff --stat` shows 4 663 insertions and 4 539 deletions; `git diff -w` shows 29 insertions and 2 deletions.
  - This is the known issue O2 from Phase 01/02. The Phase 02 handoff said to check the lockfile diff after `npm install`, and this was not done during implementation; it was found while preparing this report.
- **Impact:** LOW. Content is correct (`npm audit`: 0 vulnerabilities; `npm test`/`build` pass), but the commit diff is unreviewable.
- **Resolution:** FIXED, at the root cause (follow-up within this phase).
  - **Root cause, verified by experiment:** npm 11.6.2 writes the lockfile with `package.json`'s indentation. Re-indenting the lockfile to 2 spaces and running a no-op `npm install` turned it back into 4 spaces, because `package.json` was 4-space (the Prettier default `tabWidth: 4`).
  - **Fix:** a Prettier override gives `package.json` 2 spaces; `package.json` and `package-lock.json` were rewritten with 2-space indentation.
  - **Verified:** after `npm install`, `npm ci` and `npm install zod@4.6.5`, the lockfile diff against `HEAD` is 29 insertions and 2 deletions, its dependency content only.
- **Notes:** `package-lock.json` is in `.prettierignore`, so `format:check` does not detect the indentation. The fix makes npm itself keep it stable. Closes O2.

### F2 — The shared Vitest setup broke `node`-environment test files

- **Finding:** `src/test/setup.ts` patched `HTMLDialogElement.prototype` unconditionally. In a `@vitest-environment node` file this threw `ReferenceError: HTMLDialogElement is not defined`, and the whole suite failed to load.
- **Impact:** LOW
- **Resolution:** FIXED (guarded with `typeof HTMLDialogElement !== 'undefined'`)
- **Notes:** jsdom behavior is unchanged; all 65 pre-existing tests still pass.

### F3 — Node types are not available to `src/` type checking

- **Finding:**
  - `tsconfig.app.json` uses `types: ["vite/client"]`, so `node:http`, `node:net` and `Buffer` in the server-backed test failed with TS2591.
  - Adding `node` types to the app config would expose `process`, `Buffer` and similar globals to browser code.
  - Under `tsconfig.node.json` (no `noPropertyAccessFromIndexSignature`), ESLint `dot-notation` then demanded `headers.accept` instead of `headers['accept']`.
- **Impact:** LOW
- **Resolution:** FIXED. Node-environment tests use the suffix `.node.test.ts`, are excluded from `tsconfig.app.json` and included in `tsconfig.node.json`. ESLint `--fix` applied dot notation in that file.
- **Notes:** `client.ts` is type-checked under both configs (once directly, once through the test's import). Both pass.

### F4 — Frontend schemas are not checked against the backend's OpenAPI schema

- **Finding:**
  - The Zod schemas in `errors.ts` and `auth.ts` were written by reading `backend/src/akasha/api/schemas.py` and `api/auth.py`.
  - Nothing automated detects drift.
  - `/AGENTS.md` §21 asks that frontend types do not silently diverge from backend contracts.
- **Impact:** MEDIUM. It grows with Phase 04, which adds schemas for most endpoints.
- **Resolution:** FIXED (follow-up within this phase): the OpenAPI snapshot plus contract tests on both sides (§3, §5.6a, §8).
- **Notes:**
  - It also addresses backend Phase 09 OI-6 and the Phase 27 recommendation. Those backend reports were not edited.
  - **Mutation check of the contract test** (each mutation reverted afterwards; all 7 tests passed again):
    - `details` required again → "the error envelope matches ErrorResponse" failed
    - service path `/auth/me` → `/auth/mee` → failed with "GET /api/v1/auth/mee is not in the contract"
    - `role` enum extended with `'owner'` → `POST /auth/login` failed (nested `user.role`)
  - **Self-test of `compare`:** a mistyped field, a missing field, an extra field and a too-strict `required` each produce the expected message.

### F7 — The frontend error envelope schema required a field the contract marks optional

- **Finding:**
  - The OpenAPI `ErrorResponse` has `required: ["code", "message", "request_id"]`; `details` has a default and is optional in the contract.
  - The first frontend `errorEnvelopeSchema` required `details`.
  - Today the backend always sends `details` (`model_dump()` includes defaults), so nothing failed at runtime.
  - But a contract-conformant response without `details` would have been treated as "no envelope", losing the backend's `code` and `message`.
- **Impact:** LOW (latent)
- **Resolution:** FIXED. `details` is `.optional()`, and `ApiError.details` defaults to `{}`. Found by the new contract test.
- **Notes:** This is the kind of drift the contract test exists for.

### F8 — The backend documented one 422 response with the wrong schema

- **Finding:**
  - In the OpenAPI document, `GET /api/v1/reports/executions/{execution_id}` listed 422 as `HTTPValidationError`, which FastAPI adds automatically for routes with parameters.
  - All other error responses referenced `ErrorResponse`.
  - At runtime the backend's `RequestValidationError` handler always returns `ErrorResponse`.
- **Impact:** LOW. A generated or contract-driven client would have expected the wrong error body.
- **Resolution:** FIXED. The reports router declares `(404, 422, 500)` with `ErrorResponse`.
- **Regression test:** `test_every_documented_error_is_the_error_envelope`. With the fix temporarily reverted it failed, listing `['GET /api/v1/reports/executions/{execution_id} 422']`; with the fix restored it passes.
- **Notes:** Pre-existing backend inconsistency, found during this phase.

### F5 — The Vite dev proxy answers a dead backend with 502 and an empty body

- **Finding:** With the backend stopped, `GET http://localhost:5179/api/v1/auth/me` through `vite` returned `HTTP/1.1 502 Bad Gateway`, `Content-Type: text/plain`, an empty body and no `X-Request-ID`.
- **Impact:** LOW
- **Resolution:** Behavior verified as handled. The client maps it to `kind: 'server'`, `status: 502`, the message "The server is unavailable. Try again later." and the client-generated `requestId`. Queries retry it. The unit test "falls back to a user-facing message when the error body is not an envelope" covers the same shape with an HTML body.
- **Notes:** Production behavior depends on the reverse proxy; any non-envelope 5xx is handled the same way.

### F6 — Without a database, auth routes answer 500 `CONFIGURATION_ERROR` before body validation

- **Finding:** With the backend running without `AKASHA_DATABASE_URL`, both `GET /api/v1/auth/me` and `POST /api/v1/auth/login` with body `{}` returned 500 `CONFIGURATION_ERROR` ("Authentication is not configured (database)."). The `auth_service` dependency fails before request-body validation.
- **Impact:** LOW. This is an unconfigured deployment, and the response is a proper envelope.
- **Resolution:** OPEN, informational. Pre-existing backend behavior, not changed.
- **Notes:** The frontend shows it as `kind: 'server'` with the backend's message.

---

## 10. Bugs / Problems Found

| Problem                                                                                        | Root cause                                                                                          | Fix                                                                       | Regression test                                                           | Area             | Origin                                            |
| ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------------- | ------------------------------------------------- |
| `client.test.ts` suite failed to load in `node` environment (F2)                               | Unconditional `HTMLDialogElement.prototype` patch in shared setup                                   | `typeof` guard                                                            | `client.node.test.ts` loads and runs (22 tests)                           | Test setup       | Pre-existing limitation, exposed this phase       |
| Typecheck TS2591 for `node:http`/`node:net`/`Buffer` (F3)                                      | App tsconfig has no Node types (correctly)                                                          | `.node.test.ts` suffix, tsconfig include/exclude                          | `npm run typecheck` passes                                                | Tooling          | Introduced this phase                             |
| 5 ESLint errors (2× `no-confusing-void-expression`, 3× `dot-notation`)                         | Arrow shorthand returning `res.end()`/`push()`; different index-signature rules under node tsconfig | `eslint --fix`                                                            | `npm run lint` passes                                                     | Tests            | Introduced this phase                             |
| Lockfile re-indented (F1)                                                                      | npm writes the lockfile with `package.json`'s 4-space indentation                                   | 2-space `package.json` via Prettier override                              | Manual: `npm install`/`ci`/`install <pkg>` leave the lockfile at 2 spaces | Tooling          | Known issue O2 since Phase 01, recurred           |
| Error envelope schema too strict (F7)                                                          | `details` required, contract says optional                                                          | `.optional()`                                                             | `contract.node.test.ts` "the error envelope matches ErrorResponse"        | API layer        | Introduced this phase, found by the contract test |
| Reports 422 documented as `HTTPValidationError` (F8)                                           | FastAPI's automatic 422 for parameterized routes was not overridden in the reports router           | 422 added to the router's `ErrorResponse` codes                           | `test_every_documented_error_is_the_error_envelope`                       | Backend API docs | Pre-existing, found this phase                    |
| `tsc` TS2322 and ESLint `restrict-template-expressions` in `contract.node.test.ts`             | `schema.type` is `string \| string[]` at the final return                                           | `typeof schema.type !== 'string'` guard (arrays are already unions there) | `npm run typecheck`, `npm run lint` pass                                  | Tests            | Introduced this phase                             |
| Contract test failed to load: `openapi.json` export endpoint has no `application/json` content | The document schema required `application/json` in every `content`                                  | `application/json` optional in the document schema                        | Test loads; 7 pass                                                        | Tests            | Introduced this phase                             |

No product bugs were found in existing frontend code. One pre-existing backend documentation bug was found and fixed (F8).

---

## 11. Fixes Applied

1. **Shared test setup works in both environments.**
   - Symptom: `ReferenceError: HTMLDialogElement is not defined`, 0 tests collected in `client.test.ts`.
   - Implementation: a guard in `src/test/setup.ts`.
   - Verification: 14/14 files and 104/104 tests passed at that point (111/111 after the follow-up).
2. **Node types are scoped to node-environment tests.**
   - Symptom: TS2591 in `tsc -b`.
   - Implementation: the file was renamed to `client.node.test.ts`; `tsconfig.app.json` excludes it and `tsconfig.node.json` includes it.
   - Verification: `npm run typecheck` exits 0.
3. **Lint errors in the new test file.**
   - Implementation: `eslint --fix`.
   - Verification: `npm run lint` reports nothing.
4. **Lockfile churn (F1).**
   - Symptom: every `npm install` re-indented `package-lock.json` (a 9 065-line diff).
   - Root cause: npm uses `package.json`'s indentation.
   - Implementation: Prettier override with `tabWidth: 2` for `package.json`; both files re-indented to 2 spaces.
   - Verification: after `npm install`, `npm ci` and `npm install zod@4.6.5`, `git diff --stat frontend/package-lock.json` shows 31 changed lines (29 insertions, 2 deletions); `npm run format:check` passes.
5. **Frontend/backend schema drift undetected (F4).**
   - Implementation: OpenAPI snapshot plus `contract.node.test.ts` plus `test_openapi_contract.py`.
   - Verification: 7 + 2 tests pass, and the mutation checks fail as expected (F4, F8).
6. **Error envelope `details` optional (F7):** found and verified by the contract test.
7. **Reports 422 documentation (F8):** verified by the backend envelope test, including the revert check.

---

## 12. Testing & Verification

### Unit tests

**`src/api/queryClient.test.ts` — 16 tests, PASS:**

- Transient kinds and statuses are retried on attempts 0 and 1 and not on attempt 2: `network`, `timeout`, 502, 503, 504 (5 tests).
- These are never retried: 500, `cancelled`, `malformed_response`, 401, 403, 404, 409, 422, 429 (9 tests).
- Non-`ApiError` values are not retried.
- `createQueryClient()` defaults: queries use `shouldRetryQuery`; mutations use `retry: false`.

### Integration tests (client ↔ real HTTP server)

**`src/api/client.node.test.ts` — 22 tests, PASS.** A real `node:http` server on `127.0.0.1` with an ephemeral port; no mocked `fetch`.

**Successful requests (4):**

- method, path, query and body are sent correctly:
  - query: `undefined` dropped, numbers and booleans serialized
  - body: the JSON string
  - headers: `Content-Type`, `Accept`, `Authorization: Bearer tok`, UUID `X-Request-ID`
  - response: unknown fields stripped
- No `Authorization`/`Content-Type` without a token or body; `fetchCurrentUser` returns the parsed user.
- 204 with an empty body → `logout` resolves `undefined`.
- `login` posts the credentials and validates the session response.

**API errors (11):**

- 9 status mappings with the backend envelope: 401, 403, 404, 409, 422, 429, 413 (`http`), 500 and 503 (`server`). Each test asserts `kind`, `status`, `code`, `message`, `details` and the envelope's `requestId`.
- An expired session (401 with `WWW-Authenticate: Bearer`) → `authentication` with the backend's message.
- A non-envelope 502 HTML body → `server`, code `SERVER`, fallback message, `requestId` from the response header.

**Malformed responses (3):**

- schema violation (`role: 'root'`) → `malformed_response`, `status` 200, `cause` is a `ZodError`
- non-JSON 200 body (an SPA fallback page) → `malformed_response`
- empty 200 body where content is expected → `malformed_response`

**Transport (4):**

- unreachable port (`127.0.0.1:1`) → `network`, `status` undefined, the "could not be reached" message
- a hanging server with a 50 ms timeout → `timeout`
- caller abort after the request reached the server → `cancelled`. The test is event-driven: the abort fires once the server has received the request, not after a fixed sleep.
- an already-aborted signal → `cancelled`, and the server received no request

**Mutation check.** I temporarily replaced the caller-abort branch in `client.ts` with `false ? 'cancelled'`. Both cancellation tests failed (2 failed, 36 passed). The file was restored, and all 38 `src/api` tests passed again.

### Contract tests

**`src/api/contract.node.test.ts` — 7 tests, PASS:**

- coverage: one recorded call per service function (3)
- `POST /auth/login`: `LoginResponse` vs `loginResponseSchema` (including nested `UserDetail` and the `Role` enum); request body fields vs `LoginRequest`
- `POST /auth/logout`: documented 204 without content; `z.undefined()` accepts an empty body; no request body
- `GET /auth/me`: `UserDetail` vs `userSchema`
- error envelope vs `ErrorResponse`
- comparison self-test: an equivalent schema with a nullable datetime (Pydantic `anyOf` vs Zod type array) and an optional field passes
- comparison self-test: mistyped, missing, extra and too-strict fields produce the exact expected messages

**`backend/tests/test_openapi_contract.py` — 2 tests, PASS:**

- the snapshot equals the live document. Before the snapshot existed this failed with `FileNotFoundError`; after `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1` it passes.
- every documented 4xx/5xx references `ErrorResponse`

The mutation checks are listed under F4 and F8.

### Backend regression run

Because a backend file changed, the full backend suite was run: `uv run pytest`, **1849 passed, 6 skipped** in 184.82 s.

- The skips are opt-in: 5 × OpenRouter external smoke (`AKASHA_SMOKE_OPENROUTER_API_KEY` not set), 1 × deployment lifecycle (`AKASHA_TESTS_DEPLOYMENT=1` not set).
- The integration tests ran against Testcontainers (Docker available).
- The one warning, a Starlette `httpx` deprecation from `fastapi.testclient`, is pre-existing and unrelated.

### Component tests

**`src/app/RouteErrorBoundary.test.tsx` — 2 tests (1 new), PASS.** A page that throws `ApiError({ kind: 'network', message: 'Server unreachable.' })` inside the real route tree shows that message in the `alert` region.

### Manual integration check against the real backend

Performed while preparing this report. No files were changed.

**Setup:** backend started with `AKASHA_PORT=8765 uv run akasha serve` (no database), and `AKASHA_API_PROXY_TARGET=http://127.0.0.1:8765 npx vite --port 5179`.

**Results:**

- `GET /api/v1/auth/me` through the Vite proxy with `X-Request-ID: report-check-1`:
  - `500` with the envelope `{"code":"CONFIGURATION_ERROR",…,"request_id":"report-check-1"}`
  - the response header `x-request-id: report-check-1`
  - the id appears once in the backend's structured log
  - This confirms the proxy target configuration, the envelope shape the client parses, and request-id correlation end to end.
- Backend stopped, same request → `502`, empty `text/plain` body (F5).
- Both servers were stopped afterwards.

This was a `curl` check through the proxy, not an automated test.

### E2E tests

`npm run test:e2e`: **11/11 passed.** These are the existing Phase 01/02 specs; none are new. They confirm the root-level providers did not break the shell or the UI catalog.

### Security tests

Covered within the client tests:

- no `Authorization` header without a token
- user-facing messages only; Zod and parse errors stay in `cause`
- a non-envelope error body never becomes the message

No dedicated security suite was added.

### Not performed

- automated tests of the frontend client against the real FastAPI backend (the contract test checks schemas, not live behavior)
- performance tests
- accessibility tests (no new UI beyond the message text in an existing `ErrorState`)

---

## 13. Quality Gates

All results are from the final run on 2026-10-02, after the follow-up fixes.

| Quality Gate      | Result | Notes                                                                                                                                                                                                      |
| ----------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Formatter         | PASS   | `npm run format:check`: "All matched files use Prettier code style!" (`package-lock.json` and `openapi.json` are prettier-ignored). Backend: `uv run ruff format --check .`: "193 files already formatted" |
| Linter            | PASS   | `npm run lint`: no output (earlier failures in §10 fixed). Backend: `uv run ruff check .`: "All checks passed!"                                                                                            |
| Type Checker      | PASS   | `npm run typecheck` (`tsc -b`), exit 0 (earlier TS2591/TS2322 fixed). Backend: `uv run mypy .`: "Success: no issues found in 188 source files"                                                             |
| Unit Tests        | PASS   | `npm test`: 15 files, 111/111 (65 before this phase + 46 new)                                                                                                                                              |
| Integration Tests | PASS   | Frontend: 22 tests against a real local HTTP server. Backend: full suite 1849 passed, 6 opt-in skips (Testcontainers)                                                                                      |
| API Tests         | PASS   | Backend API tests in the full suite. Contract tests: 7 frontend + 2 backend. No automated frontend-to-live-backend test (manual check in §12)                                                              |
| E2E Tests         | PASS   | `npm run test:e2e`: 11/11                                                                                                                                                                                  |
| Build             | PASS   | `npm run build`: `index-*.js` 443.31 kB (136.01 kB gzip), `index-*.css` 23.95 kB, `UiCatalogPage-*.js` 5.12 kB                                                                                             |
| Security Checks   | PASS   | `npm audit --omit=dev`: "found 0 vulnerabilities"                                                                                                                                                          |
| Accessibility     | N/A    | No new UI components                                                                                                                                                                                       |
| Other             | PASS   | Lockfile indentation stable after `npm install`/`npm ci`/`npm install <pkg>` (F1)                                                                                                                          |

---

## 14. Acceptance Criteria Verification

The roadmap acceptance criteria (§9):

| Acceptance Criterion                                                                              | Status | Evidence                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Keine direkten Fetch-Aufrufe in Feature-Komponenten (no direct fetch calls in feature components) | PASS   | `grep` finds `fetch(` only in `src/api/client.ts`. ESLint `no-restricted-globals` makes any `fetch`/`XMLHttpRequest` use outside `src/api/**` a lint error, and `npm run lint` passes                                                                                                                                                             |
| API-Fehler haben ein einheitliches Format (API errors have a uniform format)                      | PASS   | Every rejection of `ApiClient.request` is an `ApiError` (`kind`, `status`, `code`, `details`, `requestId`, user-facing `message`). Tests cover 12 HTTP and envelope cases, 3 malformed cases and 4 transport cases (§12)                                                                                                                          |
| API Layer ist testbar (the API layer is testable)                                                 | PASS   | 22 tests against a real HTTP server with no mocked `fetch`; the client is configurable (`baseUrl`, `timeoutMs`, `getToken`) and injected through context; the retry policy is a pure function with 16 tests                                                                                                                                       |
| Environment-Konfiguration funktioniert (environment configuration works)                          | PASS   | Default base URL `/api/v1` with the Vite proxy driven by `AKASHA_API_PROXY_TARGET`. Verified manually: the request reached a backend on `127.0.0.1:8765` through `vite --port 5179` (§12). The `baseUrl` option is exercised by every client test                                                                                                 |
| Backend-Ausfall wird sauber dargestellt (backend outage is displayed cleanly)                     | PASS   | Unreachable → `network`; proxy 502 without envelope → `server` with "The server is unavailable. Try again later." (unit test plus the manual proxy check, F5). `RouteErrorBoundary` shows `ApiError.message` (component test). Queries retry transient outages up to twice. No feature screen yet consumes queries; that UI follows from Phase 05 |

Additional requirements from the task prompt:

| Requirement                                                                               | Status | Evidence                                                                                                                         |
| ----------------------------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Typed errors: network, HTTP, validation, authentication, authorization, malformed, server | PASS   | `ApiErrorKind`; tests per kind                                                                                                   |
| Request cancellation                                                                      | PASS   | 2 cancellation tests plus the mutation check                                                                                     |
| Timeout behavior                                                                          | PASS   | Timeout test (50 ms)                                                                                                             |
| Runtime response validation                                                               | PASS   | Mandatory schema; 3 malformed tests                                                                                              |
| Authentication boundary preparation                                                       | PASS   | `getToken` injection; 401 → `authentication`; token header tests                                                                 |
| API service organization                                                                  | PASS   | One module per backend router; `auth.ts` as the first module; the pattern is documented in the README                            |
| WebSocket foundation if required by architecture                                          | N/A    | Not required: architecture §22 says there is no WebSocket in V1 (§5.5)                                                           |
| Missing backend functions implemented                                                     | PASS   | No runtime functionality was missing (§7). One documentation defect fixed (F8) and the contract snapshot test added, both tested |
| Contract tests against the backend (`/AGENTS.md` §21)                                     | PASS   | `contract.node.test.ts` (7) + `test_openapi_contract.py` (2), with mutation checks                                               |
| Query and mutation infrastructure (roadmap scope)                                         | PASS   | `createQueryClient`, `QueryClientProvider` mounted, retry policy tested                                                          |

No open item from this phase remains. F1 and F4, open in the first version of this report, are fixed.

---

## 15. Security Considerations

- **Authentication:**
  - The token is sent only in the `Authorization` header: never in a URL (query values come only from explicit `query` options), never stored by the client, never logged.
  - The backend design has no cookies, so the client opens no CSRF surface.
- **Secrets:**
  - Where tokens are stored is deliberately left to Phase 21. `frontend/AGENTS.md` and `/AGENTS.md` §62 forbid `localStorage`/`sessionStorage`.
  - `login` sends the password only in the JSON body.
- **Error disclosure:**
  - `ApiError.message` is either the backend's redacted envelope message or a fixed fallback.
  - Zod issues, `SyntaxError`s and network exceptions stay in `cause` and are never rendered.
  - `RouteErrorBoundary` still hides non-API errors behind the generic text (existing test still passes).
- **Input validation at the trust boundary:**
  - Every response body is schema-validated before use. Non-JSON (e.g. an HTML fallback page) is rejected.
  - The error envelope itself is validated before its fields are used.
- **Correlation:** `X-Request-ID` is a random UUID, which satisfies the backend's acceptance rule (1–128 printable characters). It contains no user data.
- **No logging:** the API layer writes nothing to the console.
- **Not changed:** CORS (none, same origin), CSP and other headers (Phase 23).

---

## 16. Performance Considerations

- **Bundle size, measured:**
  - Main chunk: Phase 02 had 332.42 kB (104.38 kB gzip); now 443.31 kB (136.01 kB gzip), an increase of about 111 kB (31.63 kB gzip).
  - The contract test and `openapi.json` are test-only and not in the bundle: the build stayed at 136.01 kB gzip after they were added.
  - Cause: the full `zod` import plus `@tanstack/react-query`. No breakdown per library was measured.
  - `zod/mini` is the documented option if size becomes a constraint.
- **Responses** are read fully as text and then parsed. There is no streaming and no size cap in the client. Large payloads are an API design concern: pagination exists server-side.
- **Retries** are bounded: at most 2 extra attempts per query, using TanStack's default exponential delay. They happen only for transient failures, so a 4xx never multiplies traffic.
- **Timeout** defaults to 30 s per request and can be overridden per request.
- No performance tests or benchmarks were run.

---

## 17. Documentation Changes

| File                                           | Reason                | Content                                                                                                                                                                                                                       |
| ---------------------------------------------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/README.md`                           | New layer             | "API layer" section: `client.ts`, `errors.ts` (all kinds), `queryClient.ts` (retry rules), `context.ts`, service-module pattern, no WebSocket, `.node.test.ts` convention. Structure tree and state-boundary decision updated |
| `frontend/README.md` (follow-up)               | Contract and lockfile | "Contract with the backend" (snapshot, both tests, regeneration command, coverage rule); a note under Requirements on why `package.json` uses 2 spaces                                                                        |
| `backend/README.md`                            | Contract snapshot     | "Quality gates": what `tests/test_openapi_contract.py` checks and how to regenerate the snapshot                                                                                                                              |
| `backend/tests/test_openapi_contract.py`       | Contract snapshot     | The module docstring documents the purpose and the regeneration command                                                                                                                                                       |
| `docs/reports/frontend/phase-03-api-client.md` | Phase report          | This document, updated after the follow-up fixes                                                                                                                                                                              |

Code comments document the reasons for the design: the ESLint rule, the retry policy, context injection, message semantics, and the test environment.

`docs/architecture.md` and `docs/roadmap-frontend.md` were not changed. No contradiction was found; the contract snapshot implements `/AGENTS.md` §21 without changing the architecture. The backend Phase 09 and Phase 27 reports, which list the missing contract check, were left unchanged as historical records; this report records the resolution.

Documentation to update later:

- `frontend/README.md` "API layer", when Phase 04 adds domain services, query keys and hooks
- the same section, when Phase 21 decides token storage

---

## 18. Dependencies / Infrastructure Changes

| Change                                             | Type                   | Why                                                                                                                                                                           |
| -------------------------------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `zod@4.6.5`                                        | New runtime dependency | Runtime validation at API boundaries. Named in `frontend/AGENTS.md` §3; required by §10. Latest release at install time (published 2026-09-13)                                |
| `@tanstack/react-query@5.104.0`                    | New runtime dependency | Server-state solution named in `frontend/AGENTS.md` §3 and §11; roadmap Phase 03 scope "Query/Mutation Infrastructure". Latest release at install time (published 2026-09-26) |
| `tsconfig.app.json` / `tsconfig.node.json`         | Config                 | `.node.test.ts` routing (F3)                                                                                                                                                  |
| `eslint.config.js`                                 | Config                 | `fetch`/`XMLHttpRequest` restriction                                                                                                                                          |
| `src/test/setup.ts`                                | Test infrastructure    | Works for both jsdom and node environments (F2)                                                                                                                               |
| `.prettierrc` override (`package.json` → 2 spaces) | Config                 | Stops npm re-indenting the lockfile (F1)                                                                                                                                      |
| `.prettierignore` + `src/api/openapi.json`         | Test infrastructure    | The generated contract snapshot (2-space JSON from Python) is not reformatted                                                                                                 |
| `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1`                 | Test-only env variable | Read only by `backend/tests/test_openapi_contract.py`, to rewrite the snapshot; it does nothing in the application                                                            |

- Exact versions are pinned (`.npmrc save-exact=true`). `npm audit --omit=dev` reports 0 vulnerabilities. Both packages are MIT-licensed.
- No new dependency for the contract tests: Zod's built-in `z.toJSONSchema` and Node's `fs`; pytest and the existing `create_app` on the backend.
- Not changed: application environment variables, Docker, database, Redis, external services.

---

## 19. Deferred Work

| What                                                                                               | Why deferred                                                                           | Intended phase                  | Affects completion |
| -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------- | ------------------ |
| Service modules and schemas for projects, nodes, executions, triggers, credentials, reports, users | Phase 04 is "Domain Types + Server State"; adding them here would duplicate that phase | 04                              | No                 |
| Query keys, query/mutation hooks, cache invalidation                                               | Phase 04 scope                                                                         | 04                              | No                 |
| Surfacing query/mutation errors via `notify` (Phase 02 open issue O1)                              | No query or mutation consumer exists yet; normalization (the prerequisite) is done     | 04/05                           | No                 |
| Token storage, login UI, central 401 handling                                                      | Auth feature                                                                           | 21                              | No                 |
| WebSocket client                                                                                   | No backend endpoint in V1 (architecture §22)                                           | 14                              | No                 |
| `Retry-After`-aware retry for 429                                                                  | Needs a delay function; no consumer yet                                                | When a rate-limited read exists | No                 |
| Automated tests against the real backend                                                           | Needs the backend in the frontend test setup (Postgres via containers)                 | 26 (E2E)                        | No                 |

---

## 20. Known Limitations

- **L1.** Frontend schemas are hand-written. Drift is detected by the contract test, which needs:
  - every service function to be called in `contract.node.test.ts` (enforced by the coverage test for the modules it imports; a new module must be added to that import list)
  - the snapshot to be regenerated after backend API changes (enforced by the backend test)

  Only success responses and request body field names are compared. Request body value types, query parameters and path parameter names are not.

- **L2.** `rate_limited` is not retried, and `Retry-After` is not read (it is available in `details.retry_after_seconds` when the backend sends it).
- **L3.** Response bodies are buffered fully. There is no client-side size cap or streaming.
- **L4.** Fallback messages are English only. There is no i18n infrastructure.
- **L5.** `/health` and `/ready` are outside `/api` and not proxied. The client cannot call them; backend availability is inferred from API failures.
- **L6.** The client builds URLs by string concatenation. `path` must start with `/` and must not contain user input that is not encoded. Service functions are responsible for `encodeURIComponent` on path parameters; no service needs it yet.

---

## 21. Open Issues

| #   | Issue                                                  | Impact                                 | Severity | Area           | Recommended next action                                            | Target       |
| --- | ------------------------------------------------------ | -------------------------------------- | -------- | -------------- | ------------------------------------------------------------------ | ------------ |
| O1  | (Phase 01/02) Async errors not yet shown in feature UI | Normalization is done; no consumer yet | MEDIUM   | Error handling | Use `ApiError.message` in query/mutation error states and `notify` | 04/05        |
| O3  | (Phase 01) No CI pipeline                              | Gates run manually only                | MEDIUM   | Tooling        | Add CI for format, lint, typecheck, test, build, E2E               | Not assigned |
| O5  | (Phase 02) Overlay clipping                            | Unchanged                              | LOW      | UI             | —                                                                  | 24           |
| O6  | (Phase 02) `/dev/ui` in production                     | Unchanged                              | LOW      | Routing        | —                                                                  | 27           |

Closed in this phase:

- **O2** (lockfile re-indentation, since Phase 01): root cause fixed (F1).
- **O7** (no contract test): fixed (F4).

The remaining open issues are carried over from earlier phases; none results from this phase. Without CI (O3), the contract tests only run when someone runs the suites.

---

## 22. Architectural / Product Decisions

- **`ApiError.message` is always user-facing.** UI may render it without filtering. Technical detail goes to `cause` and `requestId`, never into `message`. Changing this would break the boundary's no-leak guarantee.
- **The schema is mandatory on every request.** There is no "trust the server" path.
- **Caller cancellation takes precedence over timeout in classification.** An unmount is never reported as a backend failure or retried.
- **Writes are never retried automatically.** A timed-out write may have been applied (at-least-once semantics, `/AGENTS.md` §46).
- **The client is injected via context**, not imported as a singleton.
- **No `VITE_API_BASE_URL`.** Same origin is a security and CORS decision from Phase 00. Redirect the backend with `AKASHA_API_PROXY_TARGET` or the reverse proxy.
- **Node-environment tests use the `.node.test.ts` suffix.** This keeps Node types out of browser code while allowing real-server tests.
- **No WebSocket client until the backend offers an endpoint.**
- **The backend owns the contract snapshot (`frontend/src/api/openapi.json`); the frontend validates against it.** Regenerate it with the backend test flag, never edit it by hand. Response field sets must match exactly; frontend fields may be more lenient (optional) but never stricter than the contract.
- **`package.json` is 2-space indented** because npm copies its indentation into the lockfile. Do not remove the Prettier override.

---

## 23. Repository / Git State

- **Branch:** `main`
- **Last commit:** `fd66d94 frontend phase 02 - design system and ui foundation`
- **Working tree (uncommitted):**
  - Modified:
    - `backend/README.md`, `backend/src/akasha/api/reports.py`
    - `frontend/.prettierignore`, `frontend/.prettierrc`, `frontend/README.md`, `frontend/eslint.config.js`, `frontend/package-lock.json`, `frontend/package.json`
    - `frontend/src/app/App.tsx`, `frontend/src/app/RouteErrorBoundary.test.tsx`, `frontend/src/app/RouteErrorBoundary.tsx`, `frontend/src/test/setup.ts`
    - `frontend/tsconfig.app.json`, `frontend/tsconfig.node.json`
  - Untracked: `backend/tests/test_openapi_contract.py`, `frontend/src/api/` (including `openapi.json`), `docs/reports/frontend/phase-03-api-client.md`
- **Unrelated changes:** none.
- **Diff noise:** `package-lock.json` changes only its 29 + 2 dependency lines. `package.json` shows a whole-file re-indentation (98 lines) whose content change is 3 + 1 lines (`git diff -w`). That re-indentation is the F1 fix; it could go in its own commit (`/AGENTS.md` §65).
- Nothing was committed, reset or stashed. The manual backend and Vite processes used for verification were stopped.

---

## 24. Phase Completion Assessment

### Implemented

- `ApiClient`: base URL, JSON, query, Bearer token, request id, timeout, cancellation, schema validation
- `ApiError` with 12 kinds and envelope mapping
- `createQueryClient` with the retry policy
- `ApiClientContext` / `useApiClient`
- `auth.ts` service
- `RouteErrorBoundary` API message
- ESLint fetch restriction
- README section
- OpenAPI contract snapshot with frontend (`contract.node.test.ts`) and backend (`test_openapi_contract.py`) checks
- backend reports 422 documentation fix
- permanent lockfile indentation fix

### Verified

- frontend: format, lint, typecheck, 111/111 unit, integration and contract tests, build, 11/11 E2E, `npm audit`
- backend: ruff check, ruff format, mypy, full pytest (1849 passed, 6 opt-in skips)
- mutation checks: cancellation logic, contract test (envelope, path, enum), envelope documentation test
- lockfile stability across `npm install`, `npm ci` and `npm install <pkg>`
- manual proxy-to-backend check, including the outage case

### Not implemented

- domain services beyond auth (intentionally, Phase 04)
- WebSocket (not required by the V1 architecture)

### Deferred

See §19.

### Open

- O1, O3, O5, O6: carried over from earlier phases. None is caused by this phase.
- Known limitations L1–L6 (§20) are documented scope boundaries, not unmet criteria.

### Blocking issues

None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase (Phase 04 — Domain Types + Server State)

### What Phase 04 can assume

- `QueryClientProvider` and `ApiClientContext` are mounted in `App.tsx`. `useApiClient()` works anywhere under `App`.
- `client.request(method, path, schema, { body, query, signal, timeoutMs })` returns validated data or throws `ApiError`.
- `ApiError.kind` covers every failure; `ApiError.message` can be shown to users.
- Queries retry only transient failures; mutations do not retry.

### Pattern for new services

- One module per backend router in `src/api/`, e.g. `projects.ts`.
- Each module exports Zod schemas, `z.infer` types, and functions `(client, …args, signal?) => client.request(...)`.
- Encode path parameters with `encodeURIComponent` (L6).
- Pass TanStack's `signal` from `queryFn` through to `request`.
- 204 endpoints use `z.undefined()`.
- Paginated responses follow the backend `PageInfo` (`offset`, `limit`, `has_more`) seen in `api/project_schemas.py`. Define its schema once when the first paginated endpoint is added.

### Contract (mandatory for every new service)

- Call each new service function once in `src/api/contract.node.test.ts` (and add new modules to `[auth]` in `serviceFunctions`). The coverage test fails otherwise.
- Response schemas must have the backend's exact field set, types, enums, formats and nullability. A field may be optional in the frontend even if the backend requires it, never the reverse.
- After an intended backend API change: `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py` in `backend/`, then commit `frontend/src/api/openapi.json` with the change.
- The snapshot already contains all 28 V1 paths, so Phase 04 can write its schemas against it directly.

### Tests

- Client behavior is covered. Service tests can reuse the `client.node.test.ts` pattern: a real `node:http` server and the `.node.test.ts` suffix.
- Hook tests can render with a test `ApiClient` (custom `baseUrl`) in `ApiClientContext` and a fresh `createQueryClient()`.

### Must not be changed casually

- the user-facing guarantee of `ApiError.message`
- the mandatory schema parameter
- the classification order: cancelled → timeout → network
- `mutations.retry = false`
- the ESLint `fetch` restriction
- the same-origin `/api/v1` default
- the contract tests and the rule that `openapi.json` is generated, never hand-edited
- the `package.json` 2-space Prettier override

### Known constraints

- Backend V1 has no plugin HTTP API and no WebSocket (architecture §22). Do not add services for them.
- Admin-only routers (`/credentials`, `/users`) return 403 to members, which maps to `kind: 'authorization'`.

### Prerequisites before starting Phase 04

1. Commit Phase 03. Optionally commit the `package.json` re-indentation plus the Prettier override separately from the feature changes (`/AGENTS.md` §65).
