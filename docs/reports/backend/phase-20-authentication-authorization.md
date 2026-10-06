# Phase 20 — Authentication + Authorization

## 1. Executive Summary

**Supposed to achieve.** Roadmap §24 (Phase 20) asks the backend to "secure
the backend for self-hosted usage". It names `User`, `Session`,
`AuthenticationService` and `AuthorizationService`. It requires:
- local authentication;
- basic authorization boundaries, explicitly not full enterprise RBAC;
- protection of workflows, executions, credentials, plugins and settings;
- tests for login, logout, invalid credentials, session handling,
  unauthorized access, authorized access and credential protection.

The session request added:
- secure password hashing and no plaintext passwords;
- resource ownership and access boundaries;
- secure authentication errors;
- session expiry and revocation;
- a brute-force / rate-limit boundary;
- a resource model that does not rule out later authorization models.

**Actually implemented.**
- **Identity model:** local users with two roles (`admin`, `member`), in
  `akasha.domain.identity`.
- **Password hashing:** stdlib scrypt, in `akasha.security.passwords`.
- **Sessions:** opaque server-side bearer tokens; only their SHA-256 is
  stored.
- **Use cases:** `AuthenticationService`, `UserService`,
  `AuthorizationService` and `LoginThrottle`, in `akasha.application.auth`.
- **Workflow ownership:** a new `workflows.owner_id`, via migration
  `0008_auth`.
- **HTTP layer:** default-deny guards and the `/auth` and `/users` routers,
  in `akasha.api.auth`. All non-public `/api/v1` routes are grouped under
  one protected router in `akasha.api.routes`.
- **`/metrics`:** now needs a static scrape token or an admin session.
- **Tests:** 81 new tests (31 unit, 49 API/integration, 1 migration). The
  existing API fixtures were adapted to authenticate.
- **Documentation:** `docs/architecture.md` §59/§60 and the metrics note,
  plus `backend/README.md`.

**Resulting system state.**
- An unauthenticated client can reach only these routes:
  - `/health` and `/ready`;
  - the OpenAPI documents;
  - `POST /api/v1/auth/setup` and `POST /api/v1/auth/login`;
  - `POST /api/v1/webhooks/{workflow_id}/{trigger_id}`.
- Every other route answers 401 `AUTHENTICATION_REQUIRED`. A test walks the
  OpenAPI document and verifies this for every route.
- Members see only their own workflows and those workflows' versions,
  executions, exports and reports. Anything else answers 404.
- Credentials and user management are admin-only.

**Phase Status:** COMPLETE WITH LIMITATIONS

The acceptance criterion is met. The limitations are documented in
§19–§21:
- webhooks remain public;
- credentials are installation-scoped;
- the login throttle is in-process only;
- setup is open on a fresh install until it is completed;
- there are no persisted audit records.

---

## 2. Phase Objective

Roadmap §24 (verbatim goal): "Secure the backend for self-hosted usage."

**Scope from the roadmap:**
- `User`, `Session`, `AuthenticationService`, `AuthorizationService`;
- local authentication for Version 1;
- "basic authorization boundaries without building full enterprise RBAC";
- protection of workflows, executions, credentials, plugins and settings.

**Architecture constraints** (§59/§60, `vision.md` §49/§50):
- authentication must be separated from authorization;
- future authentication providers must remain possible;
- domain resources need ownership information that makes future
  authorization possible;
- the system must not assume every installation has exactly one
  unrestricted user.

**Not responsible for** (roadmap prompt and session request):
- enterprise SSO;
- complex RBAC;
- the CLI (Phase 21);
- workers and Redis (Phase 22);
- security hardening (Phase 25);
- frontend login UI (frontend roadmap Phase 21).

---

## 3. Scope Implemented

### Domain
- `akasha/domain/identity.py` (new):
  - `Role` enum (`ADMIN`, `MEMBER`);
  - `User` entity: id prefix `user_`, `username`, `role`, `disabled`,
    `timestamps`, and an `is_admin` property;
  - `UserSession` (frozen): `token_hash`, `user_id`, `created_at`,
    `expires_at`;
  - `Identity` (frozen): `user` plus `session`;
  - `validate_username`: `^[a-z0-9][a-z0-9._-]{2,63}$`;
  - `validate_password`: length 12–256, no composition rules;
  - `may_access_workflow(user, owner_id)`.
- `akasha/domain/workflow.py`: `Workflow` gains `owner_id: str | None`,
  validated as a domain id. `Workflow.create(..., owner_id=None)`.

### Security
- `akasha/security/passwords.py` (new): the `PasswordHasher` dataclass.
  - It uses `hashlib.scrypt` with defaults N=2^14, r=8, p=5.
  - Stored format: `scrypt$n$r$p$salt$hash`, with a 16-byte salt and a
    32-byte key.
  - `verify` uses `hmac.compare_digest`. Any malformed stored value returns
    `False`.
  - `maxmem` is 256 MiB, so a tampered stored value that asks for more
    memory is refused.

### Application
- `akasha/application/auth.py` (new):
  - `AuthenticationService`: `setup`, `login`, `authenticate`, `logout`,
    `change_password`, `hash_password`. It takes an injectable `clock`,
    `hasher`, `throttle` and `session_ttl`.
  - `UserService`: `create_user`, `list_users`, `update_user`
    (`disabled` and/or password reset). Every method is admin-only.
  - `AuthorizationService`: `require_workflow`, `require_execution`,
    `owner_filter`.
  - `LoginThrottle`: per-key sliding-window counter.
  - `require_admin(identity)`.
  - `token_hash(token)` (SHA-256 hex).
  - Error classes: `AuthenticationRequiredError` (401),
    `InvalidCredentialsError` (401), `LoginThrottledError` (429),
    `PermissionDeniedError` (403), `SetupCompletedError` (409),
    `UsernameTakenError` (409), `UserNotFoundError` (404),
    `LastAdminError` (409).
- `akasha/application/persistence.py`:
  - new ports `UserRepository` and `SessionRepository`;
  - `UnitOfWork` gains `users` and `sessions`;
  - `WorkflowRepository.list` gains `owner_id`.
- `akasha/application/workflows.py`: `create_workflow(..., owner_id=None)`
  and `list_workflows(..., owner_id=None)`.
- `akasha/application/packages.py`: `import_workflow(data, *, owner_id=None)`.

### Persistence
- `akasha/infrastructure/db_models.py`:
  - new tables `users` and `sessions`;
  - `workflows.owner_id`, a foreign key to `users.id` with `ON DELETE
    RESTRICT`, plus the index `ix_workflows_owner_id`.
- `akasha/infrastructure/persistence.py`:
  - `SqlAlchemyUserRepository`: `get`, `get_with_password`, `add`,
    `add_first`, `update`, `lock_active_admins`, `list`;
  - `SqlAlchemySessionRepository`: `add`, `get_active`, `delete`,
    `delete_for_user`, `delete_expired`;
  - the workflow repository persists `owner_id` on insert only and filters
    `list` by owner.
- `migrations/versions/0008_auth.py` (new), with a reversible downgrade.

### Backend/API
- `akasha/api/auth.py` (new):
  - `bearer_token` parsing;
  - guard dependencies: `current_identity`, `authorize_path`,
    `require_admin`;
  - request/response schemas: `LoginRequest`, `LoginResponse`,
    `UserDetail`, `ChangePasswordRequest`, `CreateUserRequest`,
    `UpdateUserRequest`, `UserPage`;
  - `create_auth_router()` (`/auth/setup`, `/auth/login`, `/auth/logout`,
    `/auth/me`, `/auth/password`);
  - `create_users_router()` (`/users` POST/GET, `/users/{user_id}` PATCH).
- `akasha/api/routes.py`:
  - the v1 router now has public sub-routers (auth, webhooks) and one
    `protected` router with the dependencies `current_identity` and
    `authorize_path`;
  - credentials and users are included with an extra `require_admin`;
  - `/metrics` gets the `_metrics_access` dependency;
  - the module docstring holds the access policy.
- `akasha/api/triggers.py`: the webhook route moved into a new
  `create_webhooks_router()` so it can stay public.
- `akasha/api/workflows.py`:
  - `create_workflow` and `import_workflow` pass `owner_id=identity.user.id`;
  - `list_workflows` passes `AuthorizationService.owner_filter(identity)`.
- `akasha/api/workflow_schemas.py`: `WorkflowSummary` (and therefore
  `WorkflowDetail`) gains `owner_id`.
- `akasha/api/app.py`:
  - 401 responses carry `WWW-Authenticate: Bearer`;
  - errors with `details.retry_after_seconds` carry `Retry-After`;
  - the access log line includes `user_id`.

### Composition / configuration
- `akasha/config.py`: new settings `session_ttl_seconds` (default 43200,
  range 60 to 2,592,000) and `metrics_token` (`SecretStr`, minimum length 32).
- `akasha/bootstrap.py`:
  - `Container` gains `auth`, `authorization` and `users`;
  - `create_container` wires them when a database is configured.

### Testing
- New: `tests/test_auth_units.py` (31 tests).
- New: `tests/integration/test_auth_api.py` (49 tests).
- New: `test_0008_upgrade_leaves_existing_workflows_ownerless` in
  `tests/integration/test_persistence.py`.
- New helpers in `tests/integration/conftest.py`:
  - `AuthKit`, `make_auth_kit` and the `auth_kit` fixture;
  - `FAST_HASHER` (N=2^4) and `PASSWORD`;
  - `truncate_all` also empties `sessions` and `users`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/identity.py` | Created | User, Role, UserSession, Identity, validation, access rule |
| `backend/src/akasha/security/passwords.py` | Created | scrypt `PasswordHasher` |
| `backend/src/akasha/application/auth.py` | Created | Authentication, user, authorization use cases; throttle; errors |
| `backend/src/akasha/api/auth.py` | Created | Guards, `/auth` and `/users` routers, schemas |
| `backend/migrations/versions/0008_auth.py` | Created | `users`, `sessions`, `workflows.owner_id` |
| `backend/src/akasha/domain/workflow.py` | Modified | `owner_id` on `Workflow` |
| `backend/src/akasha/application/persistence.py` | Modified | `UserRepository`, `SessionRepository`, UoW attributes, owner filter |
| `backend/src/akasha/application/workflows.py` | Modified | owner on create, owner filter on list |
| `backend/src/akasha/application/packages.py` | Modified | owner on import |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | new tables, owner column |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | new repositories, owner persistence |
| `backend/src/akasha/api/routes.py` | Modified | protected / public / admin-only composition; `/metrics` guard |
| `backend/src/akasha/api/triggers.py` | Modified | webhook split into a public router |
| `backend/src/akasha/api/workflows.py` | Modified | identity-scoped create/list/import; docstring |
| `backend/src/akasha/api/workflow_schemas.py` | Modified | `owner_id` in responses |
| `backend/src/akasha/api/app.py` | Modified | `WWW-Authenticate`, `Retry-After`, `user_id` in access log |
| `backend/src/akasha/config.py` | Modified | `session_ttl_seconds`, `metrics_token` |
| `backend/src/akasha/bootstrap.py` | Modified | wiring of auth services |
| `backend/pyproject.toml` | Modified | ruff: `S105`/`S106` ignored under `tests/**` (test passwords) |
| `backend/tests/test_auth_units.py` | Created | unit tests |
| `backend/tests/integration/test_auth_api.py` | Created | API/security tests on PostgreSQL |
| `backend/tests/integration/conftest.py` | Modified | `AuthKit`, fast hasher, truncation of auth tables |
| `backend/tests/integration/test_persistence.py` | Modified | head `0008_auth`, table assertions, 0008 upgrade test |
| `backend/tests/integration/test_workflows_api.py` | Modified | authenticated fixture; 404 code expectation; outage test with auth |
| `backend/tests/integration/test_triggers_api.py` | Modified | authenticated fixture; unknown-workflow expectation |
| `backend/tests/integration/test_credentials.py` | Modified | authenticated fixture |
| `backend/tests/integration/test_workflow_packages_api.py` | Modified | authenticated fixture |
| `backend/tests/integration/test_reports.py` | Modified | authenticated fixture |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeUow` / `FakeWorkflows` follow the extended protocols |
| `backend/tests/test_errors.py` | Modified | `dependency_overrides[current_identity]` for the validation test |
| `backend/tests/test_request_observability.py` | Modified | `/metrics` test uses `metrics_token` |
| `backend/README.md` | Modified | auth settings and first-run instructions |
| `docs/architecture.md` | Modified | §59/§60 implementation notes; metrics note |
| `docs/reports/backend/phase-20-authentication-authorization.md` | Created | this report |

There are no new directories apart from the files above, no generated files
and no deleted files.

---

## 5. Architecture Changes

### 5.1 Opaque server-side sessions as bearer tokens

**Decision.** Sessions are random tokens (`secrets.token_urlsafe(32)`, 256
bits). Clients send them as `Authorization: Bearer`. The server stores only
their SHA-256 hash, in `sessions`.

**Context.** The architecture says "sessions/tokens as architecturally
defined" but defines neither. The frontend template (`frontend/src/auth`,
`constants/api.constant.ts`) already sends `Authorization: Bearer <token>`.
The CLI (Phase 21) also needs a header-based scheme.

**Chosen Approach.**
- One indexed lookup per request, joined with `users`. The lookup filters
  out disabled users and expired sessions.
- Expiry is absolute, with no sliding renewal.
- Revocation deletes rows.

**Alternatives Considered.**
- JWT: stateless, but it cannot be revoked without a denylist, which
  removes the advantage.
- HttpOnly session cookies: these need CSRF protection and do not fit the
  CLI.

**Reason.**
- Revocation is simple and immediate: logout, password change and
  disabling a user all take effect at once.
- There is no CSRF surface, because there is no ambient credential.
- A leaked database does not yield live tokens.

**Consequences.**
- Every authenticated request costs one database query.
- Token storage in the browser is the frontend's responsibility (frontend
  roadmap Phase 23 already covers "Token Handling"). AGENTS §62 forbids
  `localStorage`.

### 5.2 Default-deny enforcement at the router, keyed on path parameters

**Decision.** Authentication and authorization are FastAPI dependencies on
a single `protected` router, not per-handler calls. `authorize_path` checks
access whenever the matched route has a `workflow_id` or `execution_id` path
parameter.

**Context.**
- The Phase 06 and Phase 13 routers anticipated "a single router-level
  dependency" (docstrings in `api/workflows.py` and `api/credentials.py`).
- Per-handler checks can be forgotten on a new route.

**Chosen Approach.**
- `protected = APIRouter(dependencies=[Depends(current_identity),
  Depends(authorize_path)])` holds workflows, triggers, reports,
  credentials (plus `require_admin`) and users (plus `require_admin`).
- Public routes (auth, webhooks) are included separately.

**Alternatives Considered.** Passing an `Identity` into every
application-service method. Phase 06 had anticipated this ("the service
gains a principal argument then"). It was rejected for this phase because
of the size of the diff, and because the services are also meant to be
driven by the CLI as a trusted operator.

**Reason.** A new route is protected by default, and the check lives in
one place. The test
`test_every_non_public_route_requires_authentication` locks this in.

**Consequences.**
- Resource-level authorization lives in the API layer, as architecture
  §21 allows ("authorization checks" is listed as an API-layer duty).
  Application services stay principal-free, except `UserService`, which
  checks admin itself.
- The CLI (Phase 21) runs with operator trust and bypasses these checks.
  Anything it creates without an owner is admin-only.
- A new route whose resource id uses another parameter name (for example
  `version_id` alone) would not be ownership-checked automatically. It
  would still require authentication.

### 5.3 Two roles plus workflow ownership

**Decision.**
- `admin` manages users, credentials, all workflows and `/metrics`.
- `member` owns workflows.
- `workflows.owner_id` is fixed at creation or import.

**Context.** Architecture §60 requires ownership information for future
authorization, without RBAC.

**Alternatives Considered.** Teams or grants tables. These were rejected
as RBAC scope.

**Consequences.**
- Executions, versions, triggers, exports and reports inherit access from
  their workflow.
- `may_access_workflow` is the single rule to extend later, for example
  with shared workflows.

### 5.4 Credentials remain installation-scoped and admin-managed

**Decision.** The credentials API is admin-only. Credentials themselves
have no owner.

**Context.**
- The runtime resolves `credential:<name>` by name only (Phase 13).
- Owner-scoped credentials would need owner-aware resolution in the
  runtime, which is outside this phase.

**Consequences.** A member cannot list, read, create or modify credentials
through the API. However, a member's workflow can *use* any credential by
name in a node, and so could send it to an endpoint the member controls.
This is documented in architecture §60 and in §20 of this report.

### 5.5 Webhooks remain public

**Decision.** `POST /api/v1/webhooks/{workflow_id}/{trigger_id}` needs no
session.

**Reason.** Webhook callers are external systems.

**Consequences.** Per-trigger webhook secrets are deferred to Phase 25.

---

## 6. Domain / Data Model Changes

**New domain types** (`domain/identity.py`):
- `Role` (`StrEnum`: `admin`, `member`);
- `User` (`Entity`);
- `UserSession` (frozen dataclass);
- `Identity` (frozen dataclass);
- the constants `MIN_PASSWORD_LENGTH = 12` and `MAX_PASSWORD_LENGTH = 256`.

**Changed entity:** `Workflow.owner_id: str | None = None`. It is validated
as a domain id when present.

**The password hash is not part of `User`.** It crosses the repository
boundary only as a string, through `UserRepository.get_with_password` /
`add` / `add_first` / `update`. This mirrors how credential ciphertext is
kept out of `Credential`.

**Database (migration `0008_auth`):**

| Object | Definition |
|---|---|
| `users` | `id` PK, `username` UNIQUE NOT NULL, `password_hash` NOT NULL, `role` NOT NULL with `ck_users_role` (`admin`,`member`), `disabled` BOOLEAN default false, `created_at`, `updated_at` |
| `sessions` | `token_hash` PK, `user_id` FK → `users.id` ON DELETE CASCADE, `created_at`, `expires_at`; indexes `ix_sessions_user_id`, `ix_sessions_expires_at` |
| `workflows.owner_id` | nullable VARCHAR, FK `fk_workflows_owner_id` → `users.id` ON DELETE RESTRICT; index `ix_workflows_owner_id` |

**Compatibility.**
- Existing workflows get `owner_id = NULL`, which makes them admin-only.
  This is verified by `test_0008_upgrade_leaves_existing_workflows_ownerless`.
- The downgrade drops the index, the foreign key, the column and both
  tables. It was verified by the upgrade/downgrade/upgrade test.
- Users cannot be deleted while they own workflows, because of RESTRICT. No
  delete endpoint exists.

---

## 7. API / Contract Changes

### New endpoints

| Endpoint | Auth | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/v1/auth/setup` | public | `{username, password}` | 201 `UserDetail` (role `admin`) | 409 `SETUP_ALREADY_COMPLETED`, 422 `DOMAIN_VALIDATION_ERROR`/`VALIDATION_ERROR` |
| `POST /api/v1/auth/login` | public | `{username, password}` | 200 `{token, expires_at, user: UserDetail}` | 401 `INVALID_CREDENTIALS`, 429 `LOGIN_THROTTLED` (+ `Retry-After`), 422 |
| `POST /api/v1/auth/logout` | session | — | 204 | 401 |
| `GET /api/v1/auth/me` | session | — | 200 `UserDetail` | 401 |
| `POST /api/v1/auth/password` | session | `{current_password, new_password}` | 204 | 401 `INVALID_CREDENTIALS`, 422, 429 |
| `POST /api/v1/users` | admin | `{username, password, role?=member}` | 201 `UserDetail` | 401, 403 `PERMISSION_DENIED`, 409 `USERNAME_TAKEN`, 422 |
| `GET /api/v1/users?offset&limit` | admin | — | 200 `{items: UserDetail[], page}` | 401, 403 |
| `PATCH /api/v1/users/{user_id}` | admin | `{disabled?, password?}` | 200 `UserDetail` | 401, 403, 404 `USER_NOT_FOUND`, 409 `LAST_ADMIN`, 422 |

`UserDetail` has the fields `id`, `username`, `role`, `disabled`,
`created_at` and `updated_at`. It has no password or hash field.

### Changed endpoints

The following changes apply to all existing `/api/v1` routes except
webhooks:

- **Authentication.** They now require `Authorization: Bearer <token>`.
  Without it they answer 401 `AUTHENTICATION_REQUIRED` with
  `WWW-Authenticate: Bearer`.
- **Workflow and execution access.** Routes with `{workflow_id}` or
  `{execution_id}` answer 404 `WORKFLOW_NOT_FOUND` / `EXECUTION_NOT_FOUND`
  when the caller may not access the resource. The access check runs
  before the handler, so a nonexistent workflow now yields
  `WORKFLOW_NOT_FOUND` on routes that previously answered with a different
  code:
  - `GET /workflows/{id}/versions/{n}` previously gave
    `WORKFLOW_VERSION_NOT_FOUND`;
  - `POST /workflows/{id}/triggers/{t}/invoke` previously gave
    `TRIGGER_NOT_FOUND`;
  - `POST /workflows/{id}/versions/{n}/execute` previously gave
    `WORKFLOW_VERSION_NOT_FOUND`.
- **Admin-only.** `/credentials/*` is admin-only (403 for members).
- **Workflow listing.** `GET /workflows` returns only the caller's workflows
  for members, and all workflows for admins.
- **Ownership.** `POST /workflows` and `POST /workflows/import` set
  `owner_id` to the caller.
- **Response field.** `WorkflowSummary` and `WorkflowDetail` gain
  `owner_id: string | null`. This is an additive change.
- **`GET /metrics`** requires `Bearer <AKASHA_METRICS_TOKEN>` or an admin
  session. Previously it was unauthenticated.

**Unchanged and public:** `/health`, `/ready`, `/openapi.json`, `/docs`,
`/redoc`, and `POST /api/v1/webhooks/{workflow_id}/{trigger_id}`.

**Error body format:** unchanged (`code`, `message`, `details`,
`request_id`).

**Compatibility.** All of this is breaking for unauthenticated clients, by
design. The frontend currently uses mock auth (`src/mock/fakeApi`) and
must integrate with the real endpoints in its own Phase 21.

---

## 8. Implementation Details

**Request flow (protected route).**
1. `current_identity` parses the `Authorization` header (`bearer_token`).
   The scheme is matched case-insensitively. An empty token, or one longer
   than 256 characters, is treated as missing.
2. `AuthenticationService.authenticate` looks up the session with
   `SessionRepository.get_active(token_hash, now)`. That is a single
   `SELECT` joining `users`, with `expires_at > now` and `NOT disabled`.
   No match raises `AuthenticationRequiredError`.
3. `request.state.user_id` is set for the access log.
4. `authorize_path` inspects `request.path_params` and calls
   `AuthorizationService.require_workflow` or `require_execution`.
   - These load the workflow (or the execution, then its workflow) and apply
     `may_access_workflow`.
   - A denial raises the same not-found error that an unknown id would.
5. For the credentials and users routers, `require_admin` raises
   `PermissionDeniedError` for members.

`authorize_path` and `_metrics_access` resolve their services lazily from
the container. As a result, routes without ids, and `/metrics` with a valid
scrape token, do not require a database. Without a database, every
session-protected route answers 500 `CONFIGURATION_ERROR`, so the API fails
closed.

**Login.**
1. The username is lowercased and used as the throttle key.
2. `LoginThrottle.attempt` raises 429 when the key has 5 or more attempts
   inside 15 minutes. Otherwise it records the attempt immediately.
3. The service loads `(User, hash)`.
   - If the user is unknown, it verifies against a dummy hash created at
     service construction, so both cases cost one scrypt run.
   - A disabled user is rejected after verification, so the timing matches.
4. On failure it logs `login_failed` (user id only) and raises
   `INVALID_CREDENTIALS`.
5. On success it resets the key and creates the session. In the same
   transaction it deletes expired sessions (`delete_expired`).

scrypt runs in `asyncio.to_thread` so it does not block the event loop.

**Setup.** `UserRepository.add_first` runs `LOCK TABLE users IN SHARE ROW
EXCLUSIVE MODE`, counts the rows and inserts only into an empty table. This
lock mode conflicts with itself, so concurrent setups are serialized.

**Password change** (`change_password`):
1. The new password is validated and hashed.
2. A throttle attempt is recorded under the username.
3. The current password is verified.
4. The hash is updated.
5. All other sessions are deleted (`delete_for_user(keep=current)`).
6. The throttle is reset.

**Admin update** (`update_user`):
- When an active admin is being disabled, `lock_active_admins()` runs
  `SELECT … FOR UPDATE` over the active admin rows. If the count is 1 or
  less, it raises `LAST_ADMIN`.
- In PostgreSQL READ COMMITTED, a concurrent transaction waiting on these
  locks re-evaluates the `WHERE` clause. So two admins disabling each other
  cannot both succeed; this is verified by a test.
- Disabling a user or resetting their password deletes all of that user's
  sessions.

**Throttle memory.** When more than 10,000 keys are tracked, keys with no
attempt inside the window are dropped before a new one is added.

**Configuration.**
- `AuthenticationService` receives `session_ttl` from
  `settings.session_ttl_seconds`.
- The hasher and clock default to the production values. Tests inject
  `FAST_HASHER` and a controllable clock.

**Audit logging.**
- The structured events `setup_completed`, `login_succeeded`,
  `login_failed`, `logout`, `password_changed`, `user_created` and
  `user_updated` carry `user_id` / `actor_id`.
- Usernames are deliberately not logged.

---

## 9. Findings

### Finding: FastAPI 0.141 resolves included routers lazily
**Impact:** LOW
**Resolution:** WORKAROUND
**Notes:** `app.routes` lists only the application's own routes plus
`_IncludedRouter` wrappers, not the included routes. The default-deny test
therefore enumerates routes from `/openapi.json` and adds `/metrics`
explicitly, because that route has `include_in_schema=False`. A future
route with `include_in_schema=False` would not be covered by that test
automatically.

### Finding: The access check changes 404 codes for nonexistent workflows
**Impact:** LOW
**Resolution:** FIXED (tests updated to the new, more precise behavior)
**Notes:**
- Previously, `GET /workflows/{unknown}/versions/1` answered
  `WORKFLOW_VERSION_NOT_FOUND`, and invoking a trigger on an unknown workflow
  answered `TRIGGER_NOT_FOUND`.
- Now both answer `WORKFLOW_NOT_FOUND`.
- Affected tests: `test_unknown_workflow_is_structured_404_with_request_id`
  and `test_invoke_unknown_or_foreign_type_is_404`.

### Finding: The webhook route shared a router with authenticated trigger routes
**Impact:** MEDIUM
**Resolution:** FIXED
**Notes:** Router-level protection would have blocked webhooks. The route
was moved to `create_webhooks_router()` without changing its behavior.
`test_webhooks_stay_public` covers it.

### Finding: Credential use is not owner-scoped
**Impact:** MEDIUM
**Resolution:** DEFERRED
**Notes:** See §5.4 and §21.

### Finding: The documented `/metrics` behavior ("unauthenticated until Phase 20") needed a decision
**Impact:** LOW
**Resolution:** FIXED
**Notes:**
- Prometheus cannot log in, so the static `AKASHA_METRICS_TOKEN` was added.
- Without it, `/metrics` needs an admin session.
- Existing deployments that scrape without a token will get 401 after
  upgrading.

---

## 10. Bugs / Problems Found

### Login throttle bypass via parallel requests (introduced in this phase, fixed)
- **Problem:** Parallel login requests could all pass the throttle before
  any failure was recorded.
- **Root cause:** The first implementation called `check()` before password
  verification and `fail()` after it. Verification awaits
  `asyncio.to_thread`, so N parallel requests all passed `check()` while
  the count was still below the limit.
- **Fix:** The API became `LoginThrottle.attempt()` plus `reset()`.
  `attempt()` checks and records atomically (no `await` between the two
  steps), before verification. `reset()` runs on success. The same change
  was applied to `change_password`.
- **Regression test:** `test_parallel_guesses_cannot_outrun_the_throttle`
  sends 20 parallel guesses and expects exactly 5 `INVALID_CREDENTIALS` and
  15 `LOGIN_THROTTLED`.
- **Affected area:** `application/auth.py`.

### mypy error on `Insert.on_conflict_do_nothing` (introduced in this phase, fixed)
- **Problem:** The return type of `SqlAlchemyUserRepository._insert` was
  the generic `sqlalchemy.Insert`, which lacks the PostgreSQL-specific
  method.
- **Fix:** It is now typed as `sqlalchemy.dialects.postgresql.Insert`.

### Test fixtures broken by protection (expected consequence, fixed)
- Five existing tests failed after enabling protection:
  - three were 404-code or configuration expectations (§9);
  - one was the database-outage test, which now also wires auth so that a
    dead database surfaces as 500 `INTERNAL_ERROR`, not 401;
  - one was the `/metrics` test.
- `mypy .` over the tests then reported 5 errors:
  - `FakeUow` was missing `users` / `sessions`;
  - the `FakeWorkflows.list` signature needed `owner_id`;
  - `metrics_token` was passed as `str` instead of `SecretStr`;
  - one `Any` return.
- All were fixed.

No pre-existing bugs were discovered.

---

## 11. Fixes Applied

1. **Throttle race.**
   - Symptom: parallel guesses bypassed the limit (found in review).
   - Implementation: attempts are counted before the password check.
   - Verification: the new parallel test passes.
2. **Guards needed a database before they could decide.**
   - Symptom: routes without ids, and `/metrics` with a scrape token,
     answered `CONFIGURATION_ERROR` without a database, because the
     services were injected eagerly as dependencies.
   - Implementation: `authorize_path` and `_metrics_access` resolve
     services lazily.
   - Verification: `test_request_validation_error_is_structured` and
     `test_metrics_endpoint_serves_prometheus_text` pass.
3. **Private helper imported across modules.**
   - `_auth` was renamed to `auth_service` because `routes.py` imports it.
4. **Naming clash.**
   - The request schema `Credentials` was renamed to `LoginRequest`, to
     avoid confusion with the credential store.
5. **Leftover indirection.**
   - A helper `load_execution_not_found` was removed in favor of raising
     `ExecutionNotFoundError` directly.

---

## 12. Testing & Verification

### Unit Tests — `tests/test_auth_units.py` (31, PASS)
- **Hashing:**
  - the production defaults equal N=2^14, r=8, p=5, and the hash prefix is
    `scrypt$16384$8$5$`;
  - verify / reject, and the hash never contains the password;
  - salts are unique;
  - a hash verifies under different default parameters;
  - 8 malformed stored hashes never match (bad scheme, bad number, bad
    base64, n not a power of two, n requiring 1 TiB, a missing field).
- **Validation:** 5 valid and 8 invalid usernames; password length bounds.
- **Access rule:** `may_access_workflow` for owner, other member, admin and
  an ownerless workflow.
- **Token hash:** `token_hash` is stable and 64 hex characters.
- **Throttle** (fixed timestamps): blocking with an exact `Retry-After`,
  per-key isolation, window expiry, refused attempts not being counted,
  reset, and the memory bound.

### API / Integration / Security Tests — `tests/integration/test_auth_api.py` (49, PASS)
These run against real PostgreSQL 17.2 (Testcontainers) through
`httpx.ASGITransport`, with a controllable clock.

- **Setup:**
  - only once;
  - 5 concurrent setups give one 201 and four 409s, with one row;
  - input validation, and the password never echoed.
- **Login:**
  - success, `expires_at = now + TTL`, `/auth/me`;
  - case-insensitive usernames;
  - identical error for a wrong password and an unknown user, plus
    `WWW-Authenticate`;
  - a disabled user is rejected.
- **Storage and logs:**
  - `password_hash` starts with `scrypt$` and does not contain the
    password;
  - the stored session hash is not the token;
  - no password, token or wrong password appears in captured JSON logs;
  - audit events are present;
  - the access log has `user_id`.
- **Brute force:**
  - lockout after 5 failures, even with the correct password;
  - `Retry-After: 901`;
  - other users are unaffected;
  - existing sessions keep working;
  - the lock expires after the window;
  - 20 parallel guesses;
  - unknown usernames are throttled too.
- **Sessions:**
  - 8 missing or malformed `Authorization` variants give 401;
  - expiry is exact at the TTL boundary;
  - expired sessions are purged on login;
  - logout revokes only the current session, and a second logout is 401.
- **Passwords:**
  - a change revokes other sessions and keeps the current one;
  - the old password stops working;
  - a wrong current password is 401;
  - a weak new password is 422.
- **User management:**
  - create, duplicate (409) and list, with no password fields;
  - disabling revokes sessions, and they stay revoked after re-enabling;
  - an admin reset revokes sessions;
  - the last admin cannot be disabled (409 `LAST_ADMIN`), but can be once
    a second admin exists;
  - concurrent mutual disabling leaves exactly one active admin;
  - an unknown user is 404.
- **Default deny:** every route in the OpenAPI document except 5 public
  ones, plus `/metrics`, answers 401 `AUTHENTICATION_REQUIRED` without a
  session (at least 25 routes asserted).
- **Admin areas:** 8 parametrized member requests against the credentials
  and users APIs give 403, never echo the secret, and change nothing.
- **Metrics:**
  - the scrape token and an admin session give 200;
  - a member gets 403;
  - no token or a wrong token gives 401;
  - the scrape token is not a user session.
- **Ownership:**
  - 13 workflow-, execution- and report-scoped operations by another member
    all give 404 with the correct code and no workflow name in the body;
  - the listing is empty;
  - the database is unchanged (`revision`, `current_version`, execution
    count);
  - the owner and an admin have access;
  - the admin listing shows `owner_id`;
  - an imported workflow belongs to the importer;
  - an ownerless workflow is admin-only;
  - the webhook stays public (202).

### Infrastructure / Migration Tests (PASS)
- `test_migrations_upgrade_downgrade_upgrade_on_empty_database`: head
  `0008_auth`; `users` and `sessions` are present after upgrade and absent
  after downgrade.
- `test_0008_upgrade_leaves_existing_workflows_ownerless`:
  - a row written under 0007 loads with `owner_id is None`;
  - saving a workflow whose owner does not exist raises `IntegrityError`
    (the foreign key is enforced).

### Regression
The full suite passes: 1598 passed, 5 skipped. The skips are external
OpenRouter smoke tests that need `AKASHA_SMOKE_OPENROUTER_API_KEY`.

### Not performed
- No performance tests or benchmarks.
- No E2E test through a real running server process.
- No frontend or accessibility tests; they are not applicable.

---

## 13. Quality Gates

Run from `backend/` on the final working tree.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `uv run ruff format --check .` — 170 files already formatted |
| Linter | PASS | `uv run ruff check .`. Lint config changed: `S105`/`S106` ignored under `tests/**` for test passwords, documented in `pyproject.toml` |
| Type Checker | PASS | `uv run mypy .`: no issues in 166 source files. It first failed with 5 test-side errors, all fixed (§10) |
| Unit Tests | PASS | part of the full run |
| Integration Tests | PASS | PostgreSQL 17.2 via Testcontainers; `AKASHA_TESTS_REQUIRE_DOCKER=1` |
| API Tests | PASS | `test_auth_api.py` plus the adapted existing API tests |
| E2E Tests | N/A | no separate E2E performed |
| Build | N/A | no build step beyond `uv` install; the package rebuilt without error during `uv run` |
| Security Checks | PASS | covered by the security tests in §12; no dependency audit tool was run |
| Accessibility | N/A | backend only |
| Full suite | PASS | `uv run pytest`: 1598 passed, 5 skipped, 1 warning (pre-existing `StarletteDeprecationWarning` from `fastapi.testclient`) |

Intermediate failures: after protection was first enabled, the full run
showed 5 failed and 1512 passed. They were resolved as described in §10.

---

## 14. Acceptance Criteria Verification

Roadmap §24 has one acceptance criterion. It also lists required tests and
protected areas, which are evaluated here as well.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Unauthenticated clients cannot access protected application functionality | PASS | `test_every_non_public_route_requires_authentication` (all non-public routes give 401); `test_missing_or_invalid_token_is_401`; `test_metrics_access` |
| Protect workflows | PASS | `test_members_cannot_see_or_touch_other_members_workflows`, `test_ownerless_workflows_are_admin_only` |
| Protect executions | PASS | same test: `/executions/{id}` and `/reports/executions/{id}` give 404 `EXECUTION_NOT_FOUND`; the execute and invoke routes are blocked and the execution count is unchanged |
| Protect credentials | PASS | `test_members_cannot_reach_admin_areas` (5 credential operations give 403, no secret echoed); unauthenticated access gives 401 |
| Protect plugins | PASS (vacuous) | No HTTP plugin endpoints exist (architecture §36: install is CLI-only by design). Nothing to protect over HTTP |
| Protect settings | PASS (vacuous) | No settings endpoints exist. `/metrics` (operational data) is now protected |
| Tests: login | PASS | `test_login_issues_a_session_token`, `test_login_is_case_insensitive_on_username` |
| Tests: logout | PASS | `test_logout_revokes_only_that_session` |
| Tests: invalid credentials | PASS | `test_invalid_credentials_do_not_reveal_which_part_was_wrong`, `test_disabled_user_cannot_log_in` |
| Tests: session handling | PASS | `test_session_expires`, `test_expired_sessions_are_purged_on_login`, `test_change_password_revokes_other_sessions`, `test_disabling_a_user_revokes_their_sessions`, `test_password_reset_revokes_sessions` |
| Tests: unauthorized access | PASS | default-deny test, 401 variants, 403 admin areas, 404 ownership |
| Tests: authorized access | PASS | `test_owner_and_admin_have_access`, `test_admin_manages_users` |
| Tests: credential protection | PASS | `test_members_cannot_reach_admin_areas`; existing `test_openapi_responses_have_no_value_field` |
| Session request: no plaintext passwords, secure hashing | PASS | `test_passwords_and_tokens_are_stored_only_as_hashes`, hashing unit tests |
| Session request: brute-force boundary | PASS (in-process) | throttle tests incl. parallel; limitation §20 |

---

## 15. Security Considerations

- **Authentication:**
  - 256-bit random tokens;
  - only hashes stored;
  - absolute expiry;
  - revocation on logout, password change, reset and disable;
  - disabled users are rejected at the session lookup even if a session
    row remained.
- **Passwords:**
  - scrypt with OWASP parameters and per-hash salt and parameters;
  - constant-time comparison;
  - hashing in a worker thread;
  - the length policy caps input at 256 characters, which bounds hashing
    cost.
- **Username enumeration:**
  - the same error and message for an unknown user and a wrong password;
  - a dummy hash verification equalizes timing;
  - a disabled user is checked after verification.
  - Setup responses reveal whether setup was completed. This is inherent.
- **Brute force:** per-username lockout with pessimistic counting (§10).
  - An attacker can lock out a known username for 15 minutes. This
    trade-off is documented in `LoginThrottle`.
  - There is no per-IP limit, because client addresses behind a proxy are
    not trustworthy without configuration.
- **Authorization:**
  - server-side, default-deny, and checked before handlers run;
  - 404 for foreign resources, so existence is not leaked;
  - 403 only for role checks;
  - ownerless workflows are admin-only (fail-closed upgrade).
- **CSRF:** there are no cookies, so there is no ambient credential.
- **Secrets in logs and errors:**
  - request passwords are `SecretStr`;
  - validation errors never echo values (tested);
  - audit logs carry ids, not usernames;
  - tokens are never logged (tested).
- **Error disclosure:** a database outage during the session lookup
  surfaces as 500 `INTERNAL_ERROR` with no driver details
  (`test_infrastructure_failure_is_opaque_500`), not as 401.
- **Metrics:** the static token is compared with `hmac.compare_digest` and
  must be at least 32 characters.
- **Open exposures:**
  - public setup until it is completed;
  - public webhooks;
  - installation-scoped credential use (§20).

---

## 16. Performance Considerations

- **Per-request cost:** each authenticated request adds one indexed
  primary-key lookup on `sessions`, joined with `users`.
- **Access checks:**
  - routes with `workflow_id` add one workflow load;
  - routes with `execution_id` add an execution aggregate load plus a
    workflow load;
  - `require_workflow` loads the full workflow row, including the draft
    graph JSONB, where only `owner_id` is needed.
  - This was not measured. If it shows up in profiles, the next step is a
    dedicated owner-only repository query.
- **Login cost:** each login costs one scrypt run, about 170 ms on the
  development machine (measured once while choosing parameters) and 16 MiB
  of memory. It runs in the default thread pool, so many concurrent logins
  are bounded by the pool size.
- **Expired sessions** are purged on each successful login with a single
  `DELETE … WHERE expires_at <= now` (indexed).
- **Throttle memory** is bounded to about 10,000 tracked keys.
- No benchmarks were run.

---

## 17. Documentation Changes

- **`docs/architecture.md`**
  - §59 Authentication: implementation notes covering users, hashing,
    sessions, setup, the last-admin rule, enumeration and throttling,
    error codes and audit events.
  - §60 Authorization: implementation notes covering roles, ownership,
    default-deny enforcement, 404-vs-403, credential scoping and the
    public routes.
  - §113 Metrics, implementation note: `/metrics` authentication.
- **`backend/README.md`:** the `AKASHA_SESSION_TTL_SECONDS` and
  `AKASHA_METRICS_TOKEN` settings, and the first-run setup and login flow.

**To update in future phases:**
- `docs/roadmap-backend.md` was not changed.
- Architecture §61 (audit) still describes persisted audit records, which
  do not exist yet.
- The frontend roadmap Phase 21 should reference the real `/auth/*`
  endpoints.

---

## 18. Dependencies / Infrastructure Changes

- **New dependencies:** none. Password hashing uses stdlib
  `hashlib.scrypt`, which avoids adding `argon2-cffi` or `bcrypt`.
- **Environment variables:**
  - `AKASHA_SESSION_TTL_SECONDS` (default 43200);
  - `AKASHA_METRICS_TOKEN` (optional, at least 32 characters).
- **Database:** migration `0008_auth` (§6).
- **Test infrastructure:**
  - `truncate_all` includes `sessions` and `users`;
  - the `AuthKit` helper;
  - the fast scrypt parameters used in tests (N=2^4), while production
    defaults are asserted separately.
- **Tooling:** ruff per-file ignores for `tests/**` extended with
  `S105` and `S106`.
- **Docker / Redis:** no changes.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion |
|---|---|---|---|
| Per-trigger webhook secrets / signatures | Security hardening scope; webhooks must stay callable by external systems | 25 | No |
| Owner- or grant-scoped credential resolution | Needs owner-aware resolution in the runtime | not scheduled (future authorization work) | No |
| Distributed login throttle (Redis) | No Redis/worker architecture yet | 22 | No |
| Persisted, immutable audit records (§61) | Not in the Phase 20 roadmap scope; structured log events exist | not scheduled | No |
| CLI user management / bootstrap admin | CLI phase | 21 | No |
| Setup-status endpoint (`setup_required`) | Not required by the backend acceptance criteria; the frontend can react to 409 | frontend Phase 21 if needed | No |
| Role change, user deletion, self-registration | Not required; RESTRICT on owned workflows | not scheduled | No |
| Password hash re-hashing when defaults change | Parameters are stored per hash, so old hashes keep verifying | not scheduled | No |
| Recording the triggering user on executions | Reproducibility metadata; not in scope | not scheduled | No |

---

## 20. Known Limitations

- **Open setup on a fresh install.** Until the first admin exists,
  anyone who can reach the API can call `POST /auth/setup` and become
  admin. Documented in the README ("complete it before exposing the engine
  to a network").
- **Webhooks are unauthenticated.** Anyone who knows a workflow id and a
  webhook trigger id can create executions.
- **Credential use.** A member cannot read credentials via the API, but
  can reference any credential name in a node configuration, and the
  runtime will resolve it.
- **In-process throttle.** Each API process counts separately, and a
  restart clears the counters (marked with a `ponytail:` comment in
  `LoginThrottle`).
- **Throttle lockout.** A known username can be locked out for 15 minutes
  by an attacker.
- **Coverage gaps in the automatic checks.**
  - Automatic ownership checks apply only to path parameters named
    `workflow_id` or `execution_id`.
  - The default-deny test covers only routes that appear in the OpenAPI
    document, plus `/metrics`.
- **Operator-created workflows.** Services called without an owner (future
  CLI) create admin-only workflows.
- **Concurrent mutual disabling.** When two admins disable each other
  concurrently, the losing request may answer 401 instead of 409, because
  its own session was revoked first. Either way, exactly one admin stays
  active.
- **Unbounded session count.** There is no limit on sessions per user.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Public webhooks | Execution creation by anyone knowing two ids | MEDIUM | triggers API | Per-trigger secret or HMAC signature | Phase 25 |
| Credentials usable by any member's workflow | Credential exfiltration by a malicious member through a node | MEDIUM | runtime / credentials | Owner-aware credential resolution or credential grants | future authorization work |
| First-run setup race window | Takeover of a freshly deployed, network-exposed engine | MEDIUM | auth | Operator bootstrap via CLI or env and an option to disable HTTP setup | Phase 21 / 26 |
| In-process throttle | Weaker brute-force protection with several API processes | LOW | auth | Move counters to Redis | Phase 22 |
| `/metrics` now requires a token | Existing Prometheus scrapes get 401 after upgrade | LOW | operations | Document in the deployment guide | Phase 26 |

---

## 22. Architectural / Product Decisions

- **Enforcement point.** Authorization is enforced by router dependencies
  keyed on the path parameter names `workflow_id` and `execution_id`. New
  routes must keep these names, or add their own check, to stay covered.
- **404 vs 403.** Foreign resources answer 404 so ids cannot be probed.
  403 is only for role checks.
- **Ownerless workflows are admin-only.** This makes the migration
  fail-closed and gives the CLI a safe default.
- **The password hash is outside `User`.** This keeps credential material
  out of entity reprs and API mapping, mirroring how credential ciphertext
  is handled.
- **Absolute session expiry, no refresh tokens.** This is simpler.
  Re-login after the TTL is acceptable for V1 self-hosted use.
- **scrypt over argon2.** It needs no new dependency (AGENTS §53), and
  OWASP lists it as acceptable. Parameters are stored per hash, so the
  defaults can be raised later without migration.
- **Per-username throttle, counted before verification.** Client IPs are
  unreliable behind proxies. Counting after verification was proven racy.
- **The `/metrics` scrape token.** Prometheus cannot hold an expiring
  session.
- **Webhooks moved to their own public router** instead of adding
  exceptions inside the protected router.

---

## 23. Repository / Git State

- **Branch:** `main`. The last commit is `9d1b6aa backend phase 19 -
  reports`.
- **Working tree:** uncommitted Phase 20 changes.
  - 26 tracked files modified (764 insertions, 79 deletions, per
    `git diff --stat`, before this report).
  - 7 new untracked files: `migrations/versions/0008_auth.py`,
    `src/akasha/api/auth.py`, `src/akasha/application/auth.py`,
    `src/akasha/domain/identity.py`, `src/akasha/security/passwords.py`,
    `tests/integration/test_auth_api.py` and `tests/test_auth_units.py`.
  - This report is also new.
- **Unrelated changes:** none detected. All modified files belong to this
  phase.
- **Commit:** nothing was committed.

---

## 24. Phase Completion Assessment

### Implemented
- Local users and roles.
- scrypt password hashing.
- Opaque server-side sessions with expiry and revocation.
- Setup, login, logout, me and password change.
- Admin user management with last-admin protection.
- Login throttle.
- Workflow ownership and a migration.
- Default-deny protection of all non-public routes.
- Ownership checks for workflow, execution and report routes.
- Admin-only credentials and users.
- A protected `/metrics` endpoint.
- Audit log events.
- Documentation.

### Verified
- 81 new tests plus the adapted existing tests.
- Full suite: 1598 passed, 5 skipped (external).
- ruff format and check: PASS.
- `mypy .`: PASS.

### Not Implemented
- SSO, RBAC beyond two roles, user deletion, role change, self-registration.
- Persisted audit records.

### Deferred
- Webhook secrets (Phase 25).
- Distributed throttle (Phase 22).
- CLI user bootstrap (Phase 21).
- Owner-scoped credential use (future).

### Open
- See §21.

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**Phase 21 (CLI) can assume:**
- Application services are principal-free, except `UserService`, which
  requires an admin `Identity`.
- `WorkflowService.create_workflow(..., owner_id=None)` and
  `WorkflowPackageService.import_workflow(..., owner_id=None)` create
  admin-only workflows when no owner is given.
- `AuthenticationService.setup(username, password)` is the bootstrap path
  a CLI command can reuse. It works only on an empty user table.
- The services are available on `Container.auth`, `Container.authorization`
  and `Container.users`. They are wired only when `database_url` is set.

**Contracts now available:**
- `/api/v1/auth/{setup,login,logout,me,password}` and `/api/v1/users`.
- Bearer auth on all other `/api/v1` routes except webhooks.
- `owner_id` on workflow responses.
- Error codes `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIALS`,
  `LOGIN_THROTTLED`, `PERMISSION_DENIED`, `SETUP_ALREADY_COMPLETED`,
  `USERNAME_TAKEN`, `USER_NOT_FOUND`, `LAST_ADMIN`.

**Must not be changed casually:**
- The `protected` router composition in `api/routes.py`. New business
  routers belong inside it.
- The path parameter names `workflow_id` and `execution_id`, which the
  access check relies on.
- `PUBLIC_ROUTES` in `tests/integration/test_auth_api.py`. Adding to it is
  a security decision.
- Storing only token hashes and never the password inside `User`.
- `may_access_workflow` as the single workflow access rule.

**Constraints for Phase 22 (workers):** the login throttle is per process
and should move to Redis once several API processes exist.

**Prerequisites:** apply migration `0008_auth`. Set `AKASHA_METRICS_TOKEN`
where Prometheus scrapes `/metrics`.
