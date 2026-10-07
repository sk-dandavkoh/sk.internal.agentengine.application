# ADR 0011: Built-in nodes have no special rules

Status: accepted (2026-10-07)

## Context

Plugin nodes were constructed without arguments and saw only the SDK.
Built-in nodes got their dependencies through constructor factories in
`nodes/builtins.py` (LLM client, network policy, workspace, command
allowlist, size caps) and imported engine internals (`akasha.domain.ai`,
`akasha.security.*`, `akasha.errors`). A plugin could therefore not
rebuild core.llm, core.agent or core.http properly: it had no LLM client,
no network policy, no workspace and no operator settings.

Nodes also leaned on each other in odd places: transform, human and
browser imported template rendering from the LLM node, so a missing
variable in a transform reported `PROMPT_VARIABLE_MISSING`, and the
browser imported `fetch` from the HTTP node's module.

## Decision

1. **Dependencies come through the context.** `NodeExecutionContext`
   gains `services: NodeServices` (`llm`, `network`, `workspace_dir`,
   `nodes` catalog, `settings` per plugin id, `owners`) and `settings`,
   the operator settings of the plugin that provides the node (`core` for
   the built-ins). Ownership comes from the plugin registry, not from the
   node id prefix: plugin ids may contain dots. The runtime and the node
   tester hand the same services to every node; the runtime sets `nodes`
   to its catalog. The contract holds only value objects
   (`NetworkPolicy`, a directory path); the guards that act on them
   (`check_destination`, `Workspace`) stay in `akasha.security` and reach
   nodes through the SDK.
2. **Nodes are constructed without arguments.** `NodeRegistry.register`
   takes a class only and fails at startup if it needs arguments.
   `create_builtin_registry()` registers 23 classes, exactly as a plugin
   registers its own.
3. **Typed, validated operator settings.** Built-ins read a typed
   `CoreSettings` (`nodes/settings.py`), filled by the composition root
   from the existing `AKASHA_*` settings; a misspelt key is a `TypeError`,
   never a silent default. Plugins get theirs from
   `AKASHA_PLUGIN_SETTINGS='{"acme": {...}}'` (by plugin id; `core` is
   rejected), checked against the `settings_schema` their manifest
   declares when the plugin is activated: settings that do not match, or
   settings for a plugin without a schema, leave it FAILED with
   `PLUGIN_SETTINGS_INVALID`. Settings for a plugin that is not installed
   are logged (`plugin_settings_unused`). Settings carry no secrets
   (credentials do).
4. **The SDK is the whole surface.** SDK 1.3.0 additionally exports the
   LLM types, `NetworkPolicy`/`check_destination`, `Workspace`, redaction
   helpers, `credential_references`, `json_equal`, `NodeFactory`,
   `NO_DEFAULT` and `NodeServices`. `tests/test_architecture.py` asserts
   that `akasha/nodes/*` (except the registry) imports only `akasha.sdk`
   and its own package.
5. **Shared node helpers live with the nodes.** Template rendering moved
   to `nodes/core.py` with neutral codes `TEMPLATE_INVALID` and
   `TEMPLATE_VARIABLE_MISSING` (also for core.llm; no installation
   existed yet). `fetch` moved to `nodes/fetching.py`.
6. core.web_search gets a `timeout_seconds` config (default 15 s, as the
   former constructor default).

## Alternatives

- **Keep constructor injection, pass services to plugin factories.**
  Plugins would need a factory protocol; the runtime already builds a
  context per attempt, so a second channel adds nothing.
- **Load the built-ins through the plugin loader.** Proves the same
  thing at higher cost (manifest, packaging); the import rule plus
  argument-free registration already guarantees it.

## Consequences

- Any core node could be copied into a plugin and work unchanged.
- The network policy and workspace apply to plugin nodes too, when they
  use them.
- Plugins can read every plugin's settings through
  `context.services.settings`: they are trusted code in the engine
  process anyway (architecture §34), and settings hold no secrets.
- Breaking: the template error codes were renamed; `NodeRegistry.register`
  no longer takes a factory.
