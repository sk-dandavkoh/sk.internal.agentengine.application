# Phase 17 — Plugin System

## 1. Executive Summary

**What the phase had to achieve.** Roadmap §21 ("Turn custom Nodes into installable Plugins") asks for:
- `PluginManifest`, `PluginRegistry`, `PluginLoader`, `PluginInstaller`, `PluginValidator` and `PluginCompatibilityChecker`;
- the lifecycle steps discover, validate, install, register, load, disable and remove;
- plugins that declare id, version, engine compatibility, SDK compatibility, dependencies and components;
- a lifecycle test with a sample plugin.

The phase request added some requirements:
- metadata must include name, provided capabilities and documentation;
- plugin code is trusted in V1, but the boundary for future isolation must be clear;
- malformed metadata must not corrupt the registry;
- no marketplace.

**What was implemented.**
- **Manifest** (`akasha-plugin.json`): a strict Pydantic boundary in `akasha/plugins/manifest.py`. It is parsed into the domain `PluginMetadata` before any plugin code is imported.
- **Domain rules** (`akasha/domain/plugin.py`):
  - an extended `PluginMetadata` and a new `PluginNode`;
  - namespacing of plugin node ids;
  - a pure compatibility check, `compatibility_issues`.
- **Workflow dependencies:** `validate_dependencies` (`akasha/domain/workflow.py`) now also checks workflow `Dependency(kind="plugin")` when given the list of enabled plugins. `WorkflowService` passes that list at validate and publish.
- **`PluginRegistry`** (`akasha/plugins/registry.py`) runs the whole lifecycle:
  - startup discovery in dependency order;
  - all-or-nothing install;
  - loading (the single place plugin code runs);
  - enable, disable and remove with dependency protection;
  - enable/disable state that survives restarts.
- **Wiring:** a new setting `AKASHA_PLUGINS_DIR` and a new `Container.plugins` field. Bootstrap loads installed plugins into the same `NodeRegistry` the built-in nodes use.
- **Sample plugin:** `backend/examples/plugins/text_tools`, which provides the node `text_tools.slugify` and imports only `akasha.sdk`.
- **Tests:** 46 component tests (`tests/test_plugins.py`) and 1 PostgreSQL integration test of the full lifecycle through publish and the execution runtime (`tests/integration/test_plugin_runtime.py`).

**Resulting system state.**
- A node written against `akasha.sdk` can be packaged as a directory with a manifest. It can then be installed into the plugins directory, registered, discovered at startup, used in a published workflow, executed, disabled, re-enabled and removed, all without changing engine code.
- Plugin nodes reach `WorkflowService` and `ExecutionRuntime` only through the ordinary `NodeRegistry.definitions` and `NodeRegistry.catalog` views. Neither component has a plugin-specific code path.

**Acceptance.** All roadmap acceptance items have evidence (§14). The limitations that remain are documented:
- removing a plugin is not blocked by workflows that use its nodes (architecture §143);
- changes apply to one process only;
- no archive package format;
- plugin ids containing `-` cannot provide nodes (finding F5).

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §21, Phase 17:
- **Goal:** "Turn custom Nodes into installable Plugins."
- **Scope:** the six components listed in §1, supporting discover, validate, install, register, load, disable and remove.
- **Plugins must declare:** id, version, engine compatibility, SDK compatibility, dependencies and components.
- **Tests:** "Test the complete lifecycle with a sample plugin."
- **Acceptance:** "A custom Node can be created, packaged, installed, registered, discovered, used, disabled, removed without changing Engine core."

The phase request additionally required tests for:
- a valid plugin;
- an invalid manifest;
- an incompatible plugin;
- a duplicate plugin;
- loading and registration;
- the disable/remove lifecycle;
- execution of the sample plugin.

Also relevant from the architecture:
- §35–§37: plugin architecture, plugin lifecycle and the Node Registry;
- §65: plugin security. Trusted code in V1, but the engine must validate metadata, validate compatibility, track versions, control activation and record installation;
- §141–§144: discovery, installation, removal and workflow dependency resolution;
- §157: the reference plugin lifecycle.

**Explicitly not in scope:**
- a marketplace (roadmap and request);
- authentication and authorization (Phase 20);
- CLI commands (Phase 21);
- workers and multi-process coordination (Phase 22);
- workflow import/export (Phase 18);
- sandboxed execution (vision §26: "not part of v1 functionality").

---

## 3. Scope Implemented

### Domain (`akasha/domain/plugin.py`, `akasha/domain/workflow.py`)

**`PluginMetadata`** (frozen dataclass):
- Existing fields: `plugin_id`, `name`, `version`, `engine_compatibility`, `sdk_compatibility`, `description`.
- Changed: `dependencies` is now `tuple[Dependency, ...]`. It was `tuple[str, ...]`, which could not carry a version constraint.
- New fields: `nodes: tuple[PluginNode, ...]` and `documentation: str`.
- New invariants in `__post_init__`:
  - every constraint (engine, SDK, each dependency) is non-blank and parseable;
  - a plugin cannot depend on itself;
  - node ids are unique;
  - every node id starts with `"<plugin_id>."`.
- New property `plugin_dependencies: frozenset[str]`.

**`PluginNode(node_id, entry_point)`** (new):
- `entry_point` must match `module[.sub]:Attribute`, made of identifiers only.
- That rules out path separators, `..` and relative module paths.

**`compatibility_issues(metadata, *, engine_version, sdk_version, definitions, plugins)`** (new):
- Returns `ValidationIssue`s for an unsatisfied engine or SDK constraint.
- Delegates dependency checks to `validate_dependencies`.

**`validate_dependencies(dependencies, definitions, plugins=None)`** (changed):
- Plugin dependencies are checked when `plugins` (enabled id → version) is given.
- It reports the same codes as node dependencies: `MISSING_DEPENDENCY`, `INCOMPATIBLE_DEPENDENCY` and `INVALID_DEPENDENCY`.
- `WorkflowDraft.validate` and the private `_validate` gained the same optional parameter.
- `WorkflowVersion.validate`, the runtime path, is unchanged and passes no plugins (decision D3).

### Plugins package (`akasha/plugins/`)

**`manifest.py`** (new):
- `PluginManifest` is a Pydantic model with `extra="forbid"`, `strict=True` and `frozen=True`.
- Nested models: `_Dependency`, `_Node` and `_Components`.
- Field lengths and list sizes are bounded:
  - 1–100 nodes;
  - at most 50 dependencies;
  - most strings at most 128 characters.
- `read_manifest(package) -> PluginMetadata`:
  - rejects a missing or symlinked manifest, and a manifest over 64 KiB;
  - maps every JSON, Pydantic or domain error to `PluginManifestError` (`PLUGIN_MANIFEST_INVALID`, 422);
  - requires `__init__.py`;
  - requires `documentation` to resolve to a file inside the package.
- Constants: `MANIFEST_FILE = "akasha-plugin.json"`, `MAX_MANIFEST_BYTES = 65536`.

**`registry.py`** (new):
- `PluginRegistry`:
  - `load_installed()`
  - `install(source)`
  - `enable(id)`
  - `disable(id)`
  - `remove(id)`
  - `get(id)`
  - `list_plugins()`
  - the `versions` live view (`MappingProxyType`)
- `PluginState`: `ENABLED`, `DISABLED`, `FAILED`.
- `PluginRecord(plugin_id, state, metadata, error)`.
- Module-level helpers:
  - `_load`: the loader;
  - `_copy_package`: the installer copy step;
  - `_unimport`, `_module_name`, `_load_error`, `_log`.
- Error types:

| Error | Code | HTTP status |
|---|---|---|
| `PluginNotFoundError` | `PLUGIN_NOT_FOUND` | 404 |
| `PluginAlreadyInstalledError` | `PLUGIN_ALREADY_INSTALLED` | 409 |
| `PluginIncompatibleError` | `PLUGIN_INCOMPATIBLE` | 409 |
| `PluginInUseError` | `PLUGIN_IN_USE` | 409 |
| `PluginPackageError` | `PLUGIN_PACKAGE_INVALID` | 422 |
| `PluginLoadError` | `PLUGIN_LOAD_FAILED` | 422 |

- An enable or disable from the wrong state reuses the existing `InvalidStateTransitionError`.

**How the roadmap names map to code:**

| Roadmap name | Code |
|---|---|
| `PluginManifest`, `PluginValidator` | `manifest.PluginManifest` and `read_manifest` |
| `PluginCompatibilityChecker` | `domain.plugin.compatibility_issues`, applied by `PluginRegistry._check_compatible` |
| `PluginInstaller` | `install`, `remove`, `_copy_package` |
| `PluginLoader` | `_load` |
| `PluginRegistry` | `PluginRegistry` |

This follows the precedent of `NodeRegistry` in Phase 09, where several roadmap names deliberately map onto one class.

### Application / composition

- **`WorkflowService.__init__`:** new keyword `plugins: Mapping[str, str] = MappingProxyType({})`. An empty mapping means no plugins are installed, so a declared plugin dependency is `MISSING_DEPENDENCY`. Both `validate_workflow` and `publish_workflow` pass it.
- **`Settings.plugins_dir: Path | None`** (`AKASHA_PLUGINS_DIR`).
- **`Container.plugins: PluginRegistry | None`.**
- **`create_container`:** when `plugins_dir` is set, it builds a `PluginRegistry` over the built-in `NodeRegistry` and calls `load_installed()` before the services are wired. `WorkflowService` receives `plugins.versions`.

### Sample plugin (`backend/examples/plugins/text_tools/`)

- `akasha-plugin.json`: id `text_tools`, version 1.0.0, engine `>=0.1.0,<1.0.0`, SDK `>=1.0.0,<2.0.0`, one node, documentation `README.md`.
- `__init__.py`.
- `nodes.py`: `SlugifyNode`, node `text_tools.slugify` 1.0.0.
  - Input `text` (STRING), output `slug` (STRING), optional config `max_length`.
  - Imports only `akasha.sdk`, which the existing `tests/test_sdk.py::test_examples_import_only_the_sdk` now also enforces for this plugin.
- `README.md`.

### Testing

- `tests/test_plugins.py`: 46 tests, detailed in §12.
- `tests/integration/test_plugin_runtime.py`: 1 PostgreSQL integration test.
- `tests/test_architecture.py`: new dependency-direction rules for the `plugins` layer. `domain` and `nodes` may not import `plugins`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/plugins/manifest.py` | Created | Manifest boundary schema and `read_manifest` |
| `backend/src/akasha/plugins/registry.py` | Created | Lifecycle, loader, installer and error types |
| `backend/src/akasha/plugins/__init__.py` | Modified (docstring) | Corrected the dependency rules; points to `registry.py` for the trust model |
| `backend/src/akasha/domain/plugin.py` | Modified (rewritten) | `PluginNode`, extended `PluginMetadata`, `compatibility_issues` |
| `backend/src/akasha/domain/workflow.py` | Modified | `validate_dependencies`, `_validate` and `WorkflowDraft.validate` accept the optional `plugins` view |
| `backend/src/akasha/application/workflows.py` | Modified | `WorkflowService` takes `plugins` and passes it at validate and publish |
| `backend/src/akasha/config.py` | Modified | `plugins_dir` setting |
| `backend/src/akasha/bootstrap.py` | Modified | `Container.plugins`; load plugins at startup; pass `versions` to `WorkflowService` |
| `backend/examples/plugins/text_tools/` | Created (new directory, 4 files) | Sample plugin |
| `backend/tests/test_plugins.py` | Created | Component tests |
| `backend/tests/integration/test_plugin_runtime.py` | Created | Lifecycle through PostgreSQL, publish and runtime |
| `backend/tests/test_architecture.py` | Modified | Layer rules for `plugins` |
| `backend/tests/domain/test_trigger_credentials_plugin.py` | Modified | `test_valid` passes `Dependency(...)` instead of a bare string (F2) |
| `docs/sdk.md` | Modified | §9 rewritten from "arrives with Phase 17" into the plugin packaging guide |
| `docs/architecture.md` | Modified | §36 implementation note; stale Phase 16 note in §33 corrected |
| `docs/reports/backend/phase-17-plugin-system.md` | Created | This report |

- No migrations, generated files, dependency or lockfile changes.
- No API routes were added or changed.

---

## 5. Architecture Changes

### D1 — Plugin state lives on the filesystem, not in PostgreSQL

**Decision.** Installed packages live in `<plugins_dir>/<plugin_id>/`. The enable/disable state lives in `<plugins_dir>/state.json` (`{"disabled": [...]}`).

**Context.** Architecture §65 requires "control activation, record installation". Disable must survive restarts.

**Chosen approach.**
- The installed directory is the record of installation. Its manifest holds the version.
- `state.json` holds only the disabled set. It is written atomically (temp file + `os.replace`).
- `FAILED` is not persisted; it is recomputed at startup.

**Alternatives considered.** A database table for plugin records.

**Reason.**
- The engine can boot without a database. `create_container` already supports `database_url=None`, and plugins must still load then.
- Code and state side by side cannot disagree about which plugins exist.
- No migration was needed.

**Consequences.**
- Several processes (Phase 22 workers) must share the directory. They see changes only after a restart.
- An installation timestamp and the installing user are not recorded. That needs authentication (Phase 20) and possibly a database table later.

### D2 — Plugins integrate through `NodeRegistry.register` only

**Decision.** Plugin nodes are registered with the ordinary `NodeRegistry.register(node_cls)`. `WorkflowService` and `ExecutionRuntime` are unchanged in how they see nodes.

**Context.** Phase requirement: "Plugins integrate with the Node Registry without special-case coupling." Roadmap acceptance: "without changing Engine core".

**Chosen approach.**
- `PluginRegistry` owns a reference to the `NodeRegistry`.
- It registers node classes on activation and unregisters them on deactivation.
- The node contract check and the duplicate-id check are the registry's existing checks.

**Alternatives considered.** A separate plugin node catalog merged at lookup time. Rejected: two sources of truth for node types.

**Consequences.**
- Disabling a plugin makes its node types disappear from `definitions` and `catalog` at once.
- Publish reports `UNKNOWN_NODE_TYPE` and `MISSING_DEPENDENCY`. A run fails at plan load (`EXECUTION_GRAPH_INVALID`).
- Running executions keep the implementation they resolved at start. This is existing `unregister` semantics from Phase 09.

### D3 — Workflow plugin dependencies gate validate and publish, not runtime

**Decision.** `validate_dependencies` checks `kind="plugin"` dependencies only when given the enabled plugins. `WorkflowService` passes them; `load_plan`, through `WorkflowVersion.validate`, does not.

**Context.**
- Phase 04 left plugin dependencies unchecked. The docstring said "Phase 17 owns that check".
- Architecture §144 lists "required Plugin exists / compatible" before execution.

**Chosen approach.** Publish-time check only. At run time the node-type check is authoritative.

**Alternatives considered.** Passing plugin versions through `WorkflowVersion.validate`, `load_plan` and `ExecutionRuntime`.

**Reason.**
- A run can only fail because of plugins if it uses plugin nodes, and those are already checked per run through the catalog.
- A declared-but-unused plugin dependency cannot affect execution.
- This avoided changing the runtime constructor in this phase.

**Consequences.**
- A published version whose declared plugin was later disabled still runs, as long as it uses none of that plugin's nodes.
- If §144 is read strictly, threading plugin versions into the runtime is a small follow-up.

### D4 — Plugin node ids must be namespaced under the plugin id

**Decision.** Every node id a plugin declares must start with `<plugin_id>.`. This is enforced in `PluginMetadata.__post_init__`, before any code is loaded.

**Context.** Architecture §37 says node ids must be "globally unambiguous" and recommends the `plugin_name.node_name` form.

**Reason.** A plugin cannot claim `core.*` ids or ids in another plugin's namespace. Overlap is still possible between nested ids, e.g. plugin `acme` and plugin `acme.x` both claiming `acme.x.echo`. The registry's duplicate check catches that and the install rolls back (tested).

**Consequences.**
- Plugin ids containing `-` cannot provide nodes, because node ids forbid `-` (F5).
- The example Node `community.hmac_sign` from Phase 16 could only be packaged by a plugin with id `community`.

### D5 — `_load` is the only place plugin code runs (isolation seam)

**Decision.** Plugin code runs only in two places:
- `_load`: imports the package and resolves entry points;
- `NodeRegistry.register`: instantiates each node once to check its factory.

Both are wrapped so that any exception becomes `PLUGIN_LOAD_FAILED`.

**Context.** Vision §26 says the plugin system "must be abstracted so that future isolated execution models are possible".

**Consequences.** A future isolated loader can return factories for out-of-process proxy nodes from the same point. Nothing downstream would change. The engine does not claim to sandbox anything (documented in `registry.py` and `docs/sdk.md`).

### D6 — No HTTP plugin API in this phase

**Decision.** No `/api/v1/plugins` endpoints.

**Reason.**
- Authentication arrives in Phase 20. An unauthenticated install endpoint would allow remote code execution.
- A read-only list endpoint had no consumer yet.
- The CLI (Phase 21) is the intended driver.

**Consequence.** The lifecycle is currently reachable only from Python (the composition root and tests).

---

## 6. Domain / Data Model Changes

**`PluginMetadata` (changed):**
- `dependencies` changed from `tuple[str, ...]` to `tuple[Dependency, ...]`, reusing the existing workflow `Dependency(kind, name, version_constraint)`.
- Added `nodes` and `documentation`.
- Added invariants (§3) and the `plugin_dependencies` property.
- Compatibility: `PluginMetadata` was not persisted or serialized anywhere before this phase. Its only user was one domain test, which was updated. No stored data is affected.

**`PluginNode` (new):** value object `(node_id, entry_point)`.

**`PluginState` (new enum):** `ENABLED`, `DISABLED`, `FAILED`. `PluginRecord` is a new frozen dataclass. Both live in `akasha.plugins.registry`, not the domain, because they describe installation state.

**Workflow dependencies:**
- `Dependency(kind="plugin")` is now checked at validate and publish.
- Compatibility impact: a draft that declared a plugin dependency used to validate cleanly. It now fails with `MISSING_DEPENDENCY` unless that plugin is installed and enabled. That is the behaviour the Phase 04 design deferred to this phase.
- Already published versions are not re-validated for plugins (D3).

**No database tables, migrations or serialization formats changed.** Workflow graph serialization already stored `kind="plugin"` dependencies.

**New on-disk formats:**
- `akasha-plugin.json`, manifest format version 1. `manifest_version` must be exactly `1`.
- `state.json`: `{"disabled": [str, ...]}`.

---

## 7. API / Contract Changes

**HTTP API:** none.

**Plugin contract (new) — `akasha-plugin.json` v1:**

| Field | Type | Rules |
|---|---|---|
| `manifest_version` | int | must be `1` |
| `id` | str | `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$`; must equal the installed directory name |
| `name` | str | 1–128 characters, not blank |
| `version` | str | semver |
| `description` | str | at most 2000 characters, optional |
| `engine` | str | constraint grammar of `version_satisfies` |
| `sdk` | str | same grammar |
| `dependencies` | list | at most 50 entries of `{kind: "plugin" \| "node" = "plugin", id, version = "*"}` |
| `components` | object | `{nodes: [{id, entry_point}]}`, 1–100 nodes |
| `documentation` | str | optional; a file inside the package |

- Unknown keys are rejected at every level, including unknown component kinds.
- Strict typing: no coercion.
- Errors are `PLUGIN_MANIFEST_INVALID`. `details.fields` lists the failing field paths, and `details.reason` gives a bounded message for JSON or domain errors.

**Plugin lifecycle errors (new codes):**
- `PLUGIN_NOT_FOUND`
- `PLUGIN_ALREADY_INSTALLED`
- `PLUGIN_INCOMPATIBLE` (`details.issues`: list of messages)
- `PLUGIN_IN_USE` (`details.dependents`)
- `PLUGIN_PACKAGE_INVALID`
- `PLUGIN_LOAD_FAILED` (`details.error`: exception type name only)
- `NODE_ALREADY_REGISTERED` and `NODE_CONTRACT_ERROR` propagate unchanged from the node registry during activation.

**Application contract:** `WorkflowService(uow, node_definitions, events=None, plugins=MappingProxyType({}))`. Backward compatible, because the new parameter is a keyword with a default.

**Domain function signatures:** `validate_dependencies` and `WorkflowDraft.validate` gained an optional trailing parameter. Backward compatible.

**SDK contract:**
- The `akasha.sdk` surface is unchanged (`SDK_VERSION` stays `1.0.0`).
- `SDK_VERSION` is now used at runtime for plugin compatibility checks.

**Configuration contract:** new environment variable `AKASHA_PLUGINS_DIR`.

---

## 8. Implementation Details

**Startup (`create_container` → `PluginRegistry(dir, nodes)` → `load_installed()`):**
1. The constructor raises `ConfigurationError` if the directory does not exist. A typo'd path fails fast instead of silently loading nothing.
2. `_read_state()` parses `state.json`. A malformed file (bad JSON, a non-list, non-string entries) raises `ConfigurationError`.
3. It iterates `sorted(dir.iterdir())`, skipping dot-entries (staging directories, the temp state file) and non-directories. It reads each manifest.
   - An unreadable manifest, or a directory name that does not match the id, is recorded as `FAILED` (`PLUGIN_MANIFEST_INVALID`) under the directory name.
   - Plugins in the disabled set are recorded as `DISABLED`.
   - The rest are pending.
4. Dependency-ordered activation:
   - In each round, activate (in sorted order) every pending plugin that has no plugin dependency still pending.
   - If no plugin is ready, the rest form a cycle and are all marked `FAILED` (`PLUGIN_INCOMPATIBLE`).
   - Any `AkashaError` during activation marks that plugin `FAILED`. Its dependents then fail the compatibility check in a later round.
   - Loop cost is O(n²) in the number of plugins, which is acceptable for installation-scale counts.
5. The engine always finishes startup. Failures are logged as `plugin_failed` warnings.

**Activation (`_activate`):**
1. `_check_compatible` checks engine, SDK and dependencies against the current node definitions and the enabled plugin versions.
2. `_load` returns the node classes.
3. Each class goes through `NodeRegistry.register`.
4. On any exception:
   - node ids already registered by this plugin are unregistered;
   - the plugin's modules are removed from `sys.modules`;
   - `AkashaError` and `BaseException` propagate;
   - any other `Exception`, which can only come from a plugin node constructor during `register`'s factory check, is converted to `PLUGIN_LOAD_FAILED`.
5. On success the record becomes `ENABLED`, and `_enabled` and `_versions` are updated.

**Loader (`_load`):**
- The module name is `akasha_plugin_<hex(plugin_id)>`. Hex keeps different ids distinct (`a.b` vs `a_b`) and always forms a valid identifier.
- The package is created with `importlib.util.spec_from_file_location(..., submodule_search_locations=[dir])`, so relative imports inside the plugin work. It is registered in `sys.modules` and executed.
- For each entry point, `importlib.import_module(f"{package}.{module}")` and `getattr`.
- Each resolved object must be a `Node` subclass whose `definition.node_id` equals the declared id.
- Stale modules are removed first, so a reinstall with new code runs the new code (tested).
- The broad `except Exception` is deliberate and commented: plugin import errors are failed loads, not engine crashes. The traceback goes to the server log (`plugin_load_failed`, `exc_info`). The error details carry only the exception type name.

**Installer (`install`):**
1. `read_manifest(source)`.
2. Duplicate check: a record exists or the target directory exists → `PLUGIN_ALREADY_INSTALLED`.
3. `_check_compatible`.
4. `_copy_package(source, <dir>/.install-<uuid>)`:
   - rejects any symlink or non-regular file anywhere in the tree;
   - ignores `__pycache__` and `*.pyc`.
5. Re-read the manifest from the staging copy and compare it to the validated one. This catches the source changing between validation and copy.
6. Rename the staging directory to `<dir>/<id>`. The staging directory is always removed in `finally`.
7. `_activate`; on any failure the target directory is removed.
8. `_save_state()`, which also drops a stale disabled entry from an earlier install with the same id.
9. Log `plugin_installed`.

Upgrading is `remove` followed by `install`. There is no in-place upgrade.

**Disable / enable / remove:**
- `disable` requires `ENABLED`.
- `_deactivate`:
  - refuses (`PLUGIN_IN_USE`) if any enabled plugin lists this one as a plugin dependency;
  - unregisters the nodes, updates `_enabled` and `_versions`, and removes the modules;
  - returns the metadata.
- `enable` accepts `DISABLED` or a `FAILED` record that has metadata. It re-runs the compatibility check, so enabling a plugin whose dependency is disabled fails.
- `remove` deactivates if enabled (same `PLUGIN_IN_USE` protection), deletes the record and `rmtree`s the installed directory. It works for any state, including `FAILED` records without metadata.

**Concurrency:**
- All methods are synchronous and mutate in-process dictionaries. There is no locking.
- The intended callers are the composition root at startup and a future CLI. Neither runs lifecycle operations concurrently in one process.
- Filesystem I/O blocks. That is acceptable for these callers; async callers would need `asyncio.to_thread`.

**Observability:**
- Structured `akasha.plugins.registry` log records with `event` set to one of: `plugin_installed`, `plugin_enabled`, `plugin_disabled`, `plugin_removed`, `plugin_failed` (warning), `plugin_load_failed` (warning with traceback).
- Fields: `plugin_id`, `plugin_version`, `error_code`, `details`.
- No metrics, and no persisted events in the event store.

---

## 9. Findings

### F1 — Plugin dependencies of workflows were silently unchecked
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** Since Phase 04, `validate_dependencies` skipped `kind="plugin"` ("Phase 17 owns that check"). A workflow could declare a plugin dependency and publish without it. This phase checks it at validate and publish (D3). The existing domain test `test_workflow.py` (the `codes(Dependency("plugin", ...)) == []` case) still holds, because the domain path without plugins deliberately remains unchecked.

### F2 — `PluginMetadata.dependencies` could not express version constraints
- **Impact:** LOW
- **Resolution:** FIXED
- **Notes:** The Phase 02 field was `tuple[str, ...]`. Architecture §144 requires "required Plugin version is compatible". It was changed to `tuple[Dependency, ...]`. One pre-existing test (`TestPluginMetadata::test_valid`) failed on the full-suite run because it passed a bare string. It was updated to `Dependency("plugin", "community.core")`. The assertion under test (a valid id is accepted) is unchanged.

### F3 — `NodeRegistry.register` runs plugin code
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** `register` instantiates the node once to check the factory, so a plugin constructor runs outside `_load`. An exception there would have escaped as a non-`AkashaError` and aborted startup. `_activate` converts it to `PLUGIN_LOAD_FAILED` after rollback. Regression test: `test_broken_plugin_code_fails_the_install_cleanly`, the `ValueError` case.

### F4 — An HTTP install endpoint would be remote code execution before Phase 20
- **Impact:** HIGH (had it been built)
- **Resolution:** DEFERRED by design (D6)
- **Notes:** Architecture §22 (API Versioning) lists `/api/v1/plugins`. Deliberately not implemented until authentication exists.

### F5 — Plugin ids with `-` cannot provide nodes
- **Impact:** LOW
- **Resolution:** OPEN
- **Notes:**
  - `_PLUGIN_ID_RE` (Phase 02) allows `-`. `_NODE_ID_RE` (Phase 03) does not, and D4 requires node ids to start with `<plugin_id>.`.
  - Such a manifest passes manifest validation. The load then fails: the `NodeDefinition` in the plugin's own code raises `NodeContractError` at import, which becomes `PLUGIN_LOAD_FAILED`.
  - The rejection is safe but late, and the message is indirect.
  - Fix options: reject `-` in plugin ids when `nodes` is non-empty, or tighten `_PLUGIN_ID_RE`.

### F6 — Architecture §143 ("block removal if active Workflows depend on it") is not enforced
- **Impact:** MEDIUM
- **Resolution:** DEFERRED
- **Notes:**
  - Enforcing it needs a query over published versions for node types or plugin dependencies. No such repository query exists.
  - Current behaviour is non-destructive: workflow data is untouched. Publishes and runs fail with structured errors until the plugin is reinstalled or re-enabled.
  - Documented in `registry.py` and architecture §36.

### F7 — One run of a combined test subset reported "1 skipped"
- **Impact:** LOW
- **Resolution:** OPEN (not reproduced)
- **Notes:**
  - In the report-verification run, `pytest tests/test_plugins.py tests/integration/test_plugin_runtime.py tests/test_architecture.py tests/domain` reported 566 passed and 1 skipped. Immediate re-runs reported 567 passed and 0 skipped.
  - The only skip condition on this path is `pg_container`'s `docker_available()` check. That suggests a transient Docker availability probe.
  - All full-suite runs showed only the 5 expected external smoke skips.

### F8 — Tooling: the auto-mode permission classifier was unavailable
- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:** Several shell and edit steps were temporarily refused ("no verdict") and retried. No effect on the code.

---

## 10. Bugs / Problems Found

**Introduced during this phase and fixed before completion:**

1. **Non-list `disabled` value in `state.json`.**
   - Problem: `{"disabled": "acme"}` was accepted and parsed as the characters `{"a","c","m","e"}`.
   - Root cause: `_read_state` checked only that the elements were strings.
   - Fix: also require `isinstance(disabled, list)`.
   - Regression test: `test_malformed_state_file_and_missing_directory_fail_fast`, now parametrized over `'{"disabled": [1]}'`, `'{"disabled": "acme"}'`, `"[]"` and `"{"`.
   - Affected area: plugin startup.
2. **mypy error in `disable`.**
   - Problem: `_log("plugin_disabled", record.metadata)` passed `PluginMetadata | None`.
   - Fix: `_deactivate` returns the metadata it removed from `_enabled`. This also removed an `assert … # noqa` from the first draft.
   - Verified by `mypy`.
3. **Test-fixture defects** (test code only):
   - `FileExistsError` from reusing `write_plugin` with the same id and version in one directory; simplified to one `write_plugin(...).rename(...)`.
   - A 64 KiB byte string used as a parametrize id produced about 66 KB of output; fixed with explicit `ids=`.

**Pre-existing:** F1 (plugin dependencies unchecked; now resolved), F2 (untyped plugin dependencies; now resolved) and F5 (id grammar mismatch; open).

**Environment or tooling:** F7 and F8.

---

## 11. Fixes Applied

| Symptom | Root cause | Implementation | Verification |
|---|---|---|---|
| Plugin dependency declared but never checked | Phase 04 deferral | `validate_dependencies(..., plugins)`; `WorkflowService(plugins=)` | `test_workflow_plugin_dependencies_are_checked_when_plugins_are_given`; the integration test's publish after disable reports `MISSING_DEPENDENCY` |
| Plugin constructor exception would abort startup | `register` instantiates nodes | `_activate` rollback and conversion to `PLUGIN_LOAD_FAILED` | parametrized broken-code test (`ValueError`) |
| String `disabled` accepted | Missing type check | `isinstance(disabled, list)` | state-file test |
| mypy `arg-type` in `disable` | Optional metadata | `_deactivate` returns metadata | mypy PASS |
| `TestPluginMetadata::test_valid` failed | F2 type change | pass `Dependency(...)` | full suite PASS |

---

## 12. Testing & Verification

### Component tests — `tests/test_plugins.py` (46 tests, PASS)

Real temporary directories (`tmp_path`), real imports, real `NodeRegistry`. No mocks.

**Valid plugin.**
- `test_sample_plugin_installs_registers_and_executes`:
  - installs `examples/plugins/text_tools`;
  - checks `ENABLED`, the documentation file copied, and `versions`;
  - resolves `text_tools.slugify` with `>=1.0.0`;
  - runs it with `run_node` (config `max_length: 11`, input with accents) and gets `"hello-world"`.

**Invalid manifest.**
- `test_invalid_manifests_are_rejected_before_any_change` (16 cases):
  - wrong `manifest_version`;
  - bad id;
  - non-semver version;
  - malformed engine constraint;
  - missing `sdk`;
  - non-string name (strict typing);
  - unknown top-level key;
  - empty nodes;
  - unknown component kind;
  - `core.http` node claim;
  - `../evil:X` entry point;
  - duplicate node;
  - self-dependency;
  - unknown dependency kind;
  - documentation path traversal;
  - missing documentation file.

  Each case asserts `PLUGIN_MANIFEST_INVALID`, and that there are no records, no nodes and no files.
- `test_unparseable_manifests_are_rejected` (5 cases): empty file, invalid JSON, a JSON array, non-UTF-8, over 64 KiB.
- `test_manifest_errors_name_fields_not_values`: a canary value never appears in the error details.
- `test_missing_manifest_or_init_is_rejected`.
- `test_metadata_invariants`.

**Incompatible plugin.**
- `test_compatibility_issues_cover_engine_sdk_and_dependencies`: exact issue codes and their order.
- `test_incompatible_plugins_are_not_installed` (4 cases: engine, SDK, missing plugin dependency, missing node dependency).
- `test_workflow_plugin_dependencies_are_checked_when_plugins_are_given`.

**Duplicate plugin.**
- `test_duplicate_plugin_is_rejected`: `PLUGIN_ALREADY_INSTALLED`; version 1.0.0 stays active.
- `test_node_id_conflict_rolls_back_every_node_of_the_plugin`: `NODE_ALREADY_REGISTERED`, and the plugin's first node is unregistered again.

**Loading.**
- `test_broken_plugin_code_fails_the_install_cleanly` (6 cases):
  - an import that raises;
  - a missing module;
  - an entry point that is not a Node class;
  - a missing attribute;
  - a declared id that differs from the class id;
  - a constructor that raises.

  Each case asserts `PLUGIN_LOAD_FAILED` with the exception type name, and no trace left behind.
- `test_symlinks_in_packages_are_rejected`.

**Lifecycle.**
- `test_disable_enable_remove_and_reinstall`:
  - disable → node gone and `versions` empty;
  - disable again → `InvalidStateTransitionError`;
  - enable → node back;
  - enable again → error;
  - remove → nothing remains and `get` raises `PLUGIN_NOT_FOUND`;
  - reinstall version 2.0.0 → the node outputs `"2.0.0"` (the module cache was invalidated).
- `test_plugins_with_enabled_dependents_cannot_be_disabled_or_removed`:
  - installing the dependent first fails;
  - disable and remove of the base plugin raise `PLUGIN_IN_USE`;
  - enabling the dependent with its base disabled fails.

**Startup.**
- `test_startup_restores_state_in_dependency_order`: `addon` depends on `zbase` and sorts before it, yet loads after it; the disabled state is restored.
- `test_startup_records_broken_plugins_as_failed_and_keeps_going`:
  - a broken import, a two-plugin cycle and a directory name mismatch are all `FAILED` with their codes;
  - a stray file is ignored;
  - the good plugin is enabled;
  - a fixed `FAILED` plugin can be enabled;
  - a metadata-less `FAILED` plugin cannot be enabled, but can be removed.
- `test_malformed_state_file_and_missing_directory_fail_fast` (4 malformed contents, plus a missing directory).
- `test_container_loads_plugins_from_the_configured_directory`: `create_container(Settings(plugins_dir=...))` exposes `container.plugins`; both the plugin node and `core.http` are registered.

### Integration test — `tests/integration/test_plugin_runtime.py` (1 test, PASS)

PostgreSQL via Testcontainers, using the existing session container and `clean_db`.

`test_sample_plugin_lifecycle_through_the_engine`:
1. Built-in registry plus the installed sample plugin.
2. Draft `core.input(field=title) → text_tools.slugify`, with `Dependency("plugin", "text_tools", ">=1.0.0,<2.0.0")` and `Dependency("node", "text_tools.slugify", ">=1.0.0")`.
3. Publish through `WorkflowService(plugins=plugins.versions)`.
4. `ExecutionRuntime(uow, nodes.catalog)` gives `COMPLETED` with `{"slug": "plugins-without-core-changes"}`.
5. Disable:
   - publish raises `WorkflowValidationFailedError` containing `UNKNOWN_NODE_TYPE` and `MISSING_DEPENDENCY`;
   - a new run of the already published version is `FAILED` with no outputs.
6. Enable → `COMPLETED`.
7. Remove → the node type is gone; run `FAILED`.

### Architecture tests — `tests/test_architecture.py` (PASS)

- The `plugins` layer may not import fastapi, starlette, uvicorn, sqlalchemy, redis or provider SDKs.
- It may not import the internal layers `api`, `application`, `infrastructure`, `runtime` or `providers`.
- `domain` and `nodes` may not import `plugins`.

### SDK guard (PASS)

The existing `tests/test_sdk.py::test_examples_import_only_the_sdk` scans `examples/**` and so now also covers the sample plugin.

### Security tests

Included above:
- path traversal (entry point, documentation);
- symlink smuggling;
- namespace shadowing of `core.*`;
- manifest error disclosure (canary);
- strict typing;
- manifest size cap;
- broken or hostile import code contained as `PLUGIN_LOAD_FAILED`.

**Not performed:** API tests (no endpoints), E2E, performance, accessibility (no UI).

---

## 13. Quality Gates

Final runs, executed from `backend/` during report verification:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | "150 files already formatted". The first run in the phase reformatted 6 new/changed files; no unrelated files changed |
| Linter (`uv run ruff check .`) | PASS | "All checks passed!" |
| Type Checker (`uv run mypy .`) | PASS | "no issues found in 146 source files". Failed once during the phase (`registry.py:248`, arg-type), then fixed (§10) |
| Unit / component tests | PASS | `tests/test_plugins.py`: 46 passed |
| Integration tests | PASS | `test_plugin_runtime.py`: 1 passed, with Docker available |
| Full suite (`uv run pytest`) | PASS | 1292 passed, 5 skipped (all `tests/external/test_openrouter_smoke.py`, `AKASHA_SMOKE_OPENROUTER_API_KEY` not set). Failed once during the phase (`TestPluginMetadata::test_valid`, F2), then fixed |
| API tests | N/A | No API changes |
| E2E tests | N/A | Not part of this phase |
| Build | N/A | No build step changed; no container build run |
| Security checks | PASS (tests only) | No dependency audit or SAST tool run |
| Accessibility | N/A | Backend |

---

## 14. Acceptance Criteria Verification

Roadmap §21 acceptance: "A custom Node can be … without changing Engine core."

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| created | PASS | `examples/plugins/text_tools/nodes.py` (`SlugifyNode`), written against `akasha.sdk` only (`test_examples_import_only_the_sdk`) |
| packaged | PASS | Directory package with `akasha-plugin.json` (format in `manifest.py`, `docs/sdk.md` §9). No archive format or `package` command (deferred, §19); the roadmap does not require one |
| installed | PASS | `PluginRegistry.install`; `test_sample_plugin_installs_registers_and_executes`; integration test |
| registered | PASS | Nodes registered via `NodeRegistry.register`; `nodes.resolve("text_tools.slugify", ">=1.0.0")` succeeds |
| discovered | PASS | `load_installed` scans the plugins directory; `test_startup_restores_state_in_dependency_order`; `test_container_loads_plugins_from_the_configured_directory` |
| used | PASS | Integration test: published workflow executes to `COMPLETED` with the plugin node's output |
| disabled | PASS | `disable` unregisters the nodes and persists state; publish and run fail with structured errors; `enable` restores |
| removed | PASS | `remove` deletes files and nodes; the integration test run fails after removal; `test_disable_enable_remove_and_reinstall` |
| without changing Engine core | PASS | Installing a plugin requires no code change. The one-time engine changes that make this possible (bootstrap wiring, `plugins_dir`, the plugin-dependency check) are the phase's infrastructure, not per-plugin changes. `ExecutionRuntime`, `load_plan` and `NodeRegistry` are unchanged |
| "Test the complete lifecycle with a sample plugin" (Tests) | PASS | `tests/integration/test_plugin_runtime.py` |
| Plugins declare id, version, engine compatibility, SDK compatibility, dependencies, components (Scope) | PASS | Manifest fields `id`, `version`, `engine`, `sdk`, `dependencies`, `components`; all but `dependencies` are required |
| Components: `PluginManifest`, `PluginRegistry`, `PluginLoader`, `PluginInstaller`, `PluginValidator`, `PluginCompatibilityChecker` | PASS | Mapping in §3 (roadmap names map onto fewer classes, as `NodeRegistry` did) |
| Phase request: metadata includes name, documentation, provided capabilities | PASS | `name`, `documentation`, `components.nodes` |
| Phase request: malformed metadata cannot corrupt the registry | PASS | 16 + 5 invalid-manifest cases assert an untouched registry, node registry and directory |
| Phase request tests (valid, invalid manifest, incompatible, duplicate, loading, registration, disable/remove, sample execution) | PASS | §12 |
| Phase request: no marketplace | PASS | None implemented |

Nothing is PARTIAL or FAIL. The limitations in §20 are outside the literal acceptance criteria. Architecture §143 (removal blocking by workflow usage) is the most material one, which is why the status is COMPLETE WITH LIMITATIONS.

---

## 15. Security Considerations

**Plugin trust.**
- Plugin code is trusted, installation-level code running in-process. This is stated in `registry.py` and `docs/sdk.md`, and nothing claims sandboxing.
- Validation happens before code runs: manifest schema, compatibility, entry-point grammar and namespace.
- A malicious plugin can still do anything once imported. Only operators should be able to write to `AKASHA_PLUGINS_DIR` or call `install` (documented in `config.py`).

**Path traversal.**
- Entry points are identifier-only; imports stay inside the synthetic package.
- `documentation` must resolve inside the package.
- The installed path is `<dir>/<plugin_id>`, and the id regex excludes `/` and `..`.
- `remove` and `enable` only address keys already in `_records`, which come from validated ids or directory names found by listing the directory.

**Symlinks.** Any symlink in a package is rejected, as is a symlinked manifest. This prevents copying files from outside the package into the plugins directory, where documentation might later be served.

**Node namespace.** Plugins cannot register `core.*` or other plugins' namespaces (D4). Nested-namespace overlap is caught by the duplicate check.

**Resource bounds.** Manifest at most 64 KiB, at most 100 nodes, at most 50 dependencies, bounded string lengths. Total package size is not bounded (trusted operator input).

**Error disclosure.**
- Manifest errors list field paths, never values.
- Load errors expose only the exception type name.
- Tracebacks stay in server logs.

**API exposure.** No plugin endpoints (D6). Adding them requires Phase 20 authorization.

**Integrity.** A manifest re-check after copying catches a source that changed during install. There is no signature or hash verification of packages (not required for V1; see §19).

**Secrets.** Plugins receive credentials only through the existing SDK `resolve_credential` rules. Nothing plugin-specific handles secrets.

---

## 16. Performance Considerations

- **Startup:** `load_installed` is O(n²) in the number of plugins because of the rounds, plus one directory walk and manifest parse per plugin. Negligible at installation-scale counts. Not measured.
- **Install:** blocking file copy and imports; the cost scales with package size. Not measured.
- **Request path:** no impact. Publish consults an in-memory `MappingProxyType`, and the runtime uses the existing catalog.
- No caching was introduced beyond Python's module cache, which is explicitly invalidated on deactivate and reload.

---

## 17. Documentation Changes

| File | Change |
|---|---|
| `docs/sdk.md` §9 | Rewritten from "Making a Node available … arrives with Phase 17" into "Packaging a Node as a Plugin": layout, manifest example, field rules, lifecycle table, workflow plugin dependencies, trust note, direct registration fallback |
| `docs/architecture.md` §36 | New "Implementation (Phase 17)" note: manifest, compatibility, registry, all-or-nothing install, startup behaviour, state file, trust seam, non-blocking removal, single-process scope, no HTTP install |
| `docs/architecture.md` §33 | Stale "registration without engine code (Phase 17)" replaced by a pointer to §36 |
| `backend/src/akasha/plugins/__init__.py` | Dependency rules corrected |
| `backend/examples/plugins/text_tools/README.md` | Sample plugin documentation |

**Should be updated in future phases:**
- architecture §22 (`/api/v1/plugins`) and §58 (`agent-engine plugin list/install`), when Phases 20 and 21 implement them;
- §143, if removal blocking is implemented;
- no ADR was created. D1 (filesystem state) and D5 (isolation seam) could be promoted to an ADR (architecture §151 plans "ADR-006 Plugin Architecture").

---

## 18. Dependencies / Infrastructure Changes

- **New dependencies:** none. Uses the stdlib (`importlib`, `shutil`, `json`, `uuid`) and the already-installed Pydantic.
- **Removed or changed dependencies:** none. No lockfile change.
- **Configuration:** new `AKASHA_PLUGINS_DIR` (optional). When set, the directory must exist, or startup fails with `CONFIGURATION_ERROR`.
- **Database / Redis / Docker:** no changes. The integration test reuses the existing PostgreSQL Testcontainer fixture.
- **Test infrastructure:** none new.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects completion? |
|---|---|---|---|
| Blocking disable/remove while workflows use the plugin (§143) | Needs a published-version query; current behaviour is non-destructive | Phase 18 (import/export dependency resolution) or 21 (CLI with `--force`) | No (limitation) |
| HTTP plugin endpoints | Remote code execution risk before authentication | Phase 20+ | No |
| CLI `plugin list/install/...` | CLI phase | Phase 21 | No |
| Archive (zip/wheel) packages and a `package` command | A directory package satisfies the roadmap; archives add a zip-slip surface | Phase 18 or 21 | No |
| Multi-process propagation of lifecycle changes | Workers do not exist yet | Phase 22 | No |
| Plugin lifecycle events in the event store and metrics | Logs cover observability for now; no consumer | Later observability work | No |
| Installation record (timestamp, actor) | Needs authentication for the actor | Phase 20 | No |
| Plugin-provided triggers, providers, UI metadata, CLI commands (§35) | V1 plugins provide nodes only; unknown component kinds are rejected | Not scheduled | No |
| Plugin dependency check at run time (§144) | See D3 | Optional follow-up | No |
| Package signature or hash verification | Not required for V1 trusted code | Security hardening (Phase 25) | No |
| Sandboxed / isolated plugin execution | Vision §26: not V1 | Future | No |

---

## 20. Known Limitations

- Disabling or removing a plugin used by published workflows is allowed. Those workflows then fail at publish or run with structured errors.
- Lifecycle changes affect only the calling process. Other processes need a restart.
- All lifecycle methods are synchronous and unlocked. Concurrent calls in one process are not supported.
- Upgrading requires `remove` + `install`. There is no in-place upgrade and no rollback to the previous version.
- A `FAILED` plugin enabled later uses the metadata read at startup. Manifest edits made after startup are not re-read until restart.
- Plugin ids containing `-` cannot provide nodes (F5); they fail at load rather than at manifest validation.
- Package total size and file count are not limited.
- Plugin nodes are registered with the default no-argument factory. Plugins cannot receive engine-injected dependencies (e.g. the network policy) the way built-in nodes do.
- No installation timestamp or actor is recorded (§65 "record installation" is only partly met by the installed directory and its manifest).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| F5: plugin id grammar allows `-`, node ids do not | Late, indirect failure for such plugins | LOW | `domain/plugin.py` | Reject `-` in plugin ids that declare nodes (or align the regexes) with a test | Next plugin-touching phase |
| F6: §143 removal blocking | Workflows can lose their node types when a plugin is disabled | MEDIUM | `plugins/registry.py`, repositories | Add a published-version usage query; block or require explicit force | Phase 18/21 |
| F7: transient "1 skipped" in one subset run | Possible Docker availability probe flake hiding integration tests | LOW | `tests/integration/conftest.py` | Watch CI; consider failing instead of skipping when Docker is expected | Phase 26/27 CI |
| Multi-process plugin state | Workers could disagree with the API process | MEDIUM (once workers exist) | `plugins/registry.py` | Reload on start per process; define a change-notification policy | Phase 22 |

---

## 22. Architectural / Product Decisions

- **Plugin state on the filesystem (D1).** Code and state live together; the engine boots without a database.
- **Single integration point (D2).** Plugins only call `NodeRegistry.register` and `unregister`. Keep it that way: publish and runtime must not learn about plugins.
- **Plugin dependencies gate publish, node types gate runs (D3).**
- **Namespaced plugin node ids (D4).** Guards `core.*` and other plugins' namespaces.
- **`_load` is the isolation seam (D5).** Keep plugin code execution confined to it (and to `register`'s factory check). A future isolated loader replaces it.
- **No pre-auth HTTP install (D6).**
- **Startup is resilient, configuration is strict.** A broken plugin becomes `FAILED` and the engine starts. A missing plugins directory or a malformed `state.json` is an operator configuration error and fails fast.
- **All-or-nothing install.** Staging directory, then rename, then activate, with rollback of files, nodes and module cache.
- **Manifest format is versioned** (`manifest_version: 1`) and closed: unknown keys are rejected so they cannot be silently ignored.

---

## 23. Repository / Git State

- **Branch:** `main`. Last commit: `8d4a11b backend phase 16 - python node sdk`. Phase 17 is **not committed**; no commit was requested.
- **Modified (tracked, 10 files, +260/−52):**
  - `backend/src/akasha/application/workflows.py`
  - `backend/src/akasha/bootstrap.py`
  - `backend/src/akasha/config.py`
  - `backend/src/akasha/domain/plugin.py`
  - `backend/src/akasha/domain/workflow.py`
  - `backend/src/akasha/plugins/__init__.py`
  - `backend/tests/domain/test_trigger_credentials_plugin.py`
  - `backend/tests/test_architecture.py`
  - `docs/architecture.md`
  - `docs/sdk.md`
- **Untracked (new):**
  - `backend/examples/plugins/`
  - `backend/src/akasha/plugins/manifest.py`
  - `backend/src/akasha/plugins/registry.py`
  - `backend/tests/integration/test_plugin_runtime.py`
  - `backend/tests/test_plugins.py`
  - this report
- **Unrelated changes:** none detected. The working tree was clean at the start of the phase, and every change listed belongs to Phase 17.

---

## 24. Phase Completion Assessment

### Implemented
- Manifest v1 and its validator.
- Extended domain `PluginMetadata` and `PluginNode`.
- Compatibility checker covering engine, SDK, and plugin and node dependencies.
- Workflow plugin-dependency check at validate and publish.
- `PluginRegistry`: discovery, dependency-ordered startup, all-or-nothing install, loader, enable, disable, remove, and persisted state.
- Configuration and container wiring.
- Sample plugin.
- Documentation.

### Verified
- 46 component tests and 1 PostgreSQL integration test pass.
- Architecture guards pass.
- Full suite: 1292 passed, 5 expected external skips.
- ruff format, ruff check and mypy all pass.

### Not Implemented
- HTTP endpoints, CLI, archive packages, plugin events and metrics, non-node components, sandboxing.

### Deferred
- See §19. Notably §143 removal blocking and multi-process propagation.

### Open
- F5, F6, F7, and multi-process plugin state (§21).

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**Next phase:** Phase 18, Workflow Import / Export.

**What the next phase can assume:**
- A `PluginRegistry` exists (`container.plugins`, `None` when `AKASHA_PLUGINS_DIR` is unset).
- `plugins.versions` is the live mapping of enabled plugin id → version.
- `validate_dependencies(deps, definitions, plugins)` checks both node and plugin dependencies. Import validation (backend AGENTS §46: "Plugin-Abhängigkeiten") should call it with `plugins.versions`, not re-implement it.
- `WorkflowService` already validates plugin dependencies at validate and publish.
- Plugin node types are ordinary entries in `NodeRegistry`. Exports should reference node types and `Dependency(kind="plugin")` entries, never plugin code.

**Contracts that must not change casually:**
- the `akasha-plugin.json` v1 fields and rules (`docs/sdk.md` §9);
- the `PLUGIN_*` error codes;
- node id namespacing (`<plugin_id>.<name>`);
- the `state.json` shape;
- `WorkflowService(..., plugins=)`.

**Constraints:**
- Lifecycle operations are synchronous and single-process.
- Do not add an HTTP install endpoint before Phase 20 authorization.
- Keep plugin code execution confined to `_load` and `register` (D5).
- Import/export of packages must reuse the same path-traversal and symlink discipline if archives are introduced (see `_copy_package`, `read_manifest`).

**Open issues to be aware of:**
- F6: removal is not blocked by workflow usage. Import-time dependency resolution will surface such workflows as `MISSING_DEPENDENCY`.
- F5: hyphenated plugin ids.

**Prerequisites:** none beyond the current `main` plus this uncommitted Phase 17 change set, which should be committed first.
