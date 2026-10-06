# Phase 06 — Workflow Application Services

## 1. Executive Summary

Phase 06 was supposed to make the Workflow lifecycle available as
application services and through the first business API: Draft → Validate
→ Publish → Immutable Version, exposed at `/api/v1/workflows`.

What was implemented:

- `WorkflowService` (`backend/src/akasha/application/workflows.py`) with all
  nine roadmap use cases. Each one runs in one Unit of Work (one
  transaction).
- The `/api/v1/workflows` router (`api/workflows.py`) and explicit DTOs
  (`api/workflow_schemas.py`).
- Optimistic concurrency control for every workflow write: a new
  `workflows.revision` column, compare-and-swap `save`/`delete`, and
  migration `0002_workflow_revision`.
- A persisted published-version counter (`workflows.current_version`). It
  replaces the in-memory `Workflow.versions` list. That list was never
  populated by the Phase 05 repository, so a loaded workflow would have
  tried to publish version 1 again.
- 70 new tests. Integration, service and API tests run against a real
  PostgreSQL 17.2 Testcontainer.

A pre-existing test-infrastructure defect was also found and fixed. The
integration suite was silently skipped on Windows, and `mypy` failed there.

Resulting state:

- 334 tests pass and 0 are skipped (Docker available).
- `ruff format`, `ruff check` and strict `mypy` pass.
- The full lifecycle works end-to-end through HTTP against PostgreSQL.

One acceptance-relevant limitation remains. The production composition root
injects an empty node-definition catalog, because the Node Registry is
Phase 09. In a default production process every node type therefore fails
validation as `unknown_node_type`, so no workflow can be published. The
lifecycle is only publishable end-to-end when a node catalog is injected,
as the tests do. This limitation is documented, and a `ponytail:` comment
marks it in `bootstrap.py`.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §10 (Phase 06), goal: "Expose the Workflow lifecycle through
application services."

In scope:

- Use cases: `CreateWorkflow`, `GetWorkflow`, `UpdateWorkflowDraft`,
  `DeleteWorkflow`, `ValidateWorkflow`, `PublishWorkflow`,
  `GetWorkflowVersion`, `ListWorkflows`, `ListWorkflowVersions`.
- The lifecycle Draft → Validate → Publish → Immutable Version.
  "Published versions must not be mutable."
- API: `/api/v1/workflows`, `/{id}`, `/{id}/validate`, `/{id}/publish`,
  `/{id}/versions`.
- Tests: workflow creation, draft updates, validation, publish, immutable
  versions, API error handling.
- The session prompt added: typed request/response schemas, structured API
  errors, "appropriate authorization boundary preparation", handlers
  without domain or persistence logic, and persistence integration tests.

Out of scope (explicitly):

- Execution (the prompt says "Do not implement execution").
- The execution runtime and triggers (roadmap Phase 06 prompt).
- The Node Registry (Phase 09).
- Authentication and authorization (Phase 20).
- Credentials (Phase 13).
- Resource limits (Phase 24).
- Import/export (Phase 18).

---

## 3. Scope Implemented

### Application

- `WorkflowService` with these methods:
  - `create_workflow`
  - `get_workflow`
  - `list_workflows`
  - `update_draft`
  - `delete_workflow`
  - `validate_workflow`
  - `publish_workflow`
  - `get_version`
  - `list_versions`
- Application errors:
  - `WorkflowNotFoundError` (404 `WORKFLOW_NOT_FOUND`)
  - `WorkflowVersionNotFoundError` (404 `WORKFLOW_VERSION_NOT_FOUND`)
  - `WorkflowHasVersionsError` (409 `WORKFLOW_HAS_VERSIONS`)
  - `WorkflowValidationFailedError` (422 `WORKFLOW_VALIDATION_FAILED`, with
    `details.issues`)
  - `ConcurrentModificationError` (409 `CONCURRENT_MODIFICATION`, defined
    in `application/persistence.py`)
- Generic `Page[T]` result (`items`, `offset`, `limit`, `has_more`) and
  `MAX_PAGE_SIZE = 100`.
- `issue_payload()`: the single wire serialization of a
  `ValidationIssue`. Both the publish error details and the `/validate`
  response use it.
- Structured log events: `workflow_created`, `workflow_draft_updated`,
  `workflow_deleted`, `workflow_published`. They carry the fields `event`,
  `workflow_id`, `revision` and (for publish) `version`. No payload data is
  logged.

### Backend / API

- The router `create_workflows_router()` is mounted under `/api/v1` by
  `create_api_v1_router()`.
- The router has 9 operations (see §7).
- OpenAPI documents 404, 409, 422 and 500 responses as `ErrorResponse`,
  which overrides FastAPI's default `HTTPValidationError` schema for 422.
- The service is resolved through the dependency `_service(request)`. If
  no service is wired, it raises `ConfigurationError`.

### Domain

- `Workflow`:
  - `versions: list[WorkflowVersion]` and the derived `current_version`
    property were removed.
  - `current_version: int` and `revision: int` were added as fields.
  - `replace_draft(draft, *, name)` was added.
  - `create(name, *, description="")` was extended.
  - `publish_draft()` now advances the counter.
- `validate_id` and `validate_name` now raise `DomainValidationError`
  instead of a bare `ValueError`.
- `DomainValidationError` now also subclasses `ValueError`.
- `AkashaError.details` was widened from `dict[str, str]` to
  `Mapping[str, object]` and is stored as a `dict` copy.

### Persistence

- Migration `0002_workflow_revision` adds `workflows.revision` and
  `workflows.current_version`, with a backfill and check constraints.
- `SqlAlchemyWorkflowRepository.save` is now a single-statement
  compare-and-swap. The previous read-then-write upsert was removed.
- `SqlAlchemyWorkflowRepository.delete(workflow_id, *, revision)` is a
  compare-and-swap delete.
- The repository reads the new columns back into the domain object.

### Composition root

- `Container.workflows: WorkflowService | None`.
- `create_container()` wires `WorkflowService` when `database_url` is set.

### Testing

- 2 new integration test modules and extended existing modules (see §12).
- A reusable integration fixture `workflow_service` plus the node catalogs
  `SOURCE`/`SINK` in `tests/integration/conftest.py`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/application/workflows.py` | Created (246 lines) | `WorkflowService`, application errors, `Page`, `issue_payload` |
| `backend/src/akasha/api/workflows.py` | Created (111 lines) | `/api/v1/workflows` router, thin handlers, service dependency |
| `backend/src/akasha/api/workflow_schemas.py` | Created (280 lines) | Request/response DTOs, DTO↔domain mapping |
| `backend/migrations/versions/0002_workflow_revision.py` | Created (51 lines) | `revision`, `current_version` columns, backfill, check constraints |
| `backend/src/akasha/api/routes.py` | Modified | Mounts the workflows router under `/api/v1` |
| `backend/src/akasha/application/persistence.py` | Modified | `ConcurrentModificationError`; CAS contract on `save`; `delete(..., revision=)` |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | CAS `save` (INSERT … ON CONFLICT DO NOTHING / conditional UPDATE), CAS `delete`, new columns |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | `revision`, `current_version` columns, `CheckConstraint`s |
| `backend/src/akasha/domain/workflow.py` | Modified | Counter and revision fields, `replace_draft`, `versions` list removed |
| `backend/src/akasha/domain/primitives.py` | Modified | `validate_id`/`validate_name` raise `DomainValidationError` |
| `backend/src/akasha/errors.py` | Modified | `details: Mapping[str, object]`; `DomainValidationError(AkashaError, ValueError)` |
| `backend/src/akasha/bootstrap.py` | Modified | `Container.workflows`; `WorkflowService` wiring |
| `backend/tests/integration/test_workflow_service.py` | Created (20 tests) | Service use cases against PostgreSQL |
| `backend/tests/integration/test_workflows_api.py` | Created (37 tests) | HTTP stack + PostgreSQL |
| `backend/tests/integration/conftest.py` | Modified | Cross-platform Docker detection; `node_definitions`, `workflow_service` fixtures |
| `backend/tests/integration/test_persistence.py` | Modified (14 → 20 tests) | CAS, row-lock race, `0002` upgrade; `delete` signature; migration head `_HEAD` |
| `backend/tests/domain/test_workflow.py` | Modified (11 → 14 tests) | Counter semantics, `replace_draft`, counter invariant |
| `backend/tests/test_errors.py` | Modified (5 → 6 tests) | Placeholder validation test replaced by a real one; missing-persistence test |
| `backend/tests/test_lifecycle.py` | Modified (2 → 3 tests) | Container wiring with/without a database |
| `backend/README.md` | Modified | Integration-test prerequisite, Workflow API table |
| `docs/reports/backend/phase-06-workflow-application-services.md` | Created | This report |

No directories were created or deleted. No generated files were committed.
`pyproject.toml` and `uv.lock` are unchanged.

---

## 5. Architecture Changes

### 5.1 Optimistic concurrency on every workflow write

**Decision:** Every write of a `workflows` row is a compare-and-swap on
`workflows.revision`.

**Context:** AGENTS §84 says autosave must not overwrite newer server
state, and backend/AGENTS §69 lists draft updates and publish as critical
concurrency areas. The Phase 05 handoff (§25) explicitly left "draft
optimistic concurrency" and the upsert race to Phase 06. There is a
concrete hazard: publish rewrites the whole workflow row, so without a
guard a publish racing a draft update would restore the old draft.

**Chosen approach:**

- `WorkflowRepository.save(workflow)`:
  - `revision == 0` → `INSERT … ON CONFLICT (id) DO NOTHING`.
  - Otherwise → `UPDATE … WHERE id = :id AND revision = :revision`, which
    sets `revision + 1`.
  - `rowcount != 1` → `ConcurrentModificationError`.
  - On success the repository increments `workflow.revision` in memory.
- `delete(workflow_id, *, revision)` uses the same condition.
- Clients pass `expected_revision`. The service rejects a mismatch before
  writing, and the database condition closes the race between read and
  write.

**Alternatives considered:**

- `SELECT … FOR UPDATE` row locking in the service. Rejected: it needs a
  second repository method and still does not detect stale client state.
- Letting the domain bump the revision and having the repository check
  `revision - 1`. Rejected: it breaks every direct save of a mutated
  object, including existing Phase 05 tests, for no gain.
- ETag / `If-Match` headers. Not chosen: a body field is simpler for the
  CLI and tests. It can be added later without changing semantics.

**Reason:** This is the smallest mechanism that prevents lost updates,
closes the Phase 05 first-insert race, and serializes concurrent publishes.
PostgreSQL READ COMMITTED re-evaluates the `WHERE` clause after waiting for
the row lock, so the second writer loses.

**Consequences:**

- Every future writer of `workflows` must go through `save`/`delete` and
  must carry the current revision.
- `revision` is part of the public API contract.
- If a transaction is rolled back after `save`, the in-memory
  `workflow.revision` has already advanced. Services reload per use case,
  so this is currently harmless.

### 5.2 Published-version counter instead of an in-aggregate version list

**Decision:** `Workflow.current_version` is a persisted integer. The
`Workflow.versions` list was removed.

**Context:** Phase 05's `SqlAlchemyWorkflowRepository.get` never loaded
versions. `current_version` was derived from that always-empty list after
a load, so publishing a loaded workflow would produce version 1 again and
fail on `uq_workflow_versions_workflow_version`.

**Chosen approach:** The counter is stored in `workflows.current_version`
and advanced by `publish_draft()`. History is available only through
`WorkflowVersionRepository`.

**Alternatives considered:**

- Loading every version into the aggregate. Rejected: unbounded (AGENTS
  §74/§75).
- A `max(version)` query in the service. Rejected: it moves versioning
  logic out of the domain.

**Reason:** Version numbering stays in the domain, is O(1), and is
race-safe through the §5.1 compare-and-swap. The unique index stays as the
last line of defence.

**Consequences:** Architecture §6.1 still lists "versions" conceptually
under Workflow. This is now satisfied by the repository, not the aggregate.

### 5.3 Validation catalog injected into `WorkflowService`

**Decision:** `WorkflowService(uow_factory, node_definitions: Mapping[str,
NodeDefinition])`.

**Context:** `validate_graph` needs node definitions, and the registry is
Phase 09.

**Chosen approach:** The composition root passes `{}`. Tests pass their
own catalog.

**Alternatives considered:**

- Implementing a minimal registry or built-in nodes now. Rejected: that is
  Phase 09 scope (roadmap §33 phase isolation).

**Consequence:** Production cannot publish until Phase 09 (see §20).

### 5.4 Deletion policy

**Decision:** A workflow with `current_version > 0` cannot be deleted
(`409 WORKFLOW_HAS_VERSIONS`).

**Reason:** This is consistent with the Phase 05 FK `ondelete=RESTRICT`
and with vision §30 (versions are retained for execution history). Delete
is itself a compare-and-swap, so a racing publish produces a 409 instead
of an FK `IntegrityError` → 500.

### 5.5 Where structural vs semantic draft validation happens

**Decision:** `PUT` accepts semantically invalid drafts: unknown node
types, missing inputs, cycles and similar problems are reported by
`/validate` and block `/publish`. It rejects structurally broken drafts
with 422 `DOMAIN_VALIDATION_ERROR`: malformed ids, duplicate node ids,
edges to non-existent nodes, duplicate edges, and invalid trigger config.

**Reason:** Architecture §6.2 says a draft may be invalid. Structural
integrity is already a construction invariant of `Graph.add_node` and
`Graph.add_edge` (Phase 02).

### 5.6 Authorization boundary preparation

**Decision:** No placeholder auth code was added. All workflow endpoints
live on one `APIRouter`, so Phase 20 can protect all of them with one
router-level dependency. The service will then receive a principal
argument. Error codes `AUTHENTICATION_ERROR`/`AUTHORIZATION_ERROR` already
exist in the HTTP error mapping (Phase 01).

**Reason:** Roadmap §2.5 says "No Placeholder Architecture", and a no-op
auth dependency would be exactly that.

---

## 6. Domain / Data Model Changes

### Domain

| Object | Change |
|---|---|
| `Workflow` | Removed `versions: list[WorkflowVersion]` and the `current_version` property. Added fields `current_version: int = 0` and `revision: int = 0`, both validated `>= 0`. Added `replace_draft()` and private `_touch()`. `create()` accepts `description`. |
| `Workflow.publish_draft()` | Sets `current_version = snapshot.version` instead of appending to a list. Behaviour otherwise unchanged. |
| `validate_id`, `validate_name` | Raise `DomainValidationError` (still `isinstance(…, ValueError)`). |
| `DomainValidationError` | Now `(AkashaError, ValueError)`. |
| `AkashaError.details` | `Mapping[str, object]` input, stored as `dict[str, object]`. |

Field order of the `Workflow` dataclass changed: `current_version` and
`revision` were inserted before `timestamps`. No positional construction
exists in the repository (verified by the test suite passing).

### Database

| Object | Change |
|---|---|
| `workflows.revision` | `INTEGER NOT NULL DEFAULT 1` |
| `workflows.current_version` | `INTEGER NOT NULL DEFAULT 0`, backfilled with `max(workflow_versions.version)` per workflow |
| `ck_workflows_revision_positive` | `CHECK (revision >= 1)` |
| `ck_workflows_current_version_non_negative` | `CHECK (current_version >= 0)` |

`0002` has a `downgrade()` that drops both constraints and both columns.
The upgrade/downgrade/upgrade cycle is tested.

### Compatibility

- Existing rows get `revision = 1` and the correct counter (tested in
  `test_0002_upgrade_backfills_existing_workflows`).
- Persisted graph/trigger/dependency JSON is unchanged.
- The `WorkflowRepository.delete` signature changed (a keyword-only
  `revision` is required). All callers were updated.

### DTOs

See §7. They are Pydantic boundary models; the domain has no Pydantic
dependency.

---

## 7. API / Contract Changes

All endpoints are new; there were no previous business endpoints. The
error body is the existing Phase 01 `ErrorResponse`:
`{code, message, details, request_id}`.

| Method & path | Use case | Request | Success | Errors |
|---|---|---|---|---|
| `POST /api/v1/workflows` | CreateWorkflow | `CreateWorkflowRequest {name, description?}` | 201 `WorkflowDetail` | 422 `VALIDATION_ERROR` |
| `GET /api/v1/workflows?offset&limit` | ListWorkflows | `offset ≥ 0`, `1 ≤ limit ≤ 100` (default 50) | 200 `WorkflowPage {items: WorkflowSummary[], page: {offset, limit, has_more}}` | 422 |
| `GET /api/v1/workflows/{workflow_id}` | GetWorkflow | — | 200 `WorkflowDetail` | 404 `WORKFLOW_NOT_FOUND` |
| `PUT /api/v1/workflows/{workflow_id}` | UpdateWorkflowDraft (full replace) | `UpdateWorkflowRequest {expected_revision ≥ 1, name, description?, draft: DraftBody}` | 200 `WorkflowDetail` | 404, 409 `CONCURRENT_MODIFICATION`, 422 `VALIDATION_ERROR` / `DOMAIN_VALIDATION_ERROR` |
| `DELETE /api/v1/workflows/{workflow_id}` | DeleteWorkflow | — | 204 (empty body) | 404, 409 `WORKFLOW_HAS_VERSIONS` / `CONCURRENT_MODIFICATION` |
| `POST /api/v1/workflows/{workflow_id}/validate` | ValidateWorkflow | — | 200 `ValidationResponse {valid, issues[]}` | 404 |
| `POST /api/v1/workflows/{workflow_id}/publish` | PublishWorkflow | optional `PublishWorkflowRequest {expected_revision?}` | 201 `WorkflowVersionDetail` | 404, 409, 422 `WORKFLOW_VALIDATION_FAILED` (`details.issues`) |
| `GET /api/v1/workflows/{workflow_id}/versions?offset&limit` | ListWorkflowVersions | same pagination | 200 `WorkflowVersionPage` | 404 `WORKFLOW_NOT_FOUND` |
| `GET /api/v1/workflows/{workflow_id}/versions/{version}` | GetWorkflowVersion | `version ≥ 1` | 200 `WorkflowVersionDetail` | 404 `WORKFLOW_VERSION_NOT_FOUND`, 422 |

Versions expose only `GET`. `PUT`, `PATCH` and `DELETE` on
`/versions/{n}` return `405 METHOD_NOT_ALLOWED` (tested). The roadmap
lists `/{id}/versions` only; `/{id}/versions/{version}` was added because
GetWorkflowVersion needs an addressable resource.

### Schemas

- `DraftBody {graph: {nodes[], edges[]}, triggers[], dependencies[]}`.
  - `NodeInstanceBody {id, node_type, config: dict[str, JsonValue], label}`
  - `EdgeBody {source_node_id, source_port, target_node_id, target_port}`
  - `TriggerBody {id, trigger_type: TriggerType, config, enabled}`
  - `DependencyBody {kind: "node"|"plugin", name, version_constraint}`
  - All request models use `extra="forbid"`.
  - `GET` returns the same `DraftBody` shape that `PUT` accepts, so a
    read-modify-write round trip needs no conversion.
- `WorkflowSummary {id, name, description, status, revision,
  current_version, created_at, updated_at}`. `WorkflowDetail` adds `draft`.
- `WorkflowVersionSummary {id, version, schema_version, description,
  published_at}`. `WorkflowVersionDetail` adds `graph, triggers,
  dependencies`.
- `ValidationIssueOut {code: ValidationCode, message, node_id, port, edge:
  EdgeBody | null}`. Codes are the Phase 04 `ValidationCode` values in
  lower case, e.g. `missing_required_input`.
- `name`: stripped, 1–200 characters. `description`: at most 10 000
  characters.
- Node, edge, trigger and dependency ids are client-supplied and must
  match the domain id format (`node_<32 hex>`, `trig_<32 hex>`).

### Compatibility implications

- Frontend clients must send `expected_revision` on `PUT`.
- The flat error body matches architecture §146 (updated this phase to
  document the actual contract, see §9 F6).

---

## 8. Implementation Details

### Use-case flow

`api/workflows.py` handler → `WorkflowService` method → `async with
self._uow() as uow` → repositories → `uow.commit()`. Leaving the context
without a commit rolls back (Phase 05 `SqlAlchemyUnitOfWork.__aexit__`).
Handlers only map DTO ↔ domain and call one service method.

### `update_draft`

1. `_load` (404 if missing).
2. `_check_revision(workflow, expected_revision)`: 409 with `details`
   `{workflow_id, expected_revision, current_revision}`.
3. `workflow.replace_draft(draft, name=name)`.
4. `workflows.save` (compare-and-swap).
5. Commit.

The DTO→domain mapping (`DraftBody.to_domain`) runs in the handler before
the service call. Domain construction errors become 422 there, and
persistence is never reached (`test_request_validation_error_is_structured`
proves invalid requests don't reach persistence).

### `publish_workflow`

1. `_load`.
2. Optional revision check.
3. `workflow.draft.validate(definitions)`.
4. If the result is not ok → `WorkflowValidationFailedError(result)` and
   nothing is written.
5. `workflow.publish_draft()`.
6. `workflows.save(workflow)`: the compare-and-swap. A concurrent publisher
   blocks here on the row lock and then gets a 409.
7. `versions.save(workflow.id, version)`: insert-only.
8. Commit.

Both writes share one transaction. Validation is pure CPU and runs inside
the transaction; there is no I/O inside it besides the database.

### `delete_workflow`

1. `_load`.
2. `current_version > 0` → 409.
3. `workflows.delete(id, revision=workflow.revision)`. If it returns False →
   409.
4. Commit.

### Pagination

The service fetches `limit + 1` rows. `_page()` trims the extra row and
sets `has_more`. Bounds are enforced both by FastAPI `Query` validation
and by `_check_page()` in the service, so the service is safe for
non-HTTP callers.

### Dependency injection

`Container.workflows` is built once in `create_container()`. Its UoW
factory is `lambda: SqlAlchemyUnitOfWork(database)`. With no
`database_url`, `workflows` is `None` and workflow endpoints return `500
CONFIGURATION_ERROR` (tested).

### Error handling

- Every raised error is an `AkashaError` subclass with a stable `code` and
  `http_status`, mapped by the existing Phase 01 handler.
- Pydantic request errors → `422 VALIDATION_ERROR`, with `details` keyed
  by location.
- Unexpected exceptions → `500 INTERNAL_ERROR` with a fixed message. They
  are logged server-side with `exc_info`.

### Logging

Structured events are emitted after a successful commit, with
`extra={"event", "workflow_id", "revision", ["version"]}`. Names,
descriptions and graph/config payloads are not logged. Log emission is not
asserted by any test (see §20).

### Time

`replace_draft` and `publish_draft` use `akasha.domain.primitives.utcnow()`.
No injectable clock was introduced; no test depends on exact times.

---

## 9. Findings

### F1 — Integration tests were silently skipped on Windows; mypy failed there

- **Impact:** HIGH
- **Resolution:** FIXED
- **Notes:** `_docker_available()` in `tests/integration/conftest.py`
  only probed unix sockets (`socket.AF_UNIX`, `/var/run/docker.sock`). On
  Windows (Docker Desktop, named pipe; server 29.1.2 was running) it
  returned False, so all 14 Phase 05 integration tests were skipped.
  `socket.AF_UNIX` also does not exist in the Windows stubs, so
  `uv run mypy .` failed. Baseline at session start was 252 passed, 14
  skipped, and mypy FAIL (1 error). The check now pings the daemon through
  Testcontainers' own `DockerClient`.

### F2 — `Workflow.versions` was never populated after loading

- **Impact:** HIGH (would have broken the first publish of any persisted
  workflow)
- **Resolution:** FIXED (§5.2)
- **Notes:** This was a latent Phase 05 defect. No Phase 05 test published
  a workflow after reloading it.

### F3 — Phase 05 upsert race in `WorkflowRepository.save`

- **Impact:** MEDIUM
- **Resolution:** FIXED (§5.1)
- **Notes:** The Phase 05 report (§20) recorded this as a known
  limitation. Two concurrent first-inserts, or any concurrent updates,
  could overwrite each other or raise a raw `IntegrityError`.

### F4 — Malformed domain ids/names would have surfaced as HTTP 500

- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** `validate_id`/`validate_name` raised a bare `ValueError`,
  which the API maps to 500. They now raise `DomainValidationError` (422).
  It also subclasses `ValueError`, so existing
  `pytest.raises(ValueError)` assertions and the documented `ValueError`
  behaviour of `graph_serialization` still hold.

### F5 — FastAPI's default 422 OpenAPI schema misdescribes our error body

- **Impact:** MEDIUM (a generated frontend client would get wrong error
  types; AGENTS §21)
- **Resolution:** FIXED
- **Notes:** A router-level `responses=` map declares `ErrorResponse` for
  404/409/422/500. Verified in `test_openapi_documents_workflow_endpoints`.

### F6 — Error format deviates from the architecture example

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** Architecture §146 showed `{"error": {code, message, details,
  request_id}}`. Phase 01 established a flat body, and `test_errors.py`
  tests it. Rather than change the established contract, `architecture.md`
  §146 was updated to document the flat body actually implemented,
  including that `details` is an object (not an array) whose shape is
  error-specific.

### F7 — Workflow-row compare-and-swap makes the version unique index unreachable in normal flow

- **Impact:** LOW
- **Resolution:** Decided
- **Notes:** The Phase 05 handoff suggested mapping `IntegrityError` to
  409. Concurrent publishes now conflict on the workflow row before any
  version insert, as proven by
  `test_concurrent_publishes_create_exactly_one_version`. The
  `(workflow_id, version)` unique violation is therefore not translated. If
  it were ever hit, it would surface as a 500 and indicate a bug.

### F8 — `pg_stat_activity` polling triggers ruff `ASYNC110`

- **Impact:** LOW (tooling)
- **Resolution:** FIXED
- **Notes:** A `while not …: await asyncio.sleep()` loop was flagged. It
  was rewritten as a bounded `for … else: pytest.fail(...)` loop rather
  than suppressed.

### F9 — Test-client choice for async database fixtures

- **Impact:** LOW
- **Resolution:** Decided
- **Notes:** API integration tests use `httpx.AsyncClient` +
  `ASGITransport` instead of `TestClient`. The session-scoped asyncpg
  engine is bound to the session event loop, and `TestClient` runs the app
  in its own loop. This was chosen preemptively; the failure mode was not
  reproduced. `ASGITransport` does not run the lifespan, so the shared
  test database is not disposed by app shutdown, which is intended.

---

## 10. Bugs / Problems Found

| # | Problem | Origin | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|---|---|
| B1 | Integration tests skipped on Windows; mypy error | Pre-existing (Phase 05), tooling | Unix-socket-only Docker probe | Probe through `DockerClient(timeout=2).client.ping()` | The whole integration suite now runs (77 tests, 0 skipped) | Test infrastructure |
| B2 | A reloaded workflow would publish version 1 again | Pre-existing latent (Phase 05) | Counter derived from a never-loaded list | Persisted `current_version` + migration 0002 | `test_publish_creates_immutable_numbered_versions`, `test_version_counter_round_trips` | Domain/persistence |
| B3 | Lost updates / first-insert race | Pre-existing (Phase 05, known limitation) | Read-then-write upsert, unconditional UPDATE | Compare-and-swap `save`/`delete` | `test_save_is_compare_and_swap_on_revision`, `test_cas_rechecks_revision_after_waiting_for_row_lock`, `test_insert_of_existing_id_is_a_conflict_not_an_overwrite` | Persistence |
| B4 | Bad ids/names → 500 | Pre-existing (Phase 02), surfaced by the new API | `ValueError` not an `AkashaError` | `DomainValidationError` | `test_update_rejects_malformed_drafts[...not-an-id...]` | Domain/API |
| B5 | Import order (ruff I001) in conftest after editing | Introduced this phase | Manual import edit | `ruff check --fix` | Lint gate | Tests |

No bugs introduced in product code were found by the tests. All new tests
passed on their first run after implementation, apart from the lint issues
B5 and F8.

---

## 11. Fixes Applied

1. **Docker detection** (B1).
   - Symptom: `14 skipped`, mypy `Module has no attribute "AF_UNIX"`.
   - Implementation: `tests/integration/conftest.py::_docker_available`.
   - Verification: `pytest tests/integration` → 77 passed; mypy →
     success.
2. **Version counter** (B2).
   - Implementation: `domain/workflow.py`, `infrastructure/persistence.py`,
     migration 0002.
   - Verification: service and API publish tests.
3. **Compare-and-swap writes** (B3).
   - Implementation: `infrastructure/persistence.py::save/delete`.
   - Verification: the three persistence tests in the table above, plus
     service concurrency tests.
4. **Domain validation error type** (B4).
   - Implementation: `domain/primitives.py`, `errors.py`.
   - Verification: API 422 tests; the domain test suite is unchanged and
     green.
5. **OpenAPI error schemas** (F5).
   - Implementation: `api/workflows.py` router `responses`.
   - Verification: OpenAPI test.
6. **Placeholder test replaced.** `test_errors.py::test_request_validation_error_is_structured`
   previously only called `/health` and asserted a version string, with a
   comment deferring the real check to Phase 06. It now posts an invalid
   body to `/api/v1/workflows`. It asserts the structured 422 and the
   request-id echo, and it asserts that the UnitOfWork factory is never
   called.

---

## 12. Testing & Verification

The final full run was `uv run pytest`: **334 passed, 0 skipped, 1
warning**. The warning is a pre-existing Starlette deprecation for
`httpx` in `fastapi.testclient`. Environment: Windows, Python 3.14.0,
pytest 9.1.1, Docker server 29.1.2, image `postgres:17.2`.

Split:

- `tests/integration`: 77 passed.
- Everything else: 257 passed.
- Before this phase: 252 passed + 14 skipped.

### Unit tests

`tests/domain/test_workflow.py`, 14 tests (3 new):

- `current_version` increments.
- `replace_draft` replaces name and draft, keeps the counter and status,
  leaves already-published snapshots unchanged, preserves `created_at` and
  advances `updated_at`.
- A blank name is rejected, and the rejection leaves the object unchanged.
- Negative counters are rejected.

Result: PASS.

### Component tests (no Docker)

- `test_errors.py::test_request_validation_error_is_structured`
- `test_errors.py::test_workflow_api_without_persistence_is_a_configuration_error`
- `test_lifecycle.py::test_container_wires_workflow_service_with_database`
  (lazy engine, no connection)
- Extended `test_container_is_wired_into_app_state`
  (`workflows is None` without a database)

Result: PASS.

### Integration tests — application service

`tests/integration/test_workflow_service.py`, 20 tests: create/get round
trip, blank name, unknown workflow, pagination and `has_more`, 3
invalid-page cases, draft update persistence, saving an invalid draft,
stale revision without a write, concurrent updates (exactly one wins),
validate empty/valid, invalid publish persists nothing, numbered immutable
versions across a full draft rewrite, stale publish pin, concurrent
publishes (exactly one version), publish atomicity with an injected
failure (`_ExplodingUow`), delete unpublished, delete published refused,
missing version / missing workflow lookups.

Result: PASS.

### Integration tests — persistence / infrastructure

`tests/integration/test_persistence.py`, 20 tests (6 new):

- CAS delete with a stale revision.
- CAS save with a stale revision.
- Deterministic row-lock race: writer B is confirmed blocked through
  `pg_stat_activity.wait_event_type = 'Lock'` with bounded polling, A
  commits, and B gets `ConcurrentModificationError`.
- Insert over an existing id is a conflict.
- Counter round trip.
- `0001` with real rows → `0002`: backfill (`current_version` 2/0,
  `revision` 1) and a subsequent CAS save.

The existing upgrade/downgrade/upgrade test now expects head
`0002_workflow_revision`.

Result: PASS.

### API tests

`tests/integration/test_workflows_api.py`, 37 tests (with parametrization),
real HTTP stack via ASGI + PostgreSQL:

- The full lifecycle (the acceptance scenario), including the version
  staying byte-identical after the draft is emptied.
- 405 for PUT/PATCH/DELETE on a version.
- List pagination and summary shape.
- 4 bad-pagination cases.
- 6 malformed create bodies (missing, empty, whitespace, over-long,
  non-string name, unknown field) and non-JSON content.
- Structured 404 on every workflow route. The `x-request-id` echo is
  asserted on the three GET routes.
- 3 bad version-path cases.
- 409 on a stale revision.
- 7 malformed drafts (bad id, dangling edge, cron trigger without
  expression, unknown trigger type, unknown dependency kind, node missing
  `node_type`, unknown field), each asserting that nothing was written.
- Duplicate node ids.
- Structured validation issues (`unknown_node_type`, `unknown_port` with
  edge, `missing_required_input`, exact key set).
- Invalid publish → 422 with issues and no version.
- Stale publish pin → 409.
- Malformed publish body.
- Delete 204 with an empty body, then 404.
- Delete of a published workflow → 409.
- Opaque 500 when the database is unreachable.
- OpenAPI paths and error schema references.

Result: PASS.

### Security tests

Security coverage in this phase is limited to the following checks, which
are embedded in the API tests:

- Error bodies contain exactly the keys `{code, message, details,
  request_id}` and no `Traceback`.
- On database failure the response does not contain `asyncpg`,
  `127.0.0.1`, the URL password `hunter2`, `sqlalchemy` or `OSError`.
- Unknown request fields are rejected.

No authentication or authorization tests were run, because neither exists
yet. No secret-redaction tests were run, because credentials are Phase 13.

### Not performed

No E2E tests beyond the API lifecycle, and no performance, accessibility
or fuzz/property tests.

---

## 13. Quality Gates

Commands were run in `backend/` at the end of the phase and again during
this report.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 61 files already formatted. During development, formatting was applied to 6 and then 2 files. |
| Linter (`uv run ruff check .`) | PASS | I001 (import order) and ASYNC110 (polling loop) were found during development and fixed without suppression. |
| Type Checker (`uv run mypy .`, strict) | PASS | 59 source files. Baseline at phase start was **FAIL** (AF_UNIX, F1); fixed. |
| Unit Tests | PASS | Included in the 257 non-integration tests. |
| Integration Tests | PASS | 77 passed, 0 skipped, real PostgreSQL 17.2. |
| API Tests | PASS | 37 in `test_workflows_api.py`. |
| E2E Tests | N/A | No separate E2E suite. The API lifecycle test covers the phase flow. |
| Build | N/A | No build step for the backend package in this phase. |
| Security Checks | N/A | No dependency audit or SAST tooling configured. Error-disclosure assertions are in the API tests. |
| Accessibility | N/A | Backend only. |
| Migrations | PASS | Empty DB → head, head → base → head, and `0001` + data → `0002` all tested. |

---

## 14. Acceptance Criteria Verification

The roadmap Phase 06 acceptance criterion is: "A user can create and
publish a valid Workflow through the API." The table also evaluates the
phase's scope and test list, plus the session prompt's completion
condition ("Workflow lifecycle works end-to-end through the API and all
quality gates pass").

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| A user can create and publish a valid Workflow through the API | **PARTIAL** | Proven through HTTP + PostgreSQL in `test_full_lifecycle_create_edit_validate_publish`, with an injected node catalog. In a default production process (`create_container`) the catalog is `{}`, so every node is `unknown_node_type` and publishing a non-empty graph is impossible. Missing: a node catalog (Node Registry, Phase 09). Does not block the phase: supplying node definitions is roadmap Phase 09 scope, and implementing them here would violate phase isolation. |
| CreateWorkflow | PASS | `create_workflow`; `POST /api/v1/workflows`; service and API tests |
| GetWorkflow | PASS | `get_workflow`; `GET /{id}`; tests |
| UpdateWorkflowDraft | PASS | `update_draft`; `PUT /{id}`; persistence, revision and conflict tests |
| DeleteWorkflow | PASS | `delete_workflow`; `DELETE /{id}`; 204/404/409 tests |
| ValidateWorkflow | PASS | `validate_workflow`; `POST /{id}/validate`; structured-issue tests |
| PublishWorkflow | PASS | `publish_workflow`; `POST /{id}/publish`; validity, atomicity and concurrency tests |
| GetWorkflowVersion | PASS | `get_version`; `GET /{id}/versions/{version}`; tests |
| ListWorkflows | PASS | `list_workflows`; paginated `GET /workflows`; tests |
| ListWorkflowVersions | PASS | `list_versions`; paginated `GET /{id}/versions`; tests |
| Lifecycle Draft → Validate → Publish → Immutable Version | PASS | API lifecycle test; `test_publish_creates_immutable_numbered_versions` |
| Published versions must not be mutable | PASS | No write endpoints (405 tests). Repository is insert-only (Phase 05) plus the unique index. Snapshot unchanged after a draft rewrite (service and API tests). |
| API endpoints `/workflows`, `/{id}`, `/{id}/validate`, `/{id}/publish`, `/{id}/versions` | PASS | `test_openapi_documents_workflow_endpoints` |
| Tests: creation, draft updates, validation, publish, immutable versions, API error handling | PASS | §12 |
| Typed request/response schemas; structured errors | PASS | `api/workflow_schemas.py`; `ERROR_KEYS` assertions; OpenAPI `ErrorResponse` references |
| Handlers contain no domain or persistence logic | PASS | `api/workflows.py` handlers map and delegate only. `test_architecture.py` forbids `api → infrastructure` imports (passes). |
| Authorization boundary preparation | PASS (preparation only) | Single router for all workflow endpoints (§5.6). No auth enforcement exists; that is Phase 20. |
| All quality gates pass | PASS | §13 |

---

## 15. Security Considerations

- **Authentication/authorization:** None. All workflow endpoints are
  publicly callable by anyone who can reach the process. This is expected
  before Phase 20 and must not be deployed exposed.
- **Error disclosure:** Unexpected errors return a fixed message, and the
  stack trace stays in server logs (Phase 01 handler). Tested for a real
  database failure, including that the connection-URL password does not
  leak.
- **Input validation:**
  - `extra="forbid"` on every request model.
  - Enums for `trigger_type` and dependency `kind`.
  - Name/description length bounds.
  - Domain id-format validation.
  - Configs are typed as `dict[str, JsonValue]` (JSON objects only).
  - Structural graph invariants are enforced by the domain.
- **Reflected input:** `details.workflow_id` echoes the path value in JSON
  responses. This is JSON only, with no HTML rendering.
- **Secrets:** Node and trigger `config` is stored and returned as-is.
  Nothing prevents a client from putting a plaintext secret in a config.
  Credential references and redaction are Phase 13 and are not
  implemented. Validation issues contain locations only, never config
  values (Phase 04 design, reused by `issue_payload`).
- **Logging:** Only ids, revision and version are logged; no payloads.
- **Resource exhaustion:** There is no cap on node, edge or trigger counts
  or on request body size (Phase 24). Page size is capped at 100.
- **Concurrency integrity:** Compare-and-swap prevents lost updates and
  racing deletes (§5.1, §5.4).

---

## 16. Performance Considerations

- Every use case runs a bounded number of queries:
  - get: 1
  - update: 2
  - publish: 3
  - delete: 2
  - list workflows: 1
  - list versions: 2
- No N+1: graph data is one JSONB document per row (Phase 05 decision).
- The compare-and-swap is a single statement and holds the row lock only
  until commit. Publish holds it for the duration of in-memory validation.
  That is not measured, and it is expected to be negligible for the graph
  sizes supported now.
- Lists return summaries without graph documents (AGENTS §75). Detail
  responses include the full draft or version graph, and there are no
  size limits yet.
- Offset pagination costs O(offset) in PostgreSQL. Acceptable for workflow
  lists; cursor pagination is an option later (architecture §118).
- No measurements or benchmarks were taken. The full suite runs in about
  17–20 s, integration tests included (observed, not benchmarked).

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `backend/README.md` | The API is new; the test prerequisite was undocumented | Docker requirement for `tests/integration` (unix socket, named pipe, `DOCKER_HOST`), skip semantics, the Workflow API endpoint table, the revision/409 rule, the error body shape. Layout line updated. |
| `docs/architecture.md` §146 | The documented example (`{"error": {...}}`) did not match the implemented, tested contract (F6) | Rewrote the example to the actual flat `{code, message, details, request_id}` body; clarified that `details` is an object whose shape is error-specific, not an array |
| `docs/reports/backend/phase-06-workflow-application-services.md` | Phase report | This document |
| Module docstrings | In-code documentation | `application/workflows.py`, `api/workflows.py`, `api/workflow_schemas.py`, migration 0002, updated `Workflow` and `WorkflowRepository.save`/`delete` docstrings |

Not updated, and should be in the future:

- `docs/architecture.md` §6.1 / §25: mention the `revision` compare-and-swap
  convention and that version history is repository-only (§5.1, §5.2).
- No ADR was created. §5.1 is a reasonable ADR candidate.

---

## 18. Dependencies / Infrastructure Changes

- **Runtime/dev dependencies:** None added, removed or upgraded.
  `pyproject.toml` and `uv.lock` are unchanged (verified with `git diff`).
- **Test infrastructure:**
  - The Docker probe now uses `testcontainers.core.docker_client.DockerClient`,
    an already-installed dev dependency.
  - New fixtures `node_definitions` and `workflow_service`.
  - Two scratch databases are created and dropped per run inside the
    session container: `akasha_migration_check` (existing) and
    `akasha_migration_0002` (new).
- **Database:** Migration `0002_workflow_revision` (see §6).
- **Configuration/environment:** No new variables.
  `AKASHA_DATABASE_URL` now also controls whether `WorkflowService` is
  wired.
- **Docker/Redis/external services:** No changes.

---

## 19. Deferred Work

| Item | Why deferred | Target phase | Blocks this phase? |
|---|---|---|---|
| Node definition catalog in production | Node Registry scope | Phase 09 | No; causes the PARTIAL rating in §14 |
| Authentication/authorization of workflow endpoints | Explicitly Phase 20 | Phase 20 | No |
| Validation of declared dependencies at publish (architecture §3.8) | Needs registry/plugin data | Phase 09/17/18 | No |
| Trigger validation beyond construction, trigger id uniqueness | Trigger system | Phase 10 | No |
| Payload / graph-size limits | Resource limits | Phase 24 | No |
| Plaintext-secret prevention in node config | Credentials | Phase 13 | No |
| Workflow archive/disable lifecycle (vision §7) | Not in Phase 06 scope | Unassigned | No |
| Idempotency keys for create/publish | Not required by scope; publish is guarded by the revision pin | Phase 22/25 if needed | No |
| Running migrations on startup / CLI | Operations | Phase 21/26 | No |

---

## 20. Known Limitations

- A default production container cannot publish any non-empty workflow
  (empty node catalog).
- `WorkflowService.get_version` does not distinguish between an unknown
  workflow and an unknown version: both return `WORKFLOW_VERSION_NOT_FOUND`.
  `list_versions` does check the workflow and returns `WORKFLOW_NOT_FOUND`.
- A rolled-back transaction leaves `workflow.revision` already incremented
  on the in-memory object. This is harmless while services reload per use
  case, but it must be considered if objects are ever reused across Units
  of Work.
- A `(workflow_id, version)` unique violation (only reachable through a
  bug, F7) would surface as a 500, not a 409.
- Publishing an unchanged draft creates a new, identical version. There is
  no "no changes" check.
- Structured log events are emitted but not asserted by tests.
- No injectable clock. `updated_at` and `published_at` use `utcnow()`
  directly.
- Offset pagination only.
- Endpoints are unauthenticated.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended action | Target |
|---|---|---|---|---|---|
| Production cannot publish (empty node catalog) | Lifecycle unusable outside tests | MEDIUM | Composition root | Inject Node Registry definitions into `WorkflowService` in `create_container` | Phase 09 |
| Unauthenticated workflow API | Must not be exposed | MEDIUM (deployment) | API | Router-level auth dependency | Phase 20 |

The error-format mismatch with architecture §146 (previously listed here)
was resolved during report review by correcting the documentation; see §9
F6.

---

## 22. Architectural / Product Decisions

1. **`revision` is the concurrency contract for workflows.** Repositories
   compare-and-swap on it. Services check the client's `expected_revision`
   first to give a clear error. `PUT` requires it; publish accepts it
   optionally to pin "publish what I validated". Do not add unconditional
   `UPDATE workflows` statements.
2. **Version history is not part of the `Workflow` aggregate.** Use
   `current_version` for numbering and `WorkflowVersionRepository` for
   history.
3. **Drafts may be semantically invalid; they may not be structurally
   broken.** Semantic rules are reported by `/validate` and enforced only
   by `/publish`.
4. **Workflows with published versions are not deletable.** Future
   "removal" should be an archive state, not deletion of history.
5. **`issue_payload()` is the single serialization of validation issues.**
   The API `ValidationIssueOut` is validated from it. Change them
   together.
6. **API tests use `httpx.ASGITransport` on the session loop**, not
   `TestClient`, whenever the app shares the session database engine.
7. **Node definitions are injected into `WorkflowService` as a
   `Mapping`**. The Phase 09 registry should supply that mapping rather
   than change the service signature.
8. **`DomainValidationError` is also a `ValueError`.** Domain
   construction from untrusted input maps to 422 automatically.

---

## 23. Repository / Git State

- **Branch:** `main`. Last commit `713222d backend phase 05 - persistence
  foundation`.
- **Working tree:** uncommitted Phase 06 changes only.
  - Modified (14): `backend/README.md`,
    `src/akasha/{api/routes.py, application/persistence.py, bootstrap.py,
    domain/primitives.py, domain/workflow.py, errors.py,
    infrastructure/db_models.py, infrastructure/persistence.py}`,
    `tests/{domain/test_workflow.py, integration/conftest.py,
    integration/test_persistence.py, test_errors.py, test_lifecycle.py}`.
    Totals: 474 insertions, 79 deletions.
  - Untracked (7): `migrations/versions/0002_workflow_revision.py`,
    `src/akasha/api/workflow_schemas.py`, `src/akasha/api/workflows.py`,
    `src/akasha/application/workflows.py`,
    `tests/integration/test_workflow_service.py`,
    `tests/integration/test_workflows_api.py`, and this report.
- No unrelated changes were detected.
- No commit was created; commits are made only on explicit request.

---

## 24. Phase Completion Assessment

### Implemented

- All 9 roadmap use cases in `WorkflowService`.
- `/api/v1/workflows` with 9 operations and typed DTOs.
- Optimistic concurrency (compare-and-swap) for workflow writes and
  deletes.
- The persisted version counter.
- Migration 0002 with a backfill.
- Cross-platform Testcontainers detection.

### Verified

- 334 tests pass with 0 skipped, including 77 against real PostgreSQL.
- Lifecycle end-to-end through HTTP.
- Immutability of published versions.
- Atomic publish under an injected failure.
- Deterministic lost-update race.
- Concurrent publish.
- Migration from a populated previous schema.
- ruff format, ruff check and strict mypy pass.

### Not Implemented

Nothing within the Phase 06 scope list is missing.

### Deferred

See §19: node catalog (Phase 09), auth (Phase 20), limits (Phase 24),
secrets (Phase 13), dependency/trigger validation at publish.

### Open

See §21: empty production catalog, unauthenticated API.

### Blocking Issues

None for this phase. The only non-PASS acceptance item depends on roadmap
Phase 09 by design.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**Next phase:** 07 — Execution Domain + State Machine.

The next phase can assume:

- `WorkflowService.get_version(workflow_id, n)` returns an immutable
  `WorkflowVersion` snapshot. It is the only thing an Execution may
  reference (never the draft).
- `WorkflowVersion.id` (`wfver_…`) plus `(workflow_id, version)` identify a
  snapshot. Both are stable and unique.
- Every use case is its own Unit of Work. Follow the same
  `async with uow_factory() as uow: …; await uow.commit()` pattern for new
  services.
- Error pattern: subclass `AkashaError` with `code` and `http_status`;
  `details` may hold any JSON-serializable values.
- Route pattern: one `APIRouter` per resource under `/api/v1`, with a
  service dependency resolved from `request.app.state.container`, and
  router-level `responses=` pointing at `ErrorResponse`.

Must not be changed casually:

- The `revision` compare-and-swap semantics of `WorkflowRepository.save`
  and `delete`.
- The insert-only `WorkflowVersionRepository`.
- The `(workflow_id, version)` unique index.
- The flat error body.
- The DTO shapes in `api/workflow_schemas.py` (now a public contract).
- Migrations `0001` and `0002`. Add new migrations; never edit applied
  ones.

Constraints and open issues:

- The node catalog is empty in production until Phase 09.
- There is no authentication.
- There are no payload limits.

Prerequisites:

- A running Docker daemon for `tests/integration`.
- `postgres:17.2` is pulled on first run.
