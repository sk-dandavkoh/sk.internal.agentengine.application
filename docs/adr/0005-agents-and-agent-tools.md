# ADR 0005: Agents and agent tools

Status: accepted (2026-10-05)

## Context

Users want to build agents like the ones in a standalone sales-automation
package: a language model that searches the web, reads pages, keeps notes in
files and occasionally runs a program. It calls tools several times until it
can answer. The engine already had the LLM domain types for tool calls
(`ToolCall`, `ToolDefinition`, tool turns, adapter mapping) and `core.llm`,
which *returns* tool calls without running them. It had `core.web_search`,
but no agent loop, no way to fetch a URL chosen at run time, no file access
and no process execution.

Architecture §16 says an agent is built from engine primitives, not a
separate runtime. §135/§136 say tool calls go through a tool registry to
existing nodes, without a second tool system. §64 says file access is
confined and validated. The architecture test forbids starting processes,
except for the plugin installer.

## Decision

1. **`core.agent` is a node.** One node attempt runs the loop
   (model → tool calls → tool turns → model …) through the existing
   `LLMClient`. The runtime's timeouts, cancellation, retries, credential
   scoping, redaction, persistence and observability apply unchanged. The
   loop is bounded by `max_iterations` and the node timeout. It shares
   prompt templates, model config and provider error mapping with
   `core.llm`.
2. **Tools are registered nodes.** The node registry's live catalog is the
   tool registry, so built-ins and plugin nodes are tools alike. The workflow
   author fixes each tool's node type and config. The model supplies only
   input-port values, offered as a JSON Schema derived from the port
   declarations. The model can therefore choose a search query, but never
   the host, the credential or the program. Tool calls are validated like
   runtime node calls. A tool resolves only the credential references in its
   own config. Nested agents and control-flow nodes are not tools, so agent
   depth is 1.
3. **Tool failures are feedback, not failure.** A tool's error code and
   redacted message go back to the model as a tool turn. Cancellation, the
   node deadline and provider errors end the attempt.
4. **New tool nodes**, each also usable on its own in a graph:
   - `core.web_fetch`: URL as input, the same guarded `fetch` as
     `core.http` (SSRF check on every redirect hop), readable text from
     HTML via the stdlib parser.
   - `core.file_read`/`core.file_write`/`core.file_list`: inside
     `AKASHA_WORKSPACE_DIR/<workflow_id>`, paths confined by
     `security.paths.Workspace`, UTF-8 text, size-capped.
   - `core.command`: runs one program the operator allowlists
     (`AKASHA_COMMAND_ALLOWED`, empty by default, so the node is off). It
     runs argv via `asyncio.create_subprocess_exec`, never a shell. The
     environment is minimal (no engine variables, so no database URL or
     key). The working directory is inside the workflow workspace. It has a
     time limit, an output cap with the rest drained, and its own process
     group, which is killed on timeout or cancellation. The architecture
     test now also detects asyncio process creation and allows it only
     here.

## Alternatives

- **Agent as a runtime construct (loop edges, sub-executions).** Each tool
  call would be visible as its own node execution, but loops, durable
  per-call state and recovery would have to be added to the scheduler.
  §16 rules that out for now. The `steps` output and the metrics cover
  observability.
- **A separate tool abstraction with its own schemas.** That is the
  duplicate infrastructure §136 forbids, and every node would need writing
  twice.
- **Model-chosen executables, or a shell.** Rejected: the model's output is
  untrusted, and prompt injection from fetched pages could become command
  injection.
- **Container sandbox for `core.command`.** This is the right long-term
  isolation (§159), but it is out of scope for V1. Until then the node is
  opt-in and documented as trusted.

## Consequences

- `core.command` is **not a sandbox**. An allowed program runs as the
  engine's OS user with that user's file and network access. Allowing an
  interpreter (`python3`, `bash`, `node`) or anything that executes its
  arguments (`find -exec`, `git -c core.sshCommand=…`) allows arbitrary code
  in every workflow. Operators must allowlist accordingly.
- A tool call is not a node execution: it is not persisted as its own
  record, cannot be retried on its own, and is not covered by recovery. An
  interrupted agent is not re-run blindly, because it declares
  `has_side_effects`. Tool metrics and events are not exported yet. The
  agent's own metrics count calls and errors.
- Tool node types are checked when the agent starts, before the first
  model call, not at publish. They are not recorded in
  `metadata.node_versions` or in workflow dependencies. The upgrade path is
  to let node definitions declare the node types they reference.
- Workspace files are not part of database backups. Back up the
  `workspace` volume separately.
- Tool calls run one after another, in model order. Parallel tool calls
  can come later if latency matters.
