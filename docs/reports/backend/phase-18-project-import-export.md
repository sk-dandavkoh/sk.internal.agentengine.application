# Phase 18 — Project Import / Export

## 1. Executive Summary

**Supposed to achieve.** Roadmap §22 ("Make Projects portable") asks for:
- `ProjectPackage`, `ProjectManifest`, `ProjectExporter`, `ProjectImporter`,
  `SchemaValidator` and `DependencyValidator`;
- package contents: manifest, project, graph, node configuration, prompts,
  resources, documentation and assets;
- no secrets in packages;
- tests for export, import, round trip, invalid package, missing
  dependency, incompatible version and secret exclusion.

The session request added requirements:
- imports validate schema, versions, dependencies, node availability,
  plugin requirements and malformed data;
- round trip Export → Import → Validate → Compare semantic project →
  Publish → Execute;
- malicious and malformed package tests.

**Actually implemented.**
- **Package format:** a documented ZIP format
  (`akasha.application.project_package`, `docs/project-format.md`) with a
  strict manifest, an integrity inventory and a hardened reader.
- **Use cases:** export and import in `ProjectPackageService`
  (`akasha.application.packages`).
- **HTTP:** two endpoints on the existing projects router.
- **Project files:** a new persisted domain concept
  (`ProjectDraft.files` / `ProjectVersion.files`, migration
  `0007_project_files`), so documentation, prompts, resources and assets
  round-trip losslessly.
- **Audit events:** `project.exported` and `project.imported`.
- **Test-infrastructure fix:** the Testcontainers readiness probe is made
  consistent and can be made mandatory (F-3).
- **Pre-existing bugs fixed on request:** the user asked for them to be
  fixed before closing the phase.
  - Values PostgreSQL cannot store (NaN, Infinity, NUL, unpaired
    surrogates) caused 500s on the project and credential APIs.
  - The same values, and malformed node errors, crashed the execution
    runtime mid-run.

**Resulting system state.** A project can be exported (draft or published
version) and imported into an empty installation with a different
encryption key, node registry and credential values. It is then
semantically identical, can be published, and executes. This is verified
against PostgreSQL through the real runtime and an LLM node talking to a
local fake provider.

**Acceptance.** Every roadmap acceptance criterion and requested test
category passes with concrete evidence (§14).
- The first version of this report (status COMPLETE WITH LIMITATIONS)
  recorded two open issues found during report verification:
  - bug **B-9**: crafted ZIP headers made `read_package` raise an uncaught
    `NotImplementedError`, so import answered 500;
  - finding **F-3**: a flaky Docker probe silently skipped integration
    suites.
- Both were fixed afterwards on the user's request, with regression tests
  (§10, §11).
- Final full suite, run twice with Docker made mandatory
  (`AKASHA_TESTS_REQUIRE_DOCKER=1`): 1490 passed, and only the 5 external
  smoke tests were skipped.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap §22 (Phase 18):

- **Goal:** Make Projects portable.
- **Implement:** `ProjectPackage`, `ProjectManifest`, `ProjectExporter`,
  `ProjectImporter`, `SchemaValidator`, `DependencyValidator`.
- **Package contents:** manifest, project, graph, node configuration,
  prompts, resources, documentation, assets.
- **Constraint:** never include secrets.
- **Tests:** export, import, round trip, invalid package, missing
  dependency, incompatible version, secret exclusion.
- **Acceptance criterion:** "A Project can be exported and imported into
  another compatible installation."

Architecture §56/§57 give the example layout (`manifest.json`,
`project.json`, `nodes/`, `prompts/`, `resources/`, `docs/`, `assets/`) and
the example manifest fields. Backend AGENTS §46 lists what import must
validate: schema version, engine compatibility, node availability, plugin
dependencies, config, graph, resources and security rules. It also makes
round-trip tests mandatory.

**Explicitly not responsible for:**
- CLI `project import/export` (Phase 21);
- authentication and authorization of the endpoints (Phase 20);
- an editing API for project files (frontend roadmap);
- project schema migrations beyond schema 1;
- packaging plugin code (plugins are installed separately, Phase 17);
- reports (Phase 19).

---

## 3. Scope Implemented

### Domain
- **`ProjectDraft.files: dict[str, bytes]`** and
  **`ProjectVersion.files: Mapping[str, bytes]`**. `ProjectDraft.publish`
  copies the files into the version.
- **Path rule `is_project_file_path` / `validate_project_files`**
  (`domain/project.py`):
  - paths look like `<root>/<segment>…`, with root one of `docs`, `prompts`,
    `resources` or `assets`;
  - there are 1–8 segments, each `[A-Za-z0-9][A-Za-z0-9._-]{0,99}`;
  - limits: at most 100 files, 1 MiB per file and 4 MiB in total.
- **`plaintext_secret_locations(graph, triggers, files)`**:
  - extracted from `Project.replace_draft`, so export and every draft write
    share one secret rule;
  - also flags credential-shaped text in UTF-8 project files.
- **`PROJECT_SCHEMA_VERSION`**, renamed from the private `_SCHEMA_VERSION`
  and made public so packages can carry it.
- **Storable-value rule** (`domain/primitives.py`):
  - `is_storable(value)` checks for JSON with no NaN/Infinity, no NUL and
    no unpaired UTF-16 surrogates;
  - `validate_text` and `storable_text` build on it;
  - `validate_name` now also rejects unstorable characters.
- **`unstorable_locations(draft)`** (`domain/project.py`):
  `Project.replace_draft` rejects unstorable content and names its
  locations. `Project.create` validates the description.
- **Credentials:** `Credential.__post_init__` validates the description.
- **Executions** (`domain/execution.py`):
  - `ExecutionError` replaces NUL and unpaired surrogates in the message
    and details;
  - `ExecutionError.from_node_error` falls back to generic values for a
    blank message, a malformed code or an unknown kind;
  - the private `_is_json` is replaced by the shared `is_storable`.
- **Events** (`domain/event.py`): `EventType.PROJECT_EXPORTED` and
  `EventType.PROJECT_IMPORTED`, both with empty payload keys.

### Application
- **`application/project_package.py`** (format, SchemaValidator):
  - Pydantic schemas `ProjectManifest`, `Requirements`, `NodeRequirement`,
    `FileEntry` and `ProjectDocument`;
  - the `ProjectPackage` dataclass;
  - `write_package` and `read_package(data, *, engine_version)`;
  - errors `ProjectPackageInvalidError`, `ProjectPackageTooLargeError` and
    `ProjectPackageIncompatibleError`.
- **`application/packages.py`**: `ProjectPackageService.export_project`
  (the exporter), `import_project` (the importer) and `_requirement_issues`
  plus `validate_dependencies` (the DependencyValidator); also
  `ImportResult`.
- **`application/projects.py`**:
  - `_load` was renamed to the public `load_project`, which the package
    service reuses;
  - `update_draft` keeps the stored `files`.
- **`application/credentials.py`**: `update` applies a new description via
  `dataclasses.replace`, so domain validation runs again.

### Backend / API
- `GET /api/v1/projects/{project_id}/export[?version=N]` and
  `POST /api/v1/projects/import` (`api/projects.py`).
- `ProjectFileOut` and a `files` list on `ProjectDetail` and
  `ProjectVersionDetail`. New `ImportProjectResponse`
  (`api/project_schemas.py`).
- `415 → UNSUPPORTED_MEDIA_TYPE` in the HTTP error-code map
  (`api/app.py`).
- `Container.packages` wired in `create_container` (`bootstrap.py`).

### Persistence
- Migration `0007_project_files`: `projects.draft_files` and
  `project_versions.files`, both JSONB, NOT NULL, default `'{}'`.
- Table definitions updated (`infrastructure/db_models.py`).
- `serialize_files` / `deserialize_files` (`{path: base64}`) in
  `infrastructure/graph_serialization.py`. Repositories read and write the
  new columns.
- `_persistable` (node outputs) also stores the `$unpersisted` stub for NUL
  and unpaired surrogates.

### Testing
- Six new test modules and additions to five existing ones (see §12).
- The Testcontainers gate `tests/support.docker_available` is now:
  - cached for the session, with a 30 s timeout;
  - mandatory when `AKASHA_TESTS_REQUIRE_DOCKER=1` is set (F-3).

---

## 4. Files Changed

All changes are uncommitted on `main` (see §23). There are 22 modified
tracked files (+774 / −75 lines by `git diff --stat`) and 11 new files.

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/application/project_package.py` | Created | Package format, schemas, reader and writer, errors; `NotImplementedError` from `zipfile` is mapped to `PROJECT_PACKAGE_INVALID` (B-9) |
| `backend/src/akasha/application/packages.py` | Created (254 lines) | Export/import use cases, requirement checks, audit events |
| `backend/migrations/versions/0007_project_files.py` | Created | `draft_files` / `files` JSONB columns |
| `backend/src/akasha/domain/project.py` | Modified | Project files; `PROJECT_SCHEMA_VERSION`; `plaintext_secret_locations`; `unstorable_locations`; description check in `create` |
| `backend/src/akasha/domain/primitives.py` | Modified | `is_storable`, `validate_text`, `storable_text`; stricter `validate_name` |
| `backend/src/akasha/domain/execution.py` | Modified | Shared `is_storable`; `ExecutionError` sanitizing; hardened `from_node_error` |
| `backend/src/akasha/domain/credentials.py` | Modified | Description validation |
| `backend/src/akasha/domain/event.py` | Modified | `PROJECT_EXPORTED`, `PROJECT_IMPORTED` |
| `backend/src/akasha/application/projects.py` | Modified | `load_project` made public; `update_draft` keeps files |
| `backend/src/akasha/application/credentials.py` | Modified | Re-validate the description on update |
| `backend/src/akasha/infrastructure/db_models.py` | Modified | New columns |
| `backend/src/akasha/infrastructure/graph_serialization.py` | Modified | File codec |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | Files read/written; `_persistable` stubs NUL and surrogates |
| `backend/src/akasha/api/projects.py` | Modified | Export/import endpoints, streaming body cap, media-type check |
| `backend/src/akasha/api/project_schemas.py` | Modified | `ProjectFileOut`, `files` fields, `ImportProjectResponse` |
| `backend/src/akasha/api/app.py` | Modified | 415 error code |
| `backend/src/akasha/bootstrap.py` | Modified | `Container.packages` wiring |
| `backend/tests/application/test_project_package.py` | Created | Format, round-trip, property, fuzz, malicious-archive and engine-compatibility tests |
| `backend/tests/domain/test_project_files.py` | Created | File rules, secret gate, version snapshot |
| `backend/tests/domain/test_storable_values.py` | Created | Storable rule at every domain gate |
| `backend/tests/integration/test_project_packages.py` | Created | PostgreSQL round trip across installations, secrets, dependencies, atomicity |
| `backend/tests/integration/test_project_packages_api.py` | Created | HTTP layer of export/import |
| `backend/tests/integration/test_projects_api.py` | Modified | Unstorable values on create/PUT → 422 |
| `backend/tests/integration/test_credentials.py` | Modified | NUL in description → 422 |
| `backend/tests/integration/test_runtime.py` | Modified | NUL/surrogate outputs and malformed node errors no longer crash a run |
| `backend/tests/integration/test_persistence.py` | Modified | Migration head `0007`; 0006 → 0007 upgrade test |
| `backend/tests/integration/conftest.py` | Modified | `truncate_all` extracted from `clean_db` |
| `backend/tests/support.py` | Modified | `project_semantics` comparison helper; `docker_available` is cached, has a 30 s timeout and fails under `AKASHA_TESTS_REQUIRE_DOCKER=1` (F-3) |
| `backend/tests/test_docker_probe.py` | Created | Tests for the Docker gate: skip, required-fail, one decision per session |
| `backend/README.md` | Modified | Documents `AKASHA_TESTS_REQUIRE_DOCKER` |
| `docs/project-format.md` | Created | Package format guide |
| `docs/architecture.md` | Modified | §56/§57 implementation notes, storable-values rule, event list |
| `docs/reports/backend/phase-18-project-import-export.md` | Created | This report |

No new directories apart from the new files. No generated files. No
configuration or dependency files changed (`pyproject.toml` and `uv.lock`
are untouched).

---

## 5. Architecture Changes

### 5.1 Project files are a persisted part of the Project

- **Decision:** add `files` (path → bytes) to `ProjectDraft` and
  `ProjectVersion`, and persist them.
- **Context:** the package must carry resources, documentation and assets,
  but the domain had nowhere to keep them. An import would have had to
  drop them, or reject every package that contained any.
- **Chosen approach:**
  - inline storage as `{path: base64}` JSONB, snapshotted on publish;
  - strict path and size rules;
  - only import sets files, and the draft PUT keeps them.
- **Alternatives considered:** a format-only solution (prompts only in node
  config, documentation = description, other files rejected on import).
  The user was asked and chose persistence.
- **Reason:** a lossless round trip; "never silently reinterpret data".
- **Consequences:**
  - nothing in the runtime reads files yet;
  - the frontend will need an editing API;
  - larger files would need artifact storage (marked with a `ponytail:`
    note in `domain/project.py`).

### 5.2 Package format independent of the API and database schemas

- **Decision:** the package has its own Pydantic schemas
  (`ProjectDocument` and others) rather than reusing `api/project_schemas`
  or `graph_serialization`.
- **Context:** the application layer must not import the API layer, and
  the package is a long-lived, versioned contract (architecture §80/§81).
- **Chosen approach:** strict models (`extra="forbid"`, `strict=True`),
  with the format version and project schema version in the manifest.
- **Alternatives considered:** reusing `DraftBody` from the API. This was
  rejected because of the layering, and because it would couple the
  package format to API evolution.
- **Consequences:** a change to the project structure needs a schema
  version bump and a migration step in `_read_manifest`.

### 5.3 Deviations from the architecture §57 example manifest

- **Decision:**
  - there is no `nodes/` directory; node configuration lives in the
    `project.json` graph;
  - `project_version` became `source_project_version`;
  - the free-form `dependencies` became `requires`, which lists the node
    types used with their versions and the credential names;
  - `engine_compatibility` is kept and enforced.
- **Context:** the §57 example says "the exact schema may evolve".
- **Chosen approach:**
  - the exporter writes `engine_compatibility: ">=MAJOR.MINOR.0"` of its
    own engine;
  - node types are checked with SemVer caret semantics (same major, at
    least the exported version);
  - plugin needs are expressed through declared project dependencies.
- **Alternatives considered:** at first, no `engine_compatibility` at all
  (the format version alone). This was reversed after re-reading backend
  AGENTS §46, which requires an engine-compatibility check.
- **Consequences:** documented in `docs/architecture.md` §56/§57 and
  `docs/project-format.md`.

### 5.4 Export refuses secrets instead of stripping them

- **Decision:** `export_project` raises `PlaintextSecretError` (locations
  only) when content holds secret material.
- **Reason:** a silently altered export would import as a different
  project. Only data stored before Phase 13 can hit this.

### 5.5 Import creates a new, unpublished project

- **Decision:**
  - import always creates a new project id with status `DRAFT`;
  - publishing stays explicit;
  - node and trigger ids from the package are kept (they are
    project-scoped).
- **Consequence:** the same package can be imported repeatedly as
  independent projects (tested).

### 5.6 One storable-value rule for every write gate

- **Decision:** `domain.primitives.is_storable` is the single definition of
  "PostgreSQL can hold this".
  - User data is rejected (422).
  - Node data is recorded: outputs become a stub, and messages have the
    characters replaced.
- **Context:** pre-existing 500s and runtime crashes (§10). A private
  version already existed for execution input only (`_is_json` in
  `domain/execution.py`).
- **Consequence:** new write paths must call this rule. It is documented in
  architecture §53 (execution persistence notes).

---

## 6. Domain / Data Model Changes

- **Changed entities and value objects:**
  - `ProjectDraft.files` (new field);
  - `ProjectVersion.files` (new field, default `{}`);
  - `ExecutionError` now normalizes its message and details.
- **New domain functions:**
  - `is_project_file_path`, `validate_project_files`,
    `plaintext_secret_locations` and `unstorable_locations` in
    `domain/project.py`;
  - `is_storable`, `validate_text` and `storable_text` in
    `domain/primitives.py`.
- **Changed behavior:**
  - `validate_name` rejects NUL and unpaired surrogates. It is used for
    project, node metadata and dependency names; this is stricter but
    compatible, because such values could not be stored anyway;
  - `Project.create` validates the description;
  - `Project.replace_draft` validates file rules and storable content;
  - `Credential` validates its description.
- **Enums:** `EventType.PROJECT_EXPORTED` (`project.exported`) and
  `EventType.PROJECT_IMPORTED` (`project.imported`). The event schema
  version is unchanged, because adding a type needs no bump
  (`domain/event.py`).
- **Constants:** `PROJECT_SCHEMA_VERSION = 1`, `PROJECT_FILE_ROOTS`,
  `MAX_PROJECT_FILES = 100`, `MAX_PROJECT_FILE_BYTES = 1 MiB` and
  `MAX_PROJECT_FILES_TOTAL_BYTES = 4 MiB`.
- **Database:** `projects.draft_files` and `project_versions.files`, both
  JSONB, NOT NULL, server default `'{}'`. No indexes or constraints were
  added.
- **Compatibility:**
  - rows written before 0007 load with `files == {}` (tested:
    `test_0007_upgrade_gives_existing_projects_and_versions_no_files`);
  - the downgrade drops both columns and is covered by
    `test_migrations_upgrade_downgrade_upgrade_on_empty_database`.

---

## 7. API / Contract Changes

### `GET /api/v1/projects/{project_id}/export`

- **Purpose:** download a project package.
- **Input:** optional query `version` (int ≥ 1). Without it, the draft is
  exported.
- **Output:** `200 application/zip`, with header
  `Content-Disposition: attachment; filename="<project_id>-<draft|vN>.akasha.zip"`.
  The project id, not the user-provided name, is used in the header.
- **Errors:**
  - 404 `PROJECT_NOT_FOUND` or `PROJECT_VERSION_NOT_FOUND`;
  - 422 `VALIDATION_ERROR` (for `version=0`);
  - 422 `PLAINTEXT_SECRET` (legacy secret content);
  - 422 `PROJECT_PACKAGE_INCOMPATIBLE` (node type not installed).

### `POST /api/v1/projects/import`

- **Purpose:** create a new project from a package.
- **Input:** a raw `application/zip` body of at most 16 MiB, capped while
  streaming. The OpenAPI entry documents it as `string/binary`.
- **Output:** `201` with `ImportProjectResponse`:
  `{project: ProjectDetail, validation: ValidationResponse, missing_credentials: [str]}`.
- **Errors:**
  - 415 `UNSUPPORTED_MEDIA_TYPE`;
  - 413 `PROJECT_PACKAGE_TOO_LARGE`;
  - 422 `PROJECT_PACKAGE_INVALID`;
  - 422 `PROJECT_PACKAGE_INCOMPATIBLE`, whose `details.issues` is a list of
    `{code, message}`;
  - 422 `PLAINTEXT_SECRET`;
  - 422 `DOMAIN_VALIDATION_ERROR`.

### Changed response schemas (additive)
- `ProjectDetail.files` and `ProjectVersionDetail.files` are
  `list[{path, size}]`. The frontend does not consume the project API yet
  (checked in `frontend/src`).

### Changed error behavior (pre-existing endpoints)
- `POST /api/v1/projects`, `PUT /api/v1/projects/{id}`,
  `POST /api/v1/credentials` and `PATCH /api/v1/credentials/{name}` return
  `422 DOMAIN_VALIDATION_ERROR` instead of `500` for NaN, Infinity,
  `1e999`, NUL or unpaired surrogates. For the project PUT,
  `details.paths` lists the locations.

### Events
- `project.exported` carries `project_id` and `project_version` (null for
  a draft).
- `project.imported` carries `project_id`.
- Both have empty payloads.

### Package contract
- Format version 1 and project schema version 1, as specified in
  `docs/project-format.md`.

No WebSocket, Node, Plugin or SDK contract changes.

---

## 8. Implementation Details

- **Reading (`read_package`), in order:**
  1. Size check (16 MiB). Then `zipfile.ZipFile` over `BytesIO`; nothing is
     ever written to disk.
  2. `_read_entries`:
     - checks entry count (≤ 512) and file count (≤ 102) before reading;
     - checks the declared unpacked total;
     - allows only `manifest.json`, `project.json` or a valid project file
       path;
     - rejects duplicates, non-regular files (symlinks), the encryption
       flag (bit 0) and compression other than stored/deflate;
     - applies a per-entry declared-size limit, then reads at most
       `limit + 1` bytes. A complete read also verifies the CRC.
     - ZIP features `zipfile` cannot read (a "version needed" above 6.3,
       flag bit 5 "patched data", flag bit 6 "strong encryption") raise
       `NotImplementedError`, which maps to `PROJECT_PACKAGE_INVALID` like
       the other archive errors.
  3. `_read_manifest`:
     - `format` and `format_version` are checked on the raw dict before
       schema validation, so a newer format is reported as unsupported;
     - then the strict schema, the project schema version and
       `engine_compatibility` via `version_satisfies`.
  4. `_verify_inventory`: the listed set equals the present set, with sizes
     and SHA-256.
  5. `project.json` → `ProjectDocument` → domain objects (`_to_draft`).
     Domain constructor errors become `PROJECT_PACKAGE_INVALID`.
  6. `_verify_requirements`: `requires.nodes` must equal the node types in
     the graph, and `requires.credentials` the referenced credential names.
- **JSON documents** must be UTF-8 objects accepted by `is_storable`.
  Schema errors report only `loc` and `msg`, never input values.
- **Export (`export_project`):**
  - one unit of work; loads the project or version;
  - `_requirements` runs the secret check, resolves node versions from the
    registry and collects credential names;
  - `write_package` writes with fixed entry order, fixed ZIP timestamps
    (1980-01-01) and sorted JSON keys, so output is byte-deterministic;
  - appends `project.exported` in the same transaction, then publishes it
    after commit.
- **Import (`import_project`):**
  - `read_package`, then the requirement issues and `validate_dependencies`
    (node and plugin, against the live `plugins` view). Any issue raises
    `ProjectPackageIncompatibleError`.
  - `Project.create` + `replace_draft` applies the same write gate as any
    draft: files, secrets and storable content.
  - One unit of work: the missing-credential lookup (names only), the
    project insert and the `project.imported` event.
  - Returns the draft's validation result. Graph issues such as a
    disconnected node do not reject the import.
- **HTTP body cap:** `_package_body` streams `request.stream()` and raises
  413 once over 16 MiB. The media type must be exactly `application/zip`.
- **Dependency injection:** `ProjectPackageService(uow, node_definitions,
  events, plugins, engine_version)`. Production uses the same
  `nodes.definitions` and `plugins.versions` views as `ProjectService`.
- **Concurrency:** import is a single insert of a new id, so there is no
  CAS conflict. Export is read-only apart from the appended event.
- **Runtime hardening:**
  - `ExecutionError.__post_init__` applies `storable_text`;
  - `from_node_error` normalizes the kind, code and blank message;
  - `_persistable` returns a copy of `_UNPERSISTED` for unstorable outputs.

---

## 9. Findings

### F-1 — The domain had no place for resources, documentation or assets
- **Impact:** MEDIUM.
- **Resolution:** FIXED, by the user's decision (§5.1).
- **Notes:** prompts already lived in `core.llm` node config.

### F-2 — The PostgreSQL storability gap was systemic
- **Impact:** HIGH.
- **Resolution:** FIXED.
- **Notes:**
  - JSON parsing accepts NaN, Infinity, `1e999`, NUL and unpaired
    surrogates; JSONB and text columns reject them;
  - only execution input was guarded before this phase (`_is_json`);
  - see bugs B-5 to B-8.

### F-3 — The Testcontainers readiness probe can skip the integration suite silently
- **Impact:** MEDIUM (test infrastructure).
- **Resolution:** FIXED.
- **Notes:**
  - `tests/support.docker_available` pings the daemon with a 2 s timeout;
  - in this session two full runs skipped 222 and 266 Docker-dependent
    tests while `docker info` succeeded moments later;
  - the likely cause is a slow daemon wake-up (Docker Desktop);
  - the runs still report "passed", so an operator can mistake a skipped
    integration suite for a green one;
  - root cause:
    - each of the four callers (`tests/conftest.py`,
      `tests/integration/conftest.py` and two module fixtures) pinged
      separately with a 2 s timeout;
    - a daemon waking up mid-run made some fixtures skip while others
      ran, which explains the 266-skip run.
  - fix:
    - `docker_available` is `functools.cache`d (one decision per session)
      with a 30 s timeout;
    - an absent daemon still fails fast with "connection refused";
    - `AKASHA_TESTS_REQUIRE_DOCKER=1` turns the skip into a failure
      (documented in `backend/README.md`).
  - verified:
    - `tests/test_docker_probe.py` (3 tests);
    - with the daemon forced unreachable (`DOCKER_HOST` pointed at a closed
      port), the run errors under the flag and skips without it, both in
      under a second;
    - two full runs with the flag set passed (1490 passed).

### F-4 — `zipfile` raises `NotImplementedError` for some header values
- **Impact:** MEDIUM.
- **Resolution:** FIXED (bug B-9).
- **Notes:**
  - the fuzz test found it during report verification;
  - `zipfile` reads the refused flags from the central directory; the same
    flags in a local header are ignored, and such archives are still
    checked by the SHA-256 inventory.

### F-5 — Hypothesis's example database makes a found failure replay locally
- **Impact:** LOW.
- **Resolution:** informational.
- **Notes:**
  - `backend/.hypothesis/` is gitignored;
  - after B-9 was found, the fuzz test failed on every local run, while a
    fresh checkout (CI) would fail only when random search hit the case;
  - after the fix, the saved example replays and passes.

### F-6 — The architecture example manifest did not fit the domain one-to-one
- **Impact:** LOW.
- **Resolution:** FIXED (documented deviation, §5.3).

---

## 10. Bugs / Problems Found

### Introduced during this phase and fixed before completion (found by the security review loop and Hypothesis)

| ID | Problem | Root cause | Fix | Regression test |
|---|---|---|---|---|
| B-1 | A ZIP entry name flagged UTF-8 with invalid bytes gave a 500 | `UnicodeDecodeError` (a `ValueError`) was not caught around `ZipFile()` | `ValueError` added to the caught tuple | `test_undecodable_utf8_entry_name_is_invalid_not_a_crash` |
| B-2 | NaN, Infinity, `1e999` and `\u0000` in a package gave a 500 at persistence | The reader accepted every JSON value | Reject via `is_storable` | `test_values_postgresql_cannot_store_are_rejected`, `test_nul_in_a_key_is_rejected` |
| B-3 | Exporting a project with a long label or name would crash | Package schema caps (200 / 10 000 chars) were stricter than the domain | Caps removed; the document size limit bounds input | `test_whatever_a_project_may_hold_exports` |
| B-4 | The file-count limit was checked only after inflating the entries | Check order | Count checked before reading | `test_too_many_files_rejected_before_reading` |
| B-4b | Lone surrogate (`"\ud800"`) in config: export succeeded, re-import then failed | Surrogates cannot be UTF-8 encoded or stored (found by Hypothesis) | `is_storable` encodes to UTF-8 | property test strategy plus `'"\\ud800"'` cases |

### Pre-existing bugs discovered during this phase (fixed on the user's request)

| ID | Problem | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|---|
| B-5 | `POST`/`PUT /api/v1/projects` with NaN, Infinity, `1e999`, NUL or a surrogate returned 500 (asyncpg `InvalidTextRepresentationError`) | No storability check on the draft write path | `unstorable_locations` in `replace_draft`; `validate_name` / `validate_text` | `test_put_rejects_unstorable_config_values` (7 cases), `test_put_rejects_nul_anywhere_in_the_project` (6), `test_create_rejects_nul_in_name_and_description` | Projects API |
| B-6 | Credential description with NUL (create/PATCH) returned 500 | Same | `Credential.__post_init__`; `update` re-validates via `replace` | `test_nul_in_a_description_is_a_validation_error_not_a_500` | Credentials API |
| B-7 | A node output containing NUL or an unpaired surrogate aborted `ExecutionRuntime.run` with a DB error; the execution was never finished | `_persistable` only guarded NaN and non-JSON types | Stub via `is_storable` | `test_nul_in_node_output_or_error_never_crashes_the_run`, `test_lone_surrogate_in_node_output_is_stored_as_a_stub` | Runtime / persistence |
| B-8 | A `NodeError` with NUL in the message, a blank message, a malformed code or an unknown kind aborted the run | `ExecutionError` validation raised inside the executor's `except NodeError` handler | `storable_text` in `ExecutionError`; normalization in `from_node_error` | same NUL test plus `test_malformed_node_errors_are_recorded_not_fatal` (3 cases) | Runtime (untrusted plugin nodes) |

### Introduced during this phase, found during report verification — FIXED

**B-9 — crafted ZIP headers gave 500 `INTERNAL_ERROR` on import.**
- **Reproduction:** in the central directory record of any entry, set
  "version needed to extract" to 64 (6.4), set flag bit 5 (patched data),
  or set flag bit 6 (strong encryption).
- **Root cause:** `zipfile` raises `NotImplementedError` for these values.
  `read_package` did not catch it.
- **Before the fix (verified):**
  - reproduced deterministically;
  - through the HTTP app the response was
    `500 {"code": "INTERNAL_ERROR", "message": "An internal error occurred."}`;
  - the traceback went only to the server log;
  - nothing was persisted, because the failure happens before any unit of
    work.
- **Fix:** `NotImplementedError` was added to the exceptions `read_package`
  maps to `PROJECT_PACKAGE_INVALID`. The module docstring's claim "never a
  crash" now points at the fuzz test that guards it.
- **Regression tests:**
  - `test_zip_features_zipfile_cannot_read_are_invalid_not_a_crash` (3
    deterministic cases; all 3 failed before the fix);
  - `test_corrupted_packages_fail_with_package_errors_only` (fuzz).
- **Extra verification:** a one-off 20,000-mutation fuzz run (seed 18)
  outside the suite:
  - no unexpected exception;
  - 19,298 `PROJECT_PACKAGE_INVALID` and 330 `PROJECT_PACKAGE_TOO_LARGE`;
  - the remaining mutants were accepted; a second pass confirmed that every
    accepted mutant is semantically identical to the original, because the
    mutations hit bytes outside the checksummed content, such as
    timestamps.

### Infrastructure / environmental

- F-3: Docker readiness-probe timeouts caused silent and inconsistent
  skips. No product impact. Fixed (§9).

---

## 11. Fixes Applied

Each entry gives the symptom, the root cause, the implementation, and how it
was verified.

1. **Unstorable project content** (B-5)
   - **Symptom:** 500 on create/PUT.
   - **Root cause:** no check on the draft write path.
   - **Implementation:** `Project.replace_draft` → `unstorable_locations`
     (details list the locations); `validate_name` / `validate_text`.
   - **Verified:** a probe against PostgreSQL before the fix (500 via
     `DBAPIError`) and after it (422), plus the API tests above.
2. **Credential description** (B-6)
   - **Implementation:** domain validation, and `replace` on update.
   - **Verified:** probe, then API test.
3. **Runtime NUL/surrogate outputs** (B-7)
   - **Implementation:** `_persistable` uses `is_storable`.
   - **Verified:** a probe crashed with `DBAPIError` before the fix; the
     run completes after it (integration tests).
4. **Malformed node errors** (B-8)
   - **Implementation:** `ExecutionError` sanitizing and
     `from_node_error` fallbacks.
   - **Verified:** the test was written first and failed with
     `DomainValidationError` escaping `run()`; it passes after the fix.
5. **Package reader hardening** (B-1 to B-4b)
   - **Implementation:** see §10.
   - **Verified:** unit tests; B-4b was found by Hypothesis.
6. **B-9: `NotImplementedError` from `zipfile`**
   - **Symptom:** 500 on import; the fuzz test failed.
   - **Root cause:** an uncaught exception type.
   - **Implementation:** see §10.
   - **Verified:** 3 deterministic tests failed before the fix and pass
     after it; the package test file passed 5 consecutive runs, including
     the replayed Hypothesis example; the 20k-mutation fuzz run was clean.
7. **F-3: Docker probe**
   - **Implementation and verification:** see §9.
8. **Migration head test**
   - **Symptom:** `test_migrations_upgrade_downgrade_upgrade_on_empty_database`
     failed after adding 0007, because it pins the head revision.
   - **Implementation:** `_HEAD` updated to `0007_project_files`. This is
     the intended maintenance of that test.
9. **Test expectations corrected** (the tests were wrong, not the code):
   - a lone WIP sink node also yields `DISCONNECTED_NODE`;
   - `put_draft` renames the project to "demo";
   - a downstream node's output containing NUL is stubbed too;
   - B-9's first draft test also patched *local* headers, assuming
     `zipfile` refuses those flags there; it does not, so they were
     replaced with the central-directory flag bit 5.

---

## 12. Testing & Verification

The counts are from individual runs of each file during report
verification.

### Unit tests
- **`tests/domain/test_project_files.py`:** 25 passed. Valid and invalid
  paths (traversal, hidden, absolute, backslash, depth, length, unknown
  root), size and count limits, byte type, secret-bearing text files
  rejected by location, binary files not inspected, version snapshot
  isolation.
- **`tests/domain/test_storable_values.py`:** 28 passed. `is_storable`
  cases; NUL and surrogates in names, descriptions and credentials;
  unstorable locations for config, label, port, trigger, dependency and
  description; `ExecutionError` sanitizing.
- **`tests/application/test_project_package.py`:** 96 passed. It covers:
  - round-trip semantic equality and byte determinism;
  - a Hypothesis property: round trip of arbitrary storable JSON
    configuration (60 examples);
  - fuzzing: 200 random byte mutations of a valid package, plus 100
    random byte strings; these must raise only package errors;
  - ZIP features `zipfile` cannot read: 3 deterministic cases (B-9);
  - archive attacks: not a ZIP, oversized, traversal, absolute and
    unknown names, duplicate entry, symlink, encrypted flag, bzip2,
    directory entries tolerated, too many entries or files, zip bombs by
    declared size and by a lying central-directory size, an oversized
    single file;
  - manifest: missing, not a JSON object, foreign format, malformed,
    unsupported or newer format version (including a changed shape),
    unsupported schema version, schema violations, no echo of input
    values in errors;
  - inventory: unlisted, missing, tampered (checksum), duplicate listing,
    missing `project.json`;
  - project document: violations and deep nesting;
  - requirements consistency;
  - engine compatibility: recorded release line, enforcement matrix,
    malformed constraint;
  - storable values and surrogates.

### Integration tests (PostgreSQL 17.2 via Testcontainers)
- **`tests/integration/test_project_packages.py`:** 15 passed.
  - `test_round_trip_across_installations`:
    1. The source has a credential, 4 project files and published v1;
       export v1.
    2. Assert the package is secret-free: the credential value and the
       32-byte encryption key, raw and base64, in the archive and in every
       unpacked entry.
    3. Check the `project.published` and `project.exported` events.
    4. `truncate_all` to simulate an empty installation with a new key,
       registry and credential value.
    5. Import. `missing_credentials == ("llm-key",)` and validation is OK.
    6. Semantic comparison, then `project.imported`.
    7. Publish (files snapshotted), then run through `ExecutionRuntime` →
       `core.llm` → LiteLLM → the local fake server. The run is
       `COMPLETED`, and the provider saw the **target's** credential.
    8. Re-export on the target: semantically equal again.
  - The rest cover:
    - WIP draft round trip, with issues reported and files kept through PUT;
    - legacy plaintext secret in config or file: export refused, no event;
    - plaintext import rejected, with nothing persisted;
    - no credential value in an export;
    - missing node, newer major and older version rejected, with nothing
      persisted;
    - a compatible newer minor accepted;
    - declared plugin dependency missing, incompatible or satisfied;
    - an uninstalled node type blocks export;
    - unknown project or version.
- **Additions to existing integration tests** (22 tests selected, all
  passed):
  - projects API unstorable-value tests;
  - credential NUL;
  - runtime crash regressions;
  - the 0007 upgrade test;
  - the migration round trip.

### API tests
- **`tests/integration/test_project_packages_api.py`:** 8 passed.
  - Export of the draft and a version: content type and
    Content-Disposition.
  - Export errors: 404/404/422.
  - Import creates an unpublished project that can be published, and a
    second import of the same package gives an independent project.
  - Errors: 415 (missing or JSON content type), 422 malformed, 413 while
    streaming, 422 incompatible with `details.issues`.
  - The OpenAPI document shows the zip request and response.

### Infrastructure tests
- Migrations run on an empty database, upgrade → downgrade → upgrade
  reaches head `0007`, and 0006 data upgrades to 0007 (PASS).

### Security tests
- Secret exclusion, secret refusal and plaintext import rejection, ZIP
  attacks (traversal, symlink, bombs, encryption, duplicates), schema
  error non-echo, and unstorable-value handling, all listed above.
- One security review loop was run on the new code (findings B-1 to B-4).

### Test-infrastructure tests
- **`tests/test_docker_probe.py`:** 3 passed. An unreachable daemon skips
  by default and fails under `AKASHA_TESTS_REQUIRE_DOCKER=1`, and the
  decision is made once per session.

### Not performed
- **E2E:** not performed as a single journey across all phases. The
  cross-installation integration test covers export → import → publish →
  execute.
- **Performance tests:** not performed.
- **Accessibility:** not applicable (backend only).

### Full suite
- **Final** (after all fixes), two consecutive runs with
  `AKASHA_TESTS_REQUIRE_DOCKER=1`: **1490 passed, 5 skipped** each (the
  skips are the external OpenRouter smoke tests, which need
  `AKASHA_SMOKE_OPENROUTER_API_KEY`). The flag proves that every
  PostgreSQL and Mailpit suite ran.
- **History:**
  - earlier runs with Docker healthy: 1435, 1484 and 1484 passed, each
    with 5 skipped;
  - runs affected by F-3: 222 and 266 skipped; the 266-skip run also had
    1 failure whose name was not captured, most likely B-9's fuzz test;
  - forced-unavailable Docker: 1202 passed, 287 skipped.
- The one remaining warning is a third-party `StarletteDeprecationWarning`
  from importing `fastapi.testclient`. It predates this phase.

---

## 13. Quality Gates

Final runs in `backend/`, after all code changes:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 159 files already formatted |
| Linter (`uv run ruff check .`) | PASS | "All checks passed!" |
| Type Checker (`uv run mypy .`) | PASS | strict; "no issues found in 155 source files" |
| Unit Tests | PASS | Package tests: 96 passed in 5 consecutive runs, including the replayed B-9 example. It was FAIL before the B-9 fix. |
| Integration Tests (PostgreSQL) | PASS | Two full runs with `AKASHA_TESTS_REQUIRE_DOCKER=1`: 1490 passed, 5 skipped (external smoke only) |
| API Tests | PASS | 8 package API tests, plus the additions to the projects and credentials API tests |
| E2E Tests | N/A | No cross-phase E2E suite exists yet |
| Build | N/A | No separate build step for the backend package; nothing changed in `pyproject.toml` |
| Security Checks | PASS | All security tests pass, including the fuzz tests. No dependency audit tool is configured. |
| Accessibility | N/A | Backend |
| Migration tests | PASS | Head `0007`, reversible, upgrade from 0006 |

History:
- the migration head test failed once after adding 0007 (the pinned head)
  and was fixed;
- the unit gate was red because of B-9 until it was fixed;
- the Docker-skip anomaly (F-3) was fixed.

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion (roadmap §22 + session request) | Status | Evidence |
|---|---|---|
| A Project can be exported and imported into another compatible installation | PASS | `test_round_trip_across_installations`: empty database, different key, registry and credential; semantic equality; publish; execute COMPLETED |
| Implement ProjectPackage / ProjectManifest | PASS | `application/project_package.py` (`ProjectPackage`, `ProjectManifest`) |
| Implement ProjectExporter / ProjectImporter | PASS | `ProjectPackageService.export_project` / `import_project` |
| Implement SchemaValidator | PASS | `read_package`; strict schemas; `test_manifest_schema_violations`, `test_project_document_violations` |
| Implement DependencyValidator | PASS | `_requirement_issues` + `validate_dependencies`; dependency and plugin integration tests |
| Package contents: manifest, project, graph, node configuration, prompts, resources, documentation, assets | PASS | `test_export_is_deterministic_and_documented` (entry list, prompts in config); files under `docs/ prompts/ resources/ assets/`. There is no separate `nodes/` directory (§5.3). |
| Never include secrets | PASS | `assert_secret_free` over the archive and every entry (credential value and key, raw and base64); export refuses legacy secrets; no credential store or key access in the exporter |
| Test: export | PASS | integration and API export tests |
| Test: import | PASS | integration and API import tests |
| Test: round trip | PASS | unit, Hypothesis property and PostgreSQL cross-installation round trips |
| Test: invalid package | PASS | Over 60 deterministic malformed/malicious cases return structured 413/422, including the 3 B-9 header cases. Fuzz tests allow only package errors, and a 20k-mutation run found no other exception. (It was PARTIAL in the first report version, before the B-9 fix.) |
| Test: missing dependency | PASS | `test_import_rejects_unavailable_or_incompatible_node_types[missing-node]`, plugin `{}` case, API incompatible test |
| Test: incompatible version | PASS | newer-major / older-version node cases, plugin 2.0.0 case, format/schema/engine-compatibility unit tests |
| Test: secret exclusion | PASS | `test_credential_values_never_enter_an_export`, round-trip secret assertions, `test_export_refuses_legacy_plaintext_secrets` |
| Imports validate schema, versions, dependencies, Node availability, Plugin requirements, malformed data | PASS | See the rows above; the B-9 fix completes "malformed data" |
| Round trip Export → Import → Validate → Compare → Publish → Execute | PASS | `test_round_trip_across_installations` |
| Malicious/malformed package tests | PASS | `test_project_package.py` (96 passed) |

---

## 15. Security Considerations

- **Secrets:**
  - packages contain only credential references and names;
  - export re-applies `plaintext_secret_locations` and refuses on a match;
  - the exporter never touches the credential store, the cipher, or
    session state (sessions do not exist yet; Phase 20);
  - import applies the draft write gate (`PLAINTEXT_SECRET`);
  - binary project files cannot be inspected; this is documented.
- **Untrusted input:**
  - the ZIP is parsed in memory and never extracted, so path traversal
    cannot reach the filesystem;
  - names are allow-listed;
  - symlinks, encryption flag bit 0, exotic compression and duplicates are
    rejected;
  - bounds on size and count, including declared vs actual sizes;
  - strict schemas; error details carry locations only;
  - ZIP features `zipfile` cannot read are rejected as invalid (B-9,
    fixed).
- **Resource exhaustion:**
  - 16 MiB upload cap enforced while streaming;
  - 8.25 MiB maximum unpacked (256 KiB manifest + 4 MiB `project.json` +
    4 MiB files, checked on the declared sizes);
  - 512 entries at most;
  - deep JSON nesting is caught (`RecursionError`).
- **Error disclosure:**
  - `_error` in `api/app.py` redacts all error bodies;
  - the 500 path returns a generic message;
  - Content-Disposition uses the validated project id, never user text.
- **Authentication and authorization:**
  - none yet (Phase 20); both endpoints sit on the projects router, so the
    planned router-level dependency covers them;
  - import runs no code and creates only a draft, the same power as
    `POST /projects`.
- **Plugin trust:** packages never contain plugin code.
- **Runtime robustness:** untrusted (plugin) nodes can no longer abort a
  run with a malformed `NodeError` or unstorable output (B-7, B-8).
- **Logging:**
  - export and import log ids, versions and the package size only, never
    content;
  - `project.exported` / `project.imported` provide an audit trail.

---

## 16. Performance Considerations

No measurements were performed.

- Export and import build and parse the whole archive in memory. This is
  bounded by the 16 MiB upload cap and the file caps.
- Project files are stored inline in JSONB, base64 encoded (about 1.33×
  size). They are loaded with every `projects.get` / `versions.get`, so up
  to about 5.3 MiB is added per project load at the cap. This is a known
  ceiling (§20).
- `is_storable` serializes each checked value once with `json.dumps`, so
  every draft write serializes the draft content one extra time. This was
  not measured.
- Export resolves node versions from the in-memory registry, with no extra
  queries. The missing-credential check on import is one query per
  referenced credential name (N+1 over credential names, which is small in
  practice).

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/project-format.md` (new) | Package format guide (roadmap §2.2 lists `project-format.md`) | Layout, the manifest and `project.json` schemas, file rules, export and import behavior, the ordered import checks, the compatibility policy. Accurate after the B-9 fix (encrypted entries, including strong encryption, are invalid). |
| `backend/README.md` | Test infrastructure (F-3) | `AKASHA_TESTS_REQUIRE_DOCKER=1` fails the run instead of skipping Docker-dependent tests |
| `docs/architecture.md` §56/§57 | Implementation notes | Format, deviations from the example manifest, project files, services, endpoints |
| `docs/architecture.md` §53 (execution persistence) | Storable-values rule | NaN/NUL/surrogate handling per write path; node-error fallbacks |
| `docs/architecture.md` event notes | New event types | `project.exported` / `project.imported` |
| `docs/reports/backend/phase-18-project-import-export.md` | Phase report | This document |

**Should be updated in a future phase:**
- `docs/sdk.md` could note that `NodeError` fields are normalized
  (`from_node_error` fallbacks);
- the frontend roadmap and API docs when the frontend consumes
  `files` / import / export.

---

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added, removed or changed (`pyproject.toml` and
  `uv.lock` are unmodified). Only the standard library (`zipfile`,
  `hashlib`, `stat`, `zlib`) and the existing `pydantic`.
- **Database:** migration `0007_project_files` (two JSONB columns).
- **Configuration and environment variables:** no product configuration
  changed; the limits are code constants. There is one new *test-only*
  variable, `AKASHA_TESTS_REQUIRE_DOCKER` (opt-in, intended for CI).
- **Docker / Redis / external services:** unchanged.
- **Test infrastructure:**
  - `truncate_all(database)` extracted in `tests/integration/conftest.py`;
    `clean_db` uses it;
  - `project_semantics(name, draft)` in `tests/support.py`;
  - `read()` test helper in `tests/application/test_project_package.py`,
    imported by the integration tests;
  - `docker_available` hardened (F-3), with `tests/test_docker_probe.py`.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects Phase 18 completion |
|---|---|---|---|
| CLI `project import/export` | Roadmap scope | Phase 21 | No |
| Authentication on the export/import endpoints | Roadmap scope | Phase 20 | No |
| Project file editing API | Not in scope; the frontend needs it | Frontend roadmap / later backend phase | No |
| Project schema migration framework | Only schema 1 exists (`ponytail:` note in `_read_manifest`) | When schema 2 is introduced | No |
| Signed or checksummed-by-publisher packages | Not requested | Not planned | No |
| Setting `AKASHA_TESTS_REQUIRE_DOCKER=1` in CI | No CI configuration exists in the repository yet | When CI is set up | No |

---

## 20. Known Limitations

- Project files are set only by import; they cannot be edited through the
  API, and nothing in the runtime reads them.
- Files are capped at 100, 1 MiB each and 4 MiB in total, stored inline in
  JSONB. Larger assets need artifact storage.
- Binary files are not scanned for secrets.
- The package does not name the plugin providing a node type. A missing
  plugin surfaces as a missing node type unless the project declares a
  `kind: "plugin"` dependency.
- Node compatibility uses SemVer caret semantics. For `0.x` node versions
  a minor bump is not treated as breaking.
- `engine_compatibility` written by the exporter blocks import into an
  older release line (for example, a 0.2 export into 0.1) even when the
  content would work. This is conservative by design.
- Missing credentials are reported, not enforced. A run fails later with a
  structured credential error.
- `is_storable` rejects the literal six-character text `\u0000` (a known
  false positive).
- Import and export hold the whole package in memory.

---

## 21. Open Issues

No known open issues resulting from this phase.

B-9 and F-3, open in the first version of this report, are fixed (§10,
§9). The follow-ups in §19 are deferred scope, not defects.

---

## 22. Architectural / Product Decisions

- **Project files are domain state, not package-only data:** chosen by the
  user for a lossless round trip. Files are immutable in versions like the
  graph.
- **The package format is its own contract:** it is independent of the
  API DTOs and the DB codec. Changing `project.json` semantics requires a
  `PROJECT_SCHEMA_VERSION` bump.
- **Import never publishes and never merges:** it always creates a new
  project, and node and trigger ids are kept.
- **Export refuses rather than strips secrets.**
- **Rejection boundary on import:**
  - availability, version and dependency problems reject the package;
  - graph or config problems are returned as validation issues, so
    work-in-progress drafts stay portable.
- **Requirements are derived, and the reader verifies them:**
  `requires.nodes` and `requires.credentials` must match the content, so
  the manifest cannot lie about what the importer checks.
- **Storability:**
  - `is_storable` is the single definition;
  - user input is rejected;
  - node-produced data is recorded (stubbed or sanitized), so that a
    plugin cannot crash the runtime.
- **The draft PUT keeps files:** the draft API does not carry them, so a
  PUT must not erase imported files.

---

## 23. Repository / Git State

- **Branch:** `main`. Last commit `86c2aeb` ("backend phase 17 - plugin
  system").
- **Working tree:** all Phase 18 work is **uncommitted**:
  - 22 modified tracked files (see §4);
  - 11 untracked files: the migration, 2 application modules, 6 test
    modules, `docs/project-format.md` and this report.
- `backend/.hypothesis/` (gitignored) holds Hypothesis's example database.
  The saved B-9 example now passes.
- **Unrelated changes:** none detected. All modified files belong to this
  phase or to the fixes requested within it.
- The first report pass changed no product code. The B-9 and F-3 fixes
  were made afterwards, on the user's explicit request.

---

## 24. Phase Completion Assessment

### Implemented
- The package format, manifest, reader and writer, exporter, importer,
  schema and dependency validation.
- Project files with migration 0007.
- The export and import endpoints and audit events.
- Storability hardening across the project, credential and runtime write
  paths.

### Verified
- Cross-installation round trip with publish and execution on PostgreSQL.
- Secret exclusion.
- Dependency, plugin, version and engine incompatibility.
- Over 60 malformed and malicious package cases.
- Migration upgrade and downgrade.
- Lint, format and type checks pass.
- The full suite passes with Docker mandatory: 1490 passed and 5 external
  smoke tests skipped, in two consecutive runs.

### Not Implemented
- Nothing from the roadmap scope is missing.

### Deferred
- CLI (Phase 21), auth (Phase 20), the file editing API, schema
  migrations, and enabling `AKASHA_TESTS_REQUIRE_DOCKER` in CI once CI
  exists.

### Open
- None.

### Blocking Issues
- None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase

- **Can assume:**
  - `ProjectPackageService` exists (`Container.packages`), with
    `export_project(project_id, version=None) -> bytes` and
    `import_project(bytes) -> ImportResult`;
  - `read_package` / `write_package` are pure and usable by the CLI
    (Phase 21) without HTTP;
  - projects and versions carry `files`;
  - `is_storable` / `validate_text` / `storable_text` are the storability
    rule.
- **Contracts:**
  - `GET /api/v1/projects/{id}/export[?version=N]` and
    `POST /api/v1/projects/import` (`application/zip`);
  - error codes `PROJECT_PACKAGE_INVALID` (422),
    `PROJECT_PACKAGE_TOO_LARGE` (413), `PROJECT_PACKAGE_INCOMPATIBLE`
    (422 with `details.issues`) and `UNSUPPORTED_MEDIA_TYPE` (415);
  - events `project.exported` / `project.imported`;
  - package format 1 and project schema 1 (`docs/project-format.md`).
- **Do not change casually:**
  - the package format and schema without a version bump;
  - the rule that export never includes secret values;
  - `requires` consistency verification;
  - `update_draft` keeping files;
  - the determinism of `write_package`.
- **Reports (Phase 19)** read persisted executions:
  - node outputs may be `{"$unpersisted": "..."}` stubs, now also for NUL
    and surrogates;
  - error messages may contain U+FFFD where a node emitted NUL or a
    surrogate;
  - report text must stay storable and secret-safe.
- **Prerequisites and first action:**
  - commit Phase 18;
  - when CI is introduced, run it with `AKASHA_TESTS_REQUIRE_DOCKER=1`.
- **Rule for new Docker-dependent tests:** new tests that need Docker must gate on
  `tests.support.docker_available()`, never on a probe of their own.
