# Phase 17 — Credentials UI

Report date: 2026-10-04. Branch `main`. Base commit `1329ea3` (frontend phase 16 — monitoring). All work described here is **uncommitted** in the working tree (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap §23 (`docs/roadmap-frontend.md`, "Phase 17 — Credentials UI"): manage credentials securely.
- Scope: Credential List, Create, Update, Delete, Credential Type, Provider, Name, Metadata, and Usage Information "sofern verfügbar" (if available).
- Core rule: a secret value is never displayed again after creation.

The session prompt added:
- credential assignment/reference, validation and authorization errors;
- tests for create, update, delete, authorization, validation, secret masking, accidental serialization and browser storage safety;
- implement and test any backend API that is missing.

**Discovered.** No backend gap blocked the required scope. `/api/v1/credentials` already provides create, list, get, patch and delete (backend Phase 13):
- admin-only since backend Phase 20 (`api/routes.py:140`);
- values write-only (`SecretStr` in, never out);
- integration tests already cover value-never-returned, no echo in 422 bodies, and members denied.

The frontend API layer was also already in place (Phase 04): `api/credentials.ts`, `credentialQueries`, and `useCreate/Update/DeleteCredential` with `gcTime: 0`. Assignment by reference was in place too: the Phase 09 node inspector's credential-reference combobox stores only `credential:<name>`.

Two items have no backend source:
- **Usage information:** there is no endpoint that lists the projects referencing a credential.
- **Provider:** a credential has a `kind` (`api_key | smtp | provider | generic`), not a provider field. The provider is part of the LLM node config.

Neither was invented (§19).

**Actually implemented (frontend only).**
- The `/credentials` page (`frontend/src/features/credentials/`):
  - a paginated metadata table with a fixed value mask;
  - a create dialog and an edit dialog (description, plus an optional rotate value);
  - delete with confirmation;
  - a dedicated 403 state for members.
- Route wiring.
- 8 component tests that cover the secret lifecycle.
- README documentation.

**Resulting state.** An admin can list, create, edit/rotate and delete credentials. The UI never receives a plaintext value from the backend. A typed value exists in the browser only:
- in the password input;
- in the mutation's variables, from submit until the dialog closes;
- in the outgoing request body.

Tests verify that afterwards it is absent from the DOM, inputs, browser storage, the router URL, request URLs, console output and the TanStack query/mutation caches.

**Acceptance criteria.** All five roadmap criteria pass (§14).

**Phase Status:** COMPLETE WITH LIMITATIONS

The limitations: no usage information (no backend source), no separate confirmation step for rotation, and one accessibility defect in the value mask column (§9).

## 2. Phase Objective

Roadmap §23:
- **Goal:** "Credentials sicher verwalten" (manage credentials securely).
- **Key rule:** "Nach Erstellung darf ein Secret nicht erneut als Klartext angezeigt werden, sofern Backend dies nicht explizit vorsieht" (after creation, a secret must not be shown again as plaintext unless the backend explicitly provides for it).
- **OpenCode prompt:** secrets must not appear in URLs, localStorage, logs, React dev state ("unnötig persistiert", persisted unnecessarily) or error messages. Show only metadata and masked information. "STOP nach Phase 17."

`frontend/AGENTS.md` rules that apply:
- §23: no secrets in React state longer than necessary, or in URL, logs, query cache, local/session storage or export data. Do not reload and redisplay a saved secret.
- §49: distinguish exists / configured / secret / metadata. An existing credential does not mean its secret is visible. Delete, rotate and update need clear confirmation when destructive.
- §54: Credential Delete is a destructive action and needs confirmation.

**Not responsible for:**
- triggers (Phase 18);
- reports (Phase 19);
- plugins (Phase 20);
- user management, login and session storage (Phase 21);
- frontend security hardening such as CSP (Phase 23);
- per-credential grants (`docs/architecture.md` §Authorization calls them future work).

## 3. Scope Implemented

### Frontend/UI
- **`CredentialsPage`** at `/credentials`:
  - The page header has a "New credential" button, which is disabled when the list answered 403.
  - The table columns are Name (with a one-line description), Type (a `Badge` with a human label), Reference (`<code>credential:<name></code>`), Value (a fixed `••••••••`), Updated (`<time>`), and an Actions column with Edit and Delete. Each action has an accessible name, e.g. "Edit crm-api".
  - Offset pagination uses the backend's `has_more`, 50 per page, held in component state.
  - `keepPreviousData` keeps rows visible while the next page loads, and the table region gets `aria-busy`.
  - States:
    - loading, error with retry, and stale-with-warning (shared `QuerySection`);
    - empty ("No credentials yet", with a create action);
    - 403: an `ErrorState` "Admin access required", which explains that projects can still reference credentials by name.
- **`CredentialFormDialog`**, one dialog for both modes:
  - **Create:**
    - Name: required, `pattern` mirrors the backend name rule, `maxLength` 64, help text says the name is immutable.
    - Type: a `Select` over `credentialKindSchema.options`, default `api_key`.
    - Description: `maxLength` 2000.
    - Secret value: required, `maxLength` 65536.
  - **Edit:**
    - Reference and Type are shown read-only in a `<dl>`.
    - Description is prefilled.
    - "New secret value" is optional, always starts empty, and its help text says to leave it empty to keep the stored value.
  - Errors:
    - backend `VALIDATION_ERROR` details under `body.name|kind|description|value` go to the matching field;
    - any 409 goes to the Name field;
    - everything else goes to a form-level `Alert` through `errorMessage()`.
- **`DeleteDialog`** uses the shared `ConfirmDialog`:
  - danger tone, `irreversible` ("This cannot be undone."), initial focus on Cancel;
  - the text names the reference and says nodes using it fail until a credential with the same name is created again;
  - a delete error is shown inside the dialog.
- **Toasts:** "Credential “x” created", "… updated", "… rotated" (when a value was sent), "… deleted".
- **`kinds.ts`:** the `kindLabels` map, typed `satisfies Record<CredentialKind, string>`, so a new backend kind fails the typecheck.

### Routing
- `src/app/routes.tsx`: an explicit lazy route `/credentials` → `CredentialsPage`. `/credentials` was added to the list of paths excluded from the placeholder mapping.

### Testing
- `CredentialsPage.test.tsx`: 8 tests (§12).
- `RouteErrorBoundary.test.tsx`: its API-failure case now uses the Plugins placeholder instead of Credentials (§10).

### Backend/API
- No changes (§7).

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/src/features/credentials/` | Created (directory) | New feature module |
| `frontend/src/features/credentials/CredentialsPage.tsx` | Created (337 lines) | Page, list table, create/edit/delete dialog wrappers, 403 state |
| `frontend/src/features/credentials/CredentialFormDialog.tsx` | Created (182 lines) | Create/edit form; secret field handling; backend error mapping |
| `frontend/src/features/credentials/kinds.ts` | Created (8 lines) | `kindLabels`. A separate module because `react-refresh/only-export-components` forbids exporting constants from a component file |
| `frontend/src/features/credentials/CredentialsPage.test.tsx` | Created (363 lines) | Component tests for the secret lifecycle |
| `frontend/src/app/routes.tsx` | Modified (+9 lines) | Real route instead of the placeholder |
| `frontend/src/app/RouteErrorBoundary.test.tsx` | Modified (2 lines) | Its fixture relied on `/credentials` being a placeholder |
| `frontend/README.md` | Modified (+22 lines) | New "Credentials" section |
| `docs/reports/frontend/phase-17-credentials-ui.md` | Created | This report |

There were no migrations, configuration changes, dependency changes or generated-file changes. `src/api/openapi.json` is unchanged.

## 5. Architecture Changes

No architectural changes were introduced during this phase.

The implementation follows the existing architecture:
- write-only values (§32);
- admin-only credential management (§Authorization, Phase 20);
- the existing API layer and server-state hooks (`frontend/AGENTS.md` §8–§12).

## 6. Domain / Data Model Changes

Not applicable. There are no new entities, schemas or DTOs.

The frontend reuses `credentialReferenceSchema` and `credentialKindSchema` from `src/api/credentials.ts`, which are unchanged. The only new data structure is UI-local:
- `CredentialFormValues {name, kind, description, value}`: the transient submit payload, never stored;
- the `Editing` union (`{mode:'create'} | {mode:'edit', credential}`): which dialog is open.

## 7. API / Contract Changes

There were no contract changes. The phase consumes the existing endpoints:

| Endpoint | Used for | Request | Response | Errors handled in UI |
|---|---|---|---|---|
| `GET /api/v1/credentials?offset&limit` | List | — | `CredentialPage` (metadata only) | 401 (message), 403 (admin-required state), others (error with retry) |
| `POST /api/v1/credentials` | Create | `{name, kind, description, value}` | `CredentialDetail` | 422 → field or form; 409 → Name field |
| `PATCH /api/v1/credentials/{name}` | Edit/rotate | `{description}` or `{description, value}` | `CredentialDetail` | 422 → field; others → form alert |
| `DELETE /api/v1/credentials/{name}` | Delete | — | 204 | any → alert in confirm dialog |

`GET /api/v1/credentials/{name}` exists (`getCredential`) but the page does not call it. The edit dialog uses the list row, which carries the same metadata.

## 8. Implementation Details

**Secret lifecycle in the browser.** This is the security-relevant core.
1. The value input is uncontrolled (`name="value"`, no `value`/`onChange`), so React state never holds the secret while it is typed.
2. Input attributes:
   - `type="password"`: masked.
   - `autoComplete="new-password"`: keeps the browser from filling the admin's saved login password into the field. `"off"` is ignored for password fields by major browsers.
   - `spellCheck={false}`: keeps the value away from enhanced (cloud) spellcheck.
   - `autoCapitalize="off"` and `autoCorrect="off"`.
3. On submit, `FormData` reads the field once and the value goes to `mutation.mutate(...)`. From there it exists in TanStack's mutation `state.variables` and in the `JSON.stringify` request body. The `ApiClient` sends it only as the body. Query parameters carry only `offset`/`limit`.
4. When the dialog closes (after success, or Cancel after a failure), `close()` calls `mutation.reset()`, which clears `variables`. The dialog then unmounts (`editing` → `null`), which unmounts the hook's observer. The existing `secretOptions = { gcTime: 0 }` in `api/mutations.ts` evicts the mutation from the `MutationCache`. `Dialog` mounts its content only while open, so the input's DOM node is gone too.
5. **While the dialog is open after a failed submit**, the value stays in the input and in the mutation's variables. This is deliberate: the user can correct another field and resubmit without retyping. Both are cleared on close.
6. **On edit**, the payload is `{ description, ...(value && { value }) }`. An empty field is never sent, so a description edit cannot rotate or blank the secret.

**Response handling.** The response schema is a Zod `z.object`, which strips unknown keys. A `value` field that a faulty backend might add never reaches the query cache or the screen (tested). The Value column renders a constant mask, so the UI has no code path that could display a value.

**Error mapping.** `submitErrors()` in `CredentialFormDialog.tsx`:
- reads `ApiError.details["body.<field>"]` for `VALIDATION_ERROR`;
- maps `kind === 'conflict'` to the Name field;
- if no field matched, shows `errorMessage(error)` at form level.

All texts come from the backend's error envelope. The backend builds 422 details from `loc` and `msg` only, never `input` (`backend/src/akasha/api/app.py:179-192`). So validation messages cannot echo the value. Domain name errors (`DOMAIN_VALIDATION_ERROR`) carry no field details and appear at form level.

**Authorization.** The UI derives "admin" only from the backend answer (`ApiError.kind === 'authorization'` on the list query). There is no client-side role check. The backend remains authoritative (`frontend/AGENTS.md` §7).

**Cache invalidation.** These behaviors were unchanged and are inherited from `api/mutations.ts`:
- create invalidates `['credentials','list']`;
- update writes the detail into the cache and invalidates lists;
- delete removes the detail and invalidates lists.

Each mutation stays pending until its refetch finishes, so the dialog closes only on fresh data.

## 9. Findings

### Finding 1 — Backend and API layer already complete for this phase
- **Finding:** Every operation the phase needs already existed and was tested, in both the backend and the frontend API layer.
- **Impact:** LOW
- **Resolution:** FIXED (n/a)
- **Notes:** "Implement missing backend APIs" required no work for list, create, update, delete or authorization.

### Finding 2 — No usage information source
- **Finding:** No endpoint reports which projects or versions reference a credential.
- **Impact:** MEDIUM
- **Resolution:** DEFERRED
- **Notes:**
  - The roadmap makes it conditional ("sofern verfügbar"), and `frontend/AGENTS.md` forbids invented data, so the UI does not show usage.
  - The delete dialog states the consequence for referencing nodes in general terms instead.
  - A backend endpoint would have to scan drafts and published version snapshots for `credential:<name>`. That is a cross-aggregate query and the backend service has no such read today. It was judged out of proportion for an optional item.

### Finding 3 — "Provider" is not a credential attribute
- **Finding:** The roadmap lists "Provider", but the backend models only `CredentialKind`, which includes `provider`. The LLM node's `provider` is a separate config field next to its `credential` reference.
- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:** The type is shown with labels (`provider` → "LLM provider"). No provider field was invented.

### Finding 4 — Rotation has no separate confirmation step
- **Finding:** `frontend/AGENTS.md` §49 asks for clear confirmation of destructive delete, rotate and update actions. Rotation replaces the stored value irrecoverably. It happens when the user types into "New secret value" (help text: "Leave empty to keep the stored value") and presses "Save changes". There is no second confirmation dialog.
- **Impact:** LOW
- **Resolution:** OPEN
- **Notes:**
  - The roadmap acceptance criteria require confirmation for Delete only, and that is satisfied.
  - Rotation needs an explicit, non-default input (a non-empty value field), and the success toast says "rotated".
  - Whether that counts as "clear confirmation" under §49 is a product judgement. It is recorded here, not decided silently.

### Finding 5 — The mask's `aria-label` on a generic `<span>`
- **Finding:** The Value cell renders `<span aria-label="Hidden">••••••••</span>`. ARIA does not allow naming the generic role, so screen readers may ignore the label and read the bullet characters, or nothing.
- **Impact:** LOW
- **Resolution:** OPEN
- **Notes:**
  - Found while reviewing for this report. The report must not change product code, so it was not fixed.
  - Fix: `<span aria-hidden="true">••••••••</span><span className="sr-only">Hidden</span>`.
  - The test (`getAllByLabelText('Hidden')`) would need the matching query change.

### Finding 6 — `useMutation` keeps the secret in `variables` while observed
- **Finding:** TanStack Query stores mutation variables in mutation state for as long as a component observes it. `gcTime: 0` alone only helps after unmount.
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:**
  - Each dialog wrapper calls `mutation.reset()` on close.
  - The dialog wrapper components unmount on close, which ends observation, and `gcTime: 0` then evicts the mutation.
  - Fault injection (§12) showed that `gcTime: 0` with unmount already clears the cache, so `reset()` is defense in depth. Removing both `reset()` and `gcTime: 0` made 3 tests fail.

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Area | Classification |
|---|---|---|---|---|---|
| `RouteErrorBoundary.test.tsx` ("shows the user-facing message of an API failure") would no longer exercise the placeholder once `/credentials` became a real page | The test mocks `PlaceholderPage` and used the Credentials placeholder to throw an `ApiError` | Moved the fixture to `title === 'Plugins'` and `/plugins`, which is still a placeholder; the assertion is unchanged | The test itself | `src/app` | Consequence of this phase (the Phase 16 report records the same move from Monitoring to Credentials) |
| Two tests failed with "Found multiple elements with the role button and name New credential" | The header action and the empty-state action are the same button, by design | The test helper `openDialog` takes the first match (`findAllByRole(...)[0]`) | The two create tests | test code | Introduced during this phase (test only) |
| ESLint `react-refresh/only-export-components` on `CredentialFormDialog.tsx` | `kindLabels` was exported from a component file | Moved to `kinds.ts` | lint gate | feature code | Introduced during this phase |
| ESLint `no-unsafe-return` and `no-misused-spread` in the test | `JSON.parse` returned `any`; `{...localStorage}` spread a class instance | `z.record(z.string(), z.unknown()).parse(...)`; `Object.entries(localStorage)` | lint gate | test code | Introduced during this phase |

No pre-existing product bugs were discovered.

## 11. Fixes Applied

1. **Ambiguous button query.**
   - Symptom: 2/8 tests failed.
   - Root cause: a duplicate accessible name by design (see §10).
   - Implementation: helper selects the first match.
   - Verification: 8/8 pass.
2. **Lint errors (4).**
   - Symptom: `npm run lint` failed.
   - Implementation: see §10.
   - Verification: `npm run lint` is clean.
3. **Placeholder-dependent boundary test.**
   - Symptom: the fixture would target a real page.
   - Implementation: retargeted to `/plugins`.
   - Verification: `src/app` tests pass (26/26 together with the credentials tests).

## 12. Testing & Verification

### Component Tests (Vitest + React Testing Library, jsdom)

These use the real router, `ApiClient` and `QueryClient`, with `fetch` stubbed by `stubBackend` (`src/test/app.tsx`). No network.

`src/features/credentials/CredentialsPage.test.tsx`, all passing:

| Test | Verifies |
|---|---|
| lists metadata and references, never a value | Name, description, reference and type label render; the value column is masked; a `value` field injected into the list response appears neither in the DOM nor in the query cache (accidental serialization) |
| creates through a masked, write-only field and forgets the value | Input is `type=password`, `autocomplete=new-password`, `spellcheck=false`; the POST body is exactly `{name, kind, description, value}`; the dialog closes; the new row appears; `assertSecretNowhere` passes |
| shows validation and conflict errors at the field without echoing the value | A 422 `body.value` error appears at the value field (`aria-invalid`); a 409 appears at the Name field; after Cancel, `assertSecretNowhere` passes |
| updates the description without sending a value, and rotates on demand | Name is not editable; the rotate field starts empty and masked; PATCH bodies are `{description:'new'}` then `{description:'old', value}`; correct toasts; `assertSecretNowhere` passes |
| deletes only after confirmation | Cancel sends no DELETE; the dialog names the reference and says "This cannot be undone."; Confirm sends exactly one DELETE and the list refetches to empty |
| shows a failed delete in the dialog | A 404 envelope message is shown inside the confirm dialog |
| tells members that only admins manage credentials | 403 → "Admin access required"; New is disabled |
| explains an expired session | 401 → alert with the backend message |

`assertSecretNowhere` checks, for a canary value `sk-live-canary-9f8e7d6c5b4a`:
- `document.body.innerHTML`;
- every `<input>`'s `.value`;
- all `localStorage` and `sessionStorage` entries;
- the router location (pathname, search, hash);
- every request URL;
- every captured `console.log/info/warn/error/debug` call;
- the serialized state of every mutation and every query in the `QueryClient`.

**Negative tests (fault injection).** These were run manually; the code was restored after each.

| Injected defect | Result |
|---|---|
| `console.info(value)` in the create `onSubmit` | 2 tests failed (create, validation) |
| Removed `reset()` calls and set `secretOptions = {}` (default `gcTime`) | 3 tests failed (create, validation, update) |
| Removed only the `reset()` calls (kept `gcTime: 0`) | 0 failed: unmount plus `gcTime: 0` suffices (see Finding 6) |

**Full suite.** `npm test`: 32 files, 393 tests, all passed (run 2026-10-04 for this report).

### Integration / API Tests
None were added or run in this phase. The backend is unchanged. Its existing tests (`backend/tests/integration/test_credentials.py`, `test_auth_api.py::test_members_cannot_reach_admin_areas`) cover encryption, value-never-returned, no echo in 422 bodies, and member denial. They were inspected, not re-run.

### E2E Tests
Not run. No Playwright spec covers `/credentials`. `tests/fullstack/projects.spec.ts` uses credential references in the node inspector only.

### Security Tests
These are the component tests above: masking, storage, URL, logs, cache, accidental serialization, and the 403 path.

### Accessibility Tests
None automated beyond role and label queries in the component tests (labelled fields, `aria-invalid`, named action buttons, dialog roles). See Finding 5 for a defect those queries do not catch.

### Performance Tests
None.

## 13. Quality Gates

All were run from `frontend/` on 2026-10-04 while writing this report.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`npm run format:check`) | PASS | |
| Linter (`npm run lint`) | PASS | Initially failed with 4 errors (§10); fixed |
| Type Checker (`npm run typecheck`) | PASS | |
| Unit/Component Tests (`npm test`) | PASS | 32 files, 393 tests. Initially 2 failures in the new tests (§10); fixed |
| Integration Tests | N/A | No backend change; not run |
| API Tests | N/A | No API change; not run |
| E2E Tests | N/A | Not run; no spec for this page |
| Build (`npm run build`) | PASS | `CredentialsPage` chunk is 6.89 kB (2.75 kB gzip) |
| Security Checks | PASS | Secret-leak assertions and fault injection (§12) |
| Accessibility | N/A | No automated a11y audit; one manual finding open (Finding 5) |

## 14. Acceptance Criteria Verification

Roadmap §23, "Acceptance Criteria":

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Keine Secret Values in UI State Logs (no secret values in UI state logs) | PASS | The value is never in React state (uncontrolled input); mutation variables are cleared on close; `assertSecretNowhere` checks the console and query/mutation cache after create, failed create and rotate; fault injection with `console.info` fails the tests |
| Keine Secret Values in Browser Storage (none in browser storage) | PASS | No storage API is used in `features/credentials/`; `assertSecretNowhere` checks `localStorage` and `sessionStorage` |
| Keine Secret Values in URL (none in the URL) | PASS | The value goes only in the request body; list state (`offset`) is in component state; tests check router location and every request URL |
| Secret Fields werden maskiert (secret fields are masked) | PASS | `type="password"` asserted in the create and edit tests; the list Value column is a constant mask |
| Delete benötigt Bestätigung (delete requires confirmation) | PASS | "deletes only after confirmation": Cancel sends no request; only Confirm sends DELETE |

Scope items without a criterion: Usage Information is not implemented because no backend source exists (Finding 2). "Provider" is covered by the type (Finding 3). Neither blocks completion.

## 15. Security Considerations

- **Secrets:** see §8. The browser is never given a stored value; a typed value is short-lived and never in a URL, storage, log or cache after close.
- **Password managers:** `autocomplete="new-password"` prevents autofill of the user's saved login into the secret field. A browser may still offer to *save* the typed value; that prompt is browser-controlled and cannot be fully suppressed by the page.
- **Authorization:** role enforcement stays on the backend (credential router behind `require_admin`). The UI only reacts to a 403. A member who opens `/credentials` sees no data.
- **Error disclosure:** only backend envelope messages and fallback texts are shown. The backend's validation envelope excludes `input` (verified in `api/app.py`).
- **Output encoding:** names, descriptions and references render as React text (no `dangerouslySetInnerHTML`).
- **Accidental serialization:** unknown response keys are stripped by Zod (tested).
- **Path injection:** names in paths go through `encodeURIComponent` (existing `api/credentials.ts`).
- **Not addressed (outside phase):** CSP and other browser hardening (Phase 23); where the session token lives (Phase 21).

## 16. Performance Considerations

- The list is paginated server-side at 50 per page (the backend default and limit). No unbounded fetch.
- The route is lazy-loaded (a separate 6.89 kB chunk).
- No measurements were taken; none were needed at this size.

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `frontend/README.md` | New feature | A "Credentials" section: page scope, admin-only behavior, the secret-handling lifecycle, what the tests assert, and that usage information is not shown |
| `docs/reports/frontend/phase-17-credentials-ui.md` | Phase report | This document |

`docs/architecture.md` and the roadmap were not changed; no decision required it.

Future documentation:
- If a usage endpoint is added, document it in `docs/architecture.md` §32 and in the README section.

## 18. Dependencies / Infrastructure Changes

None. There are no new or changed dependencies, environment variables, Docker or database changes, and no test infrastructure changes.

## 19. Deferred Work

| What | Why deferred | Intended phase | Dependency | Affects completion |
|---|---|---|---|---|
| Credential usage information (which projects reference a name) | No backend source; the roadmap makes it conditional; inventing data is forbidden | Not assigned | Backend read model or query over drafts and versions | No |
| Separate confirmation for rotation (Finding 4) | Product judgement under `frontend/AGENTS.md` §49; not a roadmap criterion | Phase 23 or 24 (hardening/UX), or on request | None | No |
| Playwright coverage of `/credentials` | Not part of this phase's test plan | Phase 26 (E2E Testing) | Full stack with an encryption key configured | No |

## 20. Known Limitations

- Usage information is not shown, so deleting a credential that projects still reference is not blocked or listed. The confirm text warns about it in general terms.
- Pagination offset is component state, not URL state: a reload returns to page 1. This is intentional, because nothing about credentials should go into the URL.
- A failed create or update keeps the typed value in the input and in mutation variables until the dialog closes (§8.5).
- Screen-reader output for the masked Value cell is not reliable (Finding 5).
- No search or filter. The backend list endpoint has none.

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| `aria-label` on a generic `<span>` for the value mask | Screen readers may not announce "Hidden" | LOW | `CredentialsPage.tsx` Value column | Replace with `aria-hidden` bullets plus an `sr-only` "Hidden"; adjust the test query | Phase 25 (Accessibility) or earlier |
| Rotation has no second confirmation | Possible accidental rotation, mitigated by the empty-by-default field | LOW | `CredentialFormDialog` | Decide per §49; if required, confirm when `value` is non-empty | Phase 23/24 |
| No credential usage endpoint | Admins cannot see impact before delete or rotate | MEDIUM | Backend + UI | Add an admin-only `GET /credentials/{name}/usage` over drafts and published versions, then show it in the delete dialog | Unassigned |

## 22. Architectural / Product Decisions

- **The secret is never React state.** An uncontrolled input plus `FormData` at submit keeps the value out of component state, React DevTools props and re-renders. Future credential forms (e.g. trigger secrets in Phase 18) should follow the same pattern.
- **`mutation.reset()` on dialog close** for every mutation whose variables carry a secret, in addition to `gcTime: 0`.
- **Edit never sends an empty value.** "Empty means keep" is enforced in the payload construction, not only in the UI text.
- **Name and kind are read-only after creation**, matching `CredentialService.update` (name is the reference identity).
- **403 is a page state, not a generic error.** Members are told why and that references still work.
- **No usage data or provider field was invented**, per the "no invented data" rule; recorded as deferred.
- **Pagination is not kept in the URL** on this page, unlike Projects: least exposure, small lists.

## 23. Repository / Git State

- **Branch:** `main`. **HEAD:** `1329ea3` (frontend phase 16 — monitoring). Nothing committed for this phase.
- **Modified (3):**
  - `frontend/README.md`
  - `frontend/src/app/RouteErrorBoundary.test.tsx`
  - `frontend/src/app/routes.tsx`
- **Untracked:**
  - `frontend/src/features/credentials/` (4 files)
  - `docs/reports/frontend/phase-17-credentials-ui.md`
- **Unrelated changes:** none detected. The working tree was clean at session start.

## 24. Phase Completion Assessment

### Implemented
- The `/credentials` page: list, create, edit/rotate, delete with confirmation, the 403 state, and backend error mapping.
- Route wiring, tests and README.

### Verified
- 8 component tests, including secret-leak assertions over the DOM, inputs, storage, URL, request URLs, console and caches.
- Fault injection showing those assertions detect leaks.
- Full suite 393/393; lint, format, typecheck and build pass.

### Not Implemented
- Usage information (no backend source).
- A provider attribute (not in the domain).

### Deferred
- Usage endpoint, rotation confirmation, E2E coverage (§19).

### Open
- Mask accessibility, rotation confirmation, usage endpoint (§21).

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

**Phase 18 (Triggers UI) can assume:**
- an unchanged credentials API;
- `/credentials` is a real route. Remaining placeholders are `/reports`, `/plugins` and `/settings`; `RouteErrorBoundary.test.tsx` now uses `/plugins`.

**Patterns to reuse:**
- For any secret input (webhook HMAC secrets are credentials referenced by triggers, architecture §127), reuse the `CredentialFormDialog` handling:
  - uncontrolled `type="password"`, `autocomplete="new-password"`, `spellCheck={false}`;
  - `FormData` at submit;
  - `reset()` on close;
  - `gcTime: 0` on the mutation.
- `assertSecretNowhere` in `CredentialsPage.test.tsx` is a local helper. Move it to `src/test/` if a second feature needs it.

**Must not be changed casually:**
- `secretOptions = { gcTime: 0 }` in `api/mutations.ts`;
- the `reset()` calls in the credential dialogs;
- the "empty value is not sent" rule;
- Zod object stripping of unknown keys on `credentialReferenceSchema`. Do not switch it to `.passthrough()` or `.loose()`.

**Known constraints:**
- Credential management is admin-only on the backend.
- Members reference credentials by typing the name in the node inspector, because their list request answers 403.
