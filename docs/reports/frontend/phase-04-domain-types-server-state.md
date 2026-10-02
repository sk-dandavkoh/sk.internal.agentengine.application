# Phase 04 — Domain Types + Server State

## 1. Executive Summary

**Goal.** Phase 04 had to create a typed frontend representation of the backend domain, and to integrate TanStack Query as the one server-state mechanism. Queries, mutations and cache invalidation had to be structured and explicitly defined (`docs/roadmap-frontend.md` §10).

**What was implemented.** Seven new modules in `frontend/src/api/`:

- Five service modules: `projects.ts`, `executions.ts`, `reports.ts`, `nodes.ts`, `credentials.ts`.
- `plugins.ts`, a sixth service module.
- `schemas.ts`, shared building blocks.

Each service module defines the domain types as Zod schemas, with the TypeScript types inferred from them, plus one service function per backend endpoint. That is 25 new service functions, 28 including the three auth functions from Phase 03.

Two further modules form the server-state layer:

- `queries.ts`: all query keys (`queryKeys`) and query option factories per read endpoint.
- `mutations.ts`: one hook per write endpoint, each with explicit cache invalidation.

The frontend needed two backend changes:

- A new read-only, admin-only endpoint `GET /api/v1/plugins`. No HTTP contract for plugins existed.
- Execution and node-execution `status` are now enums in the OpenAPI contract instead of `string`.

**Resulting state.**

- All frontend quality gates pass: format, lint, typecheck, 146/146 Vitest tests, build, and 11/11 Playwright tests.
- All backend gates pass: ruff check, ruff format, mypy, and 1852 passed / 6 skipped pytest tests. The skips are external smoke tests that need an API key.
- No screen uses the new modules yet. The production bundle is byte-identical to Phase 03 (`index-DtcuBqox.js`, 443.31 kB), because tree-shaking removes the unused modules.

**Acceptance criteria.** All four roadmap acceptance criteria are satisfied (§14). Three items from the roadmap's type list are not fully covered:

- **Log:** the backend has no log API.
- **NodeConfigSchema:** it is an untyped JSON Schema object.
- **Metric:** only the per-execution report metrics are covered, not Prometheus.

Five of the 13 mutation hooks and five of the 12 query factories have no behavior test. Their service calls are covered only by the contract test.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §10, "Phase 04 — Domain Types + Server State":

> Ziel: Eine typisierte Frontend-Repräsentation der Backend-Domäne schaffen.

**Required types:**

- Project, ProjectDraft, ProjectVersion
- Graph, NodeInstance, Edge
- NodeDefinition, NodePort, NodeConfigSchema
- Trigger
- Execution, NodeExecution
- Event, Log, Metric, Report
- Credential Metadata
- Plugin

TanStack Query, or an equivalent server-state solution, had to be integrated.

**The roadmap's implementation prompt specified:**

- Read the backend domain models and API contracts.
- Implement typed frontend domain models and server-state infrastructure.
- No complete screens.
- No future features.
- Backend contracts are the source of truth.
- Run tests, typecheck, lint and build.

**The session prompt added:**

- Validation errors as a type.
- Separation of server state, UI state and editor state.
- Tests for query behavior, cache invalidation, loading, error, stale state, mutation success and failure, and type/response validation.
- Missing backend functions must be implemented, tested and documented.

**Explicitly not part of this phase:**

- Screens (Phase 05 onward).
- Graph editor state (Phase 07).
- Live execution and polling (Phase 14).
- Credentials UI (Phase 17).
- Plugin management UI (Phase 20).
- Session and login (Phase 21).

---

## 3. Scope Implemented

### Frontend: domain types (Zod schemas plus inferred types)

**`schemas.ts`**

- `timestamp` (`z.iso.datetime({ offset: true })`).
- `jsonObject` / `JsonObject`: `z.record(z.string(), z.json())`.
- `pageInfoSchema` and `pageSchema(item)`.
- `PageParams` (`offset?`, `limit?`).

**`projects.ts`**

| Schema | Type |
|---|---|
| `projectStatusSchema` | `ProjectStatus` |
| `nodeInstanceSchema` | `NodeInstance` |
| `edgeSchema` | `Edge` |
| `graphSchema` | `Graph` |
| `triggerTypeSchema` | `TriggerType` |
| `triggerSchema` | `Trigger` |
| `dependencySchema` | `Dependency` |
| `projectDraftSchema` | `ProjectDraft` |
| `projectFileSchema` | `ProjectFile` |
| `projectSummarySchema` | `ProjectSummary` |
| `projectSchema` | `Project` |
| `projectVersionSummarySchema` | `ProjectVersionSummary` |
| `projectVersionSchema` | `ProjectVersion` |
| `validationCodeSchema` (17 codes) | `ValidationCode` |
| `validationIssueSchema` | `ValidationIssue` |
| `validationResultSchema` | `ValidationResult` |
| `projectPageSchema`, `projectVersionPageSchema` | page types |

It also defines the input types `CreateProjectInput` and `UpdateProjectInput`.

**`executions.ts`**

- `executionStatusSchema` (9 states) and `executionSchema` (`Execution`).
- `executionPageSchema`.
- `executionEventSchema` (`ExecutionEvent`) and `executionEventPageSchema`.

**`reports.ts`**

- `nodeExecutionStatusSchema` (7 states).
- `executionErrorSchema`, `tokenUsageSchema`, `costSchema`.
- `nodeExecutionSchema` (`NodeExecution`).
- `executionMetricsSchema` (`ExecutionMetrics`).
- `executionReportSchema` (`ExecutionReport`).

**`nodes.ts`**

- `nodePortSchema` (`NodePort`).
- `nodeDefinitionSchema` (`NodeDefinition`); `config_schema` is a `JsonObject`.
- `nodeCatalogSchema`.

**`credentials.ts`**

- `credentialKindSchema` and `credentialReferenceSchema` (`CredentialReference`).
- `credentialPageSchema`.
- `CreateCredentialInput` and `UpdateCredentialInput`.

**`plugins.ts`**

- `pluginStateSchema`, `pluginSchema` (`Plugin`), `pluginListSchema`.

### Frontend: service functions

All service functions take an `ApiClient` first, following the Phase 03 pattern.

| Module | Functions |
|---|---|
| `projects.ts` | `listProjects`, `getProject`, `createProject`, `updateProject`, `deleteProject`, `validateProject`, `publishProject`, `listProjectVersions`, `getProjectVersion` |
| `executions.ts` | `listProjectExecutions`, `getExecution`, `listExecutionEvents`, `cancelExecution`, `executeProjectVersion`, `invokeTrigger` |
| `reports.ts` | `getExecutionReport` |
| `nodes.ts` | `listNodeDefinitions` |
| `credentials.ts` | `listCredentials`, `getCredential`, `createCredential`, `updateCredential`, `deleteCredential` |
| `plugins.ts` | `listPlugins` |

### Frontend: server state

**`queries.ts`**

- `queryKeys`: the single definition of every key.
- `INSTALLATION_STALE_TIME_MS` (5 minutes).
- Query option factories built with TanStack's `queryOptions`:
  - `projectQueries`: `list`, `detail`, `versions`, `version`, `executions`
  - `executionQueries`: `detail`, `events`, `report`
  - `nodeQueries`: `catalog`
  - `pluginQueries`: `list`
  - `credentialQueries`: `list`, `detail`

**`mutations.ts`**

- A private helper `useApiMutation(mutate, affects, options)` and an `invalidate(...)` helper.
- 13 hooks:
  - Projects: `useCreateProject`, `useUpdateProject`, `useDeleteProject`, `useValidateProject`, `usePublishProject`.
  - Executions: `useExecuteProjectVersion`, `useInvokeTrigger`, `useCancelExecution`.
  - Credentials: `useCreateCredential`, `useUpdateCredential`, `useDeleteCredential`.

### Backend / API

- `backend/src/akasha/api/plugins.py` (new):
  - `PluginMetadataOut`, `PluginOut` (with `from_record`) and `PluginList`.
  - `create_plugins_router()` with `GET /plugins`.
- `backend/src/akasha/api/routes.py`: the router is mounted inside the protected router with `require_admin`. The access-policy docstring is updated.
- `backend/src/akasha/api/trigger_schemas.py`: `ExecutionDetail.status` changed from `str` to `ExecutionStatus`.
- `backend/src/akasha/application/reports.py`:
  - `ExecutionSection.status` changed from `str` to `ExecutionStatus`.
  - `NodeReport.status` changed from `str` to `NodeExecutionStatus`.

### Testing

- `frontend/src/api/serverState.test.tsx` (new, 12 tests).
- `frontend/src/api/contract.node.test.ts` (extended): 28 service calls, plus handling for optional and free-form request bodies.
- `backend/tests/integration/test_plugins_api.py` (new, 3 tests, PostgreSQL via Testcontainers).

---

## 4. Files Changed

No directories were created. All new files are in the existing `frontend/src/api/`, `backend/src/akasha/api/`, `backend/tests/integration/` and `docs/reports/frontend/`.

| File | Change | Reason |
|---|---|---|
| `frontend/src/api/schemas.ts` | Created (27 lines) | Shared schemas: timestamp, JSON object, pagination |
| `frontend/src/api/projects.ts` | Created (268) | Project, draft, graph, trigger, version and validation types; project service functions |
| `frontend/src/api/executions.ts` | Created (155) | Execution and event types; execution service functions |
| `frontend/src/api/reports.ts` | Created (127) | NodeExecution, report and metrics types; report service |
| `frontend/src/api/nodes.ts` | Created (41) | NodeDefinition and NodePort; catalog service |
| `frontend/src/api/credentials.ts` | Created (88) | CredentialReference; credential service functions |
| `frontend/src/api/plugins.ts` | Created (35) | Plugin type; plugin list service |
| `frontend/src/api/queries.ts` | Created (148) | Query keys, staleness policy, query option factories |
| `frontend/src/api/mutations.ts` | Created (193) | Mutation hooks with cache invalidation |
| `frontend/src/api/serverState.test.tsx` | Created (452) | Behavior tests for queries and mutations |
| `frontend/src/api/contract.node.test.ts` | Modified (+57/−2) | Covers all new service functions; body-comparison fixes (§11) |
| `frontend/src/api/openapi.json` | Modified (generated, +176/−6) | Regenerated: new `/api/v1/plugins`, status enums |
| `frontend/README.md` | Modified (+64) | Domain types, server state, state boundaries |
| `backend/src/akasha/api/plugins.py` | Created (71) | `GET /api/v1/plugins` |
| `backend/src/akasha/api/routes.py` | Modified | Mounts the plugins router (admin only); policy docstring |
| `backend/src/akasha/api/trigger_schemas.py` | Modified | `status: ExecutionStatus` |
| `backend/src/akasha/application/reports.py` | Modified | `status: ExecutionStatus` / `NodeExecutionStatus` in the report |
| `backend/tests/integration/test_plugins_api.py` | Created (100) | API tests for the plugins endpoint |
| `backend/README.md` | Modified (+1) | Plugins endpoint in the API table |
| `docs/architecture.md` | Modified | §22: endpoint list and the plugin-management note |
| `docs/reports/frontend/phase-04-domain-types-server-state.md` | Created | This report |

`openapi.json` was generated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py`, not edited by hand.

There are no migrations, no dependency changes and no configuration changes.

---

## 5. Architecture Changes

### 5.1 A read-only plugin listing over HTTP

**Decision.** Add `GET /api/v1/plugins`: admin only, read-only.

**Context.**

- The phase requires a frontend `Plugin` type sourced from backend contracts.
- Architecture §22 stated that plugin management is "not over HTTP in V1 … operator CLI only", so no contract existed.
- The session prompt requires implementing missing backend functions.

**Chosen approach.** One GET route. It maps the existing `PluginRegistry.list_plugins()` records into `PluginOut`:

- `plugin_id`
- `state`: `enabled`, `disabled` or `failed`
- `error`: a stable code
- `metadata`: null when the manifest is unreadable

Install, enable, disable and remove remain CLI-only. If no plugin directory is configured (`container.plugins is None`), the route returns `{"items": []}`.

**Alternatives considered.**

1. Leave `Plugin` undefined in the frontend and defer it to Phase 20. Rejected: the phase explicitly lists the type, and the prompt requires filling gaps.
2. Model the type on the CLI or manifest format without an endpoint. Rejected: that would be a type with no contract to verify it.
3. A management API. Rejected: it contradicts the documented trust model (§65), which says plugins are trusted code installed by operators.

**Reason.** Listing exposes no capability beyond what the operator already has, and the CLI already displays the same data.

**Consequences.**

- Phase 20 can show installed plugins and their failures.
- Management actions would still require an architecture change.
- Architecture §22 now documents the exception.

### 5.2 Domain types are the API schemas

**Decision.** There is no separate `types/` or `domain/` tree. Each domain type is `z.infer` of the Zod schema the API layer validates responses with.

**Context.**

- Roadmap §6 and frontend AGENTS §6 sketch `domain/`, `schemas/` and `types/` directories.
- Architecture §42 recommends OpenAPI-generated types.

**Chosen approach.** Continue the Phase 03 pattern from `auth.ts`: a hand-written Zod schema per contract, checked against `openapi.json` by `contract.node.test.ts`.

**Alternatives considered.**

1. Generated types (for example `openapi-typescript`). Rejected: a new dependency, and it gives no runtime validation, which frontend AGENTS §10 requires.
2. Separate interfaces plus schemas. Rejected: two definitions that can drift apart.

**Reason.** One definition yields both the compile-time type and the runtime check, and the contract test catches drift on either side.

**Consequences.** Every new endpoint needs:

- a schema in its service module, and
- an entry in the contract test's call list (the coverage test enforces this).

### 5.3 Query option factories instead of one hook per query

**Decision.** Reads are exposed as `queryOptions` factories. Components use them as `useQuery(projectQueries.detail(useApiClient(), id))`.

**Reason.**

- The same object works for `useQuery`, `useSuspenseQuery`, `prefetchQuery` and route loaders (`queryClient.ensureQueryData`). The app uses React Router data mode, so loaders are a natural fit.
- It avoids 12 wrapper hooks.

**Consequences.** Writes are hooks (`mutations.ts`), because they need the `QueryClient` for invalidation. Reads are option factories.

### 5.4 State boundaries (clarified, not new code)

| State | Owner |
|---|---|
| Server state | TanStack Query cache only |
| UI state | Component state or the URL |
| Editor state (Phase 07) | Initialized from `project.draft`, persisted only through `useUpdateProject`; never written into the query cache |

This is documented in `frontend/README.md`. No UI or editor store was created: there is no consumer yet (YAGNI).

---

## 6. Domain / Data Model Changes

**Frontend.** All frontend types are new (§3).

**Field defaults.** Fields that have a backend default are optional on the wire, because the backend models double as request models. The frontend schemas fill in the same defaults via `.default(...)`:

| Field | Default |
|---|---|
| `NodeInstance.config` | `{}` |
| `NodeInstance.label` | `''` |
| `Graph.nodes` / `Graph.edges` | `[]` |
| `Trigger.config` | `{}` |
| `Trigger.enabled` | `true` |
| `Dependency.version_constraint` | `'*'` |
| `ProjectDraft.graph` | `{nodes: [], edges: []}` |
| `ProjectDraft.triggers` / `ProjectDraft.dependencies` | `[]` |

The output types are therefore non-optional.

`NodePort.schema` is `JsonObject | null | undefined`: optional and nullable in the contract.

**Enums mirrored from the backend:**

| Enum | Values |
|---|---|
| `ProjectStatus` | 5 |
| `TriggerType` | 6 |
| `ExecutionStatus` | `created`, `queued`, `running`, `paused`, `waiting`, `completed`, `failed`, `cancelled`, `timeout` |
| `NodeExecutionStatus` | `pending`, `ready`, `running`, `completed`, `failed`, `skipped`, `cancelled` |
| `ValidationCode` | 17 |
| `CredentialKind` | 4 |
| `PluginState` | 3 |

These are display data only. No transition rules, publishability rules or validity rules were reimplemented (AGENTS §4).

**NodeExecution.** The backend has no node-execution endpoint. Node-level state is exposed only through the execution report (`NodeReport`), as documented in `api/executions.py`. `NodeExecution` is therefore the report's node entry.

**Backend.**

- No domain entities, tables or migrations changed.
- `ExecutionDetail`, `ExecutionSection` and `NodeReport` now carry enum types instead of `str`. Serialized values are identical; `StrEnum` serializes to the same strings.
- The report's `schema_version` was not bumped, because the wire format did not change.

---

## 7. API / Contract Changes

### `GET /api/v1/plugins` (new)

- **Purpose:** list installed plugins with state and metadata.
- **Auth:** session plus admin (`require_admin`). Anonymous callers get 401 `AUTHENTICATION_REQUIRED`; members get 403 `PERMISSION_DENIED`.
- **Request:** none.
- **Response (200):**

  ```text
  {items: [{plugin_id, state, error, metadata}]}
  ```

  - `state` is `enabled | disabled | failed`.
  - `error` is a string or null.
  - `metadata` is null or `{name, version, description, engine_compatibility, sdk_compatibility, dependencies: [{kind, name, version_constraint}], node_types: [string]}`.
  - Items are sorted by `plugin_id` (`PluginRegistry.list_plugins`).
- **Errors:** 500 `ErrorResponse` is documented.
- **Compatibility:** additive.

### Status enums (changed contract metadata)

`ExecutionDetail.status`, `ExecutionReport.execution.status` and `ExecutionReport.nodes[].status` are now enums in OpenAPI.

**Compatibility.** The wire values are unchanged, but a strict client now rejects unknown values. The frontend does this deliberately: an unknown status makes the response `malformed_response`. That means adding a backend status becomes a coordinated contract change; the snapshot test and the contract test both fail until both sides are updated.

### Frontend/backend contract test

`contract.node.test.ts` checks all 28 service calls:

- method and path
- exact response field set, types, enums, formats and nullability
- request body field names

Two request-body shapes were previously unsupported and are now handled:

- **Optional bodies** (`X | null`) are compared against `X`. This applies to publish and cancel.
- **Free-form JSON object bodies** accept any key. This applies to execute and invoke.

**Unchanged.** Error envelope, WebSocket (none exists) and node/plugin SDK contracts.

---

## 8. Implementation Details

### Query key hierarchy

Keys are defined in `queryKeys` and nest by ownership.

**Projects:**

```text
['projects', 'list', page]
['projects', projectId]                                  ← detail
['projects', projectId, 'versions', 'list', page]
['projects', projectId, 'versions', version]
['projects', projectId, 'executions', page]
```

**Executions:**

```text
['executions', executionId]
['executions', executionId, 'events', after]
['executions', executionId, 'report']
```

**Installation-wide:**

```text
['nodes']
['plugins']
```

**Credentials:**

```text
['credentials', 'list', page]
['credentials', 'detail', name]
```

Invalidating `['projects', id]` reaches the project's versions and executions. Invalidating `['executions', id]` reaches its events and report.

Project ids are server-generated (`proj_…`), so `['projects', 'list']` cannot collide with a detail key. Credential names are user-chosen, so credential details use an explicit `'detail'` segment (§9, finding F6).

### Freshness

The global defaults from `createQueryClient` (Phase 03) are unchanged:

- `staleTime` 0
- queries retry only transient failures
- mutations never retry

Per-query overrides:

- `projectQueries.version`: `staleTime: Infinity`. Published versions are immutable.
- `nodeQueries.catalog` and `pluginQueries.list`: `INSTALLATION_STALE_TIME_MS` = 5 minutes. They change only when the backend restarts with different code.

### Mutation flow

`useApiMutation` binds the injected `ApiClient` (`useApiClient`) and the `QueryClient` (`useQueryClient`). `onSuccess` returns the invalidation promise, so `isPending` stays true until the refetch of active queries completes. There are no optimistic updates (AGENTS §85).

| Hook | Effect on cache |
|---|---|
| `useCreateProject` | invalidates project lists |
| `useUpdateProject` | `setQueryData(detail, response)`, invalidates lists |
| `useDeleteProject` | `removeQueries(['projects', id])`, invalidates lists |
| `useValidateProject` | none (no server change) |
| `usePublishProject` | invalidates `['projects', id]` (detail, versions, executions) and lists |
| `useExecuteProjectVersion`, `useInvokeTrigger` | invalidates `['projects', project_id, 'executions']` |
| `useCancelExecution` | invalidates `['executions', id]` and the project's executions |
| `useCreateCredential` | invalidates credential lists; `gcTime: 0` |
| `useUpdateCredential` | `setQueryData(detail, response)`, invalidates lists; `gcTime: 0` |
| `useDeleteCredential` | `removeQueries(detail)`, invalidates lists |

### Request specifics

- **Path segments** are `encodeURIComponent`-encoded: project ids, execution ids, trigger ids and credential names.
- **`publishProject`** sends `{expected_revision}` only when one is given; `cancelExecution` sends `{reason}` only when one is given. Otherwise there is no body, which the backend accepts as `null`.
- **`executeProjectVersion` and `invokeTrigger`** always send a JSON object (default `{}`).
- **Cancellation:** every query `queryFn` passes TanStack's `signal` through to `ApiClient.request`, so unmounting or superseding a query aborts the HTTP request (Phase 03 mechanism).

### Not wrapped

The following endpoints are not wrapped (§19):

- `POST /projects/import` (`application/zip` request)
- `GET /projects/{id}/export` (ZIP response)
- `/auth/setup`, `/auth/password`, `/users`
- `POST /webhooks/...` (an external, signed entry point; not called by the UI)
- `/health`, `/ready`

---

## 9. Findings

### F1 — No HTTP contract for plugins
- **Impact:** MEDIUM. The required `Plugin` type had no source of truth.
- **Resolution:** FIXED. A read-only endpoint was added (§5.1).
- **Notes:** this changes the wording of architecture §22; management remains CLI-only.

### F2 — Execution statuses were untyped in the contract
- **Impact:** MEDIUM.
  - `ExecutionDetail.status`, `ExecutionSection.status` and `NodeReport.status` were `str`.
  - The frontend could only type them as `string`, and the contract test could not detect status drift.
  - AGENTS §21 names execution states as contract-test material.
- **Resolution:** FIXED. They are now `ExecutionStatus` / `NodeExecutionStatus`; the wire is unchanged.
- **Notes:** these remain plain strings:
  - `event_type` in `EventOut` and `EventReport`
  - `ErrorReport.kind`
  - the keys of `node_status_counts`

  They are candidates for the same treatment, but there was no consumer this phase.

### F3 — No dedicated NodeExecution endpoint
- **Impact:** LOW.
- **Resolution:** DEFERRED. By design, node-level detail is the report (`api/executions.py` docstring).
- **Notes:** `NodeExecution` is the report's node entry. Phase 13/14 (Execution UI, live execution) must poll the report, or derive node state from events, to show node progress.

### F4 — Contract test could not compare optional or free-form request bodies
- **Impact:** LOW. It is test infrastructure.
- **Resolution:** FIXED (§11).

### F5 — TanStack Query tracked results do not notify about unread properties
- **Impact:** LOW. It affects tests only.
- **Resolution:** WORKAROUND, documented in the test helper.
- **Notes:**
  - `useQuery` re-renders an observer only for properties that observer has read.
  - The test helper initially read only `isSuccess`, so a later `setQueryData` or invalidation did not re-render the hook, and three tests failed.
  - Components read `data` during render, so production code is unaffected.
  - The helper `loaded()` now reads `data`.

### F6 — Potential query-key collision for credentials
- **Impact:** LOW.
- **Resolution:** FIXED before the tests ran.
- **Notes:** with `['credentials', name]`, a credential named `list` would share a prefix with `['credentials', 'list', …]`. Details now use `['credentials', 'detail', name]`.

### F7 — Secret values in mutation variables
- **Impact:** MEDIUM. Frontend AGENTS §23 forbids secrets lingering in the query cache.
- **Resolution:** WORKAROUND.
- **Notes:**
  - TanStack keeps `mutation.state.variables`, which includes the credential `value`, in the `MutationCache` for `gcTime` (default 5 minutes).
  - Credential create and update hooks now use `gcTime: 0`, so the entry is removed once no component observes it. This is verified by a test.
  - While the form component is mounted, `variables` still holds the value, the same as the form's own state. Phase 17 should call `reset()` after success.

### F8 — Plugin list is unpaginated
- **Impact:** LOW.
- **Resolution:** OPEN, a deliberate deviation.
- **Notes:** AGENTS §74 lists plugins among the collections to paginate. The endpoint follows the node-catalog precedent instead, because the list is bounded by installed packages, not user data. This is marked with a `ponytail:` comment in `api/plugins.py`.

### F9 — New modules are not yet imported by the app
- **Impact:** LOW. This is informational.
- **Resolution:** N/A.
- **Notes:** the bundle is unchanged. The first consumers arrive with Phase 05/06.

---

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Origin |
|---|---|---|---|---|---|
| B1 | `validationCodeSchema` first listed invented codes (for example `invalid_trigger`, `resource_limit_exceeded`) | Written before reading the enum from `openapi.json` | Replaced with the 17 contract values | `contract.node.test.ts` (`POST /projects/p/validate` compares enums) | Introduced this phase, fixed before any test run |
| B2 | `projects.ts` imported and re-exported a non-existent `PageInfo` type from `schemas.ts` | Leftover from a draft | Import and re-export removed | `tsc -b` | Introduced this phase |
| B3 | 4 contract tests failed (publish, cancel, execute, invoke) | Test infrastructure: the request-body check assumed an object schema | §11 fix 1 | The same 4 tests | Pre-existing test limitation, exposed by new calls |
| B4 | ESLint `no-unsafe-return` in `contract.node.test.ts` | `Object.values` over a union of module namespaces is `any[]` | Module list typed as `Record<string, unknown>[]` | `npm run lint` | Introduced this phase |
| B5 | 3 `serverState` tests failed (save, publish, cancel) | F5 | Helper reads `data` | The same tests | Introduced this phase (tests) |
| B6 | ESLint `no-unnecessary-type-parameters` on the test helper | Generic `T` used once | Generic over the hook result `R extends { data: unknown }` | `npm run lint` | Introduced this phase |
| B7 | `test_lists_installed_plugins_with_state_and_metadata` failed | Test setup: `load_installed()` on the registry that had just installed the plugin re-registered its node, which marked it FAILED | A fresh `PluginRegistry` simulates startup, as `tests/test_plugins.py` does | The test itself | Introduced this phase (test) |
| B8 | First full backend run printed "Failed to spawn: pytest" | Environment: the background shell ran in the repository root, not `backend/` | Re-ran with `uv run --directory backend` | — | Tooling / environment |

No pre-existing production bugs were discovered.

---

## 11. Fixes Applied

1. **Contract test: request bodies.**
   - **Symptom:** "fields the backend does not accept" for valid bodies.
   - **Root cause:** `normalize(requestSchema).props` was empty for `anyOf[{$ref}, null]` and for `additionalProperties` objects.
   - **Implementation:** pick the non-null union member, and skip field-name checks when the body is a free-form record.
   - **Verification:** 30/30 contract tests pass. Response-schema comparison is unchanged and passed for all 28 calls on the first run.
2. **Test helper subscription (F5).**
   - **Implementation:** `loaded()` waits on `result.current.data` being defined.
   - **Verification:** 12/12 tests pass.
   - **Mutation check:** I replaced `queryKeys.projects.detail(v.projectId)` with `queryKeys.projects.lists()` in `usePublishProject`. The publish test then failed (1 failed / 11 passed), and the original was restored.
3. **Plugin API test setup (B7).**
   - **Implementation:** a fresh registry for `load_installed()`. The `async for` generator helper was refactored to `@asynccontextmanager` / `async with`.
   - **Verification:** 3/3 pass.
4. **Plugin dependency serialization.**
   - **Symptom:** the first draft built `DependencyBody(kind=d.kind, …)` with a `# type: ignore[arg-type]`, because `Dependency.kind` is `str` and `DependencyBody.kind` is `Literal`.
   - **Implementation:** reuse the existing helper `project_schemas._dependencies`, which uses `model_validate`, and drop the suppression.
   - **Verification:** mypy and ruff pass.
5. **Error code in tests.** The test used a made-up code, `REVISION_CONFLICT`. It now uses the backend's real code, `CONCURRENT_MODIFICATION` (per `backend/README.md`).

---

## 12. Testing & Verification

### Unit / component tests (Vitest, jsdom)

`src/api/serverState.test.tsx` uses the real `ApiClient` and `createQueryClient()` inside `ApiClientContext` and `QueryClientProvider` via `renderHook`. Only `fetch` is replaced (`vi.stubGlobal`), with a route table returning real `Response` objects.

**Queries (6 tests):**

| Test | Asserts |
|---|---|
| load then expose parsed data | `isPending` first; omitted draft fields filled by defaults |
| share one request | Two observers of the same key cause one request |
| backend errors | A 404 envelope becomes `ApiError{kind: 'not_found', code, message}` with exactly 1 request (no retry) |
| contract break | `status: 'deleted'` becomes `malformed_response`; no data |
| stale data | On remount the old data is shown with `isFetching`, then replaced; 2 requests |
| immutable version | On remount not stale and not fetching; 1 request |

**Mutations (6 tests):**

| Test | Asserts |
|---|---|
| save draft | Detail set from the response (no refetch); list refetched |
| failed write | 409 becomes `ApiError{kind: 'conflict'}`; cache unchanged; 1 PUT (no retry); list not refetched |
| publish | Versions list and detail of `proj_1` refetched; `proj_2` not |
| delete | The project's detail query is removed from the cache |
| execute / cancel | Project executions list refetched after each; the execution detail shows `cancelled` |
| credential secret | List refetched; after unmount the mutation cache is empty and no query data contains the secret |

**Result:** 12/12 passed. The full Vitest run is 146/146 across 16 files.

**Not covered by behavior tests:**

- Mutation hooks: `useCreateProject`, `useValidateProject`, `useInvokeTrigger`, `useUpdateCredential`, `useDeleteCredential`.
- Query factories: `executionQueries.events` / `.report`, `nodeQueries.catalog`, `pluginQueries.list`, `credentialQueries.detail`.

Their service calls are covered by the contract test only (§21, O1).

### Contract tests

`src/api/contract.node.test.ts` (node environment): 30/30 tests. That is 28 service calls, plus error-envelope and coverage checks. Its 2 self-tests ("the comparison itself") are unchanged and pass.

### Integration / API tests (backend, real HTTP stack via `httpx.ASGITransport`, PostgreSQL via Testcontainers)

`tests/integration/test_plugins_api.py`:

- Listing with an enabled sample plugin (`examples/plugins/text_tools`) and a FAILED, unreadable package (`PLUGIN_MANIFEST_INVALID`, `metadata: null`): exact JSON comparison.
- No plugin directory configured: `{"items": []}`.
- Anonymous request: 401; member: 403.

**Result:** 3/3.

`tests/test_openapi_contract.py` passes with the regenerated snapshot. The existing integration tests for `ExecutionDetail` and reports (for example `tests/integration/test_executions_api.py`) pass with the enum types.

### Full backend suite

1852 passed, 6 skipped. The 5 visible skips are `tests/external/test_openrouter_smoke.py`, which needs `AKASHA_SMOKE_OPENROUTER_API_KEY`. There is 1 pre-existing warning: Starlette's `httpx` TestClient deprecation.

### E2E (Playwright, production build)

11/11, the existing `smoke.spec.ts` and `ui.spec.ts`. No new E2E tests: there are no new screens.

### Security tests

- Plugin endpoint authorization (401 and 403).
- Credential secret not retained in the query or mutation cache.

### Not performed

- Accessibility tests: no UI was added.
- Performance tests.

---

## 13. Quality Gates

Results are from the final run after all fixes.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (frontend) | PASS | `npm run format:check` |
| Linter (frontend) | PASS | `npm run lint`, exit 0. Two errors (B4, B6) occurred during the phase and were fixed |
| Type Checker (frontend) | PASS | `npm run typecheck` (`tsc -b`). B2 was fixed during the phase |
| Unit/Component Tests (frontend) | PASS | 146/146, 16 files. 3 failures during the phase (B5) were fixed |
| Contract Tests | PASS | 30/30. 4 failures during the phase (B3) were fixed |
| Build | PASS | `npm run build`; bundle unchanged (443.31 kB JS, gzip 136.01 kB) |
| E2E Tests | PASS | 11/11 Playwright |
| Formatter (backend) | PASS | `ruff format --check`: 195 files |
| Linter (backend) | PASS | `ruff check .` |
| Type Checker (backend) | PASS | `mypy .`: 190 source files |
| Integration / API Tests (backend) | PASS | Full suite 1852 passed, 6 skipped (external smoke). B7 was fixed |
| OpenAPI snapshot | PASS | Regenerated via `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1` |
| Security Checks | PASS (targeted) | §12; no dependency audit was run (no dependency changes) |
| Accessibility | N/A | No UI changes |

---

## 14. Acceptance Criteria Verification

These are the roadmap §10 acceptance criteria.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Domain Types sind zentral definiert | PASS | All types are in `frontend/src/api/{projects,executions,reports,nodes,credentials,plugins,schemas}.ts`, indexed in `frontend/README.md` § "Domain types"; each is checked against OpenAPI by `contract.node.test.ts` |
| Server State wird nicht unnötig über lokale React States dupliziert | PASS | No `useState` or store in `src/api`; mutations write responses into the query cache (`setQueryData`) instead of returning copies to hold. The README defines editor state as a separate local copy, persisted only via `useUpdateProject`. No screens exist yet, so this cannot be tested beyond the infrastructure |
| Queries und Mutations sind strukturiert | PASS | `queries.ts` (one key registry, 5 factory groups, 12 factories) and `mutations.ts` (13 hooks over one helper) |
| Cache Invalidation ist definiert | PASS | Every mutation declares its effect (§8 table). Behavior is verified for update, publish, delete, execute, cancel and credential create; a deliberately broken invalidation made a test fail |

**Scope list from the roadmap (not formal criteria, but phase scope):**

| Type | Status | Evidence / gap |
|---|---|---|
| Project, ProjectDraft, ProjectVersion, Graph, NodeInstance, Edge, Trigger | PASS | `projects.ts` |
| NodeDefinition, NodePort | PASS | `nodes.ts` |
| NodeConfigSchema | PARTIAL | `NodeDefinition.config_schema: JsonObject`: a raw JSON Schema, not a typed model. A typed JSON Schema subset belongs to the inspector form generator (Phase 09) |
| Execution, NodeExecution | PASS | `executions.ts`, `reports.ts` (§6 on NodeExecution) |
| Event | PASS | `ExecutionEvent` |
| Log | FAIL / not applicable | The backend has no log API; execution events are the log record. Nothing to type without inventing a contract |
| Metric | PARTIAL | `ExecutionMetrics` (per execution). The Prometheus `/metrics` endpoint is a text format for scrapers, admin- or token-only, and not in OpenAPI |
| Report | PASS | `ExecutionReport` |
| Credential Metadata | PASS | `CredentialReference` |
| Plugin | PASS | `Plugin`, plus the new backend endpoint |
| Validation errors (session prompt) | PASS | `ValidationResult`, `ValidationIssue`, `ValidationCode`; request-level 422s via `ApiError.details` (Phase 03) |

None of the PARTIAL or FAIL items block the formal acceptance criteria. They are why the status is COMPLETE WITH LIMITATIONS.

---

## 15. Security Considerations

- **Authorization:** `GET /api/v1/plugins` requires an admin session. It is mounted under the protected router with `require_admin`, alongside credentials and users. It is tested for 401 and 403.
- **Information exposure:**
  - The plugin response contains manifest metadata and a stable error code (for example `PLUGIN_LOAD_FAILED`).
  - It contains no filesystem paths, entry points or exception text. `PluginRecord.error` holds only the code.
- **Secrets in the frontend:**
  - Credential responses contain no value (contract: `CredentialDetail` has no value field).
  - Credential create and update mutations use `gcTime: 0` (F7).
  - A test asserts that the secret is absent from the query cache and that the mutation cache is empty after unmount.
  - Residual risk: the value lives in `mutation.state.variables` while the form is mounted.
- **Input to URLs:** all path parameters are `encodeURIComponent`-encoded. No secrets or personal data go into query strings; the only query parameters are `offset`, `limit` and `after`.
- **Response validation:** every response is Zod-validated. Unknown enum values are rejected as `malformed_response` rather than rendered.
- **Plugin trust model:** unchanged. No plugin code runs because of the new endpoint; it reads the registry's records.

No changes affect XSS, CSRF, SSRF, file handling, expressions or WebSockets.

---

## 16. Performance Considerations

- **Bundle:** unchanged. The new modules are not imported by the app yet.
- **Query reuse:** shared keys deduplicate requests (tested: 2 observers, 1 request).
- **Avoided refetches:**
  - Immutable versions are never refetched.
  - The node catalog and plugins are cached for 5 minutes.
  - Saving a draft uses `setQueryData` instead of a refetch of the detail.
- **Invalidation breadth:**
  - Publishing invalidates the whole `['projects', id]` subtree, including every cached executions page of that project. Only active queries refetch immediately; inactive ones are marked stale.
  - Starting or cancelling an execution invalidates all cached execution-list pages of the project.
- **Payloads:**
  - `GET /api/v1/plugins` is unpaginated (F8).
  - The node catalog was already unpaginated.
- **Measurements:** none performed.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `frontend/README.md` | New layer | Service module list; § "Domain types" (concept-to-type table, enum and default semantics); § "Server state" (`queryKeys`, factories, freshness policy, mutation invalidation, credential `gcTime`); state boundaries; what is not covered. Replaced the Phase 03 sentence "Domain services and query hooks follow in Phase 04" |
| `backend/README.md` | New endpoint | `GET /api/v1/plugins` row in the "Execution and node API" table |
| `docs/architecture.md` §22 | Contract change | `/api/v1/plugins` in the implemented-endpoints list; the plugin-management note says the endpoint only lists |
| `backend/src/akasha/api/routes.py` docstring | Access policy | Plugins added to the admin-only routes |
| `docs/reports/frontend/phase-04-domain-types-server-state.md` | Phase report | This document |

**Should be updated later:**

- Architecture §42 still recommends generated OpenAPI types. Consider amending it to describe the schema-plus-contract-test approach (§5.2), or record it in an ADR.
- Roadmap §6 and frontend AGENTS §6 sketch `domain/` and `types/` directories that this approach does not use.

---

## 18. Dependencies / Infrastructure Changes

- No new, removed or upgraded dependencies. TanStack Query 5.104.0 and Zod 4.6.5 were already installed in Phase 03.
- No configuration, environment-variable, Docker, database, migration or Redis changes.
- Test infrastructure:
  - The new frontend test stubs `fetch` per test (`vi.stubGlobal` / `vi.unstubAllGlobals`). The Phase 03 client tests keep their real local HTTP server.
  - The new backend test reuses `make_auth_kit`, the `database` and `clean_db` fixtures, and `tests.test_plugins.SAMPLE`.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion |
|---|---|---|---|
| Project import/export wrappers | The client only handles JSON bodies; ZIP upload and download need binary support in `ApiClient` | Phase 06 (Project Management) or wherever import/export UI lands | No |
| Typed NodeConfigSchema (JSON Schema subset) | Needed by the schema-driven form generator, not by server state | Phase 09 | No (PARTIAL scope item) |
| User / auth-setup / password wrappers | Session and settings scope | Phase 21 | No |
| Event polling (`refetchInterval` / incremental `after`) | Live execution scope | Phase 14 | No |
| Editor state model | Graph editor scope | Phase 07 | No |
| Resetting credential mutations after success | Needs the form | Phase 17 | No |

---

## 20. Known Limitations

- Five mutation hooks and five query factories have no behavior tests (§12).
- There is no frontend `Log` type, because no backend log API exists.
- `Metric` covers only per-execution report metrics.
- `NodeDefinition.config_schema` is an untyped JSON object.
- `event_type`, `ErrorReport.kind` and the `node_status_counts` keys are `string`, because the backend contract types them that way.
- The plugin listing is unpaginated.
- A credential secret remains in `mutation.state.variables` while the observing component is mounted.
- An unknown backend enum value makes the whole response fail validation (`malformed_response`). This is intentional: drift is caught by the contract tests, not tolerated at runtime.

---

## 21. Open Issues

| # | Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|---|
| O1 | Untested hooks and factories (§12) | A wrong invalidation in them would go unnoticed | LOW | `frontend/src/api/mutations.ts`, `queries.ts` | Add behavior tests when the first consumer is built | Phase 06 (projects), 13 (executions), 17 (credentials), 20 (plugins) |
| O2 | `api/plugins.py` imports the private `project_schemas._dependencies` | Cross-module use of an underscore helper | LOW | Backend API | Rename it to a public helper if a third user appears | Any |
| O3 | Architecture §42 (generated types) differs from practice (§5.2) | Documentation inconsistency | LOW | Docs | Amend §42 or add an ADR | Next docs pass |
| O4 | Plugin list pagination (AGENTS §74) | Deviation from a guideline | LOW | Backend API | Paginate if installations grow large | Phase 20 at the earliest |
| O5 | Remaining string-typed backend fields (`event_type`, `ErrorReport.kind`) | Less precise frontend types | LOW | Backend contract | Enum them when a consumer needs it | Phase 14/15 |

---

## 22. Architectural / Product Decisions

1. **Types are inferred from validated schemas, not declared separately (§5.2).** One definition yields both the compile-time type and the runtime check. The OpenAPI snapshot plus contract test is the drift guard.
2. **Reads are `queryOptions` factories; writes are hooks (§5.3).** Factories are reusable in route loaders. Writes need the `QueryClient` for invalidation.
3. **Every mutation awaits its invalidation; there are no optimistic updates.** The backend is authoritative (AGENTS §85), and a pending state that covers the refetch prevents rendering pre-write data as current.
4. **`setQueryData` only where the response is the full resource** (draft save, credential update). Everywhere else the cache is invalidated, so no response is reshaped client-side.
5. **Key nesting expresses ownership** (`['projects', id, …]`, `['executions', id, …]`). Prefix invalidation then encodes "this changed the project and everything under it".
6. **Strict enums on the frontend.** Unknown statuses fail validation rather than render as unknown. A new backend status must be a deliberate contract change on both sides.
7. **NodeExecution comes from the report.** No node-execution endpoint was added: the backend deliberately routes node-level detail through the report.
8. **`/api/v1/plugins` is read-only and admin-only (§5.1).** Management stays in the CLI under the trusted-code model.
9. **No UI or editor store was created.** The boundaries are documented. Stores arrive with their first consumer.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **Last commit:** `ad6f3f1 frontend phase 03 - api client and backend integration foundation`. Phase 04 is **uncommitted**.
- **Modified (8):**
  - `backend/README.md`
  - `backend/src/akasha/api/routes.py`
  - `backend/src/akasha/api/trigger_schemas.py`
  - `backend/src/akasha/application/reports.py`
  - `docs/architecture.md`
  - `frontend/README.md`
  - `frontend/src/api/contract.node.test.ts`
  - `frontend/src/api/openapi.json`
- **Untracked (13):**
  - `backend/src/akasha/api/plugins.py`
  - `backend/tests/integration/test_plugins_api.py`
  - `docs/reports/frontend/phase-04-domain-types-server-state.md`
  - `frontend/src/api/` modules: `credentials.ts`, `executions.ts`, `mutations.ts`, `nodes.ts`, `plugins.ts`, `projects.ts`, `queries.ts`, `reports.ts`, `schemas.ts`, `serverState.test.tsx`
- **Unrelated changes:** none detected. The working tree was clean at the start of the phase.
- The phase contains backend and frontend changes. Following AGENTS §65 (focused commits), they could be committed separately in this order:
  1. Backend plugins endpoint and status enums, plus the OpenAPI snapshot.
  2. Frontend domain types and server state.

---

## 24. Phase Completion Assessment

### Implemented
- 6 frontend service modules plus shared schemas.
- 25 new service functions.
- Query key registry and 12 query factories.
- 13 mutation hooks with invalidation.
- Backend `GET /api/v1/plugins`.
- Status enums in the contract.
- Documentation.

### Verified
- All frontend gates (146/146 unit, 30/30 contract, 11/11 E2E, build, lint, types, format).
- All backend gates (1852 passed / 6 skipped, ruff, mypy).
- Invalidation tests proven able to fail.

### Not Implemented
- `Log` type: no backend contract.
- Import/export wrappers.
- User-management wrappers.

### Deferred
See §19: NodeConfigSchema typing, event polling, editor state, credential form reset.

### Open
O1–O5 (§21), all LOW.

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (Phase 05 — Dashboard)

**What the next phase can assume:**

- All reads go through `useQuery(<group>Queries.<name>(useApiClient(), …))`. The groups are `projectQueries`, `executionQueries`, `nodeQueries`, `pluginQueries` and `credentialQueries`.
- All writes go through the hooks in `src/api/mutations.ts`.
- Errors are `ApiError`; branch on `kind` and display `message`.

**Data available for a dashboard:**

- `projectQueries.list` returns summaries with `status`, `current_version` and `updated_at`.
- `projectQueries.executions(projectId)` returns executions per project.
- There is **no cross-project execution list** and no aggregate statistics endpoint. A dashboard showing "recent executions" or counts across projects either fans out per project or needs a new backend endpoint. Per this phase's rule, such an endpoint must be added to the backend, tested and documented, not computed client-side.

**Do not change casually:**

- The `queryKeys` hierarchy: invalidation relies on prefixes.
- The "await invalidation, no optimistic updates" mutation contract.
- The strict enums.
- `openapi.json`: regenerate it, never edit it by hand.

**Rules for new code:**

- A new endpoint needs its schema in the owning service module and an entry in `contract.node.test.ts`; the coverage test enforces this.
- Add behavior tests for any hook or factory from O1 that the phase starts using.
- Components must render `data` (not copy it into `useState`).
- Use the design-system async states (`LoadingState`, `Skeleton`, `ErrorState`, `EmptyState`) for `isPending`, `isError` and empty pages.
