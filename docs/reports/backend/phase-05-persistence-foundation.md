# Phase 05 — Persistence Foundation

## 1. Executive Summary

Phase 05 introduced production-grade PostgreSQL persistence for the domain
without leaking database concerns into the domain layer. The phase delivered:

- async SQLAlchemy database/connection management (`akasha.infrastructure.database`)
- hand-written SQLAlchemy Core table definitions (`akasha.infrastructure.db_models`)
- Alembic migration system with one tested initial migration (`0001_initial`)
- application-owned repository + Unit-of-Work protocols (`akasha.application.persistence`)
- PostgreSQL implementations of `WorkflowRepository` and `WorkflowVersionRepository`
- explicit JSON serialization for graph/trigger/dependency documents
- database readiness integration into the composition root and `/ready`
- 14 real-PostgreSQL integration tests (Testcontainers, `postgres:17.2`) plus
  6 fast codec round-trip tests

Resulting state: Workflows (identity + mutable draft) and immutable published
Workflow Versions survive process restarts and are fully protected by database
constraints (unique version number per workflow, RESTRICT on delete while
versions exist). The domain layer has zero new dependencies and passes the
existing static dependency-direction test.

All acceptance criteria of roadmap Phase 05 are satisfied with concrete
evidence (section 14).

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap §9 (Phase 05): "Persist the domain." Introduce PostgreSQL with
database configuration, connection management, migrations, repositories and
transactions. Persist at minimum: workflows, workflow_versions,
nodes/graph data, triggers. Provide `WorkflowRepository` and
`WorkflowVersionRepository` interfaces; the application layer must depend on
interfaces. Acceptance criterion: "Workflow data survives backend restart."

Explicitly NOT in scope (roadmap and phase prompt):

- Execution persistence (may be deferred to Phase 07 — deferred, see §19)
- Application services / use cases / API endpoints (Phase 06)
- Related subsystems (events, credentials, plugins)

---

## 3. Scope Implemented

### Persistence
- `Database` class (async engine, session factory, readiness probe
  `check()`, Alembic `run_migrations()` via `asyncio.to_thread`,
  `dispose()`), `pool_pre_ping=True`, pool_size 5 + max_overflow 5
- URL handling: `normalize_async_url()` maps `postgresql://` →
  `postgresql+asyncpg://`; `normalize_sync_url()` maps to `postgresql+psycopg://`
  for synchronous Alembic
- SQLAlchemy **Core** `Table` definitions: `workflows` (id PK, name,
  description, status, draft_graph/draft_triggers/draft_dependencies JSONB,
  created_at/updated_at timestamptz) and `workflow_versions` (id PK,
  workflow_id FK → workflows RESTRICT, version int, schema_version int,
  graph/triggers/dependencies JSONB, description, published_at timestamptz;
  unique index `(workflow_id, version)`, index on `workflow_id`)
- `SqlAlchemyWorkflowRepository` (get/save-delete/list with deterministic
  ordering `created_at, id` and offset/limit pagination)
- `SqlAlchemyWorkflowVersionRepository` — deliberately **insert-only**; a
  second row with the same `(workflow_id, version)` violates the unique
  constraint (DB as last line of defence for §3.7 immutability)
- `SqlAlchemyUnitOfWork`: one session = one transaction, explicit
  `commit()`, `__aexit__` rolls back on exception, always closes the session

### Application layer (interfaces only)
- `application/persistence.py`: `WorkflowRepository`,
  `WorkflowVersionRepository`, `UnitOfWork` protocols. The version repository
  has no `update`/`delete` by design (architecture §3.7)

### Serialization
- `infrastructure/graph_serialization.py`: `serialize_graph` /
  `deserialize_graph` / trigger / dependency codecs. JSON documents mirror
  the domain model exactly; deterministic node ordering (sorted by id,
  architecture §72); loading re-runs domain constructors so malformed or
  invalid documents fail loudly

### Configuration & bootstrap
- `Settings.database_url: str | None = None` (env `AKASHA_DATABASE_URL`)
- `Container` gains `database: Database | None` and `shutdown()`;
  `create_container()` wires the database + readiness check only when a URL
  is configured — engine boots without persistence for non-DB contexts
- FastAPI lifespan now disposes the container on shutdown

### Testing
- `tests/integration/conftest.py`: session-scoped `postgres:17.2`
  Testcontainer, session event loop (pytest-asyncio), per-test
  `TRUNCATE workflow_versions, workflows CASCADE`, UoW factory fixture,
  Docker-availability probe that **skips** instead of hanging/failing when
  no daemon exists (AGENTS §117)
- `tests/integration/test_persistence.py`: 14 integration tests
- `tests/test_graph_serialization.py`: 6 codec unit tests (no Docker needed)

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/application/persistence.py` | Created | Repository + UoW protocols owned by application layer (§15/§25) |
| `backend/src/akasha/infrastructure/database.py` | Created | Engine/session lifecycle, readiness probe, migration runner, URL normalizers |
| `backend/src/akasha/infrastructure/db_models.py` | Created | Core table definitions; JSONB-with-JSON-fallback variant keeps the metadata dialect-portable |
| `backend/src/akasha/infrastructure/graph_serialization.py` | Created | Domain ⇄ JSONB codec |
| `backend/src/akasha/infrastructure/persistence.py` | Created | Repository + UoW implementations |
| `backend/alembic.ini` | Created | Alembic config; `sqlalchemy.url` injected programmatically (no credentials in VCS) |
| `backend/migrations/env.py` | Created | `target_metadata = db_models.metadata`; logging deliberately NOT via `fileConfig` (see §9) |
| `backend/migrations/script.py.mako` | Created | Template for future migrations |
| `backend/migrations/versions/0001_initial.py` | Created | Initial schema; reversible `upgrade`/`downgrade` |
| `backend/migrations/.gitkeep` | Deleted | Directory now has real content |
| `backend/src/akasha/bootstrap.py` | Modified | Container: `database`, readiness registration, `shutdown()` |
| `backend/src/akasha/api/app.py` | Modified | Lifespan calls `container.shutdown()` |
| `backend/src/akasha/config.py` | Modified | `database_url` setting |
| `backend/pyproject.toml` | Modified | New dependencies; pytest-asyncio config; mypy `explicit_package_bases` |
| `backend/uv.lock` | Modified (generated) | Lockfile for new dependencies |
| `backend/tests/integration/conftest.py` | Created | Testcontainers fixtures |
| `backend/tests/integration/test_persistence.py` | Created | 14 integration tests |
| `backend/tests/test_graph_serialization.py` | Created | Codec unit tests |

New directories: `backend/migrations/versions/`, `backend/tests/integration/`.

---

## 5. Architecture Changes

No deviation from `/docs/architecture.md` — §24/§25/§26/§79 already
prescribed this exact shape. Two implementation decisions were **clarified**
during this phase:

### Decision 1: SQLAlchemy Core tables instead of mapped ORM entities
- **Context:** architecture §79 requires repositories between DB models and
  domain; an ORM-mapped domain entity would let identity-map/lazy-loading
  behavior leak into the domain.
- **Chosen approach:** plain `Table` definitions + explicit row→domain
  reconstruction in repositories (`_to_domain`).
- **Alternatives:** classical `registry.map_imperatively` / declarative
  mapped classes mirroring domain dataclasses.
- **Reason:** zero possibility of ORM session state poisoning domain
  objects; mapping is one explicit function, fully tested by round-trips.
- **Consequences:** future persistence mappings (executions, events, …)
  follow the same pattern: Core table + codec + repository reconstruction.

### Decision 2: Graph/triggers/dependencies as JSONB documents
- **Context:** graph semantics (port compatibility, cycles) cannot be
  expressed by SQL constraints without duplicating domain validation.
- **Chosen approach:** store serialized documents opaquely; validate only
  in the domain layer on load (domain constructors re-run).
- **Alternatives:** fully normalized `nodes`/`edges` tables.
- **Reason:** no schema churn when graph structure evolves; export format
  and persistence format converge; §80 demands JSON serialization anyway.
- **Consequences:** SQL-level graph queries are impossible by design; if a
  future phase needs them, a projection table must be added explicitly
  (documented limitation, §20).

---

## 6. Domain / Data Model Changes

**No domain model changes.** The domain layer gained no imports and no new
dependencies; `test_architecture.py` (dependency-direction guard) passes
unchanged and now also covers the populated `infrastructure` layer.

New database objects (details in §3):

| Object | Type | Constraints / Indexes |
|---|---|---|
| `workflows` | table | PK `id`; `description` server_default `''` |
| `workflow_versions` | table | PK `id`; FK `workflow_id → workflows.id ON DELETE RESTRICT`; UNIQUE `(workflow_id, version)`; index `ix_workflow_versions_workflow_id` |

Compatibility: greenfield schema (first migration); the unique
`(workflow_id, version)` constraint is the enforcement point for version
immutability referenced by all later publish logic.

---

## 7. API / Contract Changes

No new endpoints. One behavioral change to an existing endpoint:

| Endpoint | Change | Compatibility |
|---|---|---|
| `GET /ready` | reports `database` readiness failure (503 with `{"status":"not_ready","failures":{"database": "..."}}`) **when** `AKASHA_DATABASE_URL` is configured; without a URL the behavior is unchanged | backward compatible; new optional failure key in the existing `failures` map |

Internal contracts introduced (not HTTP): `WorkflowRepository`,
`WorkflowVersionRepository`, `UnitOfWork` protocols; lifecycle wiring
(`Container.shutdown()` from FastAPI lifespan).

---

## 8. Implementation Details

- **Transaction ownership:** repositories receive a session and never
  commit; `SqlAlchemyUnitOfWork.__aexit__` rolls back only on exception and
  always closes the session. SQLAlchemy auto-rollback on close is the safety
  net for non-committed success paths.
- **Upsert:** `SqlAlchemyWorkflowRepository.save` does `get` → insert or
  update. Not race-safe under concurrent first-insert (documented in §20;
  harmless at the draft-frequency it serves, flagged for Phase 06 ownership).
- **IntegrityError timing:** with asyncpg, constraint violations raise at
  statement execution, not at commit — integration tests assert
  `pytest.raises(IntegrityError)` around the `save` call accordingly.
- **Migrations in async context:** `Database.run_migrations()` runs Alembic
  on a worker thread (`asyncio.to_thread`) with a synchronous psycopg3 URL;
  `alembic.ini` never contains a URL (no credentials in VCS).
- **Timestamps:** asyncpg returns naive datetimes for `timestamptz` reads;
  `_ensure_utc()` normalizes to aware UTC before constructing domain objects
  (`Timestamps`/`WorkflowVersion` expect aware datetimes).
- **Determinism:** `serialize_graph` sorts nodes by id; list queries have
  explicit `ORDER BY`.
- **Test isolation:** one container per pytest session, migrated once, every
  test starts on `TRUNCATE`d tables; no shared mutable fixtures per AGENTS
  §15/§16. Session-scoped event loop (`asyncio_default_fixture_loop_scope`,
  `asyncio_default_test_loop_scope = "session"`) is required because asyncpg
  pool connections are bound to their creating loop.

---

## 9. Findings

### Finding 1 — Alembic env.py `fileConfig` requires logging sections the project does not want
Impact: LOW. Resolution: FIXED — env.py deliberately does not call
`fileConfig`; application logging stays owned by `akasha.observability.logging`.

### Finding 2 — testcontainers image pull exceeds default interactive timeouts
The first `postgres:17.2` pull exceeded the 7-minute tool timeout and was
mistaken for a hang. Resolution: FIXED (process-wise) — pull the image
explicitly before running tests; subsequent full runs take ~5–11 s.

### Finding 3 — `testcontainers.postgres` import path deprecated in testcontainers 4.15
Resolution: FIXED — using `testcontainers.community.postgres.PostgresContainer`.

### Finding 4 — `mypy .` collided on two top-level `conftest.py` files
(tests/ and tests/integration/). Resolution: FIXED via
`explicit_package_bases = true` in `pyproject.toml`; tests remain type-checked.

### Finding 5 — asyncpg raises constraint violations at `execute`, not `commit`
Resolution: FIXED in tests (assert at the raising statement). Notes: this is
a real behavioral fact consumers must know (application services in Phase 06).

---

## 10. Bugs / Problems Found

All were introduced and fixed within this phase; no pre-existing defects or
environmental problems were found beyond the Docker daemon initially being
stopped (started via `open -a Docker`; not a code issue).

1. **Test bug:** URL rewrite `db_url.replace(dbname, scratch)` replaced
   *username and password too* because the container defaults are all
   `"test"` → `FATAL: password authentication failed for user
   "akasha_migration_check"`. Fix: `rpartition("/")` on the path segment
   only. Regression test: the migration test itself now exercises it.
2. **Test bug:** duplicate-version test reused the same snapshot object → PK
   violation instead of the intended `(workflow_id, version)` unique
   violation. Fix: construct a *different* `WorkflowVersion` (new id, same
   version) so the tested constraint is the unique index. Regression test:
   `test_duplicate_version_number_rejected_by_unique_constraint`.
3. **Test bug:** `_docker_available()` used `socket.create_connection(path)`,
   which is TCP-only → `ValueError: too many values to unpack`. Fix: explicit
   `AF_UNIX` socket connect.
4. **Lint/type findings:** blind `pytest.raises(Exception)` (B017),
   unused `noqa`, unused import, unannotated variable, `Result.rowcount`
   typing (needs `CursorResult` cast), `RowMapping` vs `Mapping` mismatch,
   UoW `__aexit__` signature narrowed from the Protocol. All fixed; final
   gates clean (§13).

---

## 11. Fixes Applied

| Symptom | Root cause | Fix | Verification |
|---|---|---|---|
| psycopg password auth failure in migration test | naive string replace on URL | `rpartition("/")` path-only replacement | migration test passes |
| wrong constraint exercised | same object saved twice (PK clash) | distinct `WorkflowVersion`, same version number | unique-index test passes |
| docker probe crash | TCP socket API on a unix socket path | `AF_UNIX` connect | skip/pass logic works both ways |
| `alembic_version` KeyError 'formatters' | `fileConfig` without `[formatters]` | drop `fileConfig` from env.py | migrations run |
| mypy duplicate module `conftest` | two conftest files, no package bases | `explicit_package_bases = true` | `mypy .` PASS |
| asyncpg naive datetimes | driver behavior for timestamptz | `_ensure_utc()` in row→domain mapping | tz-aware assertion in round-trip test |

---

## 12. Testing & Verification

### Unit Tests
- `tests/test_graph_serialization.py` — codec round-trips, JSON-safety,
  deterministic node ordering, malformed-document rejection, domain
  validation re-triggered on load (e.g. cron trigger without `cron`).
  **Result: PASS (6 tests).**
- Full pre-existing suite (domain, API skeleton, config, errors, health,
  lifecycle, logging, dependency-direction guard): **unchanged, all PASS.**

### Integration Tests (real PostgreSQL 17.2 via Testcontainers)
`tests/integration/test_persistence.py` — **14 tests, all PASS:**
full draft round-trip (nodes/edges/config/triggers/dependencies/unicode through
JSONB), get-miss→None, in-place draft update, delete existing/missing,
paginated + deterministically ordered list, publish-snapshot isolation from
later draft edits, version list/lookup (+ unknown version), unique-version
constraint, FK violation for orphan version, RESTRICT on workflow delete with
versions, rollback on exception, readiness OK/unreachable, and
migrations upgrade→downgrade→upgrade on a scratch database including
`alembic_version` content checks.

### Infrastructure Tests
- PostgreSQL container start/readiness/teardown: PASS (automatic).
- Docker availability probe behavior verified for the *available* path; the
  skip path is the same code converted from a crash (bug 3 above).

### Security Tests
- No secrets involved in this phase by design: `alembic.ini` contains no
  URL; connection string comes from settings. `Database.check()` failure
  messages contain only the exception **type name**, never URL/credentials
  (verified by `test_readiness_check_reports_unreachable_database`).

### Not performed (not applicable to this phase): API/E2E/Accessibility/Performance test categories.

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| `uv run pytest` | PASS | 266 passed (246 pre-existing + 14 integration + 6 codec), 1 benign starlette deprecation warning |
| `uv run ruff check .` | PASS | 4 findings fixed during the phase (F401, B017, unused noqa, RUF formatting) |
| `uv run ruff format --check .` | PASS | after `ruff format .` |
| `uv run mypy .` | PASS | strict mode; 8 findings fixed (CursorResult cast, RowMapping typing, `__aexit__` protocol signature, variable annotation, explicit_package_bases) |
| Migrations | PASS | upgrade/downgrade/upgrade from empty database |
| Build | N/A | library; `uv sync`/editable install verified implicitly by all commands |
| API/E2E/Security/Accessibility/Perf suites | N/A | no such scope in Phase 05; security-relevant behavior covered as noted in §12 |

---

## 14. Acceptance Criteria Verification

Roadmap Phase 05 criteria plus the session prompt's completion list:

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| database configuration | PASS | `Settings.database_url`; wired in `create_container` (test_config + container behavior exercised by app tests) |
| connection management | PASS | `Database` engine/pool, `pool_pre_ping`, dispose in lifespan; readiness probes tested |
| transaction handling | PASS | UoW protocol + implementation; rollback/commit/exception tests pass |
| SQLAlchemy models | PASS | `db_models.py` Core tables |
| Alembic migrations | PASS | `0001_initial`; upgrade/downgrade/upgrade test on scratch DB; `alembic_version` checked |
| repository abstractions | PASS | protocols in application layer; dependency-guard test passes |
| repository implementations | PASS | both repositories fully CRUD/listed/get tested via real PG |
| Workflow persistence | PASS | round-trip test |
| Workflow Version persistence | PASS | publish/snapshot-isolation/list tests |
| graph persistence | PASS | nodes+edges+config round-trip through JSONB, exact equality asserted |
| required related persistence (triggers, dependencies) | PASS | round-trip assertions in draft test; version test asserts triggers/dependencies tuples |
| Draft vs Published Version semantics | PASS | snapshot isolation test; insert-only version repository; unique constraint test |
| real PostgreSQL integration tests, Testcontainers, auto start/isolation/cleanup | PASS | session container, per-test TRUNCATE, auto teardown; no mocks anywhere in persistence tests |
| migrations / CRUD / transactions / rollback / constraints / serialization / immutable version / repository behavior tested | PASS | each maps to named tests in `test_persistence.py` |
| no domain-to-database coupling | PASS | `test_architecture.py` PASS; domain package has zero new imports (`git diff backend/src/akasha/domain` empty) |
| no unrelated application services | PASS | `application/` contains only interfaces |
| quality gates pass | PASS | §13 |
| Roadmap criterion "Workflow data survives backend restart" | PASS | proven by cross-session visibility: every read in the integration tests happens through a **new** UoW/session after commit; engine restart adds nothing beyond `Database` re-creation against the same URL |

---

## 15. Security Considerations

- **Secrets/config:** DB URL via environment/settings only; `alembic.ini`
  holds no credentials. Readiness failure strings expose exception type
  names only.
- **SQL injection:** all statements are parameterized via SQLAlchemy Core
  expressions; no string-built SQL in application code paths.
- **Data exposure at rest:** domain NodeConfig `config` JSONB may contain
  any user keys but Phase 02 already forbids plaintext secrets by contract
  (CredentialReferences only); no secret material is persisted by this phase.
- **Error disclosure:** integration tests prove constraint violations raise
  typed `IntegrityError` internally; API mapping unchanged (no new API
  surface).
- No authentication/authorization, SSRF, file, or expression-evaluation
  surface was added in this phase.

---

## 16. Performance Considerations

- Connection pool defaults (5 + 5 overflow) are deliberately conservative
  for a single self-hosted installation; no tuning measured or claimed.
- `list` queries are bounded (`limit`, default 100) with indexable ordering —
  no unbounded collections (AGENTS §74).
- JSONB chosen for graph documents: avoids N+1 row-loading of nodes/edges;
  one row per draft/version. No measurements taken; not claimed as an
  optimization, documented as a modeling decision (§5 Decision 2).
- Migration `TRUNCATE`-based test isolation and one container per session
  keep the suite at ~5–11 s total runtime (observed, not benchmarked).

---

## 17. Documentation Changes

- **This report** (`docs/reports/backend/phase-05-persistence-foundation.md`) — created.
- No changes to `/docs/architecture.md`, `/docs/vision.md`,
  `/docs/roadmap-backend.md`, `/AGENTS.md`, `/backend/AGENTS.md`: the
  implemented persistence stack matches the documented one exactly.
- Future-phase documentation hooks: Phase 06 should document the UoW usage
  pattern for application services; Phase 26 should document migration
  execution in deployment (currently `Database.run_migrations()` exists but
  has no CLI/API invocation point).

---

## 18. Dependencies / Infrastructure Changes

New runtime dependencies (pinned by `uv.lock`): `sqlalchemy[asyncio] 2.1.1`,
`asyncpg 0.31.0` (+`greenlet`), `alembic 1.20.0` (+mako, markupsafe),
`psycopg[binary] 3.3.6`. Rationale: mandated stack (SQLAlchemy/Alembic/
PostgreSQL); asyncpg for the async runtime path; psycopg3 because Alembic's
core is synchronous and asyncpg cannot serve it.

New dev dependencies: `testcontainers[postgres] 4.15.0` (+docker client,
requests…), `pytest-asyncio 1.4.0`.

New environment variable: `AKASHA_DATABASE_URL` (optional; absence = no
database wiring). Docker is required for the integration test suite;
`postgres:17.2` image pinned (AGENTS §15: never `latest`). No Redis, no
external services.

---

## 19. Deferred Work

| Item | Why deferred | Target phase | Blocks this phase? |
|---|---|---|---|
| Execution/NodeExecution persistence | roadmap: "may defer to Phase 07"; no execution runtime exists to persist | Phase 07/08 | No |
| Events table | event system does not exist | Phase 11 | No |
| Trigger as first-class table | triggers are currently documents inside draft/version; no scheduling exists | Phase 10 (revisit if trigger CRUD/scheduling needs row identity) | No |
| Application services using the repositories | explicitly out of scope ("Do not implement unrelated application services") | Phase 06 | No |
| CLI/startup invocation of migrations | no CLI yet; app startup migration policy is an operations decision | Phase 21/26 | No |
| Concurrent-insert-safe upsert in `WorkflowRepository.save` | draft-write frequency makes the window theoretical; Phase 06 owns draft mutation semantics (optimistic locking candidates) | Phase 06+ | No — recorded as limitation, §20 |

---

## 20. Known Limitations

- **`save()` read-then-write upsert is not race-safe** for concurrent
  first-inserts of the same workflow id (both see "missing", both insert,
  second gets a PK `IntegrityError`). Acceptable now; publish and
  draft-update flows in Phase 06 must revisit (optimistic locking / `ON
  CONFLICT`) — this report (§19/§25) is the authoritative record.
- **Graph not queryable via SQL** (by design, §5): any future need for
  "find workflows using node type X" requires a projection table.
- **First-time Testcontainers image pull** can exceed minutes on slow
  networks (finding 2); without Docker the integration tests skip rather
  than fail (AGENTS §117-compliant).
- **mypy** needed `explicit_package_bases = true`; a future deeper test
  tree stays compatible, but adding third-party plugins that import
  conftests by bare name should be avoided.
- Readiness probe is a `SELECT 1` round-trip: it proves connectivity, not
  that migrations are applied. A schema-version readiness signal is a
  candidate for a later operations phase (not required by Phase 05).

---

## 21. Open Issues

No known open issues resulting from this phase. (Deferred items are listed
in §19 with target phases; none are unresolved defects.)

---

## 22. Architectural / Product Decisions

Persistent knowledge from this phase:

1. **Repositories never commit; the Unit of Work owns every transaction.**
   Any future repository must follow `SqlAlchemyUnitOfWork`'s model; do not
   add commit/rollback to repository methods.
2. **Published versions are insert-only by protocol AND by constraint.** The
   application-layer `WorkflowVersionRepository` has no update/delete, and
   the unique `(workflow_id, version)` index makes a duplicate logical
   version a database error even if application code regresses.
3. **Domain objects are never ORM-managed.** New persisted concepts get:
   Core `Table` in `db_models.py` + explicit codec + row→domain
   reconstruction. Do not introduce mapped domain classes.
4. **Graph documents are opaque to SQL.** Semantic graph truth lives only in
   `akasha.domain`; JSONB is the single storage/serialization format.
5. **Alembic URLs are injected, never committed.** `alembic.ini` stays
   credential-free; sync driver is psycopg3, async runtime driver is asyncpg.
6. **asyncpg raises constraint violations at statement time**, not at
   commit — write application-service tests (Phase 06) accordingly.

---

## 23. Repository / Git State

- Branch: `main` (tracking `origin/main`, up to date at phase start: commit
  `3ecfda3 backend phase 04`).
- Working tree: **uncommitted phase changes only, no unrelated user work.**
  Tracked modifications: `backend/pyproject.toml`, `uv.lock`,
  `bootstrap.py`, `api/app.py`, `config.py`; deletion:
  `backend/migrations/.gitkeep`. Untracked additions: `alembic.ini`,
  `migrations/env.py`, `script.py.mako`, `versions/0001_initial.py`,
  `application/persistence.py`, `infrastructure/{database,db_models,
  graph_serialization,persistence}.py`, `tests/integration/`,
  `tests/test_graph_serialization.py`.
- No commit was created (commits only on explicit request).
- `git diff` was inspected during review and matches this report.

---

## 24. Phase Completion Assessment

### Implemented
Full PostgreSQL persistence foundation: config, engine/session lifecycle,
Alembic migration (initial schema), repository protocols + SQLAlchemy
implementations, Unit of Work, JSON codecs for graph/triggers/dependencies,
composition-root wiring, readiness integration, graceful disposal.

### Verified
266 tests pass; real-PostgreSQL migration cycle (upgrade/downgrade/upgrade)
on a clean scratch database; constraints, rollback, snapshot isolation,
pagination, readiness — all against an actual container. Lint, format and
strict mypy pass. Static architecture guard passes.

### Not Implemented
Nothing within phase scope.

### Deferred
Execution/event persistence, trigger tables, application services,
migration invocation entry point — all with target phases (§19).

### Open
None (§21).

### Blocking Issues
None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase (06 — Workflow Application Services)

Phase 06 can assume:

- `Container.database` may be `None` — decide how services behave without
  persistence (recommended: fail fast at startup when `database_url` is
  required, per AGENTS §52 fail-fast config).
- Build use cases against `akasha.application.persistence` protocols; obtain
  a UoW via `SqlAlchemyUnitOfWork(container.database)`; commit explicitly.
- Constraint violations surface as `sqlalchemy.exc.IntegrityError` at
  statement time — map them to domain/API errors (409 Conflict) in the
  service or error-mapping layer.
- `Workflow.publish_draft()` already implements versioning semantics; persist
  via `workflows.save(workflow)` + `versions.save(workflow.id, version)` inside
  one UoW to keep the publish atomic (both writes must commit together —
  this is the transaction-critical path of Phase 06).
- The upsert race on first insert (§20) and draft optimistic concurrency
  need decisions when `UpdateWorkflowDraft` lands.
- Do **not** change: repository commit semantics, the insert-only version
  protocol, JSONB graph documents, the unique `(workflow_id, version)`
  constraint, Alembic URL injection.
- Prerequisite for running tests: Docker daemon running (integration tests
  skip otherwise); `docker pull postgres:17.2` once recommended.
