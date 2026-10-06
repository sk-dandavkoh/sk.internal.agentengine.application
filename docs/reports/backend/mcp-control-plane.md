# MCP Control Plane & AI-Programmable Extensibility — Engineering Report

## 1. Executive Summary

**Goal.** Let authorized MCP-compatible AI agents operate the engine end to
end (discover, build, validate, publish, run, inspect, manage triggers,
import/export) and extend it with plugins they write themselves, through a
production-grade, optional MCP interface over the existing application
services.

**Result.** `akasha.mcp` serves 49 typed tools, 2 resources and 3 prompts
over the official MCP Python SDK (2.3, Streamable HTTP, stateless, JSON).
It is off by default and fails closed. It authenticates with the existing
session tokens and authorizes with the existing `AuthorizationService`.
The acceptance scenario (an agent discovers a missing capability, writes,
validates, tests, packages and installs a plugin, then builds, publishes,
runs and inspects a project using the new node) passes through the real
MCP boundary against PostgreSQL.

Three gaps in the existing system had to be closed at their own layer (not
in MCP):

| # | Gap | Layer | Fix |
|---|---|---|---|
| G1 | A published version did not record which plugin versions it ran on; installing a new plugin version silently changed what old versions did | domain / application / runtime / persistence | `ProjectVersion.plugin_versions` pinned at publish (migration 0014), enforced by `load_plan` before every run |
| G2 | No safe plugin update: upgrade was remove + install, blocked while projects use the plugin, and not atomic | plugins / application | `PluginRegistry.update` (atomic, rollback), `PluginService.update_archive` (dependency-aware, `force`) |
| G3 | Plugin lifecycle lock lived inside the REST router, so a second interface could interleave lifecycle calls | application | `application.plugins.PluginService` owns the lock; REST and MCP both use it |

Two smaller additions: `ProjectService.edit_draft` (one granular draft edit
per transaction, revision-guarded) and `PluginService.check_archive`
(static package validation that never imports code).

**Status:** COMPLETE WITH LIMITATIONS (§13). The limitations are documented
deferrals, not failing criteria. The one criterion not fully met is real
MCP client coverage beyond the official SDK client (§11).

---

## 2. Architecture

```text
MCP client ──POST /mcp──> McpEndpoint ──> MCP SDK (MCPServer, stateless) ──> tool
                            │ bearer session → Identity (AuthenticationService)
                            └ 401 before the SDK parses anything
tool ──> AuthorizationService / require_admin (same checks as REST guards)
     ──> ProjectService / ExecutionService / TriggerService / ReportService /
         ProjectPackageService / PluginService / NodeRegistry
     ──> domain ──> infrastructure / runtime
```

- `akasha/mcp/server.py`: endpoint, auth, `Kit.tool` wrapper (structured
  errors, redaction, response cap, audit log), SDK wiring.
- `akasha/mcp/tools.py`: discovery, nodes, projects, drafts, graph,
  validation, publishing, executions, observability, triggers,
  import/export, credentials metadata.
- `akasha/mcp/plugin_tools.py`: plugin authoring and lifecycle.
- `akasha/mcp/prompts.py`: prompts and static resources.
- `akasha/mcp/authoring.py`: SDK guide and starter-package scaffold.
- `api/app.py`: builds the endpoint only when enabled, as a plain route
  (not in OpenAPI); the SDK session manager runs inside the app lifespan.

MCP imports no repository, SQL or infrastructure module. This is enforced
by `tests/test_architecture.py` (new rule for `mcp`). It never calls the
REST API over HTTP. Results reuse REST DTOs (`ProjectDetail`,
`ProjectVersionDetail`, `ValidationResponse`, `ExecutionDetail`,
`EventPage`, `LogPage`, `ExecutionReport`, `NodeTypeOut`, `PluginOut`,
`CredentialPage`, `ImportProjectResponse`, …). Agent-specific shapes exist
only where a summary keeps results small (`ProjectOverview`,
`ExecutionOverview`, `NodeTypeSummary`, `DraftChange`).

Decision record: ADR 0006. User documentation: `docs/mcp.md`.

## 3. Tools, resources, prompts

49 tools (full table: `docs/mcp.md` §5/§6), each with an LLM-oriented
description, typed input and output schemas, and annotations
(read-only / destructive / idempotent / closed-world):

- discovery `system_info`; nodes `node_types_list|get`,
  `node_config_validate`
- projects `project_list|get|create|update|delete`; drafts
  `project_draft_get|update`; graph `graph_get`,
  `graph_node_add|update|delete`, `graph_edge_add|delete`
- `project_validate`, `project_publish`, `project_versions_list`,
  `project_version_get`
- executions `execution_start|list|get|node_get|cancel`; observability
  `execution_events_get`, `execution_logs_get`, `execution_metrics_get`,
  `report_get`
- triggers `trigger_list|create|update|delete` (`trigger_update.enabled`
  is enable/disable)
- `project_export`, `project_import`; `credential_list` (metadata)
- plugins `plugin_list|get|sdk_get|create|validate|test|package|install|
  update|enable|disable|remove`

Deliberately absent: a generic `manage(action=…)` tool; credential
create/update/delete (secrets must not pass through an agent's context);
user management; `trigger_get` and `report_list` (covered by `trigger_list`
and `execution_list` without duplicating them).

Resources: `akasha://plugins/sdk-guide`, `akasha://plugins/manifest-schema`.
These are static, so attaching them as documents has real value. Live data
is not exposed as resources, because tools carry pagination, filters and
authorization arguments.

Prompts: `build_workflow`, `debug_execution`, `create_plugin`. They are
guidance only, name tools, and embed no stored data.

## 4. Transport, configuration, enable/disable

- Streamable HTTP via `MCPServer.streamable_http_app(stateless_http=True,
  json_response=True)`. The SDK's ASGI handler is wrapped by our
  authenticating endpoint and added as a Starlette `Route` at `/mcp`.
  Stateless means it works with any number of API processes and with plain
  reverse proxies. No stdio: a second process would hold its own plugin
  registry (ADR 0006).
- Settings (existing `Settings`, `AKASHA_` prefix, startup-only):
  `AKASHA_MCP_ENABLED` (false), `AKASHA_MCP_MAX_REQUEST_BYTES` (4 MiB),
  `AKASHA_MCP_MAX_RESPONSE_BYTES` (256 KiB), `AKASHA_MCP_PLUGIN_CODE`
  (false). No hot reload.
- Disabled: no route, no `MCPServer`, no session manager, and the MCP
  modules are not imported (`/mcp` → 404; OpenAPI byte-identical,
  tested).
- Enabled without a database: every `/mcp` request → 500
  `CONFIGURATION_ERROR` (no sessions can exist; nobody gets in).
- Compose: nginx proxies `location = /mcp` to the API
  (`frontend/nginx.conf`), unbuffered, body limit left to the API.

## 5. OpenAPI compatibility

The REST API remains canonical. The MCP route is not an `APIRoute`, so it
is absent from `/openapi.json` (test: OpenAPI with MCP on equals OpenAPI
with MCP off). The single REST contract change is additive:
`ProjectVersionDetail.plugin_versions` (G1). The committed snapshot
`frontend/src/api/openapi.json` was regenerated, the frontend runtime
schema (`projectVersionSchema`) and six test fixtures were updated, and the
frontend contract test passes. `test_openapi_contract.py` (snapshot and
error-envelope rules) passes.

## 6. Authentication and authorization

- Bearer session tokens from `POST /api/v1/auth/login`. They are verified
  on every HTTP request by `AuthenticationService.authenticate`, the same
  code the REST guard runs. No cookies (tested: a cookie plus the CSRF
  header is still 401). Lifetime and revocation are the session's (TTL,
  logout, password change/reset, disabling the user). Tested: logout
  revokes MCP access on the next request.
- The SDK's OAuth resource-server mode was not used: it needs an
  authorization server, which does not exist, and adding one would rewrite
  authentication (out of scope).
- Authorization: per tool, `AuthorizationService.require_project` /
  `require_execution` / `owner_filter` / `require_admin`. These are the
  calls the REST guards make. Hidden resources answer `*_NOT_FOUND`
  exactly as over REST (tested side by side). Plugin and credential tools
  are admin-only. There is no service account.

## 7. Plugin lifecycle and AI-created plugins

Flow: `plugin_sdk_get` → `plugin_create` (scaffold as files) → edit →
`plugin_validate` → `plugin_test` → `plugin_package` → `plugin_install` →
node appears in `node_types_list` / `/api/v1/nodes` (the one canonical
`NodeRegistry`) → use → `plugin_disable` / `plugin_enable` →
`plugin_update` → `plugin_remove`.

- Files, not filesystem: sources travel as `{path: text}` in the call and
  are packed (`archive.pack_files`, deterministic) and unpacked with the
  existing `unpack_package` guards. There is no workspace on the server,
  no path an agent can name, and no shell.
- Static validation (`PluginService.check_archive`): archive safety,
  manifest, engine/SDK/dependency compatibility
  (`PluginRegistry.compatibility_issues`), Python syntax (`ast.parse`) and
  entry points (module present, class defined), each as
  `{code, message, path, line}`. It never imports (tested with code that
  would create a marker file on import).
- `plugin_test` imports the package under a throwaway module name
  (`PluginRegistry.load_unregistered`), so it never clobbers an installed
  plugin's modules. It runs one node through `akasha.sdk.testing.run_node`
  with a timeout. Exceptions from plugin code become `PLUGIN_TEST_FAILED`
  (type only).
- Install, update and remove go through the existing registry
  (all-or-nothing install; new atomic update), behind the shared lock.
  States: `enabled` / `disabled` / `failed` (+ error code;
  `PLUGIN_INCOMPATIBLE` is the incompatible state). Repeated transitions
  are conflicts, not no-ops.

## 8. Plugin trust boundary

Plugins are trusted, in-process, unsandboxed code. No sandbox is claimed.
The boundary is explicit:

| Step | Runs new code | Requirement |
|---|---|---|
| sdk / create / validate / package / list / get | no | admin |
| enable / disable / remove of installed plugins | only operator- or admin-installed code | admin (as REST, ADR 0001) |
| test / install / update | yes | admin **and** `AKASHA_MCP_PLUGIN_CODE=true` |

With the flag on, an admin token is shell-equivalent on the host. This is
documented in `docs/mcp.md` §6, ADR 0006 and the config comment. It is
tested both ways: with the flag off, nothing is installed and no plugin
line runs.

## 9. Versioning and reproducibility

- `plugins_in_use(graph, dependencies, plugin_nodes, plugins)` (domain)
  gives the enabled plugins a draft uses: owners of its node types (via
  `PluginRegistry.node_plugins`) plus declared plugin dependencies.
  `ProjectService.publish_project` stores them as the version's
  `plugin_versions`.
- `ProjectVersion.validate(definitions, plugins)` adds
  `pinned_plugin_issues` (exact match). `load_plan` passes the runtime's
  enabled versions (`ExecutionRuntime(plugins=PluginRegistry.versions)`).
- Tested end to end: version 1 pinned to `acme@0.1.0`; a forced update to
  0.2.0 makes version 1 fail with `EXECUTION_GRAPH_INVALID`
  (`incompatible_dependency`) before any node runs. Version 2 pins 0.2.0
  and runs the new code. Reinstalling 0.1.0 makes version 1 run again.
- Backward compatibility: migration 0014 adds the column with default
  `{}`. Older versions have no pins and run as before (unit tested).

## 10. Security review

| Concern | Treatment | Evidence |
|---|---|---|
| Disabled = closed | no route, no server | `test_disabled_by_default…`, unit `test_a_disabled_engine…` |
| Anonymous / bad tokens / cookie | 401 before the SDK | `test_unauthenticated_requests_never_reach_the_protocol` |
| No auth backend | 500 for all (fail closed) | `test_enabled_without_authentication_backing_fails_closed` |
| Revocation | per-request session check | `test_a_revoked_session_loses_mcp_access` |
| Authorization parity with REST | same service calls; `*_NOT_FOUND` for hidden | `test_members_see_only_their_projects_and_no_admin_tools` |
| Plaintext secrets / leakage | drafts reject secrets by location; errors and outputs redacted; credential values never readable or writable | `test_plaintext_secrets_are_rejected_and_never_echoed` (result, text and logs checked), unit redaction test |
| Prompt injection | content returned as data only; server instructions and prompts mark it untrusted | `test_workflow_content_is_returned_as_data_only`; documented limits |
| Plugin code execution | admin + opt-in; validation never imports | `test_plugin_code_needs_the_operator_opt_in`, `test_validation_never_executes_the_package` |
| Path traversal / malicious archives | `unpack_package` guards for every package | `test_package_paths_cannot_escape` (5 variants) |
| Malicious manifest | strict manifest model, unknown fields rejected | `test_validation_reports_actionable_issues` |
| Oversized requests / responses | SDK body cap + app cap; result cap with `RESPONSE_TOO_LARGE` | `test_oversized_requests_are_rejected`, `test_results_over_the_response_limit…` |
| Hanging plugin test | `asyncio.timeout` (cooperative code) | `test_a_failing_plugin_test_is_reported_not_raised` |
| Malformed MCP / unknown tools | rejected by the SDK before any tool runs | `test_unknown_tools_and_malformed_arguments_are_errors` |
| Log leakage | audit lines carry ids only; `mcp` SDK loggers capped at INFO (its DEBUG lines quote frames) | `test_tool_calls_are_audit_logged_without_argument_values`, unit `test_sdk_frame_logging_never_goes_below_info` |
| SSRF | no tool fetches URLs; node network policy unchanged | n/a |
| DNS rebinding | SDK guard off: bearer-only endpoint, no ambient credentials | `docs/mcp.md` §2 |
| Execution abuse / DoS | engine resource limits unchanged (active executions, graph size, timeouts); no MCP rate limit | deferred (§13) |
| Dependency attack surface | `mcp~=2.3` official SDK pinned in `uv.lock`; pulls `httpx2`, `pyjwt`, `sse-starlette`, `python-multipart`, `opentelemetry-api` | lockfile |

Fixed during the phase: `tests/integration/test_plugin_runtime.py` built
its `ExecutionRuntime` without the enabled plugin versions. With pinning,
the runtime correctly refused the pinned version. The test now wires
`plugins=` as `bootstrap` does; the pin check was not weakened.

Finding during review (fixed): the documentation-file symlink check in
`plugin_get` ran after `resolve()`, so it could never trigger. It now
checks the unresolved path. Install already rejects symlinks, so this was
defense in depth.

## 11. Tests

New:

- `tests/test_mcp_units.py`: config defaults, disabled/enabled wiring,
  fail-closed, error mapping and redaction, pinning rules, deterministic
  packing round trip, scaffold validity, manifest schema, SDK log cap.
- `tests/integration/test_mcp.py` (22): enable/disable, OpenAPI parity,
  authentication, revocation, request limit, protocol discovery
  (tools/prompts/resources), malformed calls, system info, full lifecycle
  (discover → build → validate → publish → run → inspect logs, events,
  metrics, report), invalid drafts, graph and trigger edits, whole-draft
  replace, export/import round trip and re-run, cancellation, stale
  revisions, 5 concurrent edits (exactly one wins), repeated publish,
  authorization parity, secrets, prompt-injection-as-data, response limit,
  REST/MCP semantic consistency, audit logging.
- `tests/integration/test_mcp_plugins.py` (14): the self-extension
  acceptance scenario (25 steps), reproducibility across plugin updates,
  failed-update rollback, lifecycle and dependency-aware removal,
  concurrent installs, trust gate, no code at validation, path traversal
  (5 variants), validation issues, failing/hanging plugin tests.
- `tests/runtime/test_runtime_units.py`: pinned versions at load.
- `tests/deployment/test_compose_lifecycle.py` (opt-in): MCP off in the
  default stack (`/mcp` 404); new `test_mcp_through_the_reverse_proxy_
  with_dedicated_workers`: a real SDK client through nginx, the dedicated
  worker container runs the execution, graceful API stop.
- `tests/test_architecture.py`: `akasha.mcp` must not import
  SQLAlchemy/Redis/uvicorn or `akasha.infrastructure`.

All MCP integration tests use the official MCP client over Streamable HTTP
against the app built by `create_app` from `create_container` (PostgreSQL
Testcontainer). Executions are run by calling the runtime where a worker
would, which is deterministic. No sleeps.

Results: see §12.

**MCP clients tested.** Tested: the official MCP Python SDK client
(`mcp` 2.3), in-process over ASGI in the integration suites and, in the
deployment test, over real HTTP through nginx. Not tested: Claude Code,
Codex and Hermes are not installed in this environment, so their example
configurations in `docs/mcp.md` §10 are untested. The MCP Inspector
(TypeScript SDK) would need an npm download and was not run.

## 12. Quality gates

| Gate | Result |
|---|---|
| `uv run pytest` (backend, full) | 2204 passed, 12 skipped, 5 failed. The 5 failures are Windows-host-only (`os.killpg`, symlink privilege in `test_command_node.py`/`test_file_nodes.py`) and fail identically on the code before this work; they pass on Linux CI hosts |
| MCP suites | 22 + 14 integration, 12 unit (+1 runtime unit): pass |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS |
| `uv run mypy` | PASS with `--platform linux`. On Windows, 2 pre-existing errors in `nodes/command.py` (`os.killpg`/`SIGKILL` are POSIX-only), unchanged by this work |
| OpenAPI snapshot + error envelope | PASS |
| Frontend `npm run check` (format, lint, typecheck, 482 tests, build) | PASS |
| Deployment (Compose, opt-in, real images) | 3 passed: operator lifecycle, zero-config (incl. `/mcp` 404 by default), MCP through nginx with dedicated workers |

## 13. Limitations and deferred work

- **Agent credentials**: personal access tokens were added afterwards
  (ADR 0007). Per-token scopes and OAuth 2.1 for MCP remain deferred.
- **Rate limiting**: none specific to MCP. The engine's resource limits
  and the login throttle apply; use the reverse proxy for request-rate
  limits.
- **One plugin version installed at a time**: after a forced update,
  versions pinned to the old version stop running until republished or the
  old version is reinstalled. Side-by-side versions are deferred.
- **Transitive plugin pins**: only direct use is pinned
  (`ponytail:` note in `domain/project.py`).
- **Dedicated workers** load plugins at startup: changes over MCP (or
  REST) reach them after a restart (existing ADR 0001 limitation).
- **stdio transport**: not offered (ADR 0006); use an HTTP bridge.
- **Plugin update installs libraries on the event loop** (`ponytail:` note
  in `PluginRegistry.update`); fresh installs prepare them off-loop.
- **`plugin_test`** cannot test packages with `python_requirements`
  before install. Its timeout cannot stop synchronous busy loops (nor can
  the runtime's): another reason the tool is gated.
- **No idempotency keys** on create/import/start, the same as REST.
- **Client identity** (`clientInfo`) is not audited: stateless requests do
  not carry it, and it is self-declared.

## 14. Decisions (summary)

ADR 0006: interface module over application services; official SDK,
stateless Streamable HTTP inside the API process; off by default; existing
sessions for authentication; explicit typed tools; revision CAS for
concurrency; plugins through the existing system behind a second opt-in;
plugin version pinning in the domain and runtime.

## 15. Future considerations

Scoped agent tokens; per-principal rate limits; side-by-side plugin
versions (would let pinned old versions keep running); out-of-process
plugin isolation behind `PluginRegistry._load` (the existing seam);
optional SSE notifications for execution progress once stateful sessions
are justified.
