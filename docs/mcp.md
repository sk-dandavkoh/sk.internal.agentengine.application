# MCP Control Plane

The engine can expose its application services to AI agents (Claude, Codex,
Hermes, any MCP client) over the [Model Context Protocol](https://modelcontextprotocol.io).
An authorized agent can discover node types, build and edit project drafts,
validate, publish, run and inspect executions, and, as an admin, write,
test, install and manage plugins.

MCP is **off by default**. It is one more interface over the same
application services as the REST API and the CLI. It holds no business
logic of its own (ADR 0006).

```text
MCP client ──HTTP POST /mcp──> McpEndpoint (bearer session → Identity)
                                 └─> official MCP SDK (stateless Streamable HTTP)
                                       └─> tool ──> AuthorizationService check
                                                └─> application services ──> domain
REST client ──/api/v1/...──> FastAPI routers ──────────┘
CLI ───────────────────────────────────────────────────┘
```

Code: `backend/src/akasha/mcp/` (`server.py` transport, authentication,
error and audit wrapper; `tools.py` projects/graph/executions/nodes;
`plugin_tools.py` plugins; `prompts.py` prompts and resources;
`authoring.py` the plugin SDK guide and scaffold).

---

## 1. Enable, disable, configure

| Variable | Default | Meaning |
|---|---|---|
| `AKASHA_MCP_ENABLED` | `false` | Serve MCP at `/mcp` of the API process. |
| `AKASHA_MCP_MAX_REQUEST_BYTES` | `4194304` | Cap on one MCP request body. Plugin sources and project packages travel inline. |
| `AKASHA_MCP_MAX_RESPONSE_BYTES` | `262144` | Cap on one tool result. Larger results are a `RESPONSE_TOO_LARGE` error asking for a smaller page. Minimum 1024. |
| `AKASHA_MCP_PLUGIN_CODE` | `false` | Let admins install, update and test plugin code over MCP. See §6. |

All four are read at startup. Changing them needs a restart; there is no
hot reload.

**Disabled** (the default): `create_app` adds no `/mcp` route, builds no MCP
server and starts no session manager. `/mcp` answers 404 like any unknown
path. The REST API, UI, workers and CLI are unaffected. The OpenAPI
document is byte-identical either way (tested).

**Enabled**: `/mcp` exists on the API's own host and port. In the Compose
stack the UI's nginx proxies `/mcp` to the API (`frontend/nginx.conf`), so
the endpoint is `http://<host>:8080/mcp`. Put TLS in front for anything but
loopback (docs/deployment.md).

```bash
# .env of the Compose stack
AKASHA_MCP_ENABLED=true
```

## 2. Transport

**Streamable HTTP, stateless, JSON responses** (`mcp` SDK 2.3,
`MCPServer.streamable_http_app(stateless_http=True, json_response=True)`),
mounted as a plain Starlette route inside the FastAPI app.

- Stateless: every POST is complete in itself. Any API process behind a
  load balancer can answer it, and no MCP session needs owning, expiring
  or cleaning up. Server-to-client notifications are not used.
- JSON responses, no SSE: works through any reverse proxy. nginx needs no
  upgrade headers; the shipped config turns buffering off anyway.
- Shutdown: the session manager runs inside the application lifespan and
  stops before the container shuts down, so in-flight calls finish while
  their services still exist.
- **stdio is not offered.** A stdio server would be a second process with
  its own plugin registry and database pool. Plugins installed there would
  be invisible to the API process until a restart. For stdio-only clients,
  use a stdio↔HTTP bridge (e.g. `mcp-remote`).
- The SDK's DNS-rebinding guard is off. It protects unauthenticated local
  servers from browser pages; this endpoint accepts only bearer tokens,
  which a browser page cannot attach to someone else's session.

## 3. Authentication

The engine's own accounts. There is no separate MCP account system and no
anonymous access. Agents use a **personal access token** (ADR 0007): a
long-lived, named credential that acts as one user, with that user's role.

1. Sign in to the UI as the user the agent should act as. A dedicated
   user per agent is best, with the least role that works.
2. **Settings → Access tokens → New token.** Give it a name (e.g.
   `claude-laptop`), an expiry (30/90/365 days or never), and your
   password. Copy the `akasha_pat_…` value: it is shown once. The same
   over REST:
   ```bash
   curl -s -X POST http://127.0.0.1:8080/api/v1/auth/tokens \
     -H "Authorization: Bearer <a login session token>" \
     -H 'Content-Type: application/json' \
     -d '{"name": "claude-laptop", "password": "<password>", "expires_in_days": 90}'
   ```
3. Put the token into the client's `Authorization: Bearer` header (§10).

A login session token (`POST /api/v1/auth/login`) works too, but it expires
after `AKASHA_SESSION_TTL_SECONDS` (default 12 h) and then needs a new login.

Rules:

- Every HTTP request is authenticated before the SDK sees it. No token, an
  unknown, expired or revoked token, or a `Basic` header gets 401 with
  `WWW-Authenticate: Bearer`. The browser session cookie is **not**
  accepted.
- Only the token's SHA-256 is stored. The UI and `GET /api/v1/auth/tokens`
  show its name, first characters, expiry and when it was last used
  (recorded at most hourly), never the value.
- Issuing a token needs the account password, so a stolen session or token
  cannot mint long-lived credentials.
- Revocation takes effect on the next request: **Revoke** in the UI
  (`DELETE /api/v1/auth/tokens/{id}`), `POST /api/v1/auth/logout` with the
  token itself, an admin password reset, or disabling the user (both end
  every token of that user). Changing your own password keeps your tokens:
  revoke them individually.
- At most 50 tokens per user.
- Recommendation: a dedicated user per agent, with the least role that
  works (`member` unless the agent manages plugins or credentials).
- Transport security: tokens are bearer secrets. Use HTTPS beyond
  loopback. Store the token only in the MCP client's secret config. Never
  put it in a URL.
- Without a database there are no sessions. `/mcp` then answers 500
  `CONFIGURATION_ERROR` for everyone: fail closed.

## 4. Authorization

Exactly the REST rules, from the same `AuthorizationService` and
`require_admin` the REST guards call:

| Scope | Rule |
|---|---|
| Projects, drafts, graph, triggers, versions, export, validate, publish, run | The project's owner, or an admin. A hidden project answers `PROJECT_NOT_FOUND`, not "forbidden". |
| Executions, events, logs, reports | Visible exactly when their project is (`EXECUTION_NOT_FOUND` otherwise). |
| Listings (projects, executions) | Filtered to the caller's projects; admins see all. |
| Node catalog, system_info, import | Any authenticated user. Imports are owned by the caller. |
| `credential_list`, every `plugin_*` tool | Admin only (`PERMISSION_DENIED`). |

There is no privileged service account: a tool runs as the token's user.

## 5. Tools

49 tools, each with a description written for an LLM, a typed input schema,
a typed output schema, and annotations (`readOnlyHint`, `destructiveHint`,
`idempotentHint`). Results reuse the REST DTOs wherever one fits.

| Group | Tools |
|---|---|
| Discovery | `system_info` (caller, role, capabilities, limits) |
| Nodes | `node_types_list` (filter `query`/`category`, paged, + port compatibility rules), `node_type_get` (ports, config schema, capabilities, owning plugin), `node_config_validate` |
| Projects | `project_list`, `project_get`, `project_create`, `project_update` (name/description), `project_delete` (never-published only) |
| Drafts | `project_draft_get`, `project_draft_update` (whole draft, like the editor's save) |
| Graph | `graph_get`, `graph_node_add`, `graph_node_update`, `graph_node_delete` (removes its edges), `graph_edge_add`, `graph_edge_delete` |
| Validation and publishing | `project_validate`, `project_publish`, `project_versions_list`, `project_version_get` |
| Executions | `execution_start` (latest published version by default), `execution_list`, `execution_get` (status, error, node states, metrics), `execution_node_get` (one node's outputs and failures), `execution_cancel` |
| Observability | `execution_events_get`, `execution_logs_get` (both cursor-paged by `after`), `execution_metrics_get`, `report_get` (the full redacted report: the same document REST exports) |
| Triggers | `trigger_list`, `trigger_create`, `trigger_update` (config and/or `enabled`: this is enable/disable), `trigger_delete`. Draft triggers take effect on publish. |
| Import/export | `project_export` (package as base64), `project_import` |
| Credentials | `credential_list` (names and kinds only) |
| Plugins | §6 |

Not exposed on purpose: credential create/update/delete (a secret value
would pass through the agent's context and the client's logs), user
management, and anything that runs the scheduler or workers.

### Lifecycle and revisions

```text
project_create ─> graph_node_add ─> graph_edge_add ─> project_validate
   ─> project_publish ─> execution_start ─> execution_get ─> execution_node_get
```

- Graph and draft tools always edit the **mutable draft**. Published
  versions are immutable. `execution_start` runs a published version, never
  the draft, and its result names the version.
- Every draft write takes `expected_revision` and returns the new
  `revision`. A stale revision is `CONCURRENT_MODIFICATION` with
  `retryable: true` and the current revision in `details`: re-read and
  retry. Each granular graph edit is one transaction
  (`ProjectService.edit_draft`) with the same compare-and-swap as REST
  saves, so concurrent agents, or an agent and a human editor, never
  silently overwrite each other (tested: 5 parallel edits of one revision,
  exactly 1 wins).
- `project_publish` takes an optional `expected_revision`. Pass the one
  you validated. A repeated publish with the same revision is a
  conflict, not a second version.
- Not idempotent: `project_create`, `project_import` and `execution_start`
  create a new resource on every call, like their REST counterparts.

### Results and errors

Results are structured JSON (`structuredContent`, mirrored as text). Errors
are tool results with `isError: true` and one shape:

```json
{"code": "PROJECT_VALIDATION_FAILED", "message": "...", "details": {"issues": [...]}, "retryable": false}
```

Codes are the engine's own (the same as the REST error envelope).
Messages and details are redacted. Unexpected failures are
`INTERNAL_ERROR` with no detail; the trace stays in the server log.
Malformed arguments (schema violations) and unknown tools are rejected by
the SDK before any tool runs.

### Output size

Lists are paged (`offset`/`limit`, max 100; events and logs by
`after` cursor). Summaries are the default (`project_get`, `execution_get`);
detail is separate (`project_draft_get`, `execution_node_get`, `report_get`).
Any result over `AKASHA_MCP_MAX_RESPONSE_BYTES` is refused with
`RESPONSE_TOO_LARGE` and its size, so the agent asks for less instead of
flooding its context.

## 6. Plugins

The plugin system is the existing one: `PluginRegistry` (manifest,
compatibility, installer, loader), the canonical `NodeRegistry`, and
`akasha.sdk`. MCP adds no second mechanism. REST and MCP share one
`PluginService` with one lifecycle lock per process.

| Tool | Runs plugin code? | Needs |
|---|---|---|
| `plugin_list`, `plugin_get` (incl. its documentation file) | no | admin |
| `plugin_sdk_get` (guide, manifest JSON Schema, versions, port types, error kinds) | no | admin |
| `plugin_create` (a complete starter package as files; stores nothing) | no | admin |
| `plugin_validate` (archive safety, manifest, engine/SDK/dependency compatibility, Python syntax and entry points by **parsing only**) | no | admin |
| `plugin_package` (deterministic ZIP, sha256) | no | admin |
| `plugin_test` (run one node once with the engine's checks; nothing installed) | **yes** | admin + `AKASHA_MCP_PLUGIN_CODE` |
| `plugin_install` | **yes** | admin + `AKASHA_MCP_PLUGIN_CODE` |
| `plugin_update` | **yes** | admin + `AKASHA_MCP_PLUGIN_CODE` |
| `plugin_enable`, `plugin_disable`, `plugin_remove` | only already-installed code | admin |

### Files, not filesystem

Plugin sources travel inside the call as `{"path": "utf-8 content"}`.
There is no server-side workspace, no path an agent can name on the host,
no shell. A package is always built with `pack_files` and opened by
`unpack_package`, the same checks an uploaded ZIP gets: relative paths
only, no `..`, no drive prefixes or backslashes, no symlinks, bounded
size and entry count. Installation copies into `AKASHA_PLUGINS_DIR/<id>`
through the registry, all or nothing.

### Trust model

Plugins are **trusted code running inside the engine process**. There is
no sandbox (architecture §34/§65); the SDK is an API boundary. An agent
writing a plugin does not make the plugin safe. Therefore:

- Validation never imports the package (tested with code that would
  create a file on import).
- Anything that runs or installs new code needs an admin **and** the
  operator's `AKASHA_MCP_PLUGIN_CODE=true`. With it on, an admin token is
  equivalent to shell access to the host as the engine's OS user, the
  same trade-off as `AKASHA_PLUGINS_HTTP_INSTALL` (ADR 0001). Turn it on
  only where the agent and its token are trusted to that degree, e.g. a
  development instance.
- `plugin_test` runs the package under a throwaway module name, so it
  never replaces an installed plugin's modules. Packages with
  `python_requirements` must be installed before they can be tested.

### Lifecycle states

`enabled`, `disabled`, `failed` (with an error code: `PLUGIN_INCOMPATIBLE`
is the "incompatible" state, `PLUGIN_LOAD_FAILED` a broken package).
"Installed" is any of these. Disabling unregisters the plugin's node types
from the catalog, so new validations and runs no longer find them.
Metadata stays readable. `enable` re-checks compatibility. Repeating a
transition is a conflict (`INVALID_STATE_TRANSITION`,
`PLUGIN_ALREADY_INSTALLED`), never a silent no-op.

### Update and removal

- `plugin_update` replaces an installed plugin with a new package of the
  same id, re-enabling it if it was enabled. It is atomic: if the new
  package fails to validate, copy or load, the old package is restored and
  re-activated (tested with a package that raises on import).
- Both update and removal are **dependency-aware**. While a project's
  draft or any published version uses the plugin, they answer
  `PLUGIN_IN_USE` with the project ids. `force: true` proceeds and returns
  the affected projects. Disabling is blocked while enabled plugins depend
  on it, and so is updating or removing such a plugin.

### Reproducibility: published versions pin plugin versions

Publishing records the exact version of every enabled plugin the draft
uses, through its node types or declared plugin dependencies, in the
version's `plugin_versions` (also on REST `ProjectVersionDetail`, stored
in `project_versions.plugin_versions`, migration 0014).

Before every run the runtime compares them with the enabled plugins. Any
other version, or a missing plugin, fails the run before a node executes
(`EXECUTION_GRAPH_INVALID`, issue `incompatible_dependency` /
`missing_dependency`). So:

```text
Project Version 17 ── pinned ──> acme@1.3.2
acme@2.0.0 installed (plugin_update force=true)
  → version 17 refuses to run; it never silently runs 2.0.0 code
  → publish version 18 → pinned to 2.0.0 → runs
  → reinstall 1.3.2 → version 17 runs again
```

Only one version of a plugin is installed at a time, so old versions stop
running until their plugin version is back. Running several versions side
by side is deferred (§11). Versions published before migration 0014 carry
no pins and behave as before.

## 7. Resources and prompts

Resources, static and read-only:

- `akasha://plugins/sdk-guide`: the plugin SDK guide (Markdown).
- `akasha://plugins/manifest-schema`: JSON Schema of `akasha-plugin.json`.

Live data (projects, executions, logs) is **not** a resource: tools carry
pagination, filtering and authorization arguments that resources cannot.

Prompts, guidance only: they name tools and embed no stored data.

- `build_workflow(goal)`: discover, build, validate, publish.
- `debug_execution(execution_id)`: diagnose a failed run.
- `create_plugin(capability)`: the plugin authoring loop.

## 8. Security notes

- **Prompt injection.** Everything users or workflows wrote is returned as
  data: names, descriptions, configs, logs, node outputs, LLM outputs,
  plugin metadata and documentation. The server's `instructions` (sent at
  initialize) and every prompt tell the agent to treat it as untrusted
  data. The server never interprets such content as instructions, but it
  cannot stop a client model from being influenced by it. Give agents the
  least role that works, keep `AKASHA_MCP_PLUGIN_CODE` off where plugins
  are not being developed, and review destructive actions in the client.
- **Secrets.** Configs may hold only `credential:<name>` references.
  Plaintext secrets in a draft are rejected with locations, never values
  (`PLAINTEXT_SECRET`). Error messages and details are redacted. Outputs,
  events, logs and reports are redacted like their REST counterparts.
  Credential values are never returned and cannot be set over MCP.
- **Limits.** Request and response caps (§1). Pagination caps. All engine
  resource limits apply unchanged (graph size and depth, active executions
  per project, timeouts, output sizes). Project packages are capped at
  16 MiB, plugin packages at 16 MiB compressed / 64 MiB unpacked.
- **SSRF and outbound network.** No tool fetches a URL. Nodes keep the
  engine's network policy.
- **Logging.** The SDK's loggers are capped at INFO, because its DEBUG
  lines can quote whole protocol frames. Audit lines carry ids, never
  argument values.

## 9. Observability and audit

Every tool call logs one structured line `mcp_tool_call`: `tool`,
`user_id`, `outcome` (`ok`/`error`), `error_code`, `duration_ms`, the
target ids among the arguments (`project_id`, `execution_id`,
`plugin_id`, `node_type`, `version`), and, from the request context,
`request_id`. The access log line (`http_request`, path `/mcp`) carries
the same `request_id` and `user_id`. Executions started over MCP use the
request id as their `correlation_id`. Plugin lifecycle steps additionally
log `plugin_installed`/`plugin_updated`/`plugin_enabled`/`plugin_disabled`/
`plugin_removed` with plugin id and version. The MCP client's identity
(`clientInfo`) is not logged: in stateless mode it is only known at
initialize, and it is self-declared anyway.

## 10. Client configuration

Claude Code:

```bash
claude mcp add --transport http akasha http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer akasha_pat_<token>"
```

Generic (`mcpServers` JSON, used by Claude Desktop via a bridge, Cursor,
and others):

```json
{
  "mcpServers": {
    "akasha": {
      "type": "http",
      "url": "https://akasha.example.internal/mcp",
      "headers": { "Authorization": "Bearer akasha_pat_<token>" }
    }
  }
}
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.akasha]
url = "https://akasha.example.internal/mcp"
bearer_token_env_var = "AKASHA_MCP_TOKEN"
```

Never commit the token. Revoke it in Settings → Access tokens when the
agent no longer needs it (§3).

**Tested clients**: the official MCP Python SDK client (`mcp` 2.3), over
Streamable HTTP, in the integration tests (in-process ASGI) and in the
opt-in Compose deployment test (real network, through nginx, executions
run by the dedicated worker container). Claude Code, Codex and Hermes
were not available in the development environment; their configuration
above follows their documented HTTP transport and is untested.

## 11. Limitations and deferred work

- Access tokens carry their user's full role: there are no per-token
  scopes (e.g. read-only). Use a dedicated, least-privileged user per
  agent. Deferred: token scopes; OAuth 2.1 for MCP's authorization flow.
- No MCP-specific rate limiting. The login throttle and the engine's
  resource limits apply. Rate-limit at the reverse proxy if needed.
- One installed version per plugin. Pinned older versions stop running
  after an update until reinstalled. Side-by-side versions are deferred.
- Only direct plugin use is pinned. A plugin's own plugin dependencies are
  not.
- Dedicated worker processes (`akasha worker`, the Compose `worker`
  service) load plugins at startup. A plugin installed or updated over MCP
  (or REST) reaches them after a worker restart. The in-process worker
  (`AKASHA_WORKER_ENABLED=true`) sees it immediately. This is the existing
  plugin limitation (ADR 0001), not specific to MCP.
- No stdio transport (§2).
- No idempotency keys on `execution_start`, `project_create` and
  `project_import`, the same as REST.
- `plugin_test` cannot test packages with `python_requirements` before
  install.

## 12. Troubleshooting

| Symptom | Cause |
|---|---|
| 404 on `/mcp` | `AKASHA_MCP_ENABLED` is not `true` in the API process, or the proxy does not forward `/mcp`. |
| 401 `AUTHENTICATION_REQUIRED` | Missing, mistyped, expired or revoked token, or the user was disabled or had their password reset; issue a new token. |
| 500 `CONFIGURATION_ERROR` on every request | No database configured: no sessions exist. |
| 413 | Request over `AKASHA_MCP_MAX_REQUEST_BYTES` (or `AKASHA_API_MAX_REQUEST_BYTES`). |
| `RESPONSE_TOO_LARGE` | Use `limit`, a filter, or a detail tool. |
| `PERMISSION_DENIED` on plugin or credential tools | The token's user is not an admin. |
| `PLUGIN_CODE_NOT_ALLOWED` | `AKASHA_MCP_PLUGIN_CODE` is off. |
| `CONCURRENT_MODIFICATION` | Someone else saved the draft; re-read (`graph_get`) and retry with the new revision. |
| A started execution stays `queued` | No queue/worker (`system_info.executions_run_automatically` is false). |
| A plugin node is missing in runs but present in `node_types_list` | A dedicated worker has not been restarted since the plugin changed (§11). |
| An old version fails with `incompatible_dependency` | It is pinned to a plugin version that is no longer installed (§6). |
