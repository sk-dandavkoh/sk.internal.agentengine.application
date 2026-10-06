# Phase 20 — Plugin Management UI

Report date: 2026-10-04. Branch `main`. Base commit `50e8bb4` (frontend phase 19 — reports ui). All work described here is **uncommitted and unstaged** (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap `docs/roadmap-frontend.md` §26, "Phase 20 — Plugin Management UI": "Plugins verwalten" (manage plugins).

The roadmap scope is:
- installed plugins and plugin details;
- version, compatibility and dependencies;
- enabled/disabled state;
- install and remove;
- validation errors;
- no marketplace.

The session prompt added:
- capabilities;
- communicating plugin trust implications;
- treating plugin metadata as untrusted (never as HTML);
- implementing, testing and documenting any missing backend function.

**Discovered.** The backend offered only `GET /api/v1/plugins`, admin only and read-only. Architecture §22 and §36 stated explicitly that install, enable, disable and remove were "Not over HTTP in V1", but operator CLI only. The frontend already had a list-only `api/plugins.ts` and `pluginQueries.list`. `/plugins` still rendered the shared placeholder page.

**Actually implemented:**

Backend:
- four lifecycle routes on the existing plugins router: install (upload), enable, disable and remove;
- a safe ZIP unpacker, `akasha.plugins.archive`;
- the opt-in setting `AKASHA_PLUGINS_HTTP_INSTALL` (default off);
- an `install_enabled` flag in the list response;
- 19 API integration cases and 15 archive unit cases.

Documentation:
- ADR 0001;
- architecture, deployment and SDK doc updates.

Frontend:
- `/plugins` page with plugin cards;
- install, enable/disable and remove dialogs;
- raw-body support in `ApiClient`;
- mutation hooks;
- 12 component tests, one client test, and contract-test coverage of all five plugin calls.

**Resulting state.** An admin sees every installed plugin with its state, version, engine/SDK constraints, dependencies, provided node types and failure reason. They can:
- enable, disable and remove plugins, with confirmation and backend errors shown verbatim;
- force a removal that workflows block, behind a second confirmation;
- upload a `.zip` package when the operator has turned uploads on; otherwise the page points to the CLI.

A trust warning is always visible. Every decision (compatibility, validity, in-use) is made by the backend.

All roadmap acceptance criteria are met (§14). Limitations remain:
- multi-process propagation needs a restart;
- no Playwright spec for plugins;
- the frontend README section for this feature is missing (§9, Finding 5).

**Phase Status:** COMPLETE WITH LIMITATIONS

## 2. Phase Objective

The roadmap scope is: Installed Plugins, Plugin Details, Version, Compatibility, Dependencies, Enabled/Disabled, Install, Remove, Validation Errors, "Kein Marketplace".

The roadmap's OpenCode prompt adds "Keine eigene Plugin-Validierungslogik als Ersatz für das Backend" (no frontend plugin validation as a substitute for the backend).

Acceptance criteria (§26):
1. Plugin Lifecycle wird korrekt dargestellt.
2. Kompatibilitätsprobleme werden verständlich angezeigt.
3. Keine Plugin-Installation ohne Backend-Validierung.

Explicitly **not** in scope:
- a marketplace or plugin discovery from remote sources;
- frontend-side compatibility evaluation;
- Settings/Authentication (Phase 21);
- E2E coverage (Phase 26).

## 3. Scope Implemented

### Architecture
- ADR 0001 (`docs/adr/0001-plugin-management-over-http.md`) moves the plugin lifecycle onto HTTP for admins:
  - enable, disable and remove are always available;
  - upload install requires an explicit operator opt-in;
  - unpacking is defined as a trust boundary.
- This is the first file in `docs/adr/`; the directory is new.

### Backend/API (`backend/src/akasha/api/plugins.py`)
- `POST /api/v1/plugins` (`application/zip` body, 201 → `PluginOut`):
  - refused with 403 `PLUGIN_INSTALL_DISABLED` before the body is read, unless the plugin directory is configured and `plugins_http_install` is true;
  - 415 for other media types;
  - 413 via `read_limited_body` over 16 MiB;
  - otherwise unpacks into a `tempfile.TemporaryDirectory` and calls the existing `PluginRegistry.install`.
- `POST /api/v1/plugins/{plugin_id}/enable` and `.../disable` → `PluginOut`, calling `PluginRegistry.enable` / `disable`.
- `DELETE /api/v1/plugins/{plugin_id}?force=` → `RemovePluginResponse {plugin_id, affected_workflows}`, calling the existing `application.plugins.remove_plugin` (the same use case as `akasha plugin remove`).
- `PluginList` gained `install_enabled: bool`.
- New error class `PluginInstallDisabledError` (code `PLUGIN_INSTALL_DISABLED`, HTTP 403).
- Helpers:
  - `_registry` raises `ConfigurationError` when there is no plugin directory;
  - `_workflows` raises `ConfigurationError` when there is no database (removal needs the workflow-usage check).
- The router is still mounted behind `require_admin` (`api/routes.py`, unchanged).

### Backend/Infrastructure (`backend/src/akasha/plugins/archive.py`, new)
`unpack_package(data, target) -> Path` unpacks a ZIP. It enforces:
- at most 2000 entries;
- at most 64 MiB declared unpacked size, plus a running byte budget with reads capped at budget+1, so declared sizes are not trusted;
- per entry, it rejects:
  - empty names, backslashes, absolute paths, `.`/`..`/empty path segments and `X:` drive prefixes;
  - non-regular files (symlinks, devices, via `external_attr`);
  - encrypted entries;
  - compression other than stored/deflated;
  - duplicates.
- Corrupt ZIPs (`BadZipFile`, `LargeZipFile`, `zlib.error`, `EOFError`, `ValueError`, `NotImplementedError`) and file/directory clashes (`FileExistsError`, `NotADirectoryError`) become `PluginPackageError` (422 `PLUGIN_PACKAGE_INVALID`).
- It accepts the package at the archive root or inside exactly one top-level directory.
- It never imports code; manifest and compatibility validation stay in `PluginRegistry.install` / `read_manifest`.

### Configuration (`backend/src/akasha/config.py`)
- `plugins_http_install: bool = False` (env `AKASHA_PLUGINS_HTTP_INSTALL`), with a comment stating the security trade-off.

### Frontend/API layer
- **`src/api/client.ts`:** `RequestOptions.rawBody?: { data: Blob; contentType: string }` is sent as-is with its content type and takes precedence over `body` (JSON).
- **`src/api/plugins.ts`:**
  - `pluginListSchema` gained `install_enabled`;
  - new `removePluginResponseSchema` and `MAX_PLUGIN_PACKAGE_BYTES` (16 MiB, mirrors the backend);
  - new functions `installPlugin` (raw zip, 120 s timeout), `enablePlugin`, `disablePlugin` and `removePlugin` (with `force`).
- **`src/api/mutations.ts`:** `useInstallPlugin`, `useSetPluginEnabled` and `useRemovePlugin`. Each invalidates `queryKeys.plugins()` **and** `queryKeys.nodes()`, because plugin changes add or remove node types. No optimistic updates.

### Frontend/UI (`src/features/plugins/`, new)
**`PluginsPage.tsx`:**
- header with "Install plugin", shown only when `install_enabled`;
- a permanent warning Alert, "Plugins run with full access";
- an info Alert "Uploads are off" with the CLI command when uploads are disabled;
- an empty state;
- a `<ul aria-label="Installed plugins">` of `Card`s. Each card shows:
  - name (or id when there is no metadata), id, `v<version>`;
  - a state Badge with a dot and a text label, so state is not shown by color alone;
  - description (`whitespace-pre-line`);
  - a `<dl>` with engine compatibility, SDK compatibility, dependencies (kind, name, constraint) and provided node types;
  - for `failed`, a danger Alert with the stable error code and a fixed explanation per code (`PLUGIN_MANIFEST_INVALID`, `PLUGIN_INCOMPATIBLE`, `PLUGIN_LOAD_FAILED`).
- Actions:
  - Disable (enabled);
  - Enable, or "Retry enable" for failed (only when metadata exists, because the backend refuses enable without metadata);
  - Remove.
- On 403 the page shows "Admin access required".

**`PluginDialogs.tsx`:**
- **`InstallPluginDialog`:**
  - native `<input type="file" accept=".zip,application/zip">`;
  - a client-side size check (UX only);
  - a required "I trust the source of this package" checkbox;
  - a danger-styled "Install and run plugin code" button;
  - the dialog is not dismissible while pending.
- **`SetEnabledDialog`:** a ConfirmDialog. Enable states the trust notice; disable states that workflows using the node types stop validating, publishing and running. Both state that dedicated workers see the change after a restart.
- **`RemovePluginDialog`:**
  - an irreversible ConfirmDialog;
  - on 409 `PLUGIN_IN_USE` with `details.workflows`, it lists the workflows as links and relabels the confirm button "Remove anyway", which resends with `force=true`;
  - the success toast reports how many workflows were affected.
- **`PluginError`:** the backend message plus Zod-parsed `details` (`issues`, `fields`, `reason`, `path`, `dependents`, `workflows`), all as React text.

**`label.ts`:** `pluginLabel(plugin)`.

**Routing:** `src/app/routes.tsx` routes `/plugins` to the lazy `PluginsPage` and removes it from the placeholder list.

### Testing
See §12.

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/api/plugins.py` | Modified | Lifecycle routes, `install_enabled`, `RemovePluginResponse`, `PluginInstallDisabledError` |
| `backend/src/akasha/plugins/archive.py` | Created | Safe upload unpacking (trust boundary) |
| `backend/src/akasha/config.py` | Modified | `plugins_http_install` setting |
| `backend/tests/integration/test_plugins_api.py` | Modified (largely rewritten; the 3 previous tests kept, adapted to `install_enabled`) | HTTP lifecycle tests, real PostgreSQL |
| `backend/tests/test_plugin_archive.py` | Created | Unpacker unit tests |
| `frontend/src/api/openapi.json` | Modified (generated) | Regenerated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1`; not hand-edited |
| `frontend/src/api/client.ts` | Modified | `rawBody` option |
| `frontend/src/api/client.node.test.ts` | Modified | Raw-body test |
| `frontend/src/api/plugins.ts` | Modified | Contract + service functions |
| `frontend/src/api/contract.node.test.ts` | Modified | Registers the four new service functions |
| `frontend/src/api/mutations.ts` | Modified | Plugin mutation hooks |
| `frontend/src/app/routes.tsx` | Modified | `/plugins` route |
| `frontend/src/app/RouteErrorBoundary.test.tsx` | Modified | Its crash fixture used the `/plugins` placeholder; moved to `/settings` (still a placeholder) |
| `frontend/src/features/plugins/PluginsPage.tsx` | Created | Page |
| `frontend/src/features/plugins/PluginDialogs.tsx` | Created | Dialogs, error details |
| `frontend/src/features/plugins/label.ts` | Created | Shared label helper (separate file for react-refresh lint) |
| `frontend/src/features/plugins/PluginsPage.test.tsx` | Created | 12 component tests |
| `docs/adr/0001-plugin-management-over-http.md` | Created (new directory `docs/adr/`) | Architectural decision |
| `docs/architecture.md` | Modified | §22 endpoint table + paragraph, §36 lifecycle text, §58 CLI note |
| `docs/deployment.md` | Modified | §10 Plugins: UI management, opt-in upload, worker restart |
| `docs/sdk.md` | Modified | §9 lifecycle: HTTP/UI path, ZIP layout |
| `docs/reports/frontend/phase-20-plugin-management-ui.md` | Created | This report |

There are no migrations, no dependency changes and no Docker/Compose changes.

## 5. Architecture Changes

### Decision 1: Plugin enable/disable/remove over HTTP (admin only)
- **Context:** Architecture §22/§36 kept the whole lifecycle CLI-only. One reason given was that, before authentication (backend Phase 20), an HTTP install would be remote code execution. The phase requires the lifecycle in the UI, and the prompt requires implementing missing backend APIs. Authentication and `require_admin` now exist.
- **Chosen approach:** Three routes delegate to existing code: `PluginRegistry.enable/disable` and `application.plugins.remove_plugin`. No plugin rule was added to the API layer.
- **Alternatives considered:** Keep the UI read-only (fails the phase scope).
- **Reason:** These operations only run code an operator already installed. Removal keeps the §143 safe-blocking semantics via the shared use case.
- **Consequences:** The CLI and the API share the behaviour. Architecture text was updated; ADR 0001 records it.

### Decision 2: Upload install exists but is opt-in (`AKASHA_PLUGINS_HTTP_INSTALL`, default false)
- **Context:** An uploaded package is code the engine imports in-process (§65 trusted code). Enabling uploads makes an admin account equivalent to host code execution.
- **Chosen approach:** `POST /api/v1/plugins` refuses with 403 `PLUGIN_INSTALL_DISABLED` before reading the body unless the setting is on. The list response exposes `install_enabled`, so the UI either offers the upload or shows the CLI command.
- **Alternatives considered:**
  - always-on for admins (rejected: changes the trust model of every installation silently);
  - install from a server-side path (rejected: adds nothing over the CLI and exposes filesystem paths over the API, AGENTS §8);
  - no HTTP install at all (rejected: the prompt asks for installation where the backend supports it).
- **Reason:** It keeps the previous security posture by default and makes the trade-off an explicit operator decision.
- **Consequences:** A deployment that enables it must treat admin credentials as host credentials. Documented in ADR 0001, `config.py`, `deployment.md` §10 and the UI.

### Decision 3: Upload unpacking is its own trust boundary (`akasha.plugins.archive`)
- **Context:** `PluginRegistry.install` expects a directory and validates manifest, compatibility, copy (no symlinks) and load. A ZIP adds path-traversal and decompression-bomb risks before that point.
- **Chosen approach:** A dedicated unpacker that writes only regular files below a temp directory, with entry/size caps (§3). It follows the existing workflow-package reader (`application/workflow_package.py::_read_entries`), but with plugin-sized limits.
- **Alternatives considered:** Reusing `_read_entries` directly (rejected: hard-wired to workflow-package entry names and limits).
- **Consequences:** Upgrades remain remove + install. Uploads larger than 16 MiB need the CLI.

The doc updates are architecture §22, §36 and §58, plus the new ADR.

## 6. Domain / Data Model Changes

There are no domain entity, database, migration or state-machine changes. The plugin state machine (`enabled`/`disabled`/`failed`) and the `state.json` persistence are unchanged; the API only exposes the existing transitions.

New API models: `PluginList.install_enabled`, `RemovePluginResponse`.

New frontend schemas: `pluginListSchema.install_enabled`, `removePluginResponseSchema`.

## 7. API / Contract Changes

| Endpoint | Purpose | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/v1/plugins` (changed) | List | — | `{items: PluginOut[], install_enabled: bool}` | 401, 403 |
| `POST /api/v1/plugins` (new) | Upload install | `application/zip`, ≤16 MiB | 201 `PluginOut` | 401, 403 `PERMISSION_DENIED`/`PLUGIN_INSTALL_DISABLED`, 409 `PLUGIN_ALREADY_INSTALLED`/`PLUGIN_INCOMPATIBLE` (`details.issues`), 413 `PAYLOAD_TOO_LARGE`, 415 `UNSUPPORTED_MEDIA_TYPE`, 422 `PLUGIN_PACKAGE_INVALID` (`details.path`)/`PLUGIN_MANIFEST_INVALID` (`details.fields`/`reason`)/`PLUGIN_LOAD_FAILED`, 500 `CONFIGURATION_ERROR` |
| `POST /api/v1/plugins/{id}/enable` (new) | Activate | — | `PluginOut` | 404 `PLUGIN_NOT_FOUND`, 409 `INVALID_STATE_TRANSITION`/`PLUGIN_INCOMPATIBLE`, 422 `PLUGIN_LOAD_FAILED`, 500 `CONFIGURATION_ERROR` |
| `POST /api/v1/plugins/{id}/disable` (new) | Deactivate | — | `PluginOut` | 404, 409 `INVALID_STATE_TRANSITION`/`PLUGIN_IN_USE` (`details.dependents`) |
| `DELETE /api/v1/plugins/{id}?force=bool` (new) | Remove | — | `{plugin_id, affected_workflows: string[]}` | 404, 409 `PLUGIN_IN_USE` (`details.workflows`), 500 `CONFIGURATION_ERROR` (no database) |

**Compatibility.** Adding `install_enabled` is additive. Old clients that ignore unknown fields keep working. The frontend Zod schema now requires the field, which is consistent with the regenerated snapshot.

**Contract verification.** `backend/tests/test_openapi_contract.py` passes against the regenerated `frontend/src/api/openapi.json`, and `frontend/src/api/contract.node.test.ts` checks all five plugin service functions against it.

## 8. Implementation Details

- **Order of checks in `install_plugin`:**
  1. `_install_enabled`;
  2. media type;
  3. capped body read;
  4. `_registry`;
  5. unpack into the temp dir;
  6. `registry.install`.

  The temp dir is always removed (context manager). `registry.install` copies the package into `<plugins_dir>/<id>` via its own staging/rename/rollback, so a failed install leaves nothing behind. The tests assert the plugin directory stays empty after every refused upload.
- **Concurrency.** Lifecycle handlers are `async` but call the synchronous registry on the event loop. This blocks the loop for the duration of unpack, copy and import. It also serializes lifecycle calls within the process, which the registry relies on because it has no lock. This is marked with a `ponytail:` comment (`api/plugins.py:172`).
- **Process scope.** Changes affect the API process and its in-process worker, which share the registry. Dedicated `akasha worker` processes and other API replicas load plugins only at startup (unchanged behaviour, §36). The UI and docs state this.
- **Frontend server state:**
  - the list uses `pluginQueries.list`, which has `staleTime` `INSTALLATION_STALE_TIME_MS` (5 min, pre-existing);
  - mutations invalidate plugins and nodes and stay pending until the refetch completes (existing `useApiMutation`);
  - query `retry` is the global policy (5xx other than transient are not retried; mutations never retry).
- **Removal force flow.** The force flag is derived from the last mutation error (`code === 'PLUGIN_IN_USE'` and `details.workflows` is an array). The user must click a second time; nothing is forced automatically.
- **Untrusted metadata.** All metadata and error details are React text nodes. No `dangerouslySetInnerHTML`. Workflow ids in errors become `TextLink`s to `/workflows/:id` (React Router `Link`; ids are path segments, not URLs from the payload).
- **Logging.** `PluginRegistry` already logs `plugin_installed/enabled/disabled/removed` with `plugin_id` and version. Request ids come from the existing middleware. No new logging was added.

## 9. Findings

### Finding 1 — The architecture forbade the requested capability
- **Impact:** HIGH
- **Resolution:** FIXED (ADR 0001, docs updated)
- **Notes:** §22 "Not over HTTP in V1: plugin management" and §36 "No HTTP install endpoint exists" conflicted with the phase. This was resolved by an explicit ADR rather than by a silent change.

### Finding 2 — The frontend has no session token wiring yet
- **Impact:** LOW for this phase
- **Resolution:** DEFERRED (Phase 21)
- **Notes:** `ApiClient` is constructed without `getToken`, so a browser session cannot call admin endpoints against a real backend. For the manual check, a temporary Vite config (since deleted) injected an admin bearer token through the dev proxy. The component tests are unaffected (stubbed backend).

### Finding 3 — Plugin list staleness comment is now inaccurate
- **Impact:** LOW
- **Resolution:** OPEN
- **Notes:** `src/api/queries.ts:86` and `frontend/README.md` (the "Freshness" bullet) say the plugin list "change[s] only when the backend restarts". Changes made through this UI invalidate the query. Changes made by the CLI or another admin can stay unseen for up to 5 minutes (staleTime). Behaviour is acceptable; the wording should be updated.

### Finding 4 — `Card` sections have no accessible name
- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:** `components/ui/Card.tsx` renders `<section>` without `aria-labelledby`, so cards are not `region` landmarks. The plugin list relies on `<ul>/<li>` + `<h2>` instead, and tests locate cards by heading. The shared component was not changed (out of scope).

### Finding 5 — Frontend README has no "Plugins" section
- **Impact:** LOW
- **Resolution:** OPEN
- **Notes:** Previous feature phases (Credentials, Triggers, Reports) each added a README section. This phase updated `docs/` but not `frontend/README.md`.

### Finding 6 — Query client retries 503
- **Impact:** LOW (test design)
- **Resolution:** FIXED in the test
- **Notes:** A 503 load failure is retried by `shouldRetryQuery`, so the error state never appeared within the test timeout. The API-failure test uses 500, which is not retried.

## 10. Bugs / Problems Found

All of these were introduced during this phase and fixed before completion. No pre-existing product bugs were found.

1. **Archive:** a clash of a file and a directory of the same name (`b` then `b/c`) would raise `FileExistsError`/`NotADirectoryError`, which would have become an unhandled 500.
   - *Root cause:* the except list covered only ZIP errors.
   - *Fix:* both exceptions are mapped to `PluginPackageError`.
   - *Regression test:* `test_rejects_duplicates_and_file_directory_clashes`.
2. **Dialog markup:** error details were first wrapped in `<span>` inside the ConfirmDialog description, which contains block `<div>`/`<ul>` content (invalid nesting).
   - *Fix:* replaced with `<div className="mt-3">`.
   - Found in review; not covered by a dedicated test.
3. **ESLint:** `react-refresh/only-export-components` fired because `PluginDialogs.tsx` exported `pluginLabel`. `restrict-template-expressions` fired on a number in a template.
   - *Fix:* moved the helper to `label.ts`; wrapped the number in `String(...)`.
4. **Ruff RUF043:** an unescaped regex in `pytest.raises(match=...)`.
   - *Fix:* raw string with an escaped `.`.
5. **Test expectation errors:**
   - the 415 code was expected as `HTTP_ERROR`; the app maps 415 to `UNSUPPORTED_MEDIA_TYPE`;
   - the frontend tests initially queried cards by `region` role (Finding 4);
   - a 503 was used for the failure test (Finding 6).

   All were corrected to match real behaviour; no product code was weakened.
6. **Pre-existing test fixture affected:** `RouteErrorBoundary.test.tsx` mocked the placeholder page for `/plugins`. The fixture was moved to `/settings`; the test intent is unchanged.

**Infrastructure/environment:** none.

## 11. Fixes Applied

See §10 items 1–4. Verification for each: the re-run gates in §13 and the named tests in §12.

## 12. Testing & Verification

### Unit Tests
`backend/tests/test_plugin_archive.py`: 8 functions, 15 cases. All passed. They cover:
- root and single-top-level layouts;
- missing manifest (two roots, no manifest);
- 7 unsafe paths (`../`, nested `..`, absolute, backslash, `C:`, `./`, `//`), each asserting nothing was written;
- symlink entries;
- duplicate entries;
- file/directory clash;
- a truncated ZIP;
- entry-count and unpacked-size caps (monkeypatched limits).

**Not tested:** a central directory with a lying declared size. A draft test patching ZIP bytes was dropped as fragile; the capped read is the same pattern as the tested workflow-package reader.

### Component Tests
`frontend/src/features/plugins/PluginsPage.test.tsx`: 12 tests. All passed. They cover:
- list with version, compatibility, dependencies, node types and the three states, including a metadata-less FAILED plugin without an enable button;
- metadata containing `<script>`/`<img onerror>` rendered as text, with no `img`/`script` elements created;
- uploads-off notice and no install button;
- install with the trust checkbox gating submit, a `File` body and `content-type: application/zip`;
- size-limit block with no upload request;
- invalid manifest showing `details.fields`;
- incompatible package showing `details.issues`;
- disable → enable round trip through the backend;
- disable refused (`PLUGIN_IN_USE` dependents) with state unchanged;
- remove refused by workflows → workflow links → "Remove anyway" sends `force=true` → the list becomes empty;
- load failure (500) → Retry;
- 403 → "Admin access required".

`src/api/client.node.test.ts`: a raw-body test (bytes and content type over a real Node HTTP server). Passed.

### Integration / API Tests
`backend/tests/integration/test_plugins_api.py`: 14 functions, 19 cases, through the real ASGI stack with a PostgreSQL Testcontainer. All passed. They cover:
- list with an enabled and a FAILED plugin;
- no plugin dir (empty list, upload 403, enable 500 `CONFIGURATION_ERROR`);
- upload install (top-level dir layout; persisted across a new registry);
- root layout;
- upload disabled by default;
- not-a-zip 422;
- wrong media type 415;
- invalid manifest 422 with `fields` and the value not echoed;
- incompatible 409 with `issues`;
- double install 409;
- disable/enable round trip including `INVALID_STATE_TRANSITION` and 404;
- disable blocked by a dependent plugin;
- removal blocked by a workflow dependency, then forced, then 404;
- unused removal;
- all 5 operations × (anonymous 401, member 403) with state unchanged.

**Not tested:** removal of a FAILED plugin without metadata over HTTP. It is the same `remove_plugin` path; `node_types` is empty.

### Contract Tests
- `backend/tests/test_openapi_contract.py`: passed after regeneration.
- `frontend/src/api/contract.node.test.ts`: passed, including the 4 new functions; the "every service function is covered" check passes.

### Infrastructure Tests
PostgreSQL via Testcontainers (Docker available). The full backend suite passed: 2036 passed, 6 skipped. The skips are `tests/deployment/test_compose_lifecycle.py` (needs `AKASHA_TESTS_DEPLOYMENT=1`) and 5 OpenRouter external smoke tests (need an API key). Neither is related to this phase.

### E2E Tests
- `npm run test:e2e`: 23 passed (existing specs).
- No plugin E2E spec was added (§19).

### Manual verification
Against a real backend (`akasha serve`, PostgreSQL container, `AKASHA_PLUGINS_DIR` with a broken package, `AKASHA_PLUGINS_HTTP_INSTALL=true`):
- the page rendered the FAILED plugin;
- `curl` upload of `text_tools.zip` → 201;
- the UI disable dialog → state Disabled with a toast;
- at a 375 px viewport the install dialog renders and `scrollWidth <= innerWidth`.

The UI file picker could not be driven by the browser tool; the UI upload is covered by the component test only. The preview container, servers and temporary config were removed afterwards.

### Security Tests
Covered by the cases above:
- admin authorization on every route;
- upload opt-in;
- path traversal, symlinks, size/entry caps and malformed archives;
- manifest values not echoed;
- XSS via plugin metadata.

### Accessibility Tests
There is no automated a11y tool run. What is in place:
- labelled file input, checkbox and buttons (`aria-label="Disable <name>"` etc.);
- state conveyed by text + dot, not color alone;
- dialogs on the native `<dialog>` with initial focus on Cancel (ConfirmDialog);
- the `ul`/`h2` structure.

Tests query by role and label.

### Performance Tests
None performed.

## 13. Quality Gates

Final run on 2026-10-04, after all changes:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (backend) | PASS | `uv run ruff format --check .`: 210 files already formatted |
| Formatter (frontend) | PASS | `npm run format:check` |
| Linter (backend) | PASS | `uv run ruff check .`. Earlier failure: RUF043 → fixed |
| Linter (frontend) | PASS | `npm run lint`. Earlier failures: 2 errors (§10.3) → fixed |
| Type Checker | PASS | `uv run mypy .` (205 files); `npm run typecheck` (`tsc -b`) |
| Unit/Component Tests (frontend) | PASS | Full `npm test`: 35 files, 446 tests. Re-run of `src/features/plugins src/api src/app`: 134/134 |
| Backend tests | PASS | Full `uv run pytest`: 2036 passed, 6 skipped (5 min). Re-run of plugin/contract/architecture files: 87 passed |
| Integration/API Tests | PASS | Included above (Testcontainers PostgreSQL) |
| E2E Tests | PASS | `npm run test:e2e`: 23 passed. No new spec |
| Build | PASS | `npm run build` |
| Security Checks | PASS (tests) | No separate scanner/dependency audit run |
| Accessibility | N/A (no automated audit) | Manual review only (§12) |
| Contract | PASS | OpenAPI snapshot + frontend contract test |

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Plugin Lifecycle wird korrekt dargestellt | PASS | States enabled/disabled/failed come from the backend list; every transition goes through backend endpoints and refetches (no optimistic updates). Tests: list, disable→enable round trip, refused disable keeps the state, removal updates the list. Manual check against a real backend. |
| Kompatibilitätsprobleme werden verständlich angezeigt | PASS | Install/enable errors show the backend message plus `details.issues` under "Compatibility issues". FAILED plugins show the code + an explanation per code. Dependencies and engine/SDK constraints are displayed. Tests: incompatible package, failed plugin card. |
| Keine Plugin-Installation ohne Backend-Validierung | PASS | The only install path is `POST /plugins` → `unpack_package` → `PluginRegistry.install` (manifest + compatibility before import). The frontend size check is UX only. Backend tests: invalid manifest, incompatible, malformed archive, disabled-by-default. |
| (Prompt) Installed plugins, metadata, version, dependencies, capabilities | PASS | "Capabilities" = provided node types (V1 plugins provide node types only, manifest §9 of `sdk.md`). Covered by the list test. |
| (Prompt) Trust implications communicated | PASS | Permanent warning, install dialog warning + required trust checkbox, enable confirmation text. |
| (Prompt) Metadata not trusted HTML | PASS | XSS test. |
| (Prompt) Missing backend APIs implemented, tested, documented | PASS | §7, §12, ADR 0001 + docs. |
| (Prompt) Test list/install/invalid/incompatible/enable-disable/remove/API failure/authorization | PASS | Each has backend and/or frontend tests (§12). |

None of the remaining limitations blocks completion.

## 15. Security Considerations

- **Authorization:** All plugin routes stay behind `require_admin`; this is tested for each route (401 anonymous / 403 member, and state unchanged). The frontend hiding of actions is UX only.
- **Plugin trust / RCE:** Upload install is disabled by default. Enabling it makes admin accounts host-code-capable (ADR 0001). Enabling an installed plugin also runs its code; the confirmation text says so.
- **Path traversal / archive attacks:** `archive.py` rules (§3), plus the existing `_copy_package` symlink rejection and manifest checks.
- **Resource limits:** 16 MiB request cap (streamed), 64 MiB unpacked, 2000 entries.
- **Error disclosure:**
  - manifest errors carry field locations only (pre-existing `read_manifest` behaviour; tested);
  - archive errors echo the offending entry name, truncated to 300 characters;
  - no filesystem paths of the server are returned.
- **XSS:** Metadata and error details are rendered as text (tested). No `dangerouslySetInnerHTML`.
- **CSRF:** Unchanged (bearer-token API).
- **Secrets:** None involved.

## 16. Performance Considerations

- Install, enable and disable block the event loop for the duration of unpack, copy and import (§8). This is acceptable for operator-frequency actions; it is not measured.
- The upload is buffered in memory (≤16 MiB) and written to a temp dir.
- The plugin list is unpaginated (pre-existing `ponytail:` note; bounded by installed packages).
- Each plugin mutation also refetches the node catalog.
- No measurements were taken.

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/adr/0001-plugin-management-over-http.md` | New decision | Context, decision (4 points), alternatives, consequences |
| `docs/architecture.md` | Lifted the "not over HTTP" rule | §22 endpoint table row + replacement paragraph; §36 lifecycle bullet; §58 note that HTTP removal uses `remove_plugin` |
| `docs/deployment.md` | Operator impact | §10: UI management, `AKASHA_PLUGINS_HTTP_INSTALL` risk, `docker compose restart worker` |
| `docs/sdk.md` | Plugin authors | §9: HTTP/UI lifecycle, ZIP layout |

Should be updated (open):
- `frontend/README.md`: add a "Plugins" section, and correct the "change only on backend restart" freshness wording (Findings 3 and 5);
- the `src/api/queries.ts:86` comment.

## 18. Dependencies / Infrastructure Changes

- No new or changed dependencies (backend `pyproject.toml`/`uv.lock` and frontend `package.json` are untouched). `zipfile`/`tempfile` are stdlib.
- New environment variable: `AKASHA_PLUGINS_HTTP_INSTALL` (bool, default false).
- No Docker, Compose, database or Redis changes. `docker-compose.yml` does not set the new variable, so it stays off.

## 19. Deferred Work

| What | Why | Target | Affects completion |
|---|---|---|---|
| Playwright spec for plugin management | E2E suite is its own phase | Phase 26 | No |
| Login/session wiring in the frontend | Auth phase | Phase 21 | No (component-tested; manual check used a proxy token) |
| Plugin documentation viewer (`documentation` file from the manifest) | Not exposed by the API (`PluginOut` has no docs field); not in the roadmap scope list | Unplanned | No |
| In-place upgrade | Registry supports remove + install only | Unplanned | No |
| Propagating lifecycle changes to other processes without restart | Requires a cross-process mechanism (§36) | Unplanned | No |
| Thread offload + lock for lifecycle calls | Only if installs become slow (`ponytail:` note) | When measured | No |

## 20. Known Limitations

- Dedicated workers and other API replicas see changes only after a restart. Until then, a plugin disabled via the UI can still be used by a dedicated worker.
- Upload size is capped at 16 MiB; larger packages need the CLI.
- With uploads off, the UI cannot install; this is by design.
- Changes made outside this UI (CLI, another admin) may take up to 5 minutes to appear (query `staleTime`), unless the page is reloaded after the cache expires.
- The lying-declared-size archive case is not directly tested (§12).
- Removing a FAILED plugin over HTTP is not directly tested (§12).
- No automated accessibility audit.

## 21. Open Issues

| Issue | Impact | Severity | Area | Next action | Target |
|---|---|---|---|---|---|
| `frontend/README.md` lacks a Plugins section; freshness wording stale | Docs inaccuracy | Low | Docs | Add the section, fix the wording and the `queries.ts:86` comment | Next docs touch / Phase 22 |
| Multi-process plugin state divergence until restart | Operator confusion | Medium | Runtime/ops | Documented; consider a reload signal if multi-process deployments become common | Unplanned |

## 22. Architectural / Product Decisions

- HTTP plugin lifecycle exists. Upload install is **opt-in** and must stay off by default; do not enable it in default Compose or env templates without revisiting ADR 0001.
- `install_enabled` is the frontend's only signal for offering upload. The frontend must not infer it.
- The API layer contains no plugin rules: compatibility, in-use and dependents checks live in `PluginRegistry` and `application.plugins`. CLI and HTTP must keep sharing them.
- Forced removal requires a refused attempt first in the UI (two explicit confirmations).
- Plugin metadata is untrusted display text everywhere.
- Plugin mutations invalidate the node catalog as well as the plugin list.

## 23. Repository / Git State

- **Branch:** `main`. Base commit `50e8bb4`. No commits made in this phase.
- **Working tree:** uncommitted, nothing staged.
  - Modified (14):
    - `backend/src/akasha/api/plugins.py`, `backend/src/akasha/config.py`, `backend/tests/integration/test_plugins_api.py`;
    - `docs/architecture.md`, `docs/deployment.md`, `docs/sdk.md`;
    - `frontend/src/api/{client.node.test.ts, client.ts, contract.node.test.ts, mutations.ts, openapi.json, plugins.ts}`;
    - `frontend/src/app/{RouteErrorBoundary.test.tsx, routes.tsx}`.
  - Untracked:
    - `backend/src/akasha/plugins/archive.py`, `backend/tests/test_plugin_archive.py`;
    - `docs/adr/`;
    - `frontend/src/features/plugins/`;
    - this report.
- **Unrelated changes:** none detected. The temporary `frontend/vite.preview-local.config.ts` used for the manual check was deleted, and the preview PostgreSQL container was removed.

## 24. Phase Completion Assessment

### Implemented
- Backend: lifecycle endpoints, safe ZIP unpacker, opt-in upload setting, `install_enabled`.
- Frontend: `/plugins` page, install/enable/disable/remove dialogs with backend error details, `rawBody` client support, mutation hooks.
- ADR 0001 and doc updates.

### Verified
- 19 API integration cases, 15 archive unit cases, 12 component tests, client and contract tests.
- Full backend (2036 passed) and frontend (446 passed) suites; e2e (23 passed).
- Lint, format, typecheck and build, all passing.
- Manual check against a real backend.

### Not Implemented
- Marketplace (out of scope).
- Documentation viewer.
- In-place upgrade.

### Deferred
- Plugin E2E spec (Phase 26).
- Frontend auth wiring (Phase 21).

### Open
- README section and freshness wording.
- Multi-process propagation.

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

- **Next phase:** 21, Settings + Authentication.
- **Real-backend dependency:** the Plugins page (like Credentials) needs an admin session at runtime. Once `ApiClient` receives `getToken`, the page works against the real backend without changes. 401 → handled by the existing error kinds; 403 → "Admin access required" state.
- **Available:**
  - `ApiClient` `rawBody` for any binary upload (e.g. a future workflow-package import UI);
  - `useInstallPlugin`, `useSetPluginEnabled`, `useRemovePlugin`;
  - `PluginList.install_enabled`.
- **Must not change casually:**
  - the default-off `AKASHA_PLUGINS_HTTP_INSTALL`, and the order "check opt-in before reading the body";
  - `archive.py` path and type checks;
  - text-only rendering of plugin metadata (XSS test);
  - the shared `remove_plugin` use case for CLI and API.
- **Contract test:** register any new service function in `contract.node.test.ts`; regenerate `openapi.json` with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py` after backend contract changes.
- **Known constraint:** lifecycle changes are per-process until restart.
