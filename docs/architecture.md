
# Architecture

## 1. Purpose

This document defines the technical architecture of the Agent Orchestration Engine.

It describes:

- system boundaries
- architectural layers
- core domain concepts
- backend architecture
- frontend architecture
- execution architecture
- persistence
- APIs
- events
- plugins
- Python Nodes
- security boundaries
- deployment
- scalability
- testing boundaries
- architectural constraints

The architecture must implement the principles defined in `/docs/vision.md`.

The architecture is intentionally designed so that the system can start as a single self-hosted installation while preserving clear boundaries for future scaling.

---

# 2. Architectural North Star

The fundamental execution chain is:

```text
Project
   ↓
Project Version
   ↓
Graph
   ↓
Nodes
   ↓
Execution
   ↓
Node Executions
   ↓
Events
   ↓
Logs / Metrics / Traces / Reports
```

The fundamental product relationship is:

```text
Engine
 ├── Projects
 │    ├── Draft
 │    └── Published Versions
 │         └── Graph
 │              └── Nodes
 │
 ├── Node Registry
 │    ├── Built-in Nodes
 │    ├── Python Nodes
 │    └── Plugins
 │
 ├── Execution Runtime
 │    └── Executions
 │
 ├── Trigger System
 │
 ├── Event System
 │
 ├── Persistence
 │
 └── API
```

The Engine is the product.

Projects are executable artifacts managed by the Engine.

----------

# 3. Core Architectural Principles

## 3.1 Backend Is the Source of Truth

The backend is authoritative for:

-   Projects
    
-   Project Versions
    
-   Graph validity
    
-   Node definitions
    
-   Node configuration
    
-   Credentials
    
-   Executions
    
-   Execution state
    
-   Permissions
    
-   Events
    
-   Reports
    
-   Plugin state
    

The frontend must never be treated as authoritative.

Frontend validation improves UX but does not replace backend validation.

----------

## 3.2 Domain Logic Must Not Depend on the Frontend

The backend must remain fully functional without the React application.

Every important operation must be available through backend services and/or the API.

Examples:

```text
Create Project
Validate Project
Publish Project
Run Project
Cancel Execution
Inspect Execution
Import Project
Export Project
Install Plugin
Register Node
```

The CLI should eventually consume the same API/application services as the frontend where practical.

----------

## 3.3 Domain Logic Must Not Depend on Infrastructure

Core domain logic must not directly depend on:

-   PostgreSQL
    
-   Redis
    
-   HTTP
    
-   WebSockets
    
-   React
    
-   Docker
    
-   filesystem layout
    
-   specific LLM providers
    

Infrastructure-specific behavior belongs behind interfaces.

Example:

```text
Domain
   ↓
Application Interface
   ↓
Infrastructure Adapter
   ↓
PostgreSQL / Redis / Filesystem / Provider
```

----------

## 3.4 Provider Agnostic

The engine must not fundamentally depend on one AI provider.

The architecture must support:

```text
OpenAI
Anthropic
Ollama
vLLM
other OpenAI-compatible providers
future providers
```

Provider abstraction belongs in the AI infrastructure/application layer.

The domain should operate on provider-independent concepts such as:

```text
Model
Message
Prompt
Tool
Token Usage
Completion
Embedding
Provider Error
```

----------

## 3.5 Schema Driven

Node configuration and ports must be represented by machine-readable schemas.

The same schema should be usable by:

-   backend validation
    
-   frontend forms
    
-   documentation
    
-   CLI validation
    
-   plugin validation
    
-   project import/export
    
-   runtime validation
    

The frontend should not duplicate Node configuration definitions manually.

----------

## 3.6 Explicit State

Important state transitions must be explicit.

Avoid implicit state encoded through arbitrary flags.

Examples:

```text
Execution:
CREATED
QUEUED
RUNNING
PAUSED
WAITING
COMPLETED
FAILED
CANCELLED
TIMEOUT
```

Node execution:

```text
PENDING
READY
RUNNING
COMPLETED
FAILED
SKIPPED
CANCELLED
```

State transitions must be validated.

The authoritative tables live in `backend/src/akasha/domain/execution.py`
(`EXECUTION_ACTIONS`, `NODE_ACTIONS`); transitions are keyed by named action
(`start`, `resume`, `retry`, ...), not by target state, because different
actions share a target with different legal sources. Rules (Phase 07):

- Terminal states (`COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT`; node:
  `COMPLETED`, `FAILED`, `SKIPPED`, `CANCELLED`) have no exits.
- `Execution` is the aggregate root; node transitions go through it. Nodes
  start only while the execution is `RUNNING`; in-flight nodes may still
  finish while `PAUSED`/`WAITING` or after a cancellation request.
- `COMPLETED` requires every node `COMPLETED` or `SKIPPED`. Entering any
  terminal state cancels every unfinished node.
- Retry reuses the NodeExecution (`RUNNING → READY`), only for retryable
  error kinds, within `RetryPolicy.max_attempts` (default 1 = no retry),
  never after a cancellation request; the next start is rejected before the
  recorded backoff elapses. A node that already ran cannot be skipped.
- Timeout: the execution deadline counts from first start, including paused
  and waiting time; `TIMEOUT` is only reachable once started. A node timeout
  is a `TIMEOUT`-kind error and follows the retry policy.
- Cancellation is cooperative: `request_cancellation` records a sticky,
  idempotent `CancellationState`; afterwards the only legal execution
  transition is `cancel`, so a cancelled run can never report success.

----------

## 3.7 Immutable Published Versions

A Project Draft is mutable.

A published Project Version is immutable.

Execution always references a specific Project Version.

```text
Project
  └── Draft
        ↓ publish
  Project Version 1
        ↓
  Execution A

Project Version 2
        ↓
  Execution B
```

Changing the Draft must never change an existing execution.

----------

## 3.8 Explicit Dependencies

Projects must declare dependencies where applicable.

Examples:

```text
Engine version
Node versions
Plugin versions
Project schema version
SDK version
```

Dependencies must be validated during:

-   import
    
-   publish
    
-   execution
    
-   upgrade
    

----------

# 4. High-Level System Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                         Frontend                             │
│                                                              │
│  React / Vite / Tailwind                                     │
│  ├── Project Management                                      │
│  ├── Graph Editor                                            │
│  ├── Node Library                                            │
│  ├── Node Inspector                                          │
│  ├── Execution Inspector                                     │
│  ├── Monitoring                                              │
│  ├── Reports                                                 │
│  ├── Credentials                                             │
│  └── Settings                                                │
└────────────────────────────┬─────────────────────────────────┘
                             │
                    REST / WebSocket
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                       Backend API                            │
│                                                              │
│  Authentication                                              │
│  Request Validation                                           │
│  API Schemas                                                 │
│  Error Mapping                                               │
│  WebSocket Gateway                                           │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                    Application Layer                          │
│                                                              │
│  Project Services                                            │
│  Execution Services                                          │
│  Trigger Services                                            │
│  Node Services                                               │
│  Plugin Services                                             │
│  Credential Services                                         │
│  Report Services                                             │
│  Import / Export Services                                    │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                       Domain Layer                            │
│                                                              │
│  Project                                                     │
│  Project Version                                             │
│  Graph                                                        │
│  Node                                                         │
│  Node Definition                                              │
│  Execution                                                    │
│  Node Execution                                               │
│  Trigger                                                      │
│  Event                                                        │
│  Credential Reference                                         │
│  Plugin                                                       │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                    Runtime / Services                         │
│                                                              │
│  Graph Runtime                                                │
│  Scheduler                                                    │
│  Node Executor                                                │
│  Expression Engine                                            │
│  Prompt Engine                                                │
│  AI Service                                                   │
│  Event Bus                                                    │
│  Observability                                                │
│  Artifact Service                                             │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                     Infrastructure                            │
│                                                              │
│  PostgreSQL                                                   │
│  Redis                                                        │
│  Filesystem / Object Storage                                 │
│  LLM Providers                                                │
│  SMTP                                                         │
│  Web Search                                                   │
│  HTTP                                                         │
│  Plugin Runtime                                               │
└──────────────────────────────────────────────────────────────┘
```

----------

# 5. Backend Architecture

The backend uses a layered architecture.

Recommended structure:

```text
backend/
├── app/
│   ├── api/
│   ├── application/
│   ├── domain/
│   ├── infrastructure/
│   ├── runtime/
│   ├── plugins/
│   ├── nodes/
│   ├── providers/
│   ├── observability/
│   ├── security/
│   ├── workers/
│   └── main.py
│
├── tests/
├── migrations/
└── pyproject.toml
```

The exact filesystem structure may evolve.

The architectural boundaries must remain stable even if files or packages are reorganized.

----------

# 6. Domain Layer

The domain layer contains concepts that define what the Engine is.

It must not contain:

-   FastAPI-specific code
    
-   SQLAlchemy models
    
-   Redis clients
    
-   HTTP clients
    
-   React concepts
    
-   filesystem implementation details
    
-   provider SDK implementation details
    

----------

## 6.1 Project

A Project represents a user-defined automation or agent system.

Conceptually:

```text
Project
├── id
├── name
├── description
├── metadata
├── draft
├── versions
├── triggers
├── dependencies
└── timestamps
```

A Project is the long-lived identity.

A Project Version is the immutable executable snapshot.

----------

## 6.2 Project Draft

The Draft represents the currently editable state.

The Draft may be:

```text
invalid
valid
modified
unpublished
```

A Draft is not executable as a production version unless the execution system explicitly supports draft execution.

The preferred default is:

```text
Draft
  ↓ validate
Valid Draft
  ↓ publish
Immutable Version
  ↓ execute
Execution
```

----------

## 6.3 Project Version

A Project Version is immutable.

It contains the complete configuration required to reproduce the graph definition.

It should include:

```text
project_id
version
schema_version
engine_compatibility
nodes
edges
triggers
dependencies
metadata
created_at
```

The runtime must never silently resolve mutable Draft state while executing a published version.

----------

# 7. Graph Model

A Project graph consists of:

```text
Nodes
Edges
```

An edge connects:

```text
source node + output port
            ↓
target node + input port
```

Example:

```text
[LLM]
  output: result
       ↓
[Web Search]
  input: query
```

----------

## 7.1 Node Identity

Every node instance has a unique identifier within a Project Version.

Example:

```text
node_01HXYZ...
```

Node IDs must be stable within the version.

----------

## 7.2 Node Definition vs Node Instance

These concepts must remain separate.

### Node Definition

Defines what a node type can do.

Example:

```text
web.search
llm.chat
smtp.send
control.if
```

### Node Instance

Represents one configured occurrence inside a project.

```text
Node Definition:
    web.search

Node Instance:
    id = node_123
    type = web.search
    config = {...}
```

----------

# 8. Node Architecture

Nodes are the primary extension mechanism of the Engine.

Every Node must implement a defined contract.

Conceptually:

```text
Node Contract
├── identity
├── metadata
├── version
├── configuration schema
├── input ports
├── output ports
├── validation
├── execution
├── documentation
└── capabilities
```

----------

## 8.1 Node Categories

The architecture supports categories such as:

```text
AI
Agent
Input
Output
Communication
Web
HTTP
Data
File
Database
Control Flow
Utility
Trigger
Observability
Human Interaction
```

Categories are organizational concepts.

They must not create unnecessary coupling in the runtime.

----------

# 9. Node Execution Contract

A Node receives an execution context.

Conceptually:

```python
result = await node.execute(context)
```

The context may provide:

```text
node configuration
input values
execution ID
project ID
project version
logger
metrics
credentials
event publisher
artifact service
cancellation state
runtime metadata
```

A Node must not directly control the global execution lifecycle.

For example, a Node must not directly mark the entire execution as completed.

It may return:

```text
success
failure
retryable error
waiting state
```

The runtime remains responsible for execution state.

----------

# 10. Execution Runtime

The Execution Runtime is the core of the Engine.

Its responsibilities include:

1.  load Project Version
    
2.  validate executable graph
    
3.  create Execution
    
4.  resolve trigger input
    
5.  schedule ready nodes
    
6.  execute nodes
    
7.  propagate outputs
    
8.  resolve dependencies
    
9.  handle control flow
    
10.  handle retries
    
11.  handle timeouts
    
12.  handle cancellation
    
13.  persist state
    
14.  emit events
    
15.  collect observability data
    
16.  finalize execution
    

----------

# 11. Runtime Architecture

```text
Execution Service
       │
       ▼
Execution Runtime
       │
       ├── Graph Loader
       ├── Graph Validator
       ├── Scheduler
       ├── Dependency Resolver
       ├── Node Executor
       ├── Retry Manager
       ├── Timeout Manager
       ├── Cancellation Manager
       ├── State Manager
       └── Event Publisher
```

Implementation (Phase 08, `backend/src/akasha/runtime/`): `plan.py` is the
Graph Loader, Graph Validator integration and Dependency Resolver;
`executor.py` is the Node Executor; `runtime.py` holds the Execution
Runtime, Scheduler loop and State Manager. Retry, Timeout and Cancellation
handling live in the scheduler loop; the Event Publisher is Phase 11.
Runtime rules:

- Only `QUEUED` executions run. The pinned version is re-validated against
  the runtime's node catalog before start; an invalid snapshot fails the
  execution (`EXECUTION_GRAPH_INVALID`) without starting any node.
- One scheduler coroutine owns the aggregate and is its only writer; node
  attempts are tasks that only return an outcome. No node task outlives a
  run.
- A node is decided once every upstream node is decided (`COMPLETED` or
  `SKIPPED`). An edge is *live* when its source completed and produced the
  edge's port, *dead* when the source was skipped or omitted the port. The
  node becomes `READY` if at least one incoming edge is live and every
  required input is fed by a live edge; otherwise `SKIPPED`, which cascades
  downstream. Only live edges feed inputs. Ready nodes start in node-id
  order, at most `max_concurrency` at a time. `RUNNING` is persisted before
  node code can run. The loop never inspects node types (see §14).
- The pinned version is re-validated against the runtime catalog including
  declared node-version dependencies; the resolved versions are recorded in
  `metadata.node_versions` when the execution starts.
- Inputs are resolved from upstream outputs and deep-copied per consumer; a
  `multiple` input collects one value per edge in graph edge order.
- Failure is fail-fast: the first failure (node-id order) stops in-flight
  siblings and fails the execution with that node's error. Precedence when
  several apply at once: cancellation, execution timeout, node failure.
- Stopped attempts receive `CancelledError` and see `is_cancelled()`; an
  attempt that finishes anyway is recorded truthfully. A node timeout is a
  `NODE_TIMEOUT` failure; the execution timeout ends in `TIMEOUT`.
- Every save is compare-and-swap on the execution revision: a second writer
  (another runtime, an out-of-process cancel) makes one side lose.
- A cancelled or crashed run (outer task cancellation) leaves the execution
  `RUNNING`; recovery is Phase 23. Retries are not executed yet: an
  execution with `max_attempts > 1` is refused.

----------

# 12. Scheduler

The scheduler determines which nodes are ready to execute.

A node is ready when:

```text
required dependencies are satisfied
AND
control-flow conditions permit execution
AND
node is not already completed
AND
execution is not cancelled
```

The scheduler must not execute a node before its dependencies are satisfied.

----------

# 13. Parallel Execution

Independent nodes may execute concurrently.

Example:

```text
             ┌── [Search A] ──┐
[Input] ─────┤                 ├── [Merge]
             └── [Search B] ──┘
```

Search A and Search B can execute concurrently.

The runtime must control concurrency through configurable limits.

Concurrency must not be implemented through unrestricted task creation.

----------

# 14. Control Flow

Control-flow nodes are interpreted by the runtime as regular Nodes with special runtime semantics where necessary.

Supported concepts include:

```text
If
Switch
Loop
For Each
Parallel
Merge
Delay
Retry
Try
Catch
Subgraph
```

The architecture must prevent control-flow behavior from becoming hard-coded throughout the runtime.

Prefer explicit interfaces and capabilities.

Implementation (Phase 09): branching is one generic capability. A node
declaring `capabilities.is_control_flow` may omit declared outputs; an
omitted output is an inactive branch whose consumers the runtime skips
(readiness rule in §11). `core.if` and `core.switch` route their input to
exactly one port; `core.merge` joins whatever branches are live. Conditions
are declarative config (operator + operand, JSON equality), not
expressions. Loops/ForEach, Try/Catch and subgraphs are not implemented;
graph validation still rejects every cycle.

----------

# 15. Subgraphs

A Subgraph is a reusable graph executed within another graph.

Conceptually:

```text
Main Graph
    │
    ├── Node A
    │
    ├── Subgraph
    │      ├── Node B
    │      ├── Node C
    │      └── Node D
    │
    └── Node E
```

Subgraphs should have explicit input and output contracts.

They must not rely on arbitrary access to the parent execution context.

----------

# 16. Agents

An Agent is a specialized execution abstraction built using existing Engine primitives.

Conceptually:

```text
Agent
 ├── Instructions
 ├── Model
 ├── Tools
 ├── Input
 ├── Output
 └── optional internal graph
```

The Engine should not make "Agent" a fundamentally separate runtime architecture.

Instead:

```text
Agent
    ↓
Node / Subgraph
    ↓
Execution Runtime
```

This keeps the system composable.

----------

# 17. Triggers

Triggers start executions.

Supported trigger types include:

```text
Manual
Cron
Interval
Webhook
Event
API
```

Triggers must produce a normalized trigger context.

Example:

```text
TriggerContext
├── trigger_type
├── timestamp
├── payload
├── headers
├── source
└── metadata
```

The runtime must not depend on one specific trigger mechanism.

----------

# 18. Event Architecture

Events are used for:

-   execution state updates
    
-   node lifecycle updates
    
-   monitoring
    
-   WebSocket updates
    
-   audit logging
    
-   integrations
    
-   future event-driven triggers
    

Example:

```text
execution.created
execution.started
node.started
node.completed
node.failed
execution.completed
execution.failed
execution.cancelled
```

Events should contain:

```text
event_id
event_type
timestamp
execution_id
project_id
project_version_id
node_id
payload
```

Events should be immutable.

----------

# 19. Event Flow

```text
Runtime
   │
   ▼
Event Publisher
   │
   ├── Persistence
   ├── WebSocket Gateway
   ├── Metrics
   ├── Audit
   └── Future Event Consumers
```

The runtime should publish domain/application events rather than directly manipulating frontend connections.

Implementation (Phase 11):

- `domain/event.py`: immutable `Event`, `EventType`. Payloads are closed
  per type and scalar-only (ids, statuses, error codes, attempt), so node
  inputs/outputs, config, error messages and cancellation reasons can
  structurally never enter an event. `schema_version` versions payloads.
- The `Execution` aggregate records its own lifecycle events inside the
  transition methods (`pending_events` / `pull_events`); no caller can
  change state without the event. `project.published` is recorded by
  `ProjectService.publish_project`; `project.exported` and
  `project.imported` (audit, Phase 18) by `ProjectPackageService`.
- Persistence: `EventRepository` (`events` table, append-only, idempotent
  on `event_id`). Events are appended in the *same transaction* as the
  state change they describe: never state without events, never events
  without state. `position` totally orders the events of one execution or
  project; across unrelated streams it only approximates commit order.
- Delivery: `EventPublisher` fans out in-process *after* commit,
  at-most-once, with per-subscriber error and timeout isolation. A broken
  consumer can never fail or roll back a use case. Consumers that must not
  miss events catch up from `EventRepository.list(after=position)` and
  deduplicate by `event_id`.
- Canonical state stays the `executions`/`node_executions` rows; the
  lifecycle is reconstructable from persisted events plus that state.
- Phase 16 adds `node.emitted` (payload `name`, `attempt`): a node's own
  event, declared in `NodeDefinition.events` and emitted through the SDK
  (`context.events`). The runtime records it before the attempt's outcome
  (also for failed/cancelled attempts). Name-only, so the closed-payload
  rule holds. It is not a terminal event for the lifecycle metrics.

----------

# 20. Application Layer

The application layer coordinates use cases.

Examples:

```text
CreateProject
UpdateProjectDraft
ValidateProject
PublishProject
RunProject
CancelExecution
RetryExecution
ImportProject
ExportProject
InstallPlugin
RegisterNode
CreateCredential
GenerateReport
```

Application services orchestrate domain objects and infrastructure interfaces.

They should remain independent of HTTP.

----------

# 21. API Layer

The API exposes application functionality to external clients.

Recommended stack:

```text
FastAPI
Pydantic
WebSocket
```

The API layer is responsible for:

-   authentication
    
-   request parsing
    
-   request validation
    
-   authorization checks
    
-   invoking application services
    
-   response serialization
    
-   HTTP error mapping
    

The API layer must not contain core business logic.

----------

# 22. API Versioning

Public API endpoints must be versioned.

Example:

```text
/api/v1/projects
/api/v1/nodes
/api/v1/executions
/api/v1/triggers
/api/v1/credentials
/api/v1/plugins
/api/v1/reports
```

Breaking API changes require a new API version.

----------

# 23. WebSocket Architecture

WebSockets provide live execution updates.

Example:

```text
Frontend
   │
   │ WebSocket
   ▼
WebSocket Gateway
   │
   ▼
Event Stream
   │
   ▼
Execution Events
```

WebSocket messages should use stable event schemas.

Example:

```json
{
  "type": "node.completed",
  "execution_id": "...",
  "node_id": "...",
  "timestamp": "...",
  "data": {}
}
```

The frontend must be able to recover from a dropped connection by requesting the current persisted state.

WebSocket state must never be the only source of truth.

----------

# 24. Persistence Architecture

The preferred production persistence stack is:

```text
PostgreSQL
Redis
Persistent File/Object Storage
```

----------

## 24.1 PostgreSQL

PostgreSQL stores durable structured state.

Examples:

```text
projects
project_versions
nodes
triggers
executions
node_executions
events
metrics
credentials_metadata
plugins
reports
users
audit_entries
```

Database schema must be versioned through migrations.

----------

## 24.2 Redis

Redis is used for ephemeral or coordination-oriented state.

Examples:

```text
queues
locks
pub/sub
temporary execution state
rate limiting
cache
```

Redis must not become the only durable source for critical execution state.

----------

## 24.3 File/Object Storage

Large or binary data should not unnecessarily be stored directly in PostgreSQL.

Examples:

```text
uploaded files
generated files
reports
artifacts
large execution outputs
project assets
```

The storage implementation must be abstracted.

Supported implementations may include:

```text
local filesystem
S3-compatible storage
future providers
```

----------

# 25. Repository Pattern

Infrastructure repositories should hide persistence details from the application/domain layer.

Example:

```python
class ProjectRepository:
    async def get(...)
    async def save(...)
    async def delete(...)
```

The application layer depends on the interface.

Infrastructure provides the implementation.

Example:

```text
ProjectRepository
      ↑
      │ interface
      │
PostgresProjectRepository
```

----------

# 26. Transaction Boundaries

Transactions must be explicit around state transitions.

Examples:

```text
Publish Project Version
Create Execution
Transition Execution State
Persist Node Result
Record Critical Event
```

The system must avoid partially persisted state where possible.

Distributed transactions should be avoided.

----------

# 27. AI Architecture

AI execution should follow this abstraction:

```text
LLM Node
   ↓
AI Application Service
   ↓
Provider Abstraction
   ↓
Provider Adapter
   ↓
External LLM
```

A provider abstraction may use a library such as LiteLLM.

However:

```text
LiteLLM != Domain Model
```

The Engine's domain model must remain independent of the chosen provider library.

----------

# 28. LLM Request Model

The internal representation should support:

```text
model
provider
messages
system prompt
temperature
max tokens
structured output
tools
streaming
metadata
```

Responses should normalize:

```text
content
tool calls
usage
finish reason
provider metadata
raw response reference where appropriate
```

----------

# 29. Token and Cost Tracking

LLM executions should record:

```text
provider
model
input tokens
output tokens
total tokens
estimated cost
currency
```

Cost estimates must be explicitly marked as estimates.

The runtime must not claim exact billing information unless the provider supplies authoritative billing data.

----------

# 30. Prompt Architecture

Prompts should be represented as structured configuration.

Example:

```text
Prompt
├── system
├── user
├── variables
├── template
└── metadata
```

Variables may reference execution data:

```text
{{ lead.name }}
{{ lead.company }}
{{ research.summary }}
```

Prompt rendering must happen through a controlled expression/template system.

Arbitrary Python execution is forbidden.

Implementation (Phase 15, `akasha.nodes.llm`, node type `core.llm`):

- Config: `provider`, `model`, `credential` (reference), `system_prompt`,
  `user_prompt`, `temperature`, `max_tokens`, `output_schema`, `tools`,
  `timeout_seconds`. The node reaches providers only through the domain
  port `LLMClient` (the container's `AIService`, injected via
  `create_builtin_registry(llm=...)`). Without one it fails with
  `LLM_NOT_CONFIGURED`.
- Templates: `{{ a.b.0 }}` is a dotted lookup into the `variables` input
  (mapping keys and list indexes only). There are no filters, calls,
  operators or attribute access. A malformed placeholder fails config
  validation at publish. A missing variable fails the node with
  `PROMPT_VARIABLE_MISSING` (VALIDATION) before any provider call. Strings
  are inserted verbatim and other values as JSON. The rendered prompt is
  capped at 400k characters (`PROMPT_TOO_LARGE`). This is the prompt
  subset of §31, not the general Expression Engine.
- Outputs: `text`, `structured` (reply parsed and checked against
  `output_schema` with the engine's JSON-Schema subset,
  `LLM_STRUCTURED_OUTPUT_INVALID` otherwise), `tool_calls` (returned, not
  executed) and `metadata` (provider, model, request id, finish reason,
  token usage, `cost`).
- Cost (§29): `LiteLLMAdapter` estimates from litellm's bundled list
  prices, only for routes without a custom `api_base`. The value is
  exposed as `{"amount", "currency", "estimated": true}`, or `null` when
  unknown.
- Provider errors map to `NodeError` with the `PROVIDER_*` code kept:
  rate limit and unavailable become TRANSIENT, timeout becomes TIMEOUT,
  authentication and configuration become CONFIGURATION, everything else
  PERMANENT.

----------

# 31. Expression Engine

Expressions are used for:

-   conditions
    
-   mappings
    
-   templates
    
-   routing
    
-   calculated values
    

The expression system must be:

```text
typed
safe
bounded
deterministic where possible
observable
```

It must not execute arbitrary Python code.

Expressions should support explicit access to:

```text
inputs
outputs
variables
metadata
trigger payload
execution context
```

Only explicitly exposed values may be accessible.

----------

# 32. Secrets and Credentials

Secrets must be separated from Project configuration.

Project configuration stores references:

```text
credential_ref = "credential_123"
```

Not:

```text
api_key = "sk-..."
```

Secrets must:

-   be encrypted at rest
    
-   never appear in logs
    
-   never appear in normal API responses
    
-   never appear in exported Projects
    
-   never appear in reports
    
-   be redacted from exceptions where possible
    

Credential access must happen through a dedicated service.

Implementation (Phase 13):

- **Reference format.** `credential:<name>`; the name (`[a-z0-9][a-z0-9._-]{0,63}`)
  is the credential's immutable identity. Rotation never touches projects, and
  an imported project resolves against a same-named credential on the target.
- **Storage.** `CredentialService` (`akasha.application.credentials`) encrypts
  with AES-256-GCM (`akasha.security.crypto`, `cryptography` package) before
  any repository call. The credential name is bound as associated data, so a
  ciphertext copied onto another row does not decrypt. The key comes only from
  `AKASHA_CREDENTIAL_ENCRYPTION_KEY` (32 bytes, base64). A malformed key fails
  startup; without a key the store is unavailable (`CONFIGURATION_ERROR`).
  Back the key up separately from the database (§110).
- **API.** `/api/v1/credentials` (create, list, get, patch, delete). Values
  are write-only (`SecretStr`); no endpoint returns plaintext.
- **Project config.** `Project.replace_draft` rejects secret material
  (`PLAINTEXT_SECRET`, locations only). A string under a sensitive key must be
  a reference, and no string may be credential-shaped. Graph validation
  reports the same rule for data stored earlier, so it can be neither
  published nor run.
- **Resolution boundary.** The runtime gives each node attempt a
  `NodeCredentials` resolver (`akasha.runtime.credentials`). It resolves only
  references that literally appear in that node's frozen config snapshot, so
  references arriving through ports, execution input or webhooks are refused
  (`CREDENTIAL_NOT_REFERENCED`). Values are resolved at call time, never
  earlier, and never written back.
- **Known-value masking.** Each resolved value is registered in a `ContextVar`
  scoped to the node task. Redaction then masks it exactly in that node's
  logs, errors and outputs, including those of threads started from the task.
  `Secret` wraps plaintext in memory: masked `str`/`repr`, not
  picklable or JSON-encodable.

----------

# 33. Python Node SDK

The Python SDK provides a stable developer interface.

Conceptually:

```python
class Node:
    metadata = ...
    inputs = ...
    outputs = ...
    config_schema = ...

    async def execute(self, context):
        ...
```

The SDK should provide:

```text
Node base class
Input definitions
Output definitions
Config schemas
Execution context
Logging
Metrics
Credentials
Events
Artifacts
Errors
Testing utilities
```

Implementation (Phase 16, `backend/src/akasha/sdk`, guide: `docs/sdk.md`):

- `akasha.sdk` is a facade: it re-exports the domain Node contract
  (`akasha.domain.node`) plus the three contract errors, and pins the
  surface with `SDK_VERSION` (semver, `tests/test_sdk.py` guards
  `__all__`). No parallel SDK types exist, so built-in and custom nodes
  share one contract and the runtime needs no adapter.
- Contract additions: `NodeDefinition.metrics` / `.events` (declared
  names), `NodeMetrics` / `NodeEvents` on the context (per attempt,
  undeclared names raise `NodeContractError`, at most 100 events per
  attempt), and `NodeExecutionContext.resolve_credential` (a
  `CREDENTIALS_UNAVAILABLE` error instead of an optional resolver).
- The runtime flushes both when an attempt ends: events become
  `node.emitted`, metrics `akasha_node_metric_total{node_type, metric}`.
  Per attempt by design, not live: it keeps the single-writer rule (node
  tasks never touch the aggregate or database) and one save per outcome.
  Revisit when a live-update consumer (WebSocket gateway) exists.
- Built-in nodes import the contract from `akasha.sdk` too (guarded by
  `tests/test_architecture.py`), so every built-in exercises the SDK.
- `akasha.sdk.testing.run_node` runs a node with the engine's checks in
  the engine's order and the runtime's credential access rule
  (`NodeCredentials`).
- Not provided: artifacts (no artifact storage exists yet), `node
  validate/package` commands (CLI Phase 21; `validate` is
  `check_node_contract`). Registration without engine code: plugins (§36).
- Example: `backend/examples/hmac_signer` (imports `akasha.sdk` only,
  enforced by `tests/test_sdk.py`).

----------

# 34. Python Node Isolation

Version 1 assumes trusted Python code.

Therefore:

```text
Python Node
   ↓
Engine Process
```

is acceptable for the initial architecture.

However, Node execution must be abstracted sufficiently that future isolation can be introduced.

Potential future modes:

```text
in-process
subprocess
container
worker
sandbox
```

The Node contract must remain stable across execution modes.

----------

# 35. Plugin Architecture

Plugins extend the Engine.

A plugin may provide:

```text
Nodes
Triggers
Providers
Integrations
UI metadata
CLI commands
Documentation
```

A plugin has:

```text
id
name
version
engine compatibility
SDK compatibility
dependencies
metadata
components
```

----------

# 36. Plugin Lifecycle

```text
Discover
   ↓
Validate
   ↓
Install
   ↓
Register
   ↓
Load
   ↓
Use
   ↓
Disable
   ↓
Remove
```

Plugins must be versioned.

Plugin compatibility must be checked before activation.

Implementation (Phase 17, `backend/src/akasha/plugins/`, guide in
`docs/sdk.md` §9):

- Package = directory + Python package + `akasha-plugin.json`
  (`manifest.py`, strict Pydantic, 64 KiB cap). It is parsed into the
  domain `PluginMetadata` (id, version, engine/SDK constraints,
  dependencies, provided nodes, documentation) before any code is
  imported. Plugin node ids must be namespaced `<plugin id>.<name>`.
- `domain.plugin.compatibility_issues` checks engine version, `SDK_VERSION`
  and dependencies with the project dependency rules
  (`validate_dependencies`). Given the enabled plugins, those rules now
  also check project `Dependency(kind="plugin")` at validate/publish. The
  runtime omits plugins on purpose: a run needs node types, and a disabled
  plugin's nodes are no longer registered.
- `PluginRegistry` runs the lifecycle. It registers plugin nodes through
  `NodeRegistry.register`, so publish and runtime use them like built-ins.
  Installs are all-or-nothing (staging dir, rename, rollback of files and
  nodes). Startup loads in dependency order, and marks broken, incompatible
  or cyclic plugins `FAILED` without stopping the Engine. Enable/disable
  state lives in `<plugins_dir>/state.json`, written atomically.
- Trust boundary: `_load` is the only place plugin code runs, in-process
  (trusted code, §65). A future isolated loader would register proxy
  factories there; nothing else would change.
- Not blocked: disabling or removing a plugin whose nodes Projects use.
  Their validate/publish/run fail with structured `UNKNOWN_NODE_TYPE` /
  `MISSING_DEPENDENCY` until it is back (§143 "safe blocking" needs a
  query over published versions; left for import/export and CLI).
- Changes apply to the current process. Other processes see them after a
  restart (workers, Phase 22). No HTTP install endpoint exists: before
  authentication (Phase 20) it would be remote code execution. The CLI
  (Phase 21) drives the lifecycle.

----------

# 37. Node Registry

The Node Registry provides a unified lookup mechanism.

```text
Node Registry
├── Built-in Nodes
├── Plugin Nodes
└── Custom Nodes
```

The registry must provide:

```text
register
unregister
get
list
resolve
validate
```

Node IDs must be globally unambiguous within an Engine installation.

Recommended naming:

```text
core.llm
core.http
core.if
core.loop
community.example_node
plugin_name.node_name
```

Implementation (Phase 09, `backend/src/akasha/nodes/registry.py`): one
`NodeRegistry` class provides register (contract check + factory check),
unregister, get, `list_nodes` (sorted), `resolve(node_id, constraint)` and
two live read-only views: `definitions` (for `ProjectService`) and
`catalog` (for `ExecutionRuntime`), so publish-time and run-time validation
use the same node set. Duplicate ids are rejected
(`NODE_ALREADY_REGISTERED`); unknown ids raise `NODE_NOT_FOUND`.

- One version per node type id: `NodeInstance` references a type id only.
  Compatibility is expressed by project `Dependency(kind="node")`
  constraints (`*` or comma-separated `>= <= > < ==` clauses), checked at
  validate/publish and before every run (`MISSING_DEPENDENCY`,
  `INCOMPATIBLE_DEPENDENCY`, `INVALID_DEPENDENCY`).
- A registration pairs the definition with a zero-argument factory
  (`NodeFactory` protocol). Stateless nodes register their class; nodes
  with deployment dependencies (network policy, search endpoint) register
  a closure. The runtime creates a fresh instance per attempt.
- Built-ins (`nodes/builtins.py`): core.input, core.output, core.log,
  core.if, core.switch, core.delay, core.merge, core.http, core.web_search
  (SearXNG JSON API), core.smtp. The composition root builds the registry
  from settings (`bootstrap.create_node_registry`).

----------

# 38. Frontend Architecture

Recommended stack:

```text
React
Vite
TypeScript
Tailwind CSS
```

The frontend should be structured around product domains rather than generic technical components alone.

Example:

```text
frontend/
├── src/
│   ├── app/
│   ├── features/
│   │   ├── projects/
│   │   ├── editor/
│   │   ├── nodes/
│   │   ├── executions/
│   │   ├── monitoring/
│   │   ├── reports/
│   │   ├── credentials/
│   │   ├── plugins/
│   │   └── settings/
│   ├── components/
│   ├── api/
│   ├── hooks/
│   ├── stores/
│   ├── types/
│   └── main.tsx
```

----------

# 39. Frontend State

Frontend state should be separated into:

```text
Server State
UI State
Editor State
Realtime State
```

Server state includes:

```text
projects
versions
executions
nodes
credentials metadata
plugins
reports
```

UI state includes:

```text
dialogs
navigation
filters
selected items
```

Editor state includes:

```text
nodes
edges
selection
viewport
undo/redo
draft changes
```

Realtime state contains transient execution events.

Persisted server state remains authoritative.

----------

# 40. Visual Graph Editor

The graph editor is a specialized frontend subsystem.

Responsibilities:

```text
drag nodes
connect ports
delete nodes
configure nodes
select nodes
multi-select
copy/paste
undo/redo
zoom
pan
minimap
validation
search
grouping
```

The editor must operate on the backend-defined graph model.

It must not invent its own incompatible domain model.

----------

# 41. Node Inspector

The Node Inspector is schema driven.

Example:

```text
Node Definition
      ↓
Configuration Schema
      ↓
Frontend Form Generator
      ↓
Node Configuration
```

The inspector should support:

```text
text
number
boolean
select
multi-select
secret reference
model selector
JSON
code
expression
file
advanced configuration
```

Node-specific custom UI may be supported later, but schema-driven forms are the default.

----------

# 42. Frontend API Client

The frontend must use a typed API client.

Recommended flow:

```text
OpenAPI schema
      ↓
Generated TypeScript types/client
      ↓
Frontend API layer
      ↓
React features
```

Manually duplicated API types should be minimized.

----------

# 43. Project Editing Flow

Recommended flow:

```text
Load Project
     ↓
Load Draft
     ↓
Edit locally
     ↓
Autosave / explicit save
     ↓
Backend validation
     ↓
Show validation state
     ↓
Publish
     ↓
Immutable Project Version
```

The frontend may optimistically update editor state, but persistence remains backend controlled.

----------

# 44. Validation Architecture

Validation occurs at multiple levels.

```text
Frontend Validation
        ↓
API Validation
        ↓
Domain Validation
        ↓
Project Graph Validation
        ↓
Runtime Validation
```

Frontend validation exists for user feedback.

Backend validation is authoritative.

----------

# 45. Validation Types

Examples:

```text
Schema validation
Configuration validation
Port compatibility
Required inputs
Graph connectivity
Cycle detection
Control-flow validity
Dependency validation
Node availability
Plugin compatibility
Credential availability
Engine compatibility
```

Validation errors should be structured.

Example:

```json
{
  "code": "INVALID_PORT_CONNECTION",
  "node_id": "node_123",
  "port": "query",
  "message": "Input type does not match output type."
}
```

----------

# 46. Error Architecture

Errors should be categorized.

Examples:

```text
ValidationError
ConfigurationError
NodeExecutionError
ProviderError
NetworkError
TimeoutError
CancellationError
PermissionError
CredentialError
PluginError
InternalError
```

Errors should contain stable machine-readable codes.

Human-readable messages must not be the only error identifier.

----------

# 47. Retry Architecture

Retries belong to the runtime.

A Node may signal that an error is retryable.

The runtime decides:

```text
whether to retry
how many times
backoff
maximum delay
whether execution should continue
```

Recommended strategy:

```text
exponential backoff
jitter
maximum retry count
maximum total retry duration
```

Permanent errors must not be blindly retried.

----------

# 48. Timeout Architecture

Timeouts must be configurable at multiple levels:

```text
Engine default
Project
Node
Execution
```

A timeout must result in an explicit state/error.

The runtime must ensure timed-out work does not silently continue where the execution mode permits cancellation.

----------

# 49. Cancellation

Cancellation is cooperative.

Flow:

```text
User requests cancellation
        ↓
Execution Runtime
        ↓
Cancellation Token
        ↓
Running Nodes
        ↓
Node-specific cleanup
        ↓
Execution = CANCELLED
```

Nodes should regularly check cancellation where appropriate.

----------

# 50. Observability

Observability consists of:

```text
Logs
Metrics
Traces
Events
Execution History
```

Logs should be structured.

Example:

```json
{
  "timestamp": "...",
  "level": "INFO",
  "event": "node.completed",
  "execution_id": "...",
  "node_id": "...",
  "duration_ms": 421
}
```

----------

# 51. Logging Rules

Logs must:

-   use structured fields
    
-   contain correlation IDs
    
-   include execution IDs where applicable
    
-   include node IDs where applicable
    
-   avoid secrets
    
-   avoid unnecessary sensitive payloads
    
-   support filtering
    
-   support retention policies
    

Raw prompts and outputs should not automatically be logged unless explicitly configured.

----------

# 52. Correlation IDs

Important operations must have correlation identifiers.

At minimum:

```text
request_id
execution_id
node_execution_id
project_id
project_version_id
```

Where appropriate:

```text
trace_id
span_id
```

This enables:

```text
HTTP Request
   ↓
Execution
   ↓
Node Execution
   ↓
Provider Request
```

to be correlated.

Implementation (Phase 12):

- Log format: one JSON object per line (`akasha.observability.logging`):
  `timestamp` (ISO 8601 UTC, ms), `level`, `logger`, `message`, then the
  bound context, then the record's own fields (most specific wins).
- Context propagation: `log_context(**fields)` binds fields in a
  `ContextVar`. asyncio tasks copy their creation context, so fields bound
  before `create_task` reach every line the task emits, including node code
  and libraries it calls.
  - HTTP: a middleware accepts `X-Request-ID` if it is 1-128 printable
    characters (else mints a UUID), binds `request_id`, echoes the header,
    puts it in every error body and logs one `http_request` line (method,
    path without query string, status, `duration_ms`).
  - Triggers: the request id becomes the execution's `correlation_id`.
    Scheduler-fired executions get a fresh `corr_…` id.
  - Runtime: `execution_id` for the run; each node task gets
    `execution_id`, `project_id`, `correlation_id`, `node_id`,
    `node_execution_id`, `node_type`.
- Execution observer (`observe_execution`): called right after each
  execution save commits, with the events that save persisted. It emits one
  log line per event (logger `akasha.execution`, `event` = event type) with
  ids, the event payload, `duration_ms` (terminal node/execution events),
  `queue_wait_ms` (`execution.started`) and, for failures, `error_code`,
  `error_kind`, `error_message`, `error_node_id`. Failures log at WARNING.
  Durations come from domain timestamps, so the injected clock controls
  them. The runtime has no other lifecycle logging.
- `project_version_id` from the list above is not a log field: versions are
  identified by `(project_id, project_version)` everywhere (Phase 11 §6).
  `trace_id`/`span_id` are not implemented (no tracing backend yet).

----------

# 53. Execution Persistence

An Execution stores the immutable reference to:

```text
project
project version
trigger
runtime metadata
configuration snapshot metadata
state
timestamps
result
error
```

Each Node Execution stores:

```text
node
state
input metadata
output metadata
timestamps
retry information
error
usage
cost
```

Large payloads should be stored through the artifact/storage subsystem when appropriate.

Implementation (Phase 08, migration `0003_executions`):

- `executions`: one row per execution; `(project_id, project_version)` is a
  foreign key to `project_versions` (`RESTRICT`), so a version with
  executions cannot be removed. Metadata, retry/timeout policies, error and
  cancellation are JSONB documents. `revision` is the CAS token.
- `node_executions`: one row per node of the pinned graph (unique
  `(execution_id, node_id)`, `CASCADE` from the execution) with status,
  attempt, failures (JSONB), outputs (JSONB) and timestamps.
- Outputs are stored as JSON; values PostgreSQL cannot store (bytes, NaN,
  NUL characters, unpaired surrogates) are stored as a
  `{"$unpersisted": ...}` stub. Size limits (Phase 24) and
  redaction (Phase 13) are not applied yet. Inputs are not stored; they are
  derivable from upstream outputs and the pinned graph.
- Loading takes a share lock on the execution row, so the execution and
  its node rows always come from the same committed state.

Phase 09 additions (migration `0004_execution_input`):

- `executions.input` (JSONB object, default `{}`): the payload the
  execution was started with; entry nodes (`core.input`) read it. Written
  once at creation. Rows from before 0004 read as `{}`.
- `metadata.node_versions`: node type → version resolved by the runtime at
  start (reproducibility, AGENTS §73). Absent in older rows (read as `{}`).

Storable values (added in Phase 18): PostgreSQL cannot store NaN/Infinity
in JSONB, NUL characters, or unpaired UTF-16 surrogates in either JSONB or
text. JSON parsing accepts all of them. `domain.primitives.is_storable` is
the one rule, applied where each kind of data is written:

- User data is rejected with 422 `DOMAIN_VALIDATION_ERROR`: project
  create/PUT (`Project.replace_draft` names the locations), credential
  descriptions, execution input and package import.
- Node data is recorded, never fatal: node outputs become the stub, and
  error messages have the characters replaced (`storable_text`).
- `ExecutionError.from_node_error` also falls back to generic values for a
  node error with a blank message, a malformed code or an unknown kind. A
  (plugin) node therefore cannot abort a run with a malformed error.

----------

# 54. Execution Inspector

The frontend should be able to reconstruct an execution from persisted data.

It should display:

```text
Execution status
Project
Project version
Trigger
Start time
End time
Duration
Node graph
Node states
Logs
Errors
Token usage
Costs
Outputs
Artifacts
Events
```

Realtime updates enhance the inspector but are not required to reconstruct it.

----------

# 55. Reporting

Reports are generated from persisted execution data.

Reports may include:

```text
summary
execution status
duration
nodes executed
errors
token usage
cost estimates
outputs
artifacts
timestamps
```

Report generation should not directly depend on the frontend.

----------

# 56. Import / Export Architecture

Projects must have a portable representation.

Example:

```text
project-package/
├── manifest.json
├── project.json
├── nodes/
├── prompts/
├── resources/
├── docs/
└── assets/
```

Secrets must never be included.

The package must contain enough information to validate compatibility.

----------

# 57. Project Package Manifest

The manifest should contain information such as:

```json
{
  "format_version": "1",
  "project_schema_version": "1",
  "engine_compatibility": ">=1.0.0",
  "project_version": "1.0.0",
  "dependencies": {}
}
```

The exact schema may evolve.

Implementation (Phase 18; the format guide is `docs/project-format.md`):

- The package is a ZIP archive holding `manifest.json`, `project.json` and the
  project files under `docs/`, `prompts/`, `resources/` and `assets/`. There
  is no `nodes/` directory: node configuration (prompt templates included) is
  part of the graph in `project.json`. Plugin code is never packaged;
  plugins are installed separately (§36).
- The manifest carries `format_version`, `project_schema_version`,
  `engine_version`, `engine_compatibility`, `source_project_version` and
  `requires`. `requires` lists the node types used, with their versions at
  export, and the credential names referenced. The manifest also holds an
  inventory with a SHA-256 for every entry.
- Compatibility checks:
  - the exporter writes `engine_compatibility: ">=MAJOR.MINOR.0"` of its
    own release line, and the importer checks it against its own version;
  - node types are checked with SemVer (same major, at least the exported
    version);
  - plugin needs are declared project dependencies. The previous example's
    `project_version` and `dependencies` became `source_project_version`
    and `requires`.
- **Project files** are a new part of the project: `ProjectDraft.files`,
  path → bytes, snapshotted into `ProjectVersion.files`. They are stored as
  `{path: base64}` JSONB (migration `0007_project_files`) and capped at 100
  files, 1 MiB each and 4 MiB in total. Nothing in the runtime reads them
  yet. The draft API keeps them unchanged; only import sets them.
- `application/project_package.py` holds the format: strict Pydantic
  schemas plus `read_package` / `write_package`. Reading is the trust
  boundary, and nothing is extracted to disk. `application/packages.py`
  holds the use cases (`ProjectPackageService`):
  - export is deterministic, and it refuses (never strips) secret material;
  - import is all-or-nothing and creates a new, unpublished project;
  - import rejects unsupported versions and unmet requirements;
  - import reports other graph issues and the credentials that are missing.
- API: `GET /api/v1/projects/{id}/export[?version=N]` and
  `POST /api/v1/projects/import` (an `application/zip` body, max 16 MiB,
  capped while streaming). Both live on the projects router, so the Phase 20
  router-level authentication covers them.

----------

# 58. CLI Architecture

The CLI should reuse backend application services where possible.

Example:

```text
agent-engine project validate
agent-engine project import
agent-engine project export
agent-engine project run
agent-engine project publish

agent-engine node list
agent-engine node validate
agent-engine node test

agent-engine plugin list
agent-engine plugin install

agent-engine backup
agent-engine restore
```

CLI commands must not duplicate core business logic.

----------

# 59. Authentication

Version 1 requires local authentication.

The architecture should support future authentication providers without making them mandatory.

Conceptually:

```text
Authentication
      ↓
Identity
      ↓
Authorization
      ↓
Application Service
```

Authentication must be separated from authorization.

----------

# 60. Authorization

Version 1 does not require complex enterprise RBAC.

However, domain resources should have ownership/context information sufficient for future authorization.

Avoid hard-coding assumptions that every installation has exactly one unrestricted user.

----------

# 61. Audit Architecture

Security-sensitive operations should be auditable.

Examples:

```text
login
credential created
credential changed
credential deleted
project published
project imported
project exported
plugin installed
plugin removed
execution started
execution cancelled
settings changed
```

Audit records should be immutable.

----------

# 62. Security Boundaries

Important trust boundaries include:

```text
Browser
   ↓
API
   ↓
Engine
   ↓
Python Nodes / Plugins
   ↓
External Systems
```

Additional boundaries:

```text
User Input
Expression Engine
LLM Output
External HTTP
File System
Plugin Code
Credentials
```

Every boundary must assume that incoming data may be malformed or hostile.

----------

# 63. External HTTP

HTTP Nodes must provide controls for:

```text
allowed methods
timeouts
redirect behavior
maximum response size
authentication
headers
TLS validation
retry policy
```

Future SSRF protections should be considered part of the security architecture.

Implementation (Phase 09, `security/network.py`, `nodes/http.py`):

- User-configured destinations (core.http, core.smtp) must resolve only to
  globally routable addresses; loopback, private, link-local, CGNAT,
  multicast, reserved and IPv4-mapped variants are blocked unless the
  operator sets `AKASHA_NETWORK_ALLOW_PRIVATE` or lists hosts in
  `AKASHA_NETWORK_ALLOWED_HOSTS`. Operator-configured endpoints
  (`AKASHA_WEB_SEARCH_URL`) are trusted.
- Only http/https, no credentials in URLs, no environment proxies.
  Redirects are off by default; when on, each hop (max 5) is re-checked.
- One total time limit per exchange, response cap
  `AKASHA_HTTP_MAX_RESPONSE_BYTES` (Content-Length and streamed), stable
  error codes: 429/5xx transient, other 4xx permanent.
- Known gap: the guard resolves, then the client resolves again (DNS
  rebinding window); pinning the checked IP is security-hardening scope.

----------

# 64. File Access

Nodes must not receive unrestricted filesystem access by default.

File operations should go through an Artifact/File service where practical.

Paths must be validated.

Path traversal must be prevented.

----------

# 65. Plugin Security

Plugins are executable code.

Version 1 may treat installed plugins as trusted installation-level code.

The architecture must still:

```text
validate metadata
validate compatibility
track versions
control activation
record installation
```

Future sandboxing must remain possible.

----------

# 66. Configuration Architecture

Configuration should be separated into:

```text
Engine configuration
Installation configuration
Environment configuration
Project configuration
Node configuration
Credential configuration
```

Secrets should preferably be injected through:

```text
environment
secret store
credential service
```

and not committed into source code.

----------

# 67. Environment Configuration

Example:

```text
DATABASE_URL
REDIS_URL
STORAGE_PATH
SECRET_KEY
API_HOST
API_PORT
LOG_LEVEL
```

Configuration loading should happen through a centralized configuration service.

Individual modules should not independently parse environment variables.

----------

# 68. Deployment Architecture

A standard self-hosted deployment may consist of:

```text
┌──────────────┐
│ Reverse Proxy│
└──────┬───────┘
       │
 ┌─────▼─────┐
 │ Frontend  │
 └─────┬─────┘
       │
 ┌─────▼─────┐
 │ Backend   │
 └──┬────┬───┘
    │    │
    │    ├──────────────┐
    │                   │
┌───▼────┐        ┌─────▼─────┐
│Postgres│        │   Redis   │
└────────┘        └───────────┘
    │
┌───▼────────────────┐
│ File/Object Storage│
└────────────────────┘
```

Docker Compose should be sufficient for the initial deployment model.

----------

# 69. Local Development

Local development should minimize infrastructure requirements where practical.

Possible development configuration:

```text
Backend
PostgreSQL
Redis
Frontend
```

Optional simplified development storage may use local filesystem storage.

Production assumptions must not leak into development-specific code.

----------

# 70. Health Checks

The backend should expose:

```text
/health
/ready
```

Health checks must distinguish:

```text
process is alive
```

from:

```text
application is ready to serve requests
```

Readiness may verify critical dependencies.

----------

# 71. Background Workers

Long-running executions should not depend on HTTP request lifetime.

Preferred architecture:

```text
API
 ↓
Create Execution
 ↓
Queue
 ↓
Worker / Runtime
 ↓
Execution
```

Version 1 may run the worker inside the same deployment architecture if necessary.

The internal interface should nevertheless resemble a queue-based execution model.

----------

# 72. Queue Abstraction

Execution scheduling should use an abstraction such as:

```text
ExecutionQueue
```

Possible implementation:

```text
Redis-backed queue
```

Future implementations may include:

```text
dedicated worker queue
distributed queue
cloud queue
```

The runtime must not depend directly on Redis-specific APIs.

----------

# 73. Scalability Boundary

Version 1 targets:

```text
single self-hosted installation
```

The architecture should support future separation into:

```text
API
Scheduler
Workers
Event Service
Storage
```

without requiring a complete rewrite.

Distributed execution is not a Version 1 requirement.

----------

# 74. Concurrency Control

Concurrency limits should exist at multiple levels where useful:

```text
global
project
execution
node type
provider
```

Examples:

```text
maximum concurrent executions
maximum concurrent nodes
maximum provider requests
```

This prevents a single Project from exhausting system resources.

----------

# 75. Resource Limits

The runtime should support limits for:

```text
execution duration
node duration
retries
concurrency
output size
artifact size
HTTP response size
LLM token limits
```

Limits should be explicit and observable.

----------

# 76. Caching

Caching may be introduced where useful.

Potential cache targets:

```text
Node metadata
Provider metadata
Model metadata
static resources
safe HTTP responses
```

Execution results must not be cached implicitly unless the behavior is explicitly defined.

Caching must never compromise correctness or secret isolation.

----------

# 77. Determinism

The system should maximize reproducibility by recording:

```text
Engine version
Project version
Node versions
Plugin versions
configuration
trigger input
runtime metadata
provider
model
parameters
```

AI responses may remain nondeterministic.

The architecture must distinguish:

```text
reproducible execution configuration
```

from:

```text
identical AI output
```

----------

# 78. API and Domain Schema Separation

API schemas and domain objects should not necessarily be identical.

Example:

```text
API Request DTO
       ↓
Application Command
       ↓
Domain Model
```

This prevents external API compatibility requirements from leaking directly into the domain model.

----------

# 79. Database Models

Database models are infrastructure concerns.

Avoid using ORM entities directly throughout the entire application.

Preferred:

```text
Database Model
      ↓
Repository
      ↓
Domain Model
      ↓
Application Service
```

This keeps persistence concerns isolated.

----------

# 80. Serialization

Serialization formats must be explicit.

Common formats:

```text
JSON
JSONL
binary artifacts
```

Internal Python object serialization must not be used as a portable Project format.

Project packages must use stable, documented schemas.

----------

# 81. Schema Versioning

The following versions must be tracked independently:

```text
API Version
Engine Version
Project Schema Version
Project Version
Node Version
Plugin Version
SDK Version
```

A change to one does not automatically imply a change to all others.

----------

# 82. Migration Strategy

Schema changes must be handled through explicit migrations.

Migration categories:

```text
Database migration
Project schema migration
API migration
Plugin migration
Node configuration migration
```

Project imports should support migration when a compatible migration path exists.

----------

# 83. Backward Compatibility

Compatibility should be explicit.

The Engine must reject unsupported versions rather than silently interpreting incompatible data.

Example:

```text
Unsupported project schema version
```

must result in a structured compatibility error.

----------

# 84. Testing Architecture

Testing must follow architectural boundaries.

```text
Unit Tests
   ↓
Domain / Application

Integration Tests
   ↓
Persistence / Runtime / Providers

Contract Tests
   ↓
API / Nodes / Plugins

Frontend Tests
   ↓
UI / Editor

End-to-End Tests
   ↓
Complete System
```

----------

# 85. Domain Tests

Domain tests should not require:

```text
PostgreSQL
Redis
HTTP
React
LLM provider
```

They should be fast and deterministic.

----------

# 86. Runtime Tests

Runtime tests must cover:

```text
linear graph
branching
parallel execution
merge
loops
retries
timeouts
cancellation
failures
missing dependencies
invalid graphs
node failures
```

----------

# 87. Node Contract Tests

Every Node should be testable against a standard contract.

Tests should verify:

```text
metadata
schema
ports
validation
execution
errors
version
```

Built-in and plugin Nodes should use the same contract test mechanism.

----------

# 88. Example Projects as Regression Tests

Example Projects are executable architecture tests.

At minimum:

```text
Hello World
LLM
Web Research
Lead Generation
```

Each should be importable and executable.

Breaking an example project should be treated as a significant regression.

----------

# 89. Frontend Testing

Frontend tests should cover:

```text
project loading
graph rendering
node configuration
connections
validation errors
save behavior
publish behavior
execution monitoring
WebSocket reconnection
```

The graph editor should have dedicated interaction tests.

----------

# 90. End-to-End Testing

A minimal E2E test should perform:

```text
Create Project
    ↓
Add Nodes
    ↓
Connect Nodes
    ↓
Configure Nodes
    ↓
Validate
    ↓
Publish
    ↓
Trigger Execution
    ↓
Observe Execution
    ↓
Inspect Result
```

----------

# 91. Failure Handling

Failure must be explicit at every layer.

```text
Node Failure
     ↓
Node Execution Failed
     ↓
Runtime Policy
     ├── Retry
     ├── Catch
     ├── Skip
     └── Fail Execution
```

The runtime must never silently swallow execution errors.

----------

# 92. Idempotency

Operations that may be retried should define idempotency behavior.

Examples:

```text
Trigger delivery
Webhook processing
Execution creation
Plugin installation
Project import
```

External side-effecting Nodes should expose or document idempotency capabilities where appropriate.

----------

# 93. Side Effects

Nodes may produce side effects:

```text
send email
HTTP request
write file
database mutation
external API call
```

The runtime must not assume that retrying a Node is always safe.

Node metadata may eventually expose:

```text
idempotent
non-idempotent
unknown
```

Retry policies must respect this information.

----------

# 94. Eventual Consistency

The system should prefer strong consistency for critical state transitions.

Eventual consistency is acceptable for:

```text
monitoring
metrics
live UI updates
derived reports
caches
```

The canonical execution state remains persisted backend state.

----------

# 95. Domain Events vs Infrastructure Events

Domain/application events represent meaningful Engine behavior:

```text
ExecutionCompleted
ProjectPublished
NodeFailed
```

Infrastructure events represent implementation details:

```text
RedisMessageReceived
WorkerHeartbeat
DatabaseConnectionLost
```

The two concepts must not be mixed.

----------

# 96. Dependency Direction

The dependency direction should be:

```text
API
 ↓
Application
 ↓
Domain
 ↑
Infrastructure
```

More precisely:

```text
Application → Domain
Infrastructure → Application Interfaces
Infrastructure → Domain
API → Application
Runtime → Domain / Application Interfaces
```

The domain must remain at the center.

----------

# 97. Dependency Rules

The following rules are mandatory:

### Domain

May depend on:

```text
standard library
domain-safe abstractions
```

Must not depend on:

```text
FastAPI
SQLAlchemy
Redis
React
provider SDKs
```

### Application

May depend on:

```text
Domain
interfaces
```

Must not depend on:

```text
frontend
HTTP framework details
```

### Infrastructure

May depend on:

```text
Application interfaces
Domain
external libraries
```

### API

May depend on:

```text
Application
API schemas
authentication
serialization
```

----------

# 98. Dependency Injection

Infrastructure dependencies should be injected.

Examples:

```text
ProjectRepository
ExecutionRepository
EventPublisher
CredentialService
ArtifactStorage
LLMService
ExecutionQueue
```

This improves:

-   testability
    
-   replaceability
    
-   local development
    
-   future scaling
    

----------

# 99. Time and Clock Abstraction

Code that depends on current time should use an injectable clock where deterministic tests require it.

Avoid uncontrolled direct calls to system time throughout domain logic.

----------

# 100. Randomness

Randomness must be explicit.

Where reproducibility matters:

```text
random seeds
request IDs
retry jitter
sampling parameters
```

must be distinguished.

AI provider nondeterminism must not be confused with application randomness.

----------

# 101. Configuration Resolution

Configuration should have a defined precedence.

Example:

```text
Engine Defaults
      ↓
Project Defaults
      ↓
Node Configuration
      ↓
Execution Input
```

Secret references are resolved only at runtime.

The resolved secret must not be written back into the Project configuration.

----------

# 102. Runtime Context

The execution context should expose only explicitly defined capabilities.

Conceptually:

```text
ExecutionContext
├── execution
├── project
├── node
├── inputs
├── variables
├── credentials
├── logger
├── metrics
├── artifacts
├── events
└── cancellation
```

Nodes must not receive unrestricted access to internal Engine objects.

----------

# 103. Context Isolation

A Node should only access:

```text
its own configuration
its declared inputs
approved services
explicit runtime context
```

A Node should not arbitrarily mutate:

```text
other node state
project definitions
execution state
database records
```

unless explicitly supported by a controlled service.

----------

# 104. Output Handling

Node outputs must conform to declared output schemas.

The runtime should validate outputs where practical.

Invalid output should result in a structured Node execution error.

----------

# 105. Large Data Handling

Large data should not unnecessarily flow through every graph node.

For large payloads, the system should support references:

```text
ArtifactReference
FileReference
ObjectReference
```

instead of copying full content.

This improves:

```text
memory usage
database size
execution performance
network traffic
```

----------

# 106. Artifact Architecture

Artifacts represent files or large outputs associated with executions.

Conceptually:

```text
Artifact
├── id
├── execution_id
├── node_execution_id
├── name
├── mime_type
├── size
├── storage_reference
└── metadata
```

Artifacts should be accessible through controlled services.

----------

# 107. API Payload Limits

API endpoints must define reasonable payload limits.

Particularly:

```text
project import
file upload
execution input
large outputs
report generation
```

Large payloads should use artifact storage where appropriate.

----------

# 108. Rate Limiting

Rate limiting may be applied to:

```text
authentication
webhooks
API
provider calls
resource-intensive operations
```

Rate limits must be configurable.

----------

# 109. Secrets Redaction

Redaction should occur at multiple levels:

```text
application logs
exception messages
execution logs
reports
API serialization
debug output
```

Known credential values must never be logged.

Implementation (Phase 12, `akasha.security.redaction`):

- One module decides what looks secret. Values under sensitive mapping keys
  (`password`, `api_key`, `Authorization`, `access_token`, …; not
  `*_id`/`*_type`/`*_count`) are replaced whole by `[REDACTED]`.
  Credential-shaped text is masked: URL userinfo, `Bearer`/`Basic` values,
  `key=value` with a sensitive key, `sk-…`, GitHub/Slack tokens, AWS key
  ids, JWTs.
- Applied at three choke points, not at call sites:
  1. the log formatters (every field, the message, exception text);
  2. the API error body (`message`, `details`) for every error handler;
  3. `execute_node`: node error messages/details are redacted before they
     are persisted, published or logged.
- Events stay secret-free by construction (closed scalar payloads, §18).
- Metric labels never carry ids, codes or text (§113).
- Limit: patterns catch *credential-shaped* secrets. Since Phase 13, values
  resolved from the credential store are also masked exactly within the
  resolving node's context (§32). A shapeless secret that never went through
  the store (e.g. typed into an execution input) is still not recognisable.

----------

# 110. Backup Architecture

A backup should cover:

```text
PostgreSQL
Project data
Project versions
Credentials
Plugin metadata
Artifacts
Engine configuration
```

Secret encryption keys must be handled separately and securely.

A database backup without the required encryption material may not be sufficient for full recovery.

----------

# 111. Restore Architecture

Restore should validate:

```text
Engine compatibility
schema versions
plugin compatibility
project compatibility
storage availability
credential encryption compatibility
```

Restore must not silently overwrite an active installation without explicit confirmation.

----------

# 112. Operational Architecture

The installation should expose enough information to operate the system.

Required operational concepts:

```text
health
readiness
version
database status
queue status
storage status
active executions
worker status
recent errors
```

----------

# 113. Metrics

Recommended metrics include:

```text
executions_total
executions_success_total
executions_failed_total
execution_duration
node_executions_total
node_execution_duration
active_executions
queue_depth
llm_tokens_total
llm_cost_estimated
http_requests_total
errors_total
```

Metric names are implementation details and may evolve.

Implementation (Phase 12, `akasha.observability.metrics`, Prometheus
exposition at `GET /metrics`, unauthenticated until Phase 20 like
`/health`):

| Metric | Type | Labels |
|---|---|---|
| `akasha_executions_created_total` | counter | `trigger_type` |
| `akasha_executions_started_total` | counter | — |
| `akasha_executions_finished_total` | counter | `status` |
| `akasha_execution_duration_seconds` | histogram | `status` |
| `akasha_execution_queue_wait_seconds` | histogram | — |
| `akasha_active_executions` | gauge | — (per runtime process) |
| `akasha_node_executions_finished_total` | counter | `node_type`, `status` |
| `akasha_node_execution_duration_seconds` | histogram | `node_type`, `status` |
| `akasha_node_execution_errors_total` | counter | `node_type`, `error_kind` |
| `akasha_node_metric_total` | counter | `node_type`, `metric` (declared names only, Phase 16) |

- Label values are bounded enums or the installed node types. Error codes
  are not labels (node/plugin-defined, open-ended).
- Each `EngineMetrics` owns its registry (no process-global state). The
  container's instance backs `/metrics` and `ExecutionService`. The runtime
  takes one via `ExecutionRuntime(metrics=...)` and must share the
  container's once the container constructs it (Phase 21/22).
- Counters are per process. With workers (Phase 22) every process exposes
  its own and Prometheus aggregates.
- Not yet emitted: `queue_depth` (no queue before Phase 22), retries (the
  runtime has no retries before Phase 23), `llm_tokens_total`/`llm_cost`
  (token usage and cost estimates are persisted in `core.llm` outputs and
  logged by `AIService`; the metrics need the runtime's `EngineMetrics` to
  reach the LLM path, Phases 21/22), `http_requests_total` (the `http_request` log line covers
  requests for now).

----------

# 114. Performance Architecture

Performance priorities:

1.  graph scheduling
    
2.  execution persistence
    
3.  API responsiveness
    
4.  frontend graph rendering
    
5.  large execution inspection
    
6.  LLM/provider throughput
    
7.  artifact handling
    

Premature optimization must not compromise architecture.

----------

# 115. Frontend Performance

The graph editor must support large graphs through:

```text
virtualization where necessary
memoization
incremental rendering
efficient edge rendering
debounced persistence
lazy loading
```

The exact implementation depends on the chosen graph library.

----------

# 116. Backend Performance

The backend should avoid:

```text
N+1 queries
unbounded event payloads
unbounded logs
unbounded task creation
synchronous long-running HTTP operations
```

Long-running work belongs in the execution runtime/worker architecture.

----------

# 117. Database Performance

Database access should use:

```text
appropriate indexes
pagination
bounded queries
selective loading
transaction boundaries
```

Execution history must be designed for potentially large datasets.

----------

# 118. Pagination

Large collections must be paginated.

Examples:

```text
projects
executions
events
logs
reports
plugins
nodes
```

Cursor pagination should be considered for high-volume execution/event data.

----------

# 119. Retention

The system should eventually support retention policies for:

```text
execution history
logs
events
metrics
artifacts
reports
```

Retention must be configurable.

Deletion of historical data must respect audit and compliance requirements where applicable.

----------

# 120. Frontend Navigation Model

The primary application structure should expose:

```text
Projects
Executions
Monitoring
Reports
Nodes
Credentials
Plugins
Settings
```

Project-specific navigation should expose:

```text
Overview
Editor
Versions
Triggers
Executions
Settings
```

----------

# 121. Editor Persistence

The editor should not save every low-level interaction synchronously.

Use controlled persistence such as:

```text
local editor state
      ↓
debounced save
      ↓
backend Draft
```

Explicit save/publish operations must remain available.

----------

# 122. Undo / Redo

Undo/redo belongs to the frontend editor state.

It must not be implemented by creating backend Project Versions for every editor action.

Project Versions represent meaningful published states.

----------

# 123. Autosave

Autosave should persist Draft changes.

Autosave must never automatically publish.

The distinction is:

```text
Autosave = persist Draft
Publish = create immutable Project Version
```

----------

# 124. Project Version Lifecycle

```text
Draft
  │
  ├── edit
  │
  ├── validate
  │
  └── publish
          │
          ▼
    Project Version
          │
          ├── execute
          ├── inspect
          └── export
```

Published versions are immutable.

----------

# 125. Trigger Lifecycle

Triggers are configured on the Project Draft and published alongside it; the published Project Version carries an immutable copy with stable trigger ids.

A trigger always resolves to the **latest published version** of its project: publishing a new version retires the previous version's triggers. A trigger must never ambiguously execute "whatever happens to be the current Draft" — drafts never fire.

Trigger ids are unique within a project, not globally; webhook and invoke URLs therefore carry the project id. Uniqueness and schedule rules (cron syntax, deterministic and able to fire; interval between 1 second and 1 year) are validation issues that block publishing — not construction errors — so drafts stored under older rules stay loadable and fixable.

Scheduled (cron/interval) triggers have no catch-up of missed runs: after downtime the next boundary is computed forward from *now*.

----------

# 126. Manual Execution

Manual execution should allow:

```text
select Project
select Project Version
provide input
start execution
```

The API should return an Execution ID.

The frontend then observes that execution.

----------

# 127. Webhook Execution

Webhook flow:

```text
HTTP Request
   ↓
Webhook Trigger
   ↓
Authentication / Validation
   ↓
Create Execution
   ↓
Queue Execution
   ↓
Runtime
```

The webhook endpoint should not perform arbitrary long-running graph execution synchronously.

----------

# 128. Cron Execution

Cron triggers should create executions through the same execution service used by manual/API triggers.

They must not have their own separate execution implementation.

----------

# 129. Event Trigger Execution

Internal events may trigger Projects.

Example:

```text
project.execution.completed
        ↓
matching event trigger
        ↓
new execution
```

Event-triggered execution must include safeguards against accidental infinite loops.

----------

# 130. Infinite Loop Protection

The runtime must protect against:

```text
graph cycles where invalid
unbounded loops
event-trigger recursion
retry storms
recursive subgraphs
```

Limits must be configurable.

----------

# 131. Execution Depth

Nested execution constructs should have explicit limits.

Examples:

```text
subgraph depth
loop iterations
recursive trigger depth
agent/tool call depth
```

This prevents accidental runaway execution.

----------

# 132. Node Timeouts

Every Node should have a default timeout policy.

Node-specific overrides may be supported.

Timeouts must be enforced by the runtime where possible.

----------

# 133. Provider Resilience

External AI providers may fail due to:

```text
timeouts
rate limits
temporary outages
invalid requests
quota exhaustion
authentication errors
```

Provider adapters should normalize these into stable internal error categories.

----------

# 134. Provider Metadata

The Engine should track provider/model metadata where available:

```text
provider
model
context limits
capabilities
pricing metadata
supports streaming
supports tools
supports structured output
```

Provider metadata must not be assumed to be static forever.

----------

# 135. Tool Calling

LLM tool calls should be represented as structured requests.

Example:

```text
LLM
 ↓
Tool Call
 ↓
Tool Registry
 ↓
Tool Node / Tool Adapter
 ↓
Result
 ↓
LLM
```

Tool execution must remain subject to Node/runtime security and permissions.

----------

# 136. Agent Tool Architecture

Tools should reuse existing Engine capabilities where possible.

Examples:

```text
Web Search Node
HTTP Node
Database Node
File Node
Email Node
```

Avoid implementing separate duplicate tool infrastructures for Agents.

----------

# 137. Human Interaction

Human-in-the-loop is a future extension.

The architecture should leave room for:

```text
WAITING
```

execution state.

A future human interaction Node may suspend an execution and resume it later.

----------

# 138. Execution Recovery

The runtime should persist enough state to recover from process failure.

At minimum:

```text
execution state
node execution state
project version
timestamps
retry state
```

Future worker architectures can resume eligible executions.

----------

# 139. Crash Safety

The system must distinguish:

```text
process crash
node failure
execution failure
provider failure
```

A process crash must not automatically be interpreted as a clean execution failure without recovery logic.

----------

# 140. Runtime Heartbeats

Long-running workers should expose heartbeat information.

This enables detection of:

```text
stale worker
stuck execution
dead process
```

Heartbeat monitoring is primarily operational infrastructure.

----------

# 141. Plugin Discovery

Plugins should be discoverable through:

```text
filesystem
configured plugin paths
package metadata
future registries
```

Version 1 does not require a public marketplace.

----------

# 142. Plugin Installation

Plugin installation should:

```text
validate package
check compatibility
resolve dependencies
install files/package
register metadata
load plugin
run validation
```

Failed installation must leave the system in a consistent state.

----------

# 143. Plugin Removal

A plugin must not be removed if active Projects depend on it without explicit handling.

Possible strategies:

```text
block removal
disable plugin
migration
force removal with explicit confirmation
```

Version 1 should prefer safe blocking over destructive removal.

----------

# 144. Project Dependency Resolution

Before execution, the Engine must verify:

```text
required Node exists
required Node version is compatible
required Plugin exists
required Plugin version is compatible
required provider configuration exists
```

Missing dependencies produce structured errors.

----------

# 145. Compatibility Matrix

Compatibility should eventually be represented as:

```text
Engine
  ↕
Project Schema
  ↕
Project
  ↕
Plugin
  ↕
Node
  ↕
SDK
```

Compatibility constraints must be machine-readable where possible.

----------

# 146. API Error Format

API errors must use a consistent, flat structure — not nested under an
`error` key. This is the actual contract implemented starting in the
backend skeleton phase and used by every endpoint since:

```json
{
  "code": "PROJECT_VALIDATION_FAILED",
  "message": "The project contains validation errors.",
  "details": {
    "issues": [
      {
        "code": "invalid_port_connection",
        "node_id": "node_123"
      }
    ]
  },
  "request_id": "req_123"
}
```

`details` is a JSON object, not an array — its shape is error-specific
(e.g. `{"issues": [...]}` for validation failures, or plain scalar fields
for others). `code` and `request_id` are always present; `details`
defaults to `{}` when there is nothing further to report.

----------

# 147. API Pagination Format

List APIs should expose consistent pagination.

Example:

```json
{
  "items": [],
  "page": {
    "next_cursor": "...",
    "has_more": true
  }
}
```

The exact format may evolve.

----------

# 148. API Idempotency

Where appropriate, APIs should accept idempotency keys.

Especially for:

```text
execution creation
webhook-triggered execution
plugin installation
imports
other side-effecting operations
```

----------

# 149. API Security

Every API endpoint must explicitly define:

```text
authentication requirement
authorization requirement
input schema
output schema
error behavior
rate limits where relevant
```

No endpoint should implicitly rely on frontend restrictions for security.

----------

# 150. Documentation Architecture

Documentation should mirror the architecture.

Required documentation areas include:

```text
docs/
├── vision.md
├── architecture.md
├── domain-model.md
├── node-system.md
├── execution-engine.md
├── project-format.md
├── plugin-system.md
├── sdk.md
├── api.md
├── deployment.md
├── security.md
├── testing.md
├── operations.md
├── roadmap-frontend.md
├── roadmap-backend.md
└── adr/
```

Documentation must describe behavior and contracts, not merely implementation details.

----------

# 151. Architecture Decision Records

Significant architectural decisions should be recorded as ADRs.

Examples:

```text
ADR-001 Backend Framework
ADR-002 Database
ADR-003 Graph Representation
ADR-004 Execution Runtime
ADR-005 Node Contract
ADR-006 Plugin Architecture
ADR-007 Provider Abstraction
ADR-008 Project Versioning
ADR-009 Event Architecture
```

ADRs should explain:

```text
context
decision
alternatives
consequences
```

----------

# 152. Architectural Invariants

The following must remain true:

1.  Backend is authoritative.
    
2.  Published Project Versions are immutable.
    
3.  Executions reference immutable Project Versions.
    
4.  Domain logic does not depend on infrastructure.
    
5.  Nodes implement a stable contract.
    
6.  Node configuration is schema driven.
    
7.  Secrets are never stored inside normal Project configuration.
    
8.  Secrets are never logged.
    
9.  Runtime controls execution state.
    
10.  API is not the execution runtime.
    
11.  Frontend is not the source of truth.
    
12.  Provider-specific logic is isolated.
    
13.  Plugins use explicit compatibility metadata.
    
14.  Long-running execution is decoupled from HTTP request lifetime.
    
15.  Critical state is durably persisted.
    
16.  Events are not the canonical source of execution state.
    
17.  Arbitrary Python execution is forbidden in user expressions.
    
18.  Python Nodes are trusted code in Version 1.
    
19.  Projects are portable without secrets.
    
20.  The architecture must permit future worker/process isolation.
    

----------

# 153. Architectural Anti-Patterns

The following patterns are explicitly discouraged.

## 153.1 Frontend-Only Business Logic

Bad:

```text
Frontend determines whether a graph is valid.
Backend blindly executes it.
```

Correct:

```text
Frontend validates for UX.
Backend validates authoritatively.
```

----------

## 153.2 Database-Centric Domain

Bad:

```text
SQLAlchemy Model
      ↓
everywhere
```

Correct:

```text
Database Model
      ↓
Repository
      ↓
Domain/Application
```

----------

## 153.3 Provider Leakage

Bad:

```python
domain.OpenAIMessage(...)
```

Correct:

```python
domain.Message(...)
```

Provider-specific translation occurs at the provider boundary.

----------

## 153.4 Hidden Global State

Avoid global mutable registries and global runtime state.

Prefer explicit services and dependency injection.

----------

## 153.5 HTTP-Driven Execution

Bad:

```text
POST /run
   ↓
HTTP request waits for complete execution
```

Correct:

```text
POST /run
   ↓
Create Execution
   ↓
Return Execution ID
   ↓
Worker executes
```

----------

## 153.6 Secret Leakage

Never:

```text
store API key in project JSON
log API key
return API key in API response
export API key
include API key in error message
```

----------

## 153.7 Arbitrary Eval

Never use:

```python
eval(user_expression)
```

or equivalent unrestricted execution mechanisms for Project expressions.

----------

## 153.8 Runtime-Specific Nodes

Avoid Nodes that directly depend on internal runtime implementation details.

Nodes should use the public Node SDK/context contract.

----------

## 153.9 Duplicate Execution Engines

Do not implement separate execution engines for:

```text
manual runs
cron runs
webhooks
Agents
CLI
frontend
```

All execution paths must converge on the same Execution Runtime.

----------

# 154. Reference Execution Flow

A complete execution should conceptually follow:

```text
Trigger
   ↓
Application Service
   ↓
Create Execution
   ↓
Resolve Project Version
   ↓
Validate Dependencies
   ↓
Queue Execution
   ↓
Execution Runtime
   ↓
Load Graph
   ↓
Initialize State
   ↓
Schedule Ready Nodes
   ↓
Execute Node
   ↓
Persist Node Result
   ↓
Emit Events
   ↓
Resolve Next Nodes
   ↓
Repeat
   ↓
Finalize Execution
   ↓
Persist Final State
   ↓
Emit execution.completed / failed
   ↓
Frontend receives live update
```

----------

# 155. Reference Project Lifecycle

```text
Create Project
      ↓
Edit Draft
      ↓
Autosave
      ↓
Validate
      ↓
Fix Errors
      ↓
Validate
      ↓
Publish
      ↓
Project Version
      ↓
Configure Trigger
      ↓
Execution
      ↓
Observe
      ↓
Inspect
      ↓
Report
      ↓
Export
```

----------

# 156. Reference Node Lifecycle

```text
Discover
   ↓
Register
   ↓
Validate Definition
   ↓
Available in Node Library
   ↓
Add to Project
   ↓
Configure
   ↓
Validate
   ↓
Publish
   ↓
Execute
   ↓
Observe
   ↓
Result
```

----------

# 157. Reference Plugin Lifecycle

```text
Plugin Package
      ↓
Discover
      ↓
Metadata Validation
      ↓
Compatibility Check
      ↓
Dependency Resolution
      ↓
Install
      ↓
Register
      ↓
Load
      ↓
Node Registry
      ↓
Project Usage
      ↓
Runtime
```

----------

# 158. Version 1 Architecture Boundary

Version 1 should optimize for:

```text
clarity
correctness
maintainability
self-hosting
developer experience
extensibility
observability
```

It should not prematurely optimize for:

```text
global distributed execution
multi-region deployment
Kubernetes orchestration
multi-tenant SaaS
enterprise identity
marketplace infrastructure
massive distributed workloads
```

----------

# 159. Future Architectural Extensions

The architecture should leave room for:

```text
distributed workers
containerized Node execution
sandboxed Python Nodes
multi-tenant isolation
advanced RBAC
enterprise SSO
plugin marketplace
remote workers
multi-agent systems
human-in-the-loop
advanced memory
vector storage
browser automation
external event buses
cloud object storage
horizontal scaling
```

These are future extensions, not Version 1 dependencies.

----------

# 160. Recommended Initial Technology Mapping

The initial implementation may use:

Area

Technology

Backend

Python

Package/Environment Management

uv

API

FastAPI

Validation

Pydantic

ORM/Data Access

SQLAlchemy or equivalent

Database

PostgreSQL

Queue/Cache

Redis

Frontend

React

Frontend Build

Vite

Frontend Language

TypeScript

Styling

Tailwind CSS

Graph Editor

React-compatible graph library

AI Provider Abstraction

LiteLLM or equivalent

Web Search

Provider-agnostic Node implementation

Email

SMTP Node

Realtime

WebSocket

Deployment

Docker Compose

Storage

Local filesystem initially, object storage abstraction

Testing

pytest + frontend test framework

API Schema

OpenAPI

Technology choices may change.

The architectural interfaces must survive technology changes.

----------

# 161. Implementation Priority

Architecture implementation should proceed in this order:

```text
1. Domain Model
2. Node Contract
3. Project Graph Model
4. Validation
5. Persistence Interfaces
6. PostgreSQL Persistence
7. Execution Runtime
8. Node Registry
9. Built-in Nodes
10. Trigger System
11. Event System
12. Observability
13. API
14. Python SDK
15. Plugin System
16. Frontend Project Management
17. Frontend Graph Editor
18. Execution Monitoring
19. Reports
20. Import / Export
21. Deployment / Operations
```

The exact roadmap is defined separately in:

```text
/docs/roadmap-backend.md
/docs/roadmap-frontend.md
```

----------

# 162. Definition of Architectural Completion

The architecture is successfully implemented when the following flow works without special-case implementations:

```text
Developer creates custom Python Node
        ↓
Node packaged as Plugin
        ↓
Plugin installed
        ↓
Node registered
        ↓
Node appears in Frontend
        ↓
User adds Node to Project
        ↓
Schema-driven configuration
        ↓
Project validation
        ↓
Project published
        ↓
Trigger starts Execution
        ↓
Runtime schedules Node
        ↓
Node executes
        ↓
Outputs flow through graph
        ↓
Events are emitted
        ↓
Execution state is persisted
        ↓
Frontend receives live updates
        ↓
Execution can be inspected
        ↓
Logs / tokens / costs are available
        ↓
Project Version can be exported
```

No special-case path should be required for custom Nodes.

----------

# 163. Architectural North Star

The Engine should remain understandable as this system:

```text
                 ┌──────────────────┐
                 │      Engine      │
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │     Project      │
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │ Project Version  │
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │      Graph       │
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │      Nodes       │
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │    Execution     │
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │     Events       │
                 └────────┬─────────┘
                          │
              ┌───────────┼───────────┐
              │           │           │
           Logs        Metrics      Reports
```

The architecture must preserve this conceptual simplicity even as implementation complexity grows.