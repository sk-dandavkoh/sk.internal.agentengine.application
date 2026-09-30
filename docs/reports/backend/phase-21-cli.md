# Phase 21 — CLI

## 1. Executive Summary

**Supposed to achieve.** Roadmap §25 (Phase 21) asks for "operational and
developer CLI access". It names these commands:

- `project create | validate | publish | run | import | export`
- `node list | validate | test`
- `plugin list | install | remove`
- `execution get | cancel`
- `backup`, `restore`

The roadmap requires the CLI to "reuse application services or API
contracts rather than duplicating domain logic".

The session request added tests for:

- command parsing and validation;
- success and failure paths;
- authentication where applicable;
- output and exit codes;
- integration tests for important commands.

**Actually implemented.**

- **The CLI:** a stdlib `argparse` CLI in `backend/src/akasha/cli.py`,
  exposed as the `akasha` console script and `python -m akasha`. It has
  every roadmap command, plus `project list` and `akasha serve`.
- **Service reuse:** each command calls an existing application service,
  registry, runtime or SDK helper. No command holds a domain rule.
- **Three supporting pieces:**
  - a plugin-removal use case that blocks removal while projects need the
    plugin (architecture §143), in `application/plugins.py`, backed by a
    new repository query;
  - an application-level backup/restore module,
    `infrastructure/backup.py`;
  - `ExecutionRuntime` wired into the composition root, `Container.runtime`.
- **Tests:** 36 new unit tests and 14 new integration tests on real
  PostgreSQL, including one real-subprocess test for Ctrl-C.
- **Architecture guard:** `tests/test_architecture.py` now forbids
  SQLAlchemy, FastAPI, Starlette and Redis imports in `akasha/cli.py`.

**Resulting system state.**

- An operator can manage the engine without the frontend or the HTTP API:
  - projects: create, list, validate, publish, run, import, export;
  - node types: list, validate, test;
  - plugins: list, install, remove;
  - executions: inspect, cancel;
  - the installation: back up and restore.
- Every quality gate passes: 1648 tests passed, 5 skipped.

**Acceptance criteria.** "Core Engine operations can be performed without the
frontend" is satisfied (see §14). The limitations in §20 do not block the
phase:

- no CLI login, by design;
- `project run` executes in the CLI process until Phase 22;
- restore needs the same schema revision;
- backups are built in memory.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

The roadmap (`docs/roadmap-backend.md` §25) says:

> Goal: Provide operational and developer CLI access.
> CLI commands should reuse application services or API contracts rather
> than duplicating domain logic.
> Tests: Test representative commands.
> Acceptance Criteria: Core Engine operations can be performed without the
> frontend.

`docs/architecture.md` §58 repeats the command list. It adds that the CLI
"must not duplicate core business logic". Other sections hand work to this
phase:

- **§36 (Phase 17 notes):** "The CLI (Phase 21) drives the [plugin]
  lifecycle". The §143 "safe blocking" of plugin removal was left for
  "import/export and CLI".
- **§110/§111** define what a backup should cover and what a restore must
  validate. They also say restore "must not silently overwrite an active
  installation without explicit confirmation".
- **`bootstrap.py` comments** deferred wiring the runtime to the shared
  registry, credentials and metrics to "Phase 21/22".

**Not in scope:**

- workers and queue consumption (Phase 22);
- retries and recovery of orphaned RUNNING executions (Phase 23);
- resource limits (Phase 24);
- security hardening (Phase 25);
- deployment and Docker (Phase 26).

---

## 3. Scope Implemented

### CLI (`backend/src/akasha/cli.py`)

- **Global:** `akasha [--version] [-v|--verbose] COMMAND`. Every leaf
  command accepts `--json`.
- **`serve`:** runs the HTTP server through the existing `akasha.main.main`
  (uvicorn).
- **`project`:**
  - `create NAME [--description]`
  - `list [--offset --limit]`
  - `validate ID`
  - `publish ID [--expected-revision N]`
  - `run ID [--version N] [--input JSON|@file]`
  - `import FILE`
  - `export ID [--version N] -o FILE`
- **`node`:**
  - `list`
  - `validate TARGET`
  - `test TARGET [--config] [--inputs] [--execution-input]`

  `TARGET` is a registered node type id, `path/file.py:Class`, or
  `module:Class`.
- **`plugin`:** `list`, `install PATH`, `remove ID [--force]`.
- **`execution`:** `get ID`, `cancel ID [--reason]`.
- **`backup -o FILE`** and **`restore FILE [--replace]`**.
- **Streams:** results go to stdout. Errors go to stderr, redacted, as
  `error: CODE: message` plus detail lines, or as one JSON object with
  `--json`. Logs go to stderr as text: WARNING by default, INFO with `-v`.
  Logging is not configured when `AKASHA_ENVIRONMENT=test`, because pytest
  owns the handlers, as in `create_app`.
- **Exit codes:** `0` success; `1` operation failed (an `AkashaError`, an
  invalid draft, an execution that did not complete, or a file error); `2`
  usage error (argparse); `70` unexpected internal error.

### Application

- `application/plugins.py`:
  `remove_plugin(registry, projects, plugin_id, *, force=False)`. It raises
  `PluginInUseError` with `details.projects` when projects need the plugin
  and `force` is false. It logs `plugin_removed_in_use` when forced.
- `ProjectService.projects_using_plugin(plugin_id, node_types)`.
- `ProjectRepository.using_plugin(plugin_id, node_types)`, a new protocol
  method in `application/persistence.py`.

### Persistence / Infrastructure

- `SqlAlchemyProjectRepository.using_plugin` uses one static SQL query
  (`_USING_PLUGIN`). It checks `jsonb_array_elements` over the
  `projects.draft_graph->'nodes'` and `projects.draft_dependencies` columns,
  and over the `project_versions.graph->'nodes'` and
  `project_versions.dependencies` columns of every version. It returns ids
  ordered by `p.id`.
- `infrastructure/backup.py`: `create_backup`, `restore_backup`, the
  `BackupSummary` dataclass, and three new errors: `BackupInvalidError`,
  `BackupIncompatibleError` and `RestoreTargetNotEmptyError`.

### Composition / Runtime wiring

- `bootstrap.Container.runtime: ExecutionRuntime | None`. `create_container`
  builds it with the container's `nodes.catalog`, `events`, `metrics` and
  `CredentialService`, when an encryption key is configured.

### Observability

- `configure_logging(level, *, structured=True, stream=None)` takes an
  optional stream. The default is still stdout, so existing callers are
  unchanged.

### Testing

- `backend/tests/test_cli.py` (unit, no Docker) and
  `backend/tests/integration/test_cli.py` (PostgreSQL through
  Testcontainers). See §12.
- `tests/test_architecture.py` has a new `cli` rule. The scanner now also
  checks single-module layers (`<layer>.py`).

### Documentation

- `backend/README.md`, `docs/architecture.md` (§36, §58, §110, §111) and
  this report. See §17.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/cli.py` | Created (666 lines) | The CLI: parser, command adapters, output and error handling, entry point |
| `backend/src/akasha/infrastructure/backup.py` | Created (301 lines) | Logical backup/restore of the database (§110/§111) |
| `backend/src/akasha/application/plugins.py` | Created (47 lines) | `remove_plugin` use case with project-usage blocking (§143) |
| `backend/src/akasha/application/persistence.py` | Modified | `ProjectRepository.using_plugin` protocol method |
| `backend/src/akasha/application/projects.py` | Modified | `ProjectService.projects_using_plugin` |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | `_USING_PLUGIN` SQL and `SqlAlchemyProjectRepository.using_plugin` |
| `backend/src/akasha/bootstrap.py` | Modified | `Container.runtime`; one shared `CredentialService` for the container and the runtime; stale "Phase 21/22" comments updated |
| `backend/src/akasha/observability/logging.py` | Modified | `stream` parameter so the CLI can log to stderr |
| `backend/src/akasha/__main__.py` | Modified | `python -m akasha` now runs `akasha.cli.main` |
| `backend/pyproject.toml` | Modified | Console script `akasha = "akasha.cli:main"` (was `akasha.main:main`); per-file ruff ignore of `T201` for `src/akasha/cli.py`, with a reason comment |
| `backend/tests/test_cli.py` | Created | 36 unit tests |
| `backend/tests/integration/test_cli.py` | Created | 14 integration tests (counting parametrized cases) |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeProjects` implements the new protocol method |
| `backend/tests/test_architecture.py` | Modified | `cli` import rule; scanner covers single-file layers |
| `backend/README.md` | Modified | `akasha serve`, CLI section, layout |
| `docs/architecture.md` | Modified | Implementation notes in §36, §58, §110 and §111 |
| `docs/reports/backend/phase-21-cli.md` | Created | This report |

**Not touched:**

- `backend/src/akasha/main.py` is unchanged. It remains the uvicorn
  launcher and is now reached through `akasha serve`.
- There are no migrations, no new directories, no generated files and no
  dependency or lockfile changes.

---

## 5. Architecture Changes

### 5.1 The CLI calls services in-process, not the HTTP API

- **Decision:** CLI commands build a `Container` from `AKASHA_*`
  (`create_container(load_settings())`) and call its services directly.
- **Context:** §58 allows either "application services or API contracts".
  Plugin install/remove and backup/restore deliberately have no HTTP
  endpoint (§36: an install endpoint would be remote code execution).
- **Chosen approach:** in-process services. JSON output reuses the API DTOs
  (`ProjectSummary`, `ProjectPage`, `ValidationResponse`,
  `ProjectVersionSummary`, `ImportProjectResponse`) so both surfaces print
  the same shapes.
- **Alternatives considered:** an HTTP client of `/api/v1` with a session
  token. Rejected: it could not cover plugins or backup, and it would need
  a running server.
- **Reason:** one code path for all commands, and no server needed for
  operator tasks.
- **Consequences:**
  - The CLI needs the engine's configuration: the database URL, the key and
    the plugin directory.
  - It acts with operator authority (§5.2).
  - A future remote CLI would be a separate client.

### 5.2 CLI trust model: no login

- **Decision:** the CLI has no authentication.
- **Context:** Phase 20 authenticates HTTP sessions. The CLI runs on the
  host with `AKASHA_DATABASE_URL` and `AKASHA_CREDENTIAL_ENCRYPTION_KEY`.
  Anyone holding these can already read and write the database directly.
- **Chosen approach:** operator (admin-equivalent) authority. Projects
  created or imported by the CLI get `owner_id = None`, which the API
  already treats as admin-only (see the `projects.owner_id` column comment).
- **Alternatives considered:** requiring a username and password and
  checking them through `AuthenticationService`. Rejected: it gives no
  protection against someone who holds the database URL, and a password
  prompt or argument adds exposure (§107 forbids credentials in
  command-line arguments).
- **Consequences:** access to the CLI means access to the host. Assigning
  an owner from the CLI is not supported.

### 5.3 Plugin removal blocks by default

- **Decision:** `plugin remove` refuses while any project's draft or any
  published version uses one of the plugin's node types, or declares a
  `Dependency(kind="plugin", name=<id>)`. `--force` is the explicit
  confirmation.
- **Context:** §143 says "prefer safe blocking". Phase 17 left this open
  (the §36 note).
- **Chosen approach:** a use-case function in the application layer
  (`remove_plugin`) over a repository query. `PluginRegistry.remove` itself
  is unchanged and still does not look at projects.
- **Alternatives considered:**
  - Loading every project and version into memory to scan it. Rejected:
    unbounded memory use.
  - Putting the check in `PluginRegistry`. Rejected: the `plugins` layer
    must not import the application or infrastructure layers.
- **Consequences:**
  - Published versions count, so a plugin used only by history can be
    removed only with `--force`.
  - Removal now needs a configured database.
  - `plugin disable` is not guarded, because no disable command exists.

### 5.4 Logical, application-level backup instead of `pg_dump`

- **Decision:** a backup is a gzip-compressed JSON document. It holds a
  manifest and every row of every table except `sessions`, read in one
  REPEATABLE READ transaction.
- **Context:** §111 requires restore to validate engine, schema, plugin and
  encryption compatibility, and to refuse silent overwrites.
- **Alternatives considered:** `pg_dump`/`pg_restore`. Rejected:
  - it needs a matching client binary on the host;
  - it cannot check plugins or the key before writing;
  - it is harder to test end to end.
- **Reason:** no external binary, validation before any write, and one
  restore transaction.
- **Consequences:**
  - The format is versioned (`format_version: 1`).
  - Restore requires the backup's Alembic revision to equal the target
    head. Cross-revision restore is not supported.
  - The whole document is held in memory. This is marked with a
    `ponytail:` comment in `backup.py`.

### 5.5 `akasha` becomes a multi-command CLI; the server moves to `akasha serve`

- **Decision:** the `akasha` console script now points to
  `akasha.cli:main`.
- **Consequence:** a bare `uv run akasha` now prints usage (exit 2) instead
  of starting the server. `backend/README.md` is updated.
  - Earlier phase reports (phase-00, phase-01) still mention `uv run akasha`
    as the server command. They are historical records and were not edited.
  - No Dockerfile or compose file existed that depended on the old command.

### 5.6 The runtime becomes part of the composition root

- **Decision:** `Container.runtime` exists and shares the node registry,
  credential service, event publisher and metrics.
- **Context:** `bootstrap.py` comments planned this for "Phase 21/22".
  `project run` and `execution cancel` need it.
- **Consequences:**
  - The API process now also builds an `ExecutionRuntime` object. It is
    cheap and nothing in the API calls it.
  - Phase 22 workers should use `Container.runtime`, or build one the same
    way, instead of wiring their own.

---

## 6. Domain / Data Model Changes

- **Domain:** no changes to entities, value objects, enums or state
  transitions.
- **Database:** no new tables, columns, indexes or constraints, and no
  migration.
- **Repository interface:** `ProjectRepository.using_plugin(plugin_id,
  node_types) -> Sequence[str]` is new. Every implementation must provide
  it; `FakeProjects` in `tests/application/test_trigger_service.py` was
  updated.
- **New error types:**

  | Error | Code | HTTP status | Module |
  |---|---|---|---|
  | `BackupInvalidError` | `BACKUP_INVALID` | 422 | `infrastructure.backup` |
  | `BackupIncompatibleError` | `BACKUP_INCOMPATIBLE` | 409 | `infrastructure.backup` |
  | `RestoreTargetNotEmptyError` | `RESTORE_TARGET_NOT_EMPTY` | 409 | `infrastructure.backup` |

  The existing `PluginInUseError` (`PLUGIN_IN_USE`) is now also raised with
  `details = {"plugin_id", "projects": [ids]}`.
- **New persisted artifact, backup format v1:**
  - Content: gzip-compressed JSON with keys `format` (`"akasha-backup"`),
    `format_version` (`1`), `engine_version`, `schema_revision` (Alembic
    `version_num`), `created_at` (ISO UTC), `plugins` (`{id: version}` of
    enabled plugins, sorted) and `tables` (`{table name: [row]}`).
  - Rows are ordered by primary key.
  - `DateTime` values are ISO strings and `LargeBinary` values are base64.
    JSON columns are native JSON.
  - Tables are listed parents first, in `metadata.sorted_tables` order.
    `sessions` is excluded.
- **Compatibility:** restore rejects any other `format_version`, unknown
  tables, and rows whose columns do not exactly match the current table.

---

## 7. API / Contract Changes

- **HTTP API:** no endpoints, request schemas or response schemas changed.
- **Console script (breaking for operators):** `akasha` with no arguments no
  longer starts the server; use `akasha serve`. `python -m akasha` follows
  the same change.
- **New CLI contract:**
  - the commands and flags listed in §3;
  - the exit codes 0, 1, 2 and 70;
  - `--json` output.
- **`--json` output shapes:**

  | Command | Shape |
  |---|---|
  | `project create` | `ProjectSummary` |
  | `project list` | `ProjectPage` |
  | `project validate` | `ValidationResponse` |
  | `project publish` | `ProjectVersionSummary` |
  | `project import` | `ImportProjectResponse` |
  | `project export` | `{path, bytes}` |
  | `project run`, `execution get`, `execution cancel` | CLI execution view (below) |
  | `node list`, `node validate` | `{node_id, version, name, category, description, inputs[], outputs[]}` |
  | `node test` | always JSON: `{outputs, events, metrics}` |
  | `plugin list`, `plugin install` | `{plugin_id, state, version, nodes, error}` |
  | `plugin remove` | `{plugin_id, affected_projects}` |
  | `backup` | `{path, bytes, schema_revision, engine_version, created_at, rows}` |
  | `restore` | `BackupSummary` fields |

  The CLI execution view is `{id, project_id, version, status,
  trigger_type, correlation_id, created_at, started_at, finished_at, error,
  nodes[{node_id, label, status, attempt, outputs}]}`. It is a CLI-only
  shape, because the API's `ExecutionDetail` has no error or node data.
- **Error contract on stderr:** `{code, message, details}` in JSON mode.
  The codes are the same stable `AkashaError` codes the API returns, plus
  three CLI-level ones:
  - `CONFIGURATION_ERROR`: settings failed validation;
  - `FILE_ERROR`: an `OSError` with a filename;
  - `INTERNAL_ERROR`: anything else.
- **Internal contracts:**
  - `ProjectRepository.using_plugin`;
  - `Container.runtime`;
  - `configure_logging(..., stream=)`;
  - `create_backup` / `restore_backup`;
  - `remove_plugin`.
- **Node, plugin and SDK contracts:** unchanged.

---

## 8. Implementation Details

### Command dispatch (`cli.main`)

1. `build_parser().parse_args(argv)`. Usage errors exit 2 through argparse.
   `serve` is handled before any container is built: uvicorn owns its own
   loop.
2. `load_settings()`. A pydantic `ValidationError` is reported with field
   locations and messages only, because pydantic echoes input values (such
   as a too-short `AKASHA_METRICS_TOKEN`).
3. Logging to stderr, unless `environment == "test"`.
4. `asyncio.run(_run(args, settings))`. `_run` builds the container, awaits
   the handler, and always calls `container.shutdown()`, which disposes the
   database pool. It never calls `container.start()`, so the CLI never
   starts the trigger scheduler.
5. Exception mapping:

   | Exception | Output | Exit |
   |---|---|---|
   | `AkashaError` | its code, message and redacted details | 1 |
   | `OSError` with `filename` | `FILE_ERROR` | 1 |
   | `OSError` without a filename (for example a refused database socket) | internal error | 70 |
   | any other exception | `INTERNAL_ERROR` with only the type name; the traceback goes to the stderr log through `logger.error(..., exc_info=exc)` | 70 |

### Service accessors

- `_need(service, setting)` raises `ConfigurationError` naming the missing
  variable (`AKASHA_DATABASE_URL` or `AKASHA_PLUGINS_DIR`) when the
  container lacks a service.
- This is how database-less and plugin-less installations fail: exit 1,
  and nothing is guessed.

### `project run`

- **Version:** `--version`, or `ProjectService.get_project(id).current_version`.
  Version 0 (unpublished) surfaces as the service's
  `PROJECT_VERSION_NOT_FOUND`.
- **Queue:** `TriggerService.fire_manual(project_id, version, input=...)`
  creates a QUEUED execution with MANUAL metadata and a generated
  correlation id.
- **Run:** `runtime.run(execution.id)` runs it to a terminal state in this
  process.
- **Ctrl-C:** `loop.add_signal_handler(SIGINT, ...)` schedules
  `runtime.cancel(id, reason="interrupted")`. Because the execution is
  active in this runtime, `cancel` only sets the cancellation event, and
  the scheduler loop stops node tasks cooperatively and persists CANCELLED.
  - Task references are held in a set so they are not garbage-collected.
  - `NotImplementedError` (Windows) and `RuntimeError` (not the main
    thread) are suppressed. Ctrl-C is then non-cooperative.
- **Result:** read back with `ExecutionService.get_execution` rather than
  taken from the in-memory aggregate. It shows persisted (redacted) outputs
  and is passed through `redact_mapping`.
- **Exit code:** 0 only for `completed`.

### `execution cancel`

- Calls `ExecutionRuntime.cancel`. For an execution not active in this
  process, that loads the aggregate, calls `Execution.cancel`, and saves
  with CAS.
- A terminal execution raises `INVALID_STATE_TRANSITION` from the domain
  state machine. The CLI adds no state logic.

### `node validate` / `node test`

- `_node(target)`: a target containing `:` is loaded with
  `importlib.util.spec_from_file_location` (paths ending in `.py`) or
  `importlib.import_module`. It must be a `Node` subclass and pass
  `check_node_contract`.
- Other targets resolve through `NodeRegistry.get(id)()`, so built-in nodes
  get their injected dependencies (network policy, LLM client).
- Loading runs module code. This is the same trusted-code model as custom
  nodes and plugins (§65).
- Load failures (`ImportError`, `OSError`, `SyntaxError`) become
  `NODE_NOT_FOUND` with a `reason` detail.
- `node test` delegates to `akasha.sdk.testing.run_node`. That runs the
  contract check, the plaintext-secret check, config-schema validation,
  input validation, `execute` and output validation.
- No credentials are passed (`credentials=None`): nodes that resolve
  credentials fail.

### `plugin remove`

- `remove_plugin` reads `registry.get(id).metadata`. For a `FAILED` plugin
  with an unreadable manifest the metadata is `None`, so only the
  plugin-dependency check applies.
- It then queries `projects_using_plugin` and either raises
  `PluginInUseError` or calls `PluginRegistry.remove`.

### `backup` (`create_backup`)

- **Snapshot:** `conn.execution_options(isolation_level="REPEATABLE READ")`
  plus `conn.begin()`, so all selects see one snapshot.
- **Output file:** written by `cli._write_private` with
  `os.open(O_WRONLY|O_CREAT|O_EXCL, 0o600)`. An existing file yields
  `FILE_ERROR` and the file is kept.
- **Blocking I/O:** file access in async commands goes through
  `asyncio.to_thread`.

### `restore` (`restore_backup`)

1. **Parse and validate the file** (`_read`): gzip, JSON, format, version,
   manifest field types, known tables, and list-typed tables.
2. **Plugins** (`_check_plugins`): every backed-up plugin must be enabled
   here, with the installed version satisfying
   `>=<backed-up>,<<major+1>.0.0` (`version_satisfies`).
3. **Rows** (`_decode`): each row's key set must equal the table's columns.
   `DateTime` values are parsed from ISO and `LargeBinary` from base64.
4. **Key** (`_check_key`): if there are credential rows, a cipher must be
   configured, and every `ciphertext` must decrypt with `context=name`. The
   plaintext is discarded at once.
5. **Migrate:** `Database.run_migrations(database_url)` brings the target
   to head.
6. **Load, in one transaction** (`_load`):
   - the target's `alembic_version` must equal `schema_revision`, else
     `BACKUP_INCOMPATIBLE`;
   - with `--replace`: `TRUNCATE <all tables> RESTART IDENTITY CASCADE`;
     otherwise any row in any table gives `RESTORE_TARGET_NOT_EMPTY`;
   - `ALTER TABLE events ALTER COLUMN position SET GENERATED BY DEFAULT`,
     so the original event positions can be inserted;
   - bulk insert per table, parents first;
   - `SET GENERATED ALWAYS`, then `setval(...)` so new events continue
     after `MAX(position)`.
7. **Constraint failures:** `IntegrityError` or `DataError` roll the
   transaction back and become `BACKUP_INVALID`. The installation is
   unchanged, including any TRUNCATE.

### Redaction

- All error messages and details pass `redact_text` or `redact_mapping`.
- The execution view and `node test` output pass `redact` or
  `redact_mapping`.
- Log lines use `TextFormatter`, which redacts.

---

## 9. Findings

### Finding: no process ever ran API-queued executions, and the runtime was not in the container

- **Impact:** MEDIUM
- **Resolution:** WORKAROUND. `Container.runtime` was added. The CLI runs
  what it queues itself. API-queued executions still wait for Phase 22.
- **Notes:** `api/triggers.py` documents this: "The execution is recorded
  as QUEUED. Running it is the worker's job (Phase 22)". The CLI is now the
  only built-in path that executes a project end to end outside tests.

### Finding: there is no migration command for operators

- **Impact:** MEDIUM
- **Resolution:** WORKAROUND. `restore` runs `Database.run_migrations`
  itself, so a fresh empty database can be restored into.
- **Notes:**
  - Outside tests, `run_migrations` is not called at server startup.
    Operators must run Alembic manually, as the Phase 05 report already
    notes.
  - `restore` migrates the target before checking whether it is empty. If
    the target is an older-revision database holding data, it is migrated
    even when the restore is then refused.
  - A dedicated `db migrate` command, or migrating at startup, belongs to
    operations (Phase 26).

### Finding: `events.position` is `GENERATED ALWAYS`

- **Impact:** LOW
- **Resolution:** FIXED (handled in restore).
- **Notes:** explicit positions cannot be inserted without
  `OVERRIDING SYSTEM VALUE`. Restore temporarily switches the column to
  `BY DEFAULT` inside the transaction, then resets the identity sequence.
  Positions are preserved exactly. This is verified by the event-list
  comparison and by a new run after restore succeeding.

### Finding: `configure_logging` always wrote to stdout

- **Impact:** LOW
- **Resolution:** FIXED (`stream` parameter).
- **Notes:** CLI results must own stdout so that `--json` output stays
  parseable.

### Finding: the architecture test silently skipped single-module layers

- **Impact:** LOW
- **Resolution:** FIXED.
- **Notes:**
  - `test_dependency_direction` only scanned directories and skipped
    missing ones. A `cli` rule on `cli.py` would have passed vacuously.
  - The scanner now also checks `<layer>.py`.
  - Verified by temporarily appending `import sqlalchemy` to `cli.py`: the
    test failed with `cli.py must not import sqlalchemy`. The line was then
    removed.

### Finding: asyncio signal handlers work only on the main thread

- **Impact:** LOW
- **Resolution:** WORKAROUND.
- **Notes:**
  - `loop.add_signal_handler` raises `RuntimeError` outside the main
    thread, as happens in the threaded integration tests.
  - The CLI suppresses it; Ctrl-C then stops the process uncooperatively.
  - The cooperative path is covered by a real-subprocess test.

### Finding: the API's `ExecutionDetail` DTO has no error or node information

- **Impact:** LOW
- **Resolution:** WORKAROUND. The CLI has its own execution view,
  `_execution_payload`.
- **Notes:** the frontend will need similar data. A richer execution DTO
  belongs to the API or frontend phases.

### Finding: SQLAlchemy `JSON` stores Python `None` as JSON `null`, not SQL NULL

- **Impact:** LOW
- **Resolution:** no change needed.
- **Notes:** the repositories already write `None` the same way, and
  reading back yields `None` for both forms. Restore reproduces what a
  normal write produces.

---

## 10. Bugs / Problems Found

All of the following were introduced during this phase and fixed before
completion. No pre-existing product bugs were found. No infrastructure or
environment problems occurred: Docker was available and every Testcontainers
test ran.

| Problem | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|
| A refused database connection would be reported as `FILE_ERROR` | The first draft mapped every `OSError` to a file error; `ConnectionRefusedError` is an `OSError` | Only an `OSError` with a `filename` is a file error; others are internal (exit 70) | `test_unreachable_database_is_an_internal_error_without_secrets` | `cli.main` |
| A restore with a constraint-violating row surfaced as `INTERNAL_ERROR` | `IntegrityError` escaped `restore_backup` | Map `IntegrityError` / `DataError` to `BACKUP_INVALID`; the transaction rollback keeps the database unchanged | `test_restore_rejects_incompatible_backups_without_changes[bad_status]` | `backup.py` |
| CLI logging would evict pytest's log handlers | `configure_logging` clears the root handlers | Skip it when `environment == "test"`, as `create_app` does | the whole test suite (caplog-based tests still pass) | `cli.main` |
| The signal-handler setup raised in a non-main thread | `add_signal_handler` raises `RuntimeError` there | Suppress `RuntimeError` as well as `NotImplementedError` | integration tests run the CLI in a thread | `project_run` |
| Lint findings in the new code | `print` in the CLI (T201), blocking `Path.write_bytes` in async (ASYNC240), f-string SQL builder (S608) | T201 ignored for `cli.py` with a reason; file I/O through `asyncio.to_thread`; the SQL rewritten as one static literal | `ruff check` | `cli.py`, `persistence.py` |
| mypy error in table rendering | Header row and data rows had different tuple types | `_table(rows, header)` | `mypy` | `cli.py` |

Test-authoring mistakes, fixed in the tests:

- a wrong expected port type (`any` instead of `json`);
- a plugin test publishing an empty graph, which is invalid;
- restore tests running the echo project without its required input.

The implementation was correct in each case.

---

## 11. Fixes Applied

- **Refused database connection reported as `FILE_ERROR`.** Fixed with the
  `filename` check in the `OSError` handler. Verified by the unit test that
  points `AKASHA_DATABASE_URL` at `127.0.0.1:1`. It asserts exit 70 and
  that the password is absent from both streams.
- **Constraint violation during restore surfaced as an internal error.** The
  load is wrapped in `(IntegrityError, DataError)` → `BACKUP_INVALID`.
  Verified by the parametrized test: a snapshot is equal before and after a
  failed `--replace` restore.
- **pytest log handlers evicted.** Logging is not configured in the test
  environment. Verified by the full suite passing.
- **Architecture guard blind to single-file layers.** The scanner was
  extended. Verified by a temporary injected violation.

---

## 12. Testing & Verification

### Unit Tests: `backend/tests/test_cli.py`, 36 tests, PASS

These call `akasha.cli.main([...])` in-process and capture stdout and
stderr. An autouse fixture removes every `AKASHA_*` variable and sets
`AKASHA_ENVIRONMENT=test`.

- **Parsing** (9 parametrized cases, all exit 2 with usage on stderr):
  - no command; an unknown command; a missing subcommand;
  - a missing positional; a missing `--output`;
  - a non-integer `--version`;
  - invalid JSON, a non-object JSON value, and an unreadable `@file` for
    JSON options.
- **Help and version:** `--help` and `--version` exit 0. `--input @file` is
  accepted.
- **Configuration:**
  - database commands without `AKASHA_DATABASE_URL` → exit 1,
    `CONFIGURATION_ERROR` naming the variable;
  - plugin commands without `AKASHA_PLUGINS_DIR` → the same;
  - an invalid `AKASHA_METRICS_TOKEN` is reported without echoing its
    value;
  - the JSON error format on stderr;
  - an unreachable database → exit 70, no password in the output.
- **Nodes:**
  - `node list` in text and JSON;
  - `node validate` for a file-loaded class, a registered id, and a class
    without `execute` (`NODE_CONTRACT_ERROR`);
  - five bad-target cases (`NODE_NOT_FOUND`);
  - `node test` with a file class and with registered `core.input`;
  - four failure cases: config-schema, input type, missing input,
    plaintext secret, each with the expected code and the secret absent
    from stderr.
- **Plugins:**
  - install then list, text and JSON, including the node registered in a
    fresh process;
  - duplicate install → `PLUGIN_ALREADY_INSTALLED`;
  - invalid package: nothing left in the plugin directory;
  - `plugin remove` without a database is refused and the plugin is kept.
- **Backup file helper:** mode 0600, and `FileExistsError` with the
  original content kept.

### Integration Tests: `backend/tests/integration/test_cli.py`, 14 tests, PASS

These run on real PostgreSQL `postgres:17.2`. Each command runs
`akasha.cli.main` in a worker thread with its own container and event loop,
configured through environment variables. Arrangement and assertions use the
application services on the test loop.

- **`test_project_lifecycle`:**
  - `create --json` gives `owner_id = None`;
  - validate → `valid`;
  - publish → version 1;
  - the project appears in `list`;
  - `run --input` → `completed`, with per-node statuses keyed by label;
  - `execution get`;
  - export version 1 → import → a new id, and the validation passes.
- **`test_invalid_draft_fails_validate_and_publish`:** exit 1,
  `PROJECT_VALIDATION_FAILED`, `current_version` still 0.
- **`test_publish_with_stale_revision_conflicts`:**
  `CONCURRENT_MODIFICATION`.
- **`test_run_failures`:**
  - a missing project → `PROJECT_NOT_FOUND`;
  - an unpublished project → `PROJECT_VERSION_NOT_FOUND`;
  - `core.http` to loopback, blocked by the default network policy → status
    `failed`, exit 1.
- **`test_execution_cancel`:**
  - a queued execution → `cancelled`, reason persisted;
  - cancelling again → `INVALID_STATE_TRANSITION`;
  - an unknown id → `EXECUTION_NOT_FOUND`.
- **`test_plugin_remove_is_blocked_while_projects_need_it`:**
  - blocked by draft usage, and by a published version that declares a
    plugin dependency while its current draft does not;
  - an unrelated project is not listed;
  - `details.projects` is exact and sorted;
  - `--force` removes the plugin and reports the affected projects.
- **`test_unused_plugin_is_removed`:** removal succeeds; a second removal
  gives `PLUGIN_NOT_FOUND`.
- **`test_backup_destroy_restore_run`** (AGENTS §100):
  - arrange: an admin with a session, an encrypted credential, and a
    published project that has been run;
  - backup: file mode 0600, `sessions` not in the counts, the secret not in
    the decompressed file; a second backup to the same file →
    `FILE_ERROR`;
  - restore into the live database → `RESTORE_TARGET_NOT_EMPTY`;
  - `truncate_all`, then restore → the project, version, execution and
    event history (ids, types, times, order) are equal;
  - the credential decrypts to the original value;
  - the admin can log in, and the old session is rejected;
  - the restored project runs again, so the event identity continues;
  - `--replace` returns exactly to the backed-up state, and the new
    execution is gone.
- **`test_restore_rejects_incompatible_backups_without_changes`** (4
  parametrized cases):
  - schema revision → `BACKUP_INCOMPATIBLE`;
  - required plugin missing → `BACKUP_INCOMPATIBLE`;
  - extra column → `BACKUP_INVALID`;
  - check-constraint violation inside the load → `BACKUP_INVALID`.

  In every case the installation snapshot is unchanged.
- **`test_restore_checks_file_and_key`:**
  - a garbage file → `BACKUP_INVALID`;
  - a wrong key and a missing key → `BACKUP_INCOMPATIBLE`;
  - a missing file → `FILE_ERROR`.
- **`test_ctrl_c_cancels_the_run_cooperatively`:**
  - a real `python -m akasha project run` subprocess on a
    `core.delay(3600)` graph;
  - the test waits for the persisted `running` status, then sends SIGINT;
  - result: exit 1, "cancelled" on stdout, and a persisted `cancelled`
    status with reason `interrupted`.

### Architecture Test: PASS

`tests/test_architecture.py`, now with the `cli` rule, was verified against
an injected violation (§9).

### Infrastructure Tests: PASS

- The Testcontainers PostgreSQL 17.2 suite runs with
  `AKASHA_TESTS_REQUIRE_DOCKER=1`, so no test was skipped for missing
  Docker.
- `restore` exercises the Alembic migrations against the shared test
  database (already at head, so the run is a no-op).

### E2E / manual

A manual smoke script (scratchpad, not committed) ran text-mode commands
against a throwaway `postgres:17.2` container: `project list`, `validate`,
`publish`, a successful `run`, a failing `run`, `execution get`,
`execution cancel` (terminal), `backup`, `restore` (refused), `restore
--replace`, and `project import` of a non-package. The output was reviewed
for readability. It was not automated beyond the integration tests.

### Security Tests: PASS

See §15. All run in the normal suite.

### Not performed

- Component, API (no HTTP change), accessibility and performance tests.
- The CLI was not tested on Windows.

---

## 13. Quality Gates

All results come from the final run on the current working tree.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `uv run ruff format --check .` → 175 files already formatted |
| Linter | PASS | `uv run ruff check .`; one documented per-file ignore (`T201` in `src/akasha/cli.py`); earlier T201/ASYNC240/S608 findings fixed (§10) |
| Type Checker | PASS | `uv run mypy .` (strict) → no issues in 171 source files; an earlier `_table` typing error was fixed |
| Unit Tests | PASS | Part of the full suite; `tests/test_cli.py` has 36 tests |
| Integration Tests | PASS | Part of the full suite; `tests/integration/test_cli.py` has 14 tests |
| Full suite | PASS | `AKASHA_TESTS_REQUIRE_DOCKER=1 uv run pytest` → 1648 passed, 5 skipped, 1 warning, 131.86 s |
| Skipped tests | — | The 5 skips are `tests/external/test_openrouter_smoke.py`: `AKASHA_SMOKE_OPENROUTER_API_KEY` not set, opt-in by design |
| Warning | — | The one warning is the existing `StarletteDeprecationWarning` from `fastapi.testclient` (not from this phase) |
| API Tests | N/A | No HTTP change; the existing API tests pass within the suite |
| E2E Tests | N/A | Only the real-subprocess Ctrl-C test and a manual smoke; no automated E2E suite exists |
| Build | PASS | `uv build` produced wheel and sdist; `dist/` was removed afterwards (it is git-ignored) |
| Security Checks | PASS | Security-focused tests pass (§15); no separate scanner exists in the repository |
| Accessibility | N/A | Backend CLI |

---

## 14. Acceptance Criteria Verification

The roadmap's explicit acceptance criterion comes first, then each
roadmap-scoped command, the scope rule, the roadmap test requirement, and
the session's testing list.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Core Engine operations can be performed without the frontend | PASS | `test_project_lifecycle` runs create → validate → publish → run → inspect → export → import through the CLI alone; plugins, executions and backup/restore are covered by their own integration tests |
| `project create` | PASS | `test_project_lifecycle` |
| `project validate` | PASS | `test_project_lifecycle`, `test_invalid_draft_fails_validate_and_publish` |
| `project publish` | PASS | the same two tests plus `test_publish_with_stale_revision_conflicts` |
| `project run` | PASS | `test_project_lifecycle`, `test_run_failures`, `test_ctrl_c_cancels_the_run_cooperatively` |
| `project import` | PASS | `test_project_lifecycle`; garbage input gives `PROJECT_PACKAGE_INVALID` (manual smoke) |
| `project export` | PASS | `test_project_lifecycle` |
| `node list` | PASS | `test_node_list` |
| `node validate` | PASS | `test_node_validate`, `test_node_validate_rejects_bad_targets` |
| `node test` | PASS | `test_node_test_runs_the_node`, `test_node_test_registered_node`, `test_node_test_failures` |
| `plugin list` | PASS | `test_plugin_install_and_list`, integration plugin tests |
| `plugin install` | PASS | `test_plugin_install_and_list`, `test_plugin_install_rejects_invalid_package` |
| `plugin remove` | PASS | `test_plugin_remove_is_blocked_while_projects_need_it`, `test_unused_plugin_is_removed`, `test_plugin_remove_needs_the_database` |
| `execution get` | PASS | `test_project_lifecycle`, `test_execution_cancel` |
| `execution cancel` | PASS | `test_execution_cancel`; cooperative cancel of a running execution through `test_ctrl_c_cancels_the_run_cooperatively` |
| `backup` | PASS | `test_backup_destroy_restore_run` |
| `restore` | PASS | `test_backup_destroy_restore_run`, `test_restore_rejects_incompatible_backups_without_changes`, `test_restore_checks_file_and_key` |
| Reuse application services; no duplicated domain logic | PASS | §3/§8 map each command to its service; `cli.py` has no SQLAlchemy/FastAPI imports (enforced by the architecture test); the one new rule (plugin-usage blocking) lives in `application/plugins.py` |
| Roadmap tests: "Test representative commands" | PASS | Every command has at least one success test and one failure test |
| Session: command parsing | PASS | `test_usage_errors_exit_2` (9 cases), `test_version_and_help` |
| Session: validation | PASS | JSON option validation; project, node, config and backup validation tests |
| Session: success paths | PASS | the integration tests above |
| Session: failure paths | PASS | see §12 |
| Session: authentication where applicable | PARTIAL (by design) | The CLI has no login (§5.2); the tests verify the configuration gate (no database or plugin directory → refusal) and that restored accounts can log in while old sessions are rejected. A CLI login was judged not applicable; this does not block the phase |
| Session: output | PASS | text and `--json` assertions in the unit and integration tests; JSON error format test |
| Session: exit codes | PASS | 0/1/2/70 asserted across the tests |
| Session: integration tests for important commands | PASS | 14 integration tests |

---

## 15. Security Considerations

- **Authentication and authorization:** the CLI has no login (§5.2).
  - Its authority equals holding the `AKASHA_*` configuration: the database
    URL and the encryption key.
  - CLI-created projects are ownerless, so the API shows them to admins
    only.
  - This is documented in `cli.py`, the README and architecture §58.
- **Secrets on the command line:**
  - `node test` deliberately offers no credential flag (§107).
  - JSON options accept `@file` for larger payloads.
  - No CLI option takes a secret.
- **Secret redaction:**
  - Error messages and details are redacted.
  - Configuration errors print field locations and messages only. Tested:
    an invalid metrics token value is not echoed.
  - Database connection errors print only the exception type. Tested: the
    database password is not printed.
  - The execution view and `node test` output are redacted.
  - A plaintext secret in `node test --config` is refused.
- **Backup file:**
  - It contains password hashes (scrypt) and credential ciphertext, never
    plaintext. Tested: the secret string is not in the decompressed file.
  - It is created with mode 0600 through `O_EXCL`, so it never overwrites
    or loosens an existing file.
  - The encryption key is not included; restore refuses without the key
    that decrypts the credentials.
  - Sessions are not backed up, so restored installations start logged out
    (tested).
- **Restore input:**
  - The format, rows and manifest are validated.
  - Unknown tables and columns are rejected.
  - Constraint violations roll back.
  - The file is not size-limited or protected against gzip bombs. It is
    operator-supplied, and this is recorded in §20.
- **Plugin and node code execution:**
  - `plugin install` and `node validate/test module:Class` run Python code
    in the CLI process, under the existing trusted-code model (§65).
  - `plugin remove` is now blocked while projects need the plugin.
- **SQL:** the new `_USING_PLUGIN` query is a static literal with bound
  parameters. The `TRUNCATE` statement is built only from `metadata` table
  names.
- **SSRF:** unchanged. `project run` uses the same `NetworkPolicy`; a
  loopback `core.http` destination is blocked (tested in
  `test_run_failures`).

---

## 16. Performance Considerations

- **Backup:**
  - The whole backup is built in memory. `create_backup` holds every row,
    and the JSON plus gzip copies.
  - Restore holds the decompressed document and the decoded rows.
  - There is a `ponytail:` marker at the construction site. Streaming per
    table is the upgrade path.
  - No measurements were performed.
- **Restore:** decrypts every stored credential once, to validate the key.
  The cost is linear in the number of credentials.
- **Plugin usage query:** one SQL statement using `jsonb_array_elements`
  over every project draft and every published version. No JSONB index
  exists, so it is a full scan of `projects` and `project_versions`. This
  is acceptable for a rare operator action; not measured.
- **`project run`:** uses the runtime's default `max_concurrency=8`.
- **Process startup:** each CLI invocation builds a full container,
  including loading installed plugins, and creates a pool that is disposed
  at exit. Not measured.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `backend/README.md` | Changed entry point; new CLI | `uv run akasha serve`; a CLI section with a command table, trust model, backup contents and exclusions, and exit codes; `cli.py` in the layout tree |
| `docs/architecture.md` §58 | CLI implementation notes | commands, service mapping, `project run` behaviour, plugin removal guard, trust model, output and exit codes, what is not provided |
| `docs/architecture.md` §110 | Backup implementation | format, snapshot isolation, what is and is not included, file mode, in-memory limitation |
| `docs/architecture.md` §111 | Restore implementation | validation order, schema-revision rule, one-transaction load, `--replace` semantics, rollback guarantee |
| `docs/architecture.md` §36 | Stale deferral | "left for import/export and CLI" replaced by a reference to the new guard |
| `docs/reports/backend/phase-21-cli.md` | Phase report | this document |

Documentation that should be updated later:

- **Phase 26 operations docs:** a migration procedure (§9) and backup
  handling.
- **Phase 22:** when workers exist, the "`project run` runs in-process"
  statements in the README and §58.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or upgraded. `uv.lock` is
  unchanged. `argparse`, `gzip`, `json`, `base64` and `importlib` are
  stdlib.
- **`pyproject.toml`:**
  - console script target changed to `akasha.cli:main`;
  - ruff per-file ignore `"src/akasha/cli.py" = ["T201"]`, with a comment:
    printing is the CLI's interface.
- **Environment variables:** none new. The CLI reads the existing
  `AKASHA_*` settings. Each command needs:
  - `AKASHA_DATABASE_URL`: project, execution, backup and restore
    commands, and `plugin remove`;
  - `AKASHA_PLUGINS_DIR`: plugin commands;
  - `AKASHA_CREDENTIAL_ENCRYPTION_KEY`: `project run` for nodes that
    resolve credentials, and restoring a backup that holds credentials.
- **Database:** no migration. Restore runs the existing migrations.
- **Docker:** no image or compose changes.
- **Test infrastructure:** no new fixtures in `conftest.py`. The CLI
  integration tests reuse `db_url`, `database`, `uow`, `clean_db`,
  `auth_kit` and `truncate_all`.

---

## 19. Deferred Work

None of these items affects the completion of this phase.

| Item | Why deferred | Intended phase | Blocker |
|---|---|---|---|
| Running executions queued by the API or triggers | Needs the worker/queue architecture | Phase 22 | — |
| Recovery of executions orphaned RUNNING (for example a CLI killed with SIGKILL) | Recovery semantics are their own phase | Phase 23 | — |
| Restore across schema revisions (older backup → newer engine) | Needs restore at the backup's revision followed by an upgrade, with metadata reflection; not required by the roadmap | Unassigned (Phase 26 candidate) | — |
| Streaming or size-bounded backup/restore | Not needed at current scale; marked `ponytail:` | Phase 24 / 26 | — |
| Operator migration command (`db migrate`) | Not in the Phase 21 scope; restore migrates as needed | Phase 26 | — |
| `plugin enable` / `disable` commands, and a usage guard for disable | Not in the roadmap command list | Unassigned | — |
| CLI for users and credentials, draft editing, project deletion, execution listing | Not in the roadmap command list; available through the API | Unassigned | — |
| Assigning an owner to CLI-created or imported projects | Needs a username lookup; ownerless (admin-only) is safe | Unassigned | — |
| Richer execution DTO in the API (error and node states) | API contract change outside this phase | Frontend/API phase | — |

---

## 20. Known Limitations

- **No CLI authentication.** Access to the CLI and its configuration is
  access to the installation (§5.2).
- **`project run` executes inside the CLI process.**
  - A killed process (SIGKILL, host crash) leaves the execution RUNNING
    until Phase 23 recovery exists.
  - On Windows, or outside the main thread, Ctrl-C is not cooperative.
- **Cancel from another process.** `execution cancel` on an execution run by
  another process persists CANCELLED. The owning process then fails with
  `CONCURRENT_MODIFICATION` on its next save, which is the runtime's
  existing CAS semantics.
- **Same schema revision required.** Restore requires the backup's schema
  revision to equal the target head.
- **Restore migrates first.** It migrates the target before checking
  whether it is empty (§9).
- **In memory, no size limit.** Backup and restore are held in memory, and
  restore has no size or decompression limit.
- **Backup coverage.** The backup does not include plugin code, engine
  configuration or the encryption key; the operator must preserve these.
  Artifacts do not exist yet.
- **Plugin check strictness.** The restore plugin check requires plugins
  that were enabled at backup time to be enabled now. A plugin that was
  disabled at backup time is not recorded.
- **`plugin remove` needs the database** even on an installation without
  projects.
- **`node test` has no credentials.** Nodes that resolve credentials fail.
- **Platform-specific query.** `ProjectRepository.using_plugin` is
  PostgreSQL-specific, like the rest of the persistence layer.
- **Changed server command.** `akasha` without arguments no longer starts
  the server.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| API- and trigger-queued executions are never run by any process | Webhooks, schedules and API runs stay QUEUED | MEDIUM (pre-existing, known) | runtime / workers | Implement workers that use `Container.runtime` | Phase 22 |
| No operator migration path apart from restore or raw Alembic | Fresh installs and upgrades need manual Alembic | MEDIUM | operations | Add `akasha db migrate`, or migrate at startup | Phase 26 |
| Restore migrates the target before its emptiness check | An older non-empty database pointed at by mistake is upgraded, though not overwritten | LOW | backup | Check emptiness and revision before migrating, or require an explicit flag | Phase 25 / 26 |
| No size or decompression limit on restore input | A huge or malicious file could exhaust memory (operator-supplied) | LOW | backup | Stream the input and cap its size | Phase 24 / 25 |

---

## 22. Architectural / Product Decisions

- **The CLI is an in-process operator tool, not an API client** (§5.1). The
  API DTOs are reused for JSON output so both surfaces print the same
  shapes.
- **No login; operator authority; CLI-created projects are ownerless**
  (§5.2).
- **Plugin removal is blocked by any usage in a draft or published
  version**, with `--force` as the override (§5.3). The rule lives in
  `application/plugins.py`, not in `PluginRegistry`, which must stay free
  of persistence.
- **The backup is logical, versioned, and validated before any write;
  restore is atomic** (§5.4). Event positions are restored exactly.
  Sessions and the key are never included.
- **`akasha serve`** is the server command (§5.5).
- **`Container.runtime`** is the canonical runtime and shares the
  container's registry, credentials, events and metrics (§5.6).
- **Exit codes 0, 1, 2 and 70** are a stable scripting contract, and
  `--json` errors on stderr use the API's `{code, message, details}` shape.
- **argparse** was chosen over click or typer, so no dependency was added.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **Base commit:** `00b8bf0 backend phase 20 - authentication and
  authorization`.
- **Working tree:** uncommitted. Nothing was committed in this phase.
- **Modified (11 files):**
  - `backend/README.md`
  - `backend/pyproject.toml`
  - `backend/src/akasha/__main__.py`
  - `backend/src/akasha/application/persistence.py`
  - `backend/src/akasha/application/projects.py`
  - `backend/src/akasha/bootstrap.py`
  - `backend/src/akasha/infrastructure/persistence.py`
  - `backend/src/akasha/observability/logging.py`
  - `backend/tests/application/test_trigger_service.py`
  - `backend/tests/test_architecture.py`
  - `docs/architecture.md`

  Totals: 162 insertions, 20 deletions.
- **Untracked (6 files):**
  - `backend/src/akasha/application/plugins.py`
  - `backend/src/akasha/cli.py`
  - `backend/src/akasha/infrastructure/backup.py`
  - `backend/tests/integration/test_cli.py`
  - `backend/tests/test_cli.py`
  - `docs/reports/backend/phase-21-cli.md`
- **Unrelated changes:** none detected. The tree was clean at the start of
  the phase.
- **Build output:** `uv build` wrote `backend/dist/`, which is git-ignored.
  It was removed after the gate ran.

---

## 24. Phase Completion Assessment

### Implemented

- **All roadmap commands:**
  - `project create | validate | publish | run | import | export`
  - `node list | validate | test`
  - `plugin list | install | remove`
  - `execution get | cancel`
  - `backup`, `restore`
- **Also added:** `project list` and `akasha serve`.
- **Supporting pieces:**
  - the plugin-removal usage guard;
  - logical backup/restore;
  - runtime wiring in the container;
  - logging to stderr for the CLI.

### Verified

- 36 unit and 14 integration tests on real PostgreSQL, including backup →
  destroy → restore → run and a real-process Ctrl-C test.
- The full suite: 1648 passed.
- ruff format and check, strict mypy, `uv build`.
- The architecture guard, proven against an injected violation.

### Not Implemented

- CLI authentication (deliberately, §5.2).
- The commands outside the roadmap list (§19).

### Deferred

- Workers (Phase 22), recovery (Phase 23), cross-revision restore,
  streaming backup and a migration command. See §19.

### Open

- API-queued executions are never run; no migration command; restore
  migrates before its emptiness check; no restore size limit. See §21.

### Blocking Issues

- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (Phase 22 — Workers + Queue Architecture)

- **Assume:**
  - `Container.runtime` is a correctly wired `ExecutionRuntime`: the shared
    `NodeRegistry.catalog`, `CredentialService`, `EventPublisher` and
    `EngineMetrics`.
  - Workers should take the runtime from the container, or build one the
    same way. Do not create a second, differently configured runtime.
- **Available contracts:**
  - `TriggerService.fire_manual` and `ExecutionService.create_execution`
    produce QUEUED executions.
  - `ExecutionRuntime.run` and `ExecutionRuntime.cancel` are the only
    execution drivers.
  - `akasha project run` is the reference consumer: queue, then run
    in-process.
- **When workers arrive:**
  - Decide whether `project run` keeps running in-process, perhaps as a
    `--local` mode, or only enqueues and follows the execution.
  - Update README and §58 either way.
  - `execution cancel` from the CLI must then signal the owning worker
    rather than rely on the CAS conflict.
- **Do not change casually:**
  - the CLI exit codes (0, 1, 2, 70);
  - the `--json` shapes, which reuse the API DTOs;
  - the backup `format_version: 1` layout (bump it for any change);
  - `akasha serve`;
  - `ProjectRepository.using_plugin` semantics (draft plus all versions);
  - the rule that `cli.py` must not import SQLAlchemy or FastAPI.
- **Constraints:**
  - Restore requires an equal schema revision, so any new migration makes
    older backups unrestorable with the new engine. Document this in
    release notes, or implement cross-revision restore.
  - Every new table must be representable in the backup:
    - `DateTime` and `LargeBinary` columns are handled;
    - other non-JSON types, or new identity columns, need handling in
      `backup.py`;
    - the tests catch regressions through the round-trip test.
- **Prerequisites:** none outstanding from this phase.
