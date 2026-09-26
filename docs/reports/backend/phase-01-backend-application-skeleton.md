# Phase 01 — Backend Application Skeleton

## 1. Executive Summary

Phase 01 required establishing the backend application architecture: the ten
architectural layers, application bootstrap, explicit configuration, a FastAPI
application with routing foundation, health/readiness endpoints, lifecycle
handling, an error-handling foundation, structured logging, and a testable
application factory.

All of it was implemented on top of the Phase 00 skeleton. The result is a
running HTTP service: `uv run akasha` loads validated configuration from
`AKASHA_*` environment variables (fails fast on invalid input), builds the
FastAPI app via a factory, and serves `GET /health`, `GET /ready`, and an
empty versioned `/api/v1` router. Layer boundaries are documented in each
package's `__init__.py` and — not just convention but enforced — verified
by a static AST test (`tests/test_architecture.py`) that fails the build if
the domain layer imports FastAPI/SQLAlchemy/Redis/etc.

All phase acceptance criteria are satisfied. 20 tests pass; ruff check, ruff
format, and mypy (strict) all pass; a real-process smoke test (`uv run akasha`
+ curl) confirmed `/health` and `/ready` respond correctly.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Per `/docs/roadmap-backend.md` §5 (Phase 01 — Backend Application Skeleton):

> Create the architectural layer structure.

Scope: initial separation of `api/ application/ domain/ infrastructure/
runtime/ nodes/ providers/ plugins/ security/ observability/`; application
bootstrap; dependency injection foundation; configuration service; logging
foundation; API application; health endpoint; readiness endpoint.

API deliverables: `GET /health`, `GET /ready`.

Required tests: application startup, health endpoint, readiness endpoint,
configuration loading, dependency wiring.

Acceptance criterion: the backend starts with a clearly separated
architecture; no business logic in API routes.

The phase was explicitly NOT responsible for: the domain model (Phase 02),
persistence/PostgreSQL (Phase 05), any business endpoints under `/api/v1`
(Phase 06+), authentication (Phase 20), metrics beyond logging (Phase 12).

---

## 3. Scope Implemented

### Architecture
- Ten layer packages created under `backend/src/akasha/`: `api/`,
  `application/`, `domain/`, `infrastructure/`, `runtime/`, `nodes/`,
  `providers/`, `plugins/`, `security/`, `observability/`.
- Each `__init__.py` documents the layer's responsibility, its allowed/forbidden
  dependencies, and which phase introduces its content. These are boundary
  documents, not empty markers.
- `tests/test_architecture.py` enforces the dependency direction statically:
  it parses every `.py` file inside the guarded layers with `ast` and fails
  if e.g. `domain/` imports `fastapi`, `sqlalchemy`, `redis`, `httpx`, or any
  other akasha layer (`api`, `application`, `infrastructure`, `runtime`), and
  if `api/` imports `infrastructure`. It also asserts all ten layers exist.

### Configuration
- `src/akasha/config.py`: `Settings(BaseSettings)` via `pydantic-settings`,
  env prefix `AKASHA_` (`environment`, `host`, `port`, `log_level`).
  `load_settings()` raises `pydantic.ValidationError` on invalid config —
  startup fails fast before uvicorn binds.

### Bootstrap / Dependency Injection
- `src/akasha/bootstrap.py`: `Container` dataclass (composition root) holding
  `settings` and `ready_checks: dict[str, ReadyCheck]`; `create_container()`.
  `Container.readiness_failures()` runs all registered async checks and
  returns `{name: reason}` for failures.
- Container is stored on `app.state`; no global singletons. Tests build their
  own app/container through the same factory functions.

### API Application
- `src/akasha/api/app.py`: `create_app(settings, container)` factory; FastAPI
  `lifespan` context logs `application_started` / `application_stopped`
  structured events; error handlers registered centrally.
- `src/akasha/api/routes.py`:
  - `GET /health` → `{"status": "ok", "version": __version__}` — dependency-free
    ("process lives", per backend/AGENTS.md §67).
  - `GET /ready` → runs container readiness checks; `200 {"status": "ready", "failures": {}}`
    or `503 {"status": "not_ready", "failures": {name: reason}}`.
  - `create_api_v1_router()` — an empty `APIRouter(prefix="/api/v1")`, the
    mounting point for business endpoints from Phase 06 onward.

### Error-Handling Foundation
- `src/akasha/errors.py`: `AkashaError` base (`code`, `http_status`, `message`,
  `details`) and `ConfigurationError` subclass.
- `src/akasha/api/schemas.py`: `ErrorResponse` Pydantic model (`code`,
  `message`, `details`, `request_id`); `request_id()` uses the `X-Request-Id`
  header or generates a UUID.
- Handlers in `register_error_handlers()`:
  - `AkashaError` → its own code/status.
  - `RequestValidationError` → `422 VALIDATION_ERROR` with per-field messages.
  - `StarletteHTTPException` → mapped codes (`404 NOT_FOUND`, `405`, `401`,
    `403`, `409`) so no default `{"detail": ...}` bodies leak.
  - bare `Exception` → `500 INTERNAL_ERROR`, generic message; the traceback is
    logged server-side via `logger.exception` only.

### Logging Foundation
- `src/akasha/observability/logging.py`: `JsonFormatter` (stdlib-only) emits
  one JSON object per record with `timestamp` (UTC), `level`, `logger`,
  `message`, exception info, and any `extra` context fields flattened to top
  level. `configure_logging(level)` installs it on the root logger.
- In `environment == "test"`, `create_app` deliberately does NOT reconfigure
  logging, so pytest's `caplog` handlers are not evicted.

### Entry Point
- `src/akasha/main.py` replaced the Phase 00 stub: `main()` loads settings
  (fail-fast) and runs `uvicorn.run("akasha.api.app:create_app", factory=True,
  host=..., port=...)`.

### Testing
- 8 test files, 20 tests (see §12).

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/pyproject.toml` | Modified | Added `fastapi`, `pydantic-settings`, `uvicorn[standard]`; dev: `httpx` |
| `backend/uv.lock` | Modified | Lockfile regenerated by `uv add` |
| `backend/src/akasha/main.py` | Modified | Phase 00 stub → real uvicorn bootstrap |
| `backend/tests/test_foundation.py` | Modified | `main()` now blocks on a server; replaced call with factory smoke test |
| `backend/src/akasha/config.py` | Created | Central validated configuration |
| `backend/src/akasha/bootstrap.py` | Created | Composition root (`Container`, readiness checks) |
| `backend/src/akasha/errors.py` | Created | `AkashaError` hierarchy foundation |
| `backend/src/akasha/api/{__init__,app,routes,schemas}.py` | Created | App factory, routers, error schemas/handlers |
| `backend/src/akasha/observability/{__init__,logging}.py` | Created | JSON logging foundation |
| `backend/src/akasha/{application,domain,infrastructure,runtime,nodes,providers,plugins,security}/__init__.py` | Created | Layer boundary documents (rules + target phase) |
| `backend/tests/conftest.py` | Created | `settings` / `client` fixtures |
| `backend/tests/test_architecture.py` | Created | Dependency-direction AST guard + layer existence |
| `backend/tests/test_config.py` | Created | Defaults, env override, fail-fast validation |
| `backend/tests/test_health.py` | Created | `/health`, `/ready` (ok + failing check), v1 path isolation |
| `backend/tests/test_lifecycle.py` | Created | Lifespan log events, container wiring into `app.state` |
| `backend/tests/test_logging.py` | Created | JSON formatter context fields |
| `backend/tests/test_errors.py` | Created | Error mapping, 500 traceback redaction, structured 404 |
| `backend/README.md` | Modified | Run/config/layout documentation updated to Phase 01 state |

No migrations (persistence arrives in Phase 05). `backend/migrations/` still
contains only `.gitkeep`. No unrelated user changes were present or touched.

---

## 5. Architecture Changes

### Decision 1: Composition root as a dataclass on `app.state`, not a DI framework

- **Context:** Phase 01 requires an "dependency injection foundation". Frameworks
  (e.g. `dependency-injector`, `punq`) add a dependency and indirection with no
  use cases to inject yet.
- **Chosen approach:** `bootstrap.Container` dataclass, created by
  `create_container(settings)`, stored on `app.state`; `create_app` accepts `settings`
  and `container` overrides, which is the seam tests already use
  (`test_container_is_wired_into_app_state`).
- **Alternatives considered:** DI framework; FastAPI `Depends` wiring everything
  (rejected — couples composition to HTTP; application services must also be
  usable from CLI/workers).
- **Consequences:** later phases add repositories/services as `Container` fields
  or constructor args of application services. The seam for test substitution
  exists now; no framework lock-in. If DI complexity ever outgrows this, the
  factory functions remain the single place to upgrade.

### Decision 2: Logging via stdlib + a small JSON formatter, not structlog

- **Context:** backend/AGENTS.md §51 requires structured logs; the roadmap
  assigns full observability to Phase 12.
- **Chosen approach:** ~30-line `JsonFormatter` on stdlib logging; context via
  `extra={...}`. No new dependency.
- **Consequences:** Phase 12 extends this (correlation-id middleware,
  metrics). If field interpolation/redaction requirements grow beyond what a
  formatter should do, introducing `structlog` behind `configure_logging` is a
  localized change.

### Decision 3: Readiness as a registered-check registry

- **Context:** §67 (backend/AGENTS.md) demands readiness ≠ "HTTP server runs",
  but Phase 01 has no dependencies to check (PostgreSQL arrives in Phase 05).
- **Chosen approach:** `Container.ready_checks: dict[str, ReadyCheck]`,
  `await readiness_failures()` → `{name: reason}`; `/ready` returns 503 if any
  check fails. With zero checks the engine is ready by definition.
- **Consequences:** Phase 05+ registers a `database` check, Phase 22 a `redis`
  check, without touching the endpoint. Documented in-code so a later phase
  cannot mistake `/ready` for a liveness duplicate.

### Decision 4: Dependency boundaries enforced by a static test

- **Context:** "clearly separated architecture" is otherwise unverifiable —
  boundaries written only in docstrings rot silently.
- **Chosen approach:** `test_architecture.py` walks guarded layer packages with
  `ast` and fails on forbidden imports (`domain` → fastapi/sqlalchemy/redis/
  httpx/pydantic_settings/other layers; `application`/`runtime`/`nodes` →
  fastapi/starlette; `api` → `infrastructure`).
- **Alternatives considered:** `import-linter` (capable but a new dependency
  and config to learn; the AST check is ~40 lines and exactly targets the
  documented rules). **Reason:** zero new tooling, runs in the normal suite,
  extendable per layer.
- **Consequences:** future phases adding domain code violate the build the
  moment they import FastAPI — the enforcement exists before the domain does.

### Decision 5: Operational endpoints outside `/api/v1`

`/health` and `/ready` are mounted at the root, `/api/v1` is separate.
Reason: operational endpoints are infrastructure contracts (load balancer /
container probes), not versioned business API; keeps `/api/v1` free to version
without touching probe configuration. Consistent with vision §46.

---

## 6. Domain / Data Model Changes

None. No domain entities were implemented — that is Phase 02 by design.
New boundary schemas only: `ErrorResponse`, `HealthResponse`, `ReadyResponse`
(Pydantic API models in `akasha.api`, not domain models).

---

## 7. API / Contract Changes

| Endpoint | Purpose | Response | Errors |
|---|---|---|---|
| `GET /health` | liveness ("process lives") | `200 {"status": "ok", "version": str}` | none by design |
| `GET /ready` | readiness ("dependencies usable") | `200 {"status": "ready", "failures": {}}` or `503 {"status": "not_ready", "failures": {name: reason}}` | none exposed; failures listed by check name |

Global response contract introduced:

- All error responses (validation, HTTP errors, domain errors, unexpected 500s)
  use `ErrorResponse`: `{code, message, details, request_id}`.
- `request_id` reflects the `X-Request-Id` request header when present, else a
  generated UUID. It is per-request fallback context; middleware-scoped
  correlation propagation into logs is Phase 12.

Compatibility: no prior API existed. These two endpoints + the error envelope
are now stable foundation contracts; later endpoints must reuse
`ErrorResponse`.

---

## 8. Implementation Details

- **Bootstrap order** (`main.py`): `load_settings()` (fails fast) →
  `uvicorn.run(..., factory=True)` → `create_app()` → `configure_logging` (skipped
  in `environment="test"`) → `FastAPI(lifespan=...)` → include operational +
  v1 routers → `register_error_handlers()`.
- **Container:** plain dataclass; FastAPI reaches it via `request.app.state.container`
  (`/ready` handler). No business code exists in any router.
- **Logging:** `JsonFormatter` whitelists record internals via
  `logging.makeLogRecord({})` keys, flattens everything else (context fields),
  appends formatted exceptions. UTC via `logging.Formatter.converter = time.gmtime`.
- **Error flow:** domain/app code raises `AkashaError` subclasses with stable
  `code`/`http_status`; the handler layer is the only place that maps them to
  HTTP. Unexpected exceptions are logged with traceback server-side and returned
  as a generic `INTERNAL_ERROR` — verified by test that the original message
  never reaches the body.
- **Test isolation:** the `settings` fixture pins `environment="test"`,
  `log_level="CRITICAL"`; `create_app` in test mode leaves root logging to
  pytest so `caplog` works. Each test builds its own app via `TestClient`
  context manager (which drives lifespan).

---

## 9. Findings

### Finding 1: FastAPI 0.141 / Starlette 1.7 no longer flattens included routers

`app.include_router(...)` produces `_IncludedRouter` entries under `app.routes`
(path attribute `None`) instead of inlined routes.

- **Impact:** LOW
- **Resolution:** FIXED (test approach changed)
- **Notes:** The initial Phase 00-style route-introspection test
  (`{route.path for route in app.routes}`) failed against this version.
  Replaced with a behavioral test: requests through `TestClient` assert on
  `/health` + `/ready` status codes — contract-level, not implementation-level.
  Also surfaced a `StarletteDeprecationWarning` about `httpx` vs a future
  `httpx2` TestClient dependency — a warning only, pytest still passes; watch
  on dependency upgrades.

### Finding 2: `configure_logging` conflicts with pytest `caplog`

`logging.root.handlers.clear()` in app construction evicts pytest's capture
handler when the order collides.

- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** `create_app` skips logging configuration entirely when
  `environment == "test"`; the formatter itself is covered directly by
  `test_logging.py`. Side effect: apps built with `environment="test"` rely on
  ambient logging config — acceptable because "test" is an explicit opt-in
  environment, never a production value.

### Finding 3: Default FastAPI 404 body is unstructured

Unknown routes answered `{"detail": "Not Found"}`, violating the project's
own structured-error rule (backend/AGENTS.md §11).

- **Impact:** MEDIUM — would have become an inconsistent API contract
- **Resolution:** FIXED
- **Notes:** `StarletteHTTPException` handler maps 404/405/401/403/409 to
  `ErrorResponse` codes. Covered by `test_404_is_structured`.

---

## 10. Bugs / Problems Found

All defects below were introduced and fixed within this phase (verified by the
failing-then-passing test runs during implementation). No pre-existing bugs
were found in Phase 00 code.

| Problem | Root cause | Fix | Regression test |
|---|---|---|---|
| `test_application_starts` (Phase 00) would hang | `main()` now starts a blocking uvicorn server | Replaced with `test_entry_point_exists` + factory smoke test | `test_app_factory_serves_endpoints` |
| Route introspection test failed (no `/health` in `app.routes`) | `_IncludedRouter` lazy mounting in Starlette 1.7 | Behavioral TestClient assertions instead | `test_app_factory_serves_endpoints` |
| `caplog` empty in lifespan test | `configure_logging` clearing root handlers after caplog attached | App creation moved before `caplog.at_level`; then (final fix) skip logging config in `environment="test"` | `test_lifespan_logs_start_and_stop` |
| Unstructured 404 responses | FastAPI default exception handler | `StarletteHTTPException` handler → `ErrorResponse` | `test_404_is_structured` |
| Unused imports / long lines | Iteration residue | removed/wrapped (ruff) | `ruff check` gate |

---

## 11. Fixes Applied

See §10 — every fix is listed with symptom, root cause, implementation and its
regression test. No fixes to Phase 00 code beyond the lifecycle-mandated
changes in `main.py` / `test_foundation.py` (entry point now must serve HTTP).

---

## 12. Testing & Verification

### Unit Tests
- Config: defaults, `AKASHA_*` env override, `ValidationError` on bad port — PASS
- JSON formatter: level/logger/message + context fields (`event`,
  `execution_id`, `duration_ms`) in output — PASS
- `AkashaError` code/status override — PASS
- Architecture: dependency-direction AST rules, layer existence — PASS
- Pass count: 20/20.

### Component Tests
- Container wiring into `app.state`; lifespan `application_started` /
  `application_stopped` log events via real `TestClient` startup/shutdown — PASS

### API Tests
- `GET /health` 200 + body; `GET /ready` 200; `/ready` 503 with failing check
  (registered per-request via `app.state.container.ready_checks`); operational
  endpoints absent from `/api/v1`; structured `AkashaError` response; 500
  without traceback leakage; structured 404 — PASS
- Real HTTP stack (TestClient against the actual app factory), not isolated
  router functions.

### Infrastructure Tests
- N/A — no PostgreSQL/Redis in Phase 01 (arrives Phase 05).

### E2E Tests
- Process-level smoke: `uv run akasha` started, `curl /health` → 200,
  `curl /ready` → 200, unknown route → structured 404, graceful SIGTERM
  shutdown logged — PASS (manual, once; not part of the pytest suite).

### Security Tests
- Error-disclosure: 500 body contains neither exception message nor class —
  PASS (`test_unexpected_error_does_not_leak_traceback`).

### Performance / Accessibility Tests
- N/A (backend skeleton, no UI, no measured hot path yet).

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 30 files; initial failures fixed by `ruff format .` |
| Linter (`uv run ruff check .`) | PASS | All checks passed (after removing unused imports) |
| Type Checker (`uv run mypy .`, strict) | PASS | 28 source files, no issues |
| Unit/Component/API Tests (`uv run pytest`) | PASS | 20 passed, 1 external deprecation warning (Starlette/httpx) |
| Integration Tests | N/A | no infrastructure in this phase |
| Infrastructure Tests | N/A | Testcontainers introduced with Phase 05 |
| E2E Tests | PASS (manual smoke) | `./uv run akasha` + curl; not yet automated |
| Build (`uv sync` / wheel build) | PASS | `uv add` rebuilt and installed the package repeatedly |
| Security Checks | PARTIAL | only error-disclosure + architecture-boundary coverage; dedicated hardening is Phase 25 |
| Accessibility | N/A | backend only |

---

## 14. Acceptance Criteria Verification

Roadmap §5 (Phase 01) criteria:

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Backend starts with clearly separated architecture | PASS | 10 layer packages w/ boundary docs; `test_architecture.py` enforces direction; real-process smoke passes |
| Application bootstrap exists | PASS | `main.py` + `create_app()` + `lifespan`; startup/shutdown logged (`test_lifecycle.py`) |
| Dependency injection foundation | PASS | `Container` composition root on `app.state`, factory injection (`test_container_is_wired_into_app_state`) |
| Configuration service | PASS | `Settings`/`load_settings` + env/invalid tests |
| Logging foundation | PASS | `JsonFormatter` + `test_logging.py` |
| API application + routing foundation | PASS | `create_app`, operational router, `/api/v1` router mounted |
| `GET /health` | PASS | `test_health::test_health` |
| `GET /ready` | PASS | `test_health` (ok + 503 failing-check cases) |
| Error-handling foundation | PASS | `AkashaError`, 4 handlers, `test_errors.py` |
| Test application factory | PASS | `create_app(settings, container)` used by all suites |
| Application startup/config/health/readiness/wiring tested | PASS | §12 list |
| No business logic in API routes | PASS | routers contain only health/ready wiring; domain layer is empty; boundary rules make violations build-breaking |

---

## 15. Security Considerations

- **Error disclosure:** unexpected exceptions never surface message/class to
  clients; traceback is server-log only (tested). HTTP errors expose only
  mapped codes and the exception `detail` string.
- **Logging:** structured logging must not receive secrets; no secret material
  exists yet in the codebase. Redaction enforcement arrives with credentials
  (Phase 13) and observability tests (Phase 12).
- **Input validation:** Pydantic validation errors are answered with 422 +
  per-field details — no raw Pydantic internals forwarded.
- **Config:** no defaults that carry secrets; the only settings are
  host/port/log level/environment.
- Not applicable yet: authN/authZ (Phase 20), SSRF/HTTP node (Phase 09),
  expression evaluation (later), plugins (Phase 17), WebSocket.

---

## 16. Performance Considerations

None measured; nothing in this phase is on a hot path beyond two trivial
endpoints. Readiness runs `N` registered async checks on each probe — with the
current zero checks this is O(0); if future checks become expensive the
endpoint needs a cache/interval, which is noted for Phase 05/22 (when real
checks land) rather than solved now without a measurable need.

---

## 17. Documentation Changes

| File | Change |
|---|---|
| `backend/README.md` | Updated: env configuration, run instructions, full layer layout, pointer to boundary-location + guard test |
| `backend/src/akasha/*/__init__.py` | Self-documenting layer boundaries (responsibility, allowed deps, target phase) created |
| This report | Created |

Future phases should update: API docs (when `/api/v1` gains endpoints,
Phase 06), observability docs (Phase 12).

---

## 18. Dependencies / Infrastructure Changes

| Dependency | Change | Why |
|---|---|---|
| `fastapi >= 0.141.1` | added | API layer (mandated stack, backend/AGENTS.md §3) |
| `pydantic-settings >= 2.15.0` | added | validated env configuration (§65) |
| `uvicorn[standard] >= 0.54.0` | added | ASGI server for the process entry point |
| `httpx >= 0.28.1` | added (dev) | FastAPI `TestClient` transport |

Environment variables introduced: `AKASHA_ENVIRONMENT`, `AKASHA_HOST`,
`AKASHA_PORT`, `AKASHA_LOG_LEVEL`. Lockfile (`uv.lock`) committed-consistent.
No Docker/compose changes (Phase 26), no database/Redis (Phase 05/22).

---

## 19. Deferred Work

| What | Why deferred | Target phase | Blocks phase? |
|---|---|---|---|
| PostgreSQL / repositories / migrations | explicitly Phase 05 | 05 | no |
| Business endpoints under `/api/v1` | roadmap isolation | 06+ | no |
| Real readiness checks (database, redis) | no dependencies exist to check; registry seam built instead | 05, 22 | no |
| Correlation-id middleware / metrics / full observability | Phase 12 scope | 12 | no |
| Secret redaction machinery | Phase 13 | 13 | no |
| Authentication/authorization | Phase 20 | 20 | no |
| Automated process-level E2E (spawn/kill server in pytest) | manual smoke sufficient for skeleton; no behavior to regress against beyond tests already covering the app factory | 26/27 | no |
| `httpx2` migration for TestClient deprecation warning | warning only; upstream API still settling | dependency upgrade cycle | no |

---

## 20. Known Limitations

- `/ready` trivially reports "ready" — no real dependencies exist yet. Correct
  per §67-readiness definition, but the endpoint only earns meaning when
  Phase 05/22 register checks.
- `request_id` in error responses is generated per handler call, not yet
  middleware-scoped; log lines and error bodies cannot yet be correlated by
  request id (Phase 12).
- Health endpoint reports the package `__version__`; engine-version semantics
  (vs schema/plugin versions, vision §56) start mattering in later phases.
- Settings are intentionally minimal (4 fields); no YAML/file config, no
  secrets-bearing settings yet.

---

## 21. Open Issues

No known open issues resulting from this phase. (The `httpx2` TestClient
deprecation warning is tracked under Deferred Work; it is not a defect.)

---

## 22. Architectural / Product Decisions

Persistent decisions that future phases must respect (see §5 for full
rationale):

1. **Composition root lives in `akasha/bootstrap.py` as a dataclass
   `Container` on `app.state`.** Don't introduce a DI framework without
   replacing this seam deliberately; tests inject through `create_app`.
2. **Operational endpoints are root-mounted, business endpoints under
   `/api/v1`.** Do not move `/health`/`/ready` under a versioned prefix.
3. **Logging config belongs to app bootstrap and is skipped in
   `environment="test"`** (pytest owns handlers). `environment="test"` is a
   reserved, test-only value.
4. **Layer dependency rules are enforced by `tests/test_architecture.py`.**
   Extending the rule table is preferred over a new tool as long as the AST
   scan covers the need.
5. **All API errors must go through `ErrorResponse`.** Adding an ad-hoc error
   body breaks the contract established here.
6. **Logging stays stdlib + JSON formatter until Phase 12 proves the need for
   more.**

---

## 23. Repository / Git State

- Branch: `main`; base commit: `0004d7f backend phase 00 - repository foundation`.
- Working tree: not committed (per session rule, git mutations require
  explicit request). 5 modified files (see diff stat: pyproject +7, main.py
  rewritten, test_foundation.py rewritten, README +29, uv.lock regenerated),
  7 new source modules/packages + 8 new test files untracked (full inventory
  in §4).
- No unrelated changes detected; `.ruff_cache`/`.mypy_cache`/`.pytest_cache`
  are gitignored local artifacts.

---

## 24. Phase Completion Assessment

### Implemented
Ten layers with enforced boundaries; validated env config; composition root;
FastAPI factory with lifespan; `/health`, `/ready`, `/api/v1` mount;
error-handling foundation with structured envelope; stdlib JSON logging;
uvicorn entry point.

### Verified
20 tests pass; ruff/format/mypy(strict) pass; dependency-direction guard runs
in suite; real-process startup + endpoints + shutdown verified once manually.

### Not Implemented
Domain model, persistence, business API — all correctly out of scope
(Phases 02/05/06).

### Deferred
See §19 (readiness checks substance, observability depth, redaction, auth —
all mapped to their roadmap phases).

### Open
None.

### Blocking Issues
None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase (Phase 02 — Core Domain Model)

**You can assume:**
- Package `akasha`, uv-managed venv, ruff/mypy(strict)/pytest all green.
- `create_app(settings=None, container=None)` is the factory to extend; settings
  loading is centralized and fails fast.
- Tests live in `backend/tests/`, import from `src` via the installed package;
  fixtures in `conftest.py` (`settings`, `client`).

**Contracts now available:**
- `akasha.errors.AkashaError` — raise subclasses with `code`/`http_status`;
  the API layer maps them. New error types in Phase 02+ should subclass it, not
  invent parallel hierarchies.
- `akasha.api.schemas.ErrorResponse` — the only legal error body shape.
- Container/`create_container` — where repositories/services get registered
  when Phase 05+ needs them.

**Must NOT change casually:**
- The `RULES`/`INTERNAL_RULES` tables in `tests/test_architecture.py` encode
  the documented boundaries; domain code must compile against them (no
  FastAPI/Pydantic-settings/infrastructure imports inside `domain/`). Domain
  models should prefer `dataclass`/`Enum` per backend/AGENTS.md §4.1 — Pydantic
  stays at boundaries (§12).
- `environment="test"` semantics in `create_app`.
- Root mounting of `/health` and `/ready`.

**Prerequisites for Phase 02:** none beyond this phase. Do not start
implementing repositories — that is Phase 05.
