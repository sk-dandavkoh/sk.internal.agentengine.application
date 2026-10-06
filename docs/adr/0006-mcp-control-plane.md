# ADR 0006: MCP control plane

Status: accepted (2026-10-06)

## Context

External AI agents (Claude, Codex, Hermes, other MCP clients) should be able
to operate the engine: discover node types, build and publish workflows, run
and inspect executions, and extend the engine with plugins. The Model
Context Protocol is the common interface these agents speak. The REST API
already exposes the same operations to the UI. The application services
behind it are HTTP-free (backend AGENTS §8/§45).

Forces:

- MCP must not become a second business-logic implementation, nor bypass
  authentication, authorization or the draft → publish → version lifecycle.
- It must be optional and fail closed.
- Agents are LLMs: they need explicit, well-described, typed tools,
  structured errors, bounded results, and they retry and run concurrently.
- Plugins are executable code in the engine process (no sandbox, §34/§65).
- A published version must not change behavior when a plugin is upgraded.
  Before this ADR only a declared constraint (`>=1.0`) was checked, and
  only at validate/publish time.

## Decision

1. **MCP is an interface module**, `akasha.mcp`, parallel to `akasha.api`
   and the CLI. Tools call application services (`WorkflowService`,
   `ExecutionService`, `TriggerService`, `ReportService`,
   `WorkflowPackageService`, `PluginService`, `NodeRegistry`) and the same
   `AuthorizationService`/`require_admin` checks the REST guards use. They
   reuse REST DTOs as results wherever one fits. MCP never touches
   repositories or SQL, and never calls the REST API over HTTP.
2. **Official SDK, Streamable HTTP, stateless, JSON responses**, mounted
   at `/mcp` inside the API's FastAPI app as a plain route, so it is not in
   the OpenAPI document. Stateless means no MCP session state, which works
   with any number of API processes and any reverse proxy. **No stdio**: a
   separate process would have its own plugin registry, out of sync with
   the API's.
3. **Off by default** (`AKASHA_MCP_ENABLED=false`): no route, no server, no
   session manager. Settings are startup-only.
4. **Authentication = existing session tokens** (`Authorization: Bearer`),
   checked in the endpoint before the SDK parses anything. No cookies, no
   anonymous access, no service account. The SDK's OAuth resource-server
   mode was rejected: there is no authorization server, and inventing one
   would rewrite authentication (out of scope). Long-lived scoped agent
   tokens are deferred.
5. **Explicit tools, not a generic `manage(action=…)`**. 49 tools, typed in
   and out, with annotations. One error shape: `{code, message, details,
   retryable}` with the engine's error codes, redacted. Results are capped
   (`AKASHA_MCP_MAX_RESPONSE_BYTES`); lists are paged.
6. **Concurrency through the existing revision CAS.** Granular graph edits
   go through the new `WorkflowService.edit_draft` (read, apply one domain
   `Graph`/draft method, `replace_draft`, save with `expected_revision`, all
   in one transaction). Plugin lifecycle calls from REST and MCP share one
   lock in the new `application.plugins.PluginService`. It moved out of
   the REST router.
7. **Plugins through the existing system, behind a second opt-in.**
   Sources travel as in-call file maps, packed and unpacked with the
   existing archive rules. There is no server workspace and no filesystem
   or shell tool. Static validation parses but never imports. Installing,
   updating or test-running code needs an admin **and**
   `AKASHA_MCP_PLUGIN_CODE=true`. New in the registry: atomic `update`
   (restores the old package on any failure) and `load_unregistered` (test
   import under a throwaway module name). Update and remove are refused
   while workflows use the plugin unless forced.
8. **Published versions pin plugin versions.** Publish records
   `plugin_versions` (id → exact version of each enabled plugin the draft
   uses) on the `WorkflowVersion` (migration 0014; also returned by REST).
   The runtime refuses to run a version whose pinned plugins are not
   enabled in exactly those versions (`EXECUTION_GRAPH_INVALID`). This
   lives in the domain, application and runtime layers, not in MCP.

## Alternatives

- *MCP as a thin proxy to the REST API*: double serialization, a second
  auth hop, and request/response shapes designed for a UI rather than for
  agents. Rejected; direct service calls are what the CLI already does.
- *A separate MCP process / stdio server*: its own plugin registry and
  pools, so its plugin state would be invisible to the API until restart.
  Rejected for V1.
- *A generic resource-oriented tool* (`manage(action, resource, …)`):
  weak schemas, poor discoverability for models. Rejected.
- *Sandboxed plugin execution*: there is no isolation mechanism. Claiming
  one would be false. The opt-in plus admin requirement makes the trust
  decision explicit instead.
- *Side-by-side plugin versions* for exact reproducibility of old versions:
  needs per-version module namespaces, dependency directories and catalog
  entries. Deferred. Pinning plus fail-closed already guarantees no silent
  behavior change.

## Consequences

- Agents get the same capabilities and the same limits as a user with the
  same role. Hidden workflows are hidden, admin tools are admin-only.
- With `AKASHA_MCP_PLUGIN_CODE=true` an admin token is as powerful as
  shell access on the host. Documented; off by default.
- After a forced plugin update, versions pinned to the old plugin version
  stop running (a visible, structured failure) until they are republished
  or the old plugin version is reinstalled.
- REST `WorkflowVersionDetail` gains `plugin_versions` (additive; the
  OpenAPI snapshot and frontend schemas are updated).
- Dedicated worker processes still pick up plugin changes only on restart
  (ADR 0001); unchanged.
