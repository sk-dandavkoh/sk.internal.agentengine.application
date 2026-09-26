# Backend Roadmap

## 1. Purpose

This roadmap defines the complete backend implementation plan for the Agent Orchestration Engine.

The roadmap is intentionally divided into **isolated implementation phases**.

Each phase is designed so that OpenCode can:

1. start a fresh session
2. read the repository documentation
3. inspect the current implementation
4. implement exactly one phase
5. run all relevant tests
6. verify the phase against its acceptance criteria
7. stop with a clean, reviewable result

A phase must produce a meaningful, working increment of the backend.

Phases must not depend on undocumented work from future phases.

---

# 2. Implementation Rules

## 2.1 One Phase Per Session

OpenCode should normally work on exactly one roadmap phase per session.

A session must not silently start implementing future phases.

If the current phase reveals a required architectural issue affecting a future phase:

- fix the issue if it is necessary for the current phase
- document the issue
- do not implement unrelated future functionality

---

## 2.2 Read Documentation Before Coding

At the beginning of every phase, OpenCode must read:

```text
/AGENTS.md
/backend/AGENTS.md
/docs/vision.md
/docs/architecture.md
/docs/roadmap-backend.md
```

If relevant, also read:

```text
/docs/domain-model.md
/docs/node-system.md
/docs/execution-engine.md
/docs/project-format.md
/docs/plugin-system.md
/docs/sdk.md
```

Only documents that already exist need to be read.

----------

## 2.3 Inspect Existing Implementation

Before modifying code, OpenCode must:

-   inspect the current backend structure
    
-   inspect relevant existing modules
    
-   inspect tests
    
-   identify existing abstractions
    
-   identify incomplete implementations
    
-   avoid duplicating existing functionality
    

The repository is the source of truth for the current implementation state.

----------

## 2.4 Do Not Skip Tests

Every phase must include appropriate tests.

A phase is not complete when the implementation merely compiles.

A phase is complete when:

```text
Implementation
    +
Tests
    +
Validation
    +
Documentation
    +
Acceptance Criteria
```

are satisfied.

----------

## 2.5 No Placeholder Architecture

Do not create large amounts of:

```text
TODO
pass
NotImplementedError
fake implementations
mock production services
hard-coded demo behavior
```

unless the phase explicitly requires a deliberate interface stub.

Interfaces should be implemented sufficiently for the current phase.

----------

## 2.6 Preserve Architectural Boundaries

Implementation must follow:

```text
API
 ↓
Application
 ↓
Domain
 ↑
Infrastructure
```

and:

```text
Frontend
 ↓
API
 ↓
Application Services
 ↓
Domain / Runtime
```

Do not introduce shortcuts merely to finish a phase faster.

----------

# 3. Phase Dependency Graph

```text
Phase 00
Repository Foundation
        │
        ▼
Phase 01
Backend Skeleton
        │
        ▼
Phase 02
Domain Model
        │
        ▼
Phase 03
Node Contract
        │
        ▼
Phase 04
Project Graph + Validation
        │
        ▼
Phase 05
Persistence
        │
        ▼
Phase 06
Project Application Services
        │
        ▼
Phase 07
Execution Domain + State Machine
        │
        ▼
Phase 08
Execution Runtime
        │
        ▼
Phase 09
Node Registry + Built-in Nodes
        │
        ▼
Phase 10
Trigger System
        │
        ▼
Phase 11
Event System
        │
        ▼
Phase 12
Observability
        │
        ▼
Phase 13
Credentials + Secrets
        │
        ▼
Phase 14
AI Provider Architecture
        │
        ▼
Phase 15
LLM Node
        │
        ▼
Phase 16
Python Node SDK
        │
        ▼
Phase 17
Plugin System
        │
        ▼
Phase 18
Project Import / Export
        │
        ▼
Phase 19
Reports
        │
        ▼
Phase 20
Authentication + Authorization
        │
        ▼
Phase 21
CLI
        │
        ▼
Phase 22
Workers + Queue Architecture
        │
        ▼
Phase 23
Reliability + Recovery
        │
        ▼
Phase 24
Performance + Resource Limits
        │
        ▼
Phase 25
Security Hardening
        │
        ▼
Phase 26
Operations + Deployment
        │
        ▼
Phase 27
Backend V1 Integration
```

----------

# 4. Phase 00 — Repository Foundation

## Goal

Create the backend development foundation without implementing business functionality.

## Scope

Implement:

```text
backend/
├── pyproject.toml
├── src/
│   └── ...
├── tests/
└── migrations/
```

Configure:

-   Python version
    
-   uv
    
-   linting
    
-   formatting
    
-   type checking
    
-   pytest
    
-   test discovery
    
-   basic application entry point
    

Establish the package naming convention.

## Tests

Create a minimal test proving:

```text
backend imports successfully
test suite executes
application starts
```

## Acceptance Criteria

-   `uv sync` works
    
-   test suite runs
    
-   linting works
    
-   formatting works
    
-   type checking works
    
-   backend process starts
    
-   no business logic exists yet
    

## Deliverable

A clean, reproducible backend development environment.

----------

# 5. Phase 01 — Backend Application Skeleton

## Goal

Create the architectural layer structure.

## Scope

Implement the initial separation:

```text
api/
application/
domain/
infrastructure/
runtime/
nodes/
providers/
plugins/
security/
observability/
```

Create:

-   application bootstrap
    
-   dependency injection foundation
    
-   configuration service
    
-   logging foundation
    
-   API application
    
-   health endpoint
    
-   readiness endpoint
    

## API

Implement:

```text
GET /health
GET /ready
```

## Tests

Test:

-   application startup
    
-   health endpoint
    
-   readiness endpoint
    
-   configuration loading
    
-   dependency wiring
    

## Acceptance Criteria

The backend starts with a clearly separated architecture.

No business logic belongs in the API routes.

----------

# 6. Phase 02 — Core Domain Model

## Goal

Implement the fundamental domain objects.

## Scope

Implement:

```text
Project
ProjectDraft
ProjectVersion
Graph
NodeInstance
Edge
Trigger
Execution
NodeExecution
CredentialReference
PluginMetadata
```

Define:

-   IDs
    
-   timestamps
    
-   lifecycle state
    
-   value objects
    
-   domain validation primitives
    

Do not implement database persistence yet.

## Tests

Test:

-   creation
    
-   invariants
    
-   invalid states
    
-   serialization where appropriate
    
-   equality/identity behavior
    
-   state transitions that already belong to the domain
    

## Acceptance Criteria

The core domain can be instantiated and validated without PostgreSQL, Redis, FastAPI or external services.

----------

# 7. Phase 03 — Node Contract

## Goal

Implement the foundational Node system.

## Scope

Implement:

```text
NodeDefinition
NodeInstance
NodeMetadata
NodeInput
NodeOutput
NodeConfigurationSchema
NodeCapabilities
NodeExecutionContext
NodeResult
NodeError
```

Define the base Node contract.

Support:

```text
identity
version
metadata
configuration schema
input ports
output ports
validation
execution
documentation metadata
```

## Tests

Create contract tests for a minimal example Node.

Test:

-   metadata
    
-   configuration
    
-   ports
    
-   validation
    
-   execution
    
-   errors
    
-   versioning
    

## Acceptance Criteria

A Node can be defined independently from the runtime and later executed through the standardized contract.

----------

# 8. Phase 04 — Project Graph + Validation

## Goal

Make Projects structurally validatable.

## Scope

Implement:

-   graph construction
    
-   node insertion
    
-   node removal
    
-   edge creation
    
-   edge removal
    
-   port resolution
    
-   type compatibility
    
-   required input validation
    
-   graph connectivity validation
    
-   cycle detection
    
-   duplicate node detection
    
-   missing node detection
    
-   configuration validation
    

Implement structured validation errors.

## Tests

Cover:

```text
valid graph
invalid connection
missing input
invalid configuration
unknown node
duplicate node
cycle
parallel branches
merge
```

## Acceptance Criteria

A Project Draft can be fully validated without executing it.

----------

# 9. Phase 05 — Persistence Foundation

## Goal

Persist the domain.

## Scope

Introduce PostgreSQL.

Implement:

```text
database configuration
connection management
migrations
repositories
transactions
```

Persist at minimum:

```text
projects
project_versions
nodes / graph data
triggers
```

Execution persistence may be introduced in this phase only if required by the repository architecture; otherwise defer it to Phase 07.

## Repository Interfaces

Implement:

```text
ProjectRepository
ProjectVersionRepository
```

The application layer must depend on interfaces.

## Tests

Use integration tests against PostgreSQL.

Test:

-   create
    
-   read
    
-   update
    
-   delete where applicable
    
-   transactions
    
-   migrations
    
-   project version persistence
    

## Acceptance Criteria

Project data survives backend restart.

----------

# 10. Phase 06 — Project Application Services

## Goal

Expose the Project lifecycle through application services.

## Scope

Implement:

```text
CreateProject
GetProject
UpdateProjectDraft
DeleteProject
ValidateProject
PublishProject
GetProjectVersion
ListProjects
ListProjectVersions
```

Implement the lifecycle:

```text
Draft
 ↓
Validate
 ↓
Publish
 ↓
Immutable Version
```

Published versions must not be mutable.

## API

Implement:

```text
/api/v1/projects
/api/v1/projects/{id}
/api/v1/projects/{id}/validate
/api/v1/projects/{id}/publish
/api/v1/projects/{id}/versions
```

## Tests

Test:

-   project creation
    
-   draft updates
    
-   validation
    
-   publish
    
-   immutable versions
    
-   API error handling
    

## Acceptance Criteria

A user can create and publish a valid Project through the API.

----------

# 11. Phase 07 — Execution Domain + State Machine

## Goal

Create the formal execution model.

## Scope

Implement:

```text
Execution
NodeExecution
ExecutionState
NodeExecutionState
ExecutionError
RetryPolicy
TimeoutPolicy
CancellationState
```

Execution states:

```text
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

Node states:

```text
PENDING
READY
RUNNING
COMPLETED
FAILED
SKIPPED
CANCELLED
```

Implement explicit state transition validation.

## Tests

Test every valid and invalid state transition.

## Acceptance Criteria

Execution lifecycle is deterministic and invalid transitions are rejected.

----------

# 12. Phase 08 — Execution Runtime

## Goal

Implement the core graph execution engine.

## Scope

Implement:

```text
GraphLoader
GraphValidator
ExecutionRuntime
Scheduler
DependencyResolver
NodeExecutor
StateManager
```

Support:

```text
linear execution
branching
parallel independent nodes
output propagation
node failure
execution failure
successful completion
```

Implement execution persistence.

## Tests

Required scenarios:

```text
A → B → C

A → B
  ↘ C

A → B
  ↘ C → D

Node failure

Invalid graph

Parallel execution
```

## Acceptance Criteria

A published Project Version can be executed from start to finish.

----------

# 13. Phase 09 — Node Registry + Built-in Nodes

## Goal

Create the unified Node Registry and first production Nodes.

## Scope

Implement:

```text
NodeRegistry
NodeResolver
NodeMetadataRegistry
```

Built-in Nodes:

```text
core.input
core.output
core.log
core.if
core.switch
core.delay
core.merge
core.http
core.web_search
core.smtp
```

The exact initial Node set may be adjusted according to implementation feasibility.

## Tests

Test:

-   registration
    
-   lookup
    
-   version compatibility
    
-   Node execution
    
-   schema validation
    

## Acceptance Criteria

Nodes are discoverable through the registry and executable through the same runtime.

----------

# 14. Phase 10 — Trigger System

## Goal

Allow executions to start through different trigger mechanisms.

## Scope

Implement:

```text
ManualTrigger
APITrigger
CronTrigger
IntervalTrigger
WebhookTrigger
```

Create:

```text
TriggerService
TriggerRegistry
TriggerContext
```

All triggers must converge on the same Execution Service.

## Tests

Test:

-   manual trigger
    
-   API trigger
    
-   cron scheduling
    
-   interval scheduling
    
-   webhook payload
    
-   invalid trigger configuration
    

## Acceptance Criteria

No trigger type implements its own execution engine.

----------

# 15. Phase 11 — Event System

## Goal

Introduce structured application/runtime events.

## Scope

Implement:

```text
Event
EventType
EventPublisher
EventSubscriber
EventRepository
```

Events:

```text
execution.created
execution.started
execution.completed
execution.failed
execution.cancelled

node.started
node.completed
node.failed
node.skipped
```

Persist events.

## Tests

Test:

-   event creation
    
-   publication
    
-   persistence
    
-   subscription
    
-   event ordering where guaranteed
    
-   event payload validation
    

## Acceptance Criteria

Execution lifecycle can be reconstructed from persisted events plus canonical execution state.

----------

# 16. Phase 12 — Observability

## Goal

Make executions professionally observable.

## Scope

Implement:

```text
structured logging
execution correlation IDs
node correlation IDs
metrics
execution timing
node timing
error recording
```

Collect:

```text
execution count
success/failure
duration
node duration
active executions
queue depth where applicable
```

## Tests

Verify:

-   correlation IDs
    
-   structured log fields
    
-   metrics
    
-   execution timing
    
-   secret-safe logging
    

## Acceptance Criteria

A failed execution can be diagnosed from structured backend observability data.

----------

# 17. Phase 13 — Credentials + Secrets

## Goal

Introduce secure credential management.

## Scope

Implement:

```text
Credential
CredentialReference
CredentialService
Secret encryption
Secret decryption
Secret redaction
```

Support:

```text
API keys
SMTP credentials
provider credentials
generic secret values
```

Project configuration must contain references rather than raw secrets.

## Tests

Test:

-   encryption/decryption
    
-   access control
    
-   reference resolution
    
-   redaction
    
-   export exclusion
    
-   log exclusion
    

## Acceptance Criteria

Secrets never appear in Project JSON, normal API responses, logs or execution reports.

----------

# 18. Phase 14 — AI Provider Architecture

## Goal

Implement provider-independent LLM infrastructure.

## Scope

Create:

```text
AIService
LLMProvider
ProviderAdapter
LLMRequest
LLMResponse
Message
ToolCall
TokenUsage
ProviderError
```

Support at least one real provider.

Provider-specific implementation must stay behind the provider abstraction.

LiteLLM may be used as an infrastructure abstraction.

## Tests

Use mocked provider adapters for deterministic tests.

Test:

-   request mapping
    
-   response normalization
    
-   usage extraction
    
-   error normalization
    
-   timeout behavior
    

## Acceptance Criteria

The domain/runtime does not depend on a specific LLM provider.

----------

# 19. Phase 15 — LLM Node

## Goal

Implement the production LLM Node.

## Scope

Support:

```text
provider
model
system prompt
user prompt
variables
temperature
max tokens
structured output where supported
tool calls where supported
```

Implement prompt rendering.

Implement token usage and cost estimation.

## Tests

Test:

```text
prompt rendering
variable substitution
missing variables
provider errors
token usage
cost calculation
structured output
```

## Acceptance Criteria

An LLM Node can be placed into a Project and executed through the normal runtime.

----------

# 20. Phase 16 — Python Node SDK

## Goal

Make custom Node development possible without modifying Engine core.

## Scope

Implement SDK components:

```text
Node
NodeMetadata
Input
Output
ConfigSchema
ExecutionContext
Logger
Metrics
Credentials
Artifacts
Events
NodeError
```

Provide developer utilities:

```text
node test
node validate
node package
```

where practical.

## Tests

Create an external-style example custom Node using only the SDK.

## Acceptance Criteria

A developer can implement a custom Node without importing internal Engine implementation modules.

----------

# 21. Phase 17 — Plugin System

## Goal

Turn custom Nodes into installable Plugins.

## Scope

Implement:

```text
PluginManifest
PluginRegistry
PluginLoader
PluginInstaller
PluginValidator
PluginCompatibilityChecker
```

Support:

```text
discover
validate
install
register
load
disable
remove
```

Plugins must declare:

```text
id
version
engine compatibility
SDK compatibility
dependencies
components
```

## Tests

Test the complete lifecycle with a sample plugin.

## Acceptance Criteria

A custom Node can be:

```text
created
packaged
installed
registered
discovered
used
disabled
removed
```

without changing Engine core.

----------

# 22. Phase 18 — Project Import / Export

## Goal

Make Projects portable.

## Scope

Implement:

```text
ProjectPackage
ProjectManifest
ProjectExporter
ProjectImporter
SchemaValidator
DependencyValidator
```

Package contents:

```text
manifest
project
graph
node configuration
prompts
resources
documentation
assets
```

Never include secrets.

## Tests

Test:

```text
export
import
round trip
invalid package
missing dependency
incompatible version
secret exclusion
```

## Acceptance Criteria

A Project can be exported and imported into another compatible installation.

----------

# 23. Phase 19 — Reports

## Goal

Generate useful execution reports from persisted data.

## Scope

Implement:

```text
ReportService
ExecutionReport
ReportGenerator
```

Reports should contain:

```text
project
project version
execution status
duration
nodes
errors
token usage
cost estimates
outputs
artifacts
timestamps
```

Support at least one export format.

## Tests

Test:

-   successful report
    
-   failed execution report
    
-   cost information
    
-   secret redaction
    
-   artifact references
    

## Acceptance Criteria

Reports can be generated without requiring the frontend.

----------

# 24. Phase 20 — Authentication + Authorization

## Goal

Secure the backend for self-hosted usage.

## Scope

Implement:

```text
User
Session
AuthenticationService
AuthorizationService
```

Version 1 should support local authentication.

Implement basic authorization boundaries without building full enterprise RBAC.

Protect:

```text
projects
executions
credentials
plugins
settings
```

## Tests

Test:

```text
login
logout
invalid credentials
session handling
unauthorized access
authorized access
credential protection
```

## Acceptance Criteria

Unauthenticated clients cannot access protected application functionality.

----------

# 25. Phase 21 — CLI

## Goal

Provide operational and developer CLI access.

## Scope

Implement:

```text
project create
project validate
project publish
project run
project import
project export

node list
node validate
node test

plugin list
plugin install
plugin remove

execution get
execution cancel

backup
restore
```

CLI commands should reuse application services or API contracts rather than duplicating domain logic.

## Tests

Test representative commands.

## Acceptance Criteria

Core Engine operations can be performed without the frontend.

----------

# 26. Phase 22 — Workers + Queue Architecture

## Goal

Separate long-running execution from API request lifetime.

## Scope

Implement:

```text
ExecutionQueue
Worker
WorkerManager
Job
Heartbeat
```

Flow:

```text
API
 ↓
Create Execution
 ↓
Queue
 ↓
Worker
 ↓
Runtime
```

Redis may be used as the initial queue implementation.

## Tests

Test:

-   enqueue
    
-   dequeue
    
-   worker execution
    
-   worker failure
    
-   duplicate delivery handling
    
-   heartbeat
    
-   queue recovery
    

## Acceptance Criteria

Long-running Projects do not depend on an open HTTP request.

----------

# 27. Phase 23 — Reliability + Recovery

## Goal

Make execution resilient to failures.

## Scope

Implement:

```text
retry policies
exponential backoff
timeouts
cancellation
stale execution detection
worker crash recovery
execution recovery
```

Support error categories:

```text
transient
permanent
validation
configuration
provider
timeout
cancellation
```

## Tests

Simulate:

```text
Node failure
provider timeout
worker crash
process restart
retry exhaustion
cancellation
```

## Acceptance Criteria

The system never silently loses execution state.

----------

# 28. Phase 24 — Performance + Resource Limits

## Goal

Protect the Engine from uncontrolled workloads.

## Scope

Implement configurable limits for:

```text
execution duration
node duration
loop iterations
concurrency
output size
artifact size
HTTP response size
LLM token usage
retry count
execution depth
```

Implement concurrency controls.

## Tests

Test that every relevant limit is enforced.

## Acceptance Criteria

A malformed or badly configured Project cannot trivially consume unlimited resources.

----------

# 29. Phase 25 — Security Hardening

## Goal

Perform a dedicated backend security pass.

## Scope

Review and harden:

```text
authentication
authorization
credential storage
secret redaction
file access
HTTP access
SSRF protections
path traversal
input validation
plugin handling
expression evaluation
API limits
webhooks
error messages
logging
```

Implement security tests.

## Acceptance Criteria

All documented security boundaries are tested and no known critical secret-leak or arbitrary-code paths remain.

----------

# 30. Phase 26 — Operations + Deployment

## Goal

Make the backend production-deployable for self-hosted installations.

## Scope

Implement/document:

```text
Docker image
Docker Compose
environment configuration
health checks
readiness checks
database migrations
startup behavior
shutdown behavior
logging
backup
restore
```

Ensure persistent volumes are correctly configured.

## Tests

Validate a clean installation from scratch.

Test:

```text
start
migrate
create data
restart
recover data
backup
restore
```

## Acceptance Criteria

A new self-hosted installation can be started reproducibly.

----------

# 31. Phase 27 — Backend V1 Integration

## Goal

Validate the complete backend as one coherent system.

## Scope

Run the complete end-to-end flow:

```text
Create Project
      ↓
Add Nodes
      ↓
Configure Nodes
      ↓
Validate
      ↓
Publish
      ↓
Configure Trigger
      ↓
Execute
      ↓
Observe
      ↓
Inspect
      ↓
Generate Report
      ↓
Export Project
      ↓
Import Project
      ↓
Execute Imported Project
```

Use the official example Projects.

At minimum:

```text
Hello World
LLM
Web Research
Lead Generation
```

## Tests

Run:

```text
unit tests
integration tests
contract tests
API tests
runtime tests
plugin tests
security tests
E2E tests
```

## Acceptance Criteria

The complete backend fulfills the Version 1 architecture and the success criteria defined in `/docs/vision.md`.

----------

# 32. Phase Completion Protocol

Every phase must finish with the following process.

## Step 1 — Inspect

OpenCode inspects:

```text
current code
current tests
documentation
previous phase output
```

## Step 2 — Plan

Before coding, OpenCode creates a short internal implementation plan containing:

```text
files to create
files to modify
interfaces
tests
migration impact
risks
```

## Step 3 — Implement

Implement only the current phase.

## Step 4 — Test

Run:

```text
formatter
linter
type checker
unit tests
integration tests
phase-specific tests
```

as applicable.

## Step 5 — Review

Verify:

```text
architecture
security
error handling
typing
tests
documentation
backward compatibility
```

## Step 6 — Clean Up

Remove:

```text
unused imports
dead code
debug output
temporary files
unnecessary TODOs
duplicate implementations
```

## Step 7 — Final Verification

Check every acceptance criterion.

## Step 8 — Report

The final session response should contain:

```text
Phase completed
Implemented:
- ...

Tests:
- ...

Validation:
- ...

Important architectural decisions:
- ...

Known limitations:
- ...

Recommended next phase:
- Phase XX
```

Do not claim completion if acceptance criteria are not satisfied.

----------

# 33. Phase Isolation Rules

A phase may modify previous-phase code if required to integrate the current functionality.

A phase must not intentionally implement the next phase.

Example:

```text
Phase 08 Execution Runtime
```

may create interfaces required for:

```text
Phase 09 Node Registry
```

but must not implement the complete Node Registry.

----------

# 34. Definition of Done

A phase is DONE only when:

```text
[ ] Scope implemented
[ ] Architecture respected
[ ] Tests implemented
[ ] Tests passing
[ ] Formatting passing
[ ] Linting passing
[ ] Type checking passing
[ ] Database migrations complete where required
[ ] API contracts updated where required
[ ] Documentation updated where required
[ ] No secrets introduced
[ ] No unrelated future features implemented
[ ] Acceptance criteria satisfied
```

----------

# 35. Phase Status Tracking

The roadmap itself should be treated as the canonical implementation sequence.

Recommended status values:

```text
PLANNED
IN_PROGRESS
BLOCKED
COMPLETED
```

Do not mark a phase as completed merely because coding has stopped.

A phase becomes:

```text
COMPLETED
```

only after its acceptance criteria and verification are complete.

----------

# 36. Recommended OpenCode Session Pattern

Each phase should be executed using the following pattern:

```text
Session Start
     ↓
Read AGENTS.md
     ↓
Read relevant architecture docs
     ↓
Read this roadmap
     ↓
Identify current phase
     ↓
Inspect repository
     ↓
Create implementation plan
     ↓
Implement phase
     ↓
Run tests
     ↓
Fix failures
     ↓
Run full relevant validation
     ↓
Review architecture
     ↓
Update documentation
     ↓
Final phase report
     ↓
STOP
```

OpenCode should not automatically continue into the next phase.

----------

# 37. Phase Prompts

The following prompts are intended to be used as separate OpenCode sessions.

----------

## Phase 00 Prompt

```text
Implement Backend Roadmap Phase 00 — Repository Foundation.

Read first:
- /AGENTS.md
- /backend/AGENTS.md
- /docs/vision.md
- /docs/architecture.md
- /docs/roadmap-backend.md

Inspect the existing repository before changing anything.

Implement ONLY Phase 00.

Create the backend development foundation:
- Python/uv setup
- package structure
- test infrastructure
- formatting
- linting
- type checking
- migrations directory
- minimal application entry point

Do not implement business functionality from later phases.

Run all relevant validation.

Do not stop at the first successful implementation. Review the resulting structure and make sure it follows the architecture.

At the end, report:
- implemented files
- tests
- validation results
- known limitations
- whether Phase 00 acceptance criteria are fully satisfied

Do not start Phase 01.
```

----------

## Phase 01 Prompt

```text
Implement Backend Roadmap Phase 01 — Backend Application Skeleton.

Read:
- /AGENTS.md
- /backend/AGENTS.md
- /docs/vision.md
- /docs/architecture.md
- /docs/roadmap-backend.md

Inspect the current repository and Phase 00 implementation.

Implement ONLY Phase 01.

Create the backend architectural layers and application bootstrap:
- api
- application
- domain
- infrastructure
- runtime
- nodes
- providers
- plugins
- security
- observability
- configuration
- dependency injection
- health endpoint
- readiness endpoint

Keep business logic out of API routes.

Run tests, formatter, linter and type checker.

Verify the architecture after implementation.

Do not implement Phase 02 or later.

At the end provide a concise phase completion report and stop.
```

----------

## Phase 02 Prompt

```text
Implement Backend Roadmap Phase 02 — Core Domain Model.

Read the required project documentation and inspect all existing backend code.

Implement ONLY Phase 02.

Create the core domain objects:
- Project
- ProjectDraft
- ProjectVersion
- Graph
- NodeInstance
- Edge
- Trigger
- Execution
- NodeExecution
- CredentialReference
- PluginMetadata

Focus on domain correctness and explicit invariants.

Do not introduce PostgreSQL persistence yet.

Domain code must not depend on FastAPI, SQLAlchemy, Redis or external providers.

Create comprehensive domain tests.

Run all relevant validation.

Do not implement Phase 03 or later.

Stop after Phase 02 is complete.
```

----------

## Phase 03 Prompt

```text
Implement Backend Roadmap Phase 03 — Node Contract.

Read all relevant documentation and inspect the existing domain model.

Implement ONLY Phase 03.

Create the standardized Node contract:
- NodeDefinition
- NodeInstance integration
- metadata
- version
- input ports
- output ports
- configuration schema
- capabilities
- execution context
- result
- errors
- validation

Create a reusable Node contract test suite.

Implement a minimal example Node for testing.

Keep the Node contract independent from the future execution runtime.

Run all validation.

Do not implement the Node Registry, built-in Nodes, SDK or Plugin System yet.

Stop after Phase 03.
```

----------

## Phase 04 Prompt

```text
Implement Backend Roadmap Phase 04 — Project Graph + Validation.

Read the project documentation and inspect all current domain and Node code.

Implement ONLY Phase 04.

Implement:
- graph construction
- node management
- edge management
- port resolution
- type compatibility
- required input validation
- configuration validation
- graph connectivity validation
- cycle detection
- duplicate node detection
- missing node detection
- structured validation errors

Create comprehensive tests for valid and invalid graphs.

Do not implement persistence or runtime execution.

Run all relevant validation.

Stop after Phase 04.
```

----------

## Phase 05 Prompt

```text
Implement Backend Roadmap Phase 05 — Persistence Foundation.

Read the project documentation and inspect the existing backend architecture.

Implement ONLY Phase 05.

Introduce PostgreSQL persistence with:
- database configuration
- migrations
- connection management
- repository interfaces
- PostgreSQL repository implementations
- transactions

Persist Projects and Project Versions and their graph data.

Keep persistence concerns outside the domain layer.

Create integration tests.

Run migrations, tests, formatter, linter and type checker.

Do not implement execution persistence unless strictly required by the current architecture.

Do not implement Phase 06.

Stop after Phase 05.
```

----------

## Phase 06 Prompt

```text
Implement Backend Roadmap Phase 06 — Project Application Services.

Read all relevant documentation and inspect the existing backend.

Implement ONLY Phase 06.

Implement:
- CreateProject
- GetProject
- UpdateProjectDraft
- DeleteProject
- ValidateProject
- PublishProject
- GetProjectVersion
- ListProjects
- ListProjectVersions

Implement immutable Project Versions.

Expose the required /api/v1/projects endpoints.

Create API and application tests.

Do not implement execution runtime or triggers.

Run all validation.

Stop after Phase 06.
```

----------

## Phase 07 Prompt

```text
Implement Backend Roadmap Phase 07 — Execution Domain + State Machine.

Read all relevant documentation and inspect the current implementation.

Implement ONLY Phase 07.

Implement:
- Execution
- NodeExecution
- execution states
- node execution states
- retry policy
- timeout policy
- cancellation state
- structured execution errors
- explicit state transitions

Create comprehensive state transition tests.

Do not implement the graph execution runtime yet.

Do not implement triggers, queues or workers.

Run all validation.

Stop after Phase 07.
```

----------

## Phase 08 Prompt

```text
Implement Backend Roadmap Phase 08 — Execution Runtime.

Read:
- /AGENTS.md
- /backend/AGENTS.md
- /docs/vision.md
- /docs/architecture.md
- /docs/roadmap-backend.md

Inspect all existing domain, persistence and Node contract code.

Implement ONLY Phase 08.

Build:
- GraphLoader
- GraphValidator integration
- ExecutionRuntime
- Scheduler
- DependencyResolver
- NodeExecutor
- StateManager

Support:
- linear graphs
- branching
- parallel independent nodes
- output propagation
- node failures
- successful completion
- execution persistence

Use the existing Node contract.

Do not implement the future Node Registry, Trigger System, Event System or Worker System except for minimal interfaces absolutely required.

Create thorough runtime tests.

Run the complete relevant test suite.

Review for race conditions, state consistency and incorrect execution ordering.

Stop after Phase 08.
```

----------

## Phase 09 Prompt

```text
Implement Backend Roadmap Phase 09 — Node Registry + Built-in Nodes.

Read all relevant documentation and inspect the current Node and runtime implementation.

Implement ONLY Phase 09.

Create:
- NodeRegistry
- NodeResolver
- metadata registry

Implement the planned built-in Nodes.

All Nodes must use the existing Node contract and execution runtime.

Do not create a second execution mechanism.

Create Node contract and runtime integration tests.

Do not implement the Python SDK or Plugin System yet.

Stop after Phase 09.
```

----------

## Phase 10 Prompt

```text
Implement Backend Roadmap Phase 10 — Trigger System.

Read the roadmap and architecture and inspect the current execution services.

Implement ONLY Phase 10.

Implement:
- TriggerService
- TriggerRegistry
- TriggerContext
- Manual Trigger
- API Trigger
- Cron Trigger
- Interval Trigger
- Webhook Trigger

Every trigger must converge on the existing Execution Service and Execution Runtime.

Do not create trigger-specific execution engines.

Create tests for every trigger type.

Stop after Phase 10.
```

----------

## Phase 11 Prompt

```text
Implement Backend Roadmap Phase 11 — Event System.

Read the architecture and inspect the current execution lifecycle.

Implement ONLY Phase 11.

Implement:
- Event model
- Event types
- Event publisher
- Event subscribers
- Event persistence

Cover execution and node lifecycle events.

Events must not become the canonical source of execution state.

Create comprehensive event tests.

Stop after Phase 11.
```

----------

## Phase 12 Prompt

```text
Implement Backend Roadmap Phase 12 — Observability.

Read the roadmap and architecture.

Implement ONLY Phase 12.

Implement:
- structured logging
- correlation IDs
- execution/node correlation
- metrics
- execution timing
- node timing
- error recording

Ensure secrets cannot leak into logs.

Create tests for observability behavior.

Do not implement unrelated operational features.

Stop after Phase 12.
```

----------

## Phase 13 Prompt

```text
Implement Backend Roadmap Phase 13 — Credentials + Secrets.

Read all security-related documentation.

Implement ONLY Phase 13.

Implement:
- Credential model
- CredentialReference
- CredentialService
- encrypted storage
- resolution
- redaction
- secure access

Ensure raw secrets never appear in:
- Project configuration
- normal API responses
- logs
- reports
- exports

Create dedicated security tests.

Review existing code for accidental secret leakage introduced by earlier phases and fix issues that are directly relevant.

Do not implement full authentication/authorization yet.

Stop after Phase 13.
```

----------

## Phase 14 Prompt

```text
Implement Backend Roadmap Phase 14 — AI Provider Architecture.

Read the architecture and inspect existing Nodes and services.

Implement ONLY Phase 14.

Create:
- AIService
- provider abstraction
- provider adapter interface
- normalized request/response types
- token usage
- tool calls
- provider error normalization

Integrate one real provider through the abstraction.

Provider-specific code must remain isolated.

Use mocked providers for deterministic tests.

Do not implement the LLM Node yet.

Stop after Phase 14.
```

----------

## Phase 15 Prompt

```text
Implement Backend Roadmap Phase 15 — LLM Node.

Read all relevant documentation and inspect the provider abstraction.

Implement ONLY Phase 15.

Implement the LLM Node with:
- provider
- model
- system prompt
- user prompt
- variables
- temperature
- max tokens
- structured output where supported
- token usage
- cost estimation

Implement controlled prompt rendering.

Create comprehensive tests.

Do not introduce provider-specific logic into the Node contract or domain model.

Stop after Phase 15.
```

----------

## Phase 16 Prompt

```text
Implement Backend Roadmap Phase 16 — Python Node SDK.

Read the architecture, Node contract and current implementation.

Implement ONLY Phase 16.

Create a public developer SDK that exposes:
- Node
- metadata
- ports
- config schema
- execution context
- logging
- metrics
- credentials
- artifacts
- events
- errors
- testing helpers

Create an example external-style custom Node that only uses the SDK.

Ensure custom Node developers do not need internal Engine imports.

Do not implement the Plugin System yet.

Stop after Phase 16.
```

----------

## Phase 17 Prompt

```text
Implement Backend Roadmap Phase 17 — Plugin System.

Read the architecture, Node SDK and roadmap.

Implement ONLY Phase 17.

Implement:
- PluginManifest
- PluginRegistry
- PluginLoader
- PluginInstaller
- PluginValidator
- compatibility checks

Support:
discover
validate
install
register
load
disable
remove

Create an actual sample plugin containing a custom Node.

Test the full lifecycle.

Do not implement a marketplace.

Stop after Phase 17.
```

----------

## Phase 18 Prompt

```text
Implement Backend Roadmap Phase 18 — Project Import / Export.

Read the project format requirements and inspect the current Project model.

Implement ONLY Phase 18.

Implement:
- manifest
- package format
- exporter
- importer
- schema validation
- dependency validation

Never export secrets.

Create round-trip tests.

Test incompatible versions and missing dependencies.

Stop after Phase 18.
```

----------

## Phase 19 Prompt

```text
Implement Backend Roadmap Phase 19 — Reports.

Read the architecture and inspect execution persistence.

Implement ONLY Phase 19.

Implement:
- ReportService
- ExecutionReport
- report generation
- at least one export format

Reports must be generated from persisted backend state.

Ensure secrets are redacted.

Create tests for successful and failed executions.

Stop after Phase 19.
```

----------

## Phase 20 Prompt

```text
Implement Backend Roadmap Phase 20 — Authentication + Authorization.

Read the security requirements.

Implement ONLY Phase 20.

Implement:
- local users
- authentication
- sessions
- authorization
- protected API endpoints

Do not build enterprise SSO or complex RBAC.

Create security-focused tests.

Verify credentials and project data cannot be accessed by unauthorized users.

Stop after Phase 20.
```

----------

## Phase 21 Prompt

```text
Implement Backend Roadmap Phase 21 — CLI.

Read the architecture and inspect the existing application services.

Implement ONLY Phase 21.

Create the CLI for the roadmap-defined project, node, plugin, execution, backup and restore operations.

Reuse application services instead of duplicating business logic.

Create CLI tests.

Stop after Phase 21.
```

----------

## Phase 22 Prompt

```text
Implement Backend Roadmap Phase 22 — Workers + Queue Architecture.

Read the execution architecture.

Implement ONLY Phase 22.

Introduce:
- ExecutionQueue
- Job
- Worker
- WorkerManager
- heartbeat
- Redis-backed queue if appropriate

Change long-running execution so it is not tied to HTTP request lifetime.

Preserve the existing Execution Runtime.

Create queue and worker integration tests.

Stop after Phase 22.
```

----------

## Phase 23 Prompt

```text
Implement Backend Roadmap Phase 23 — Reliability + Recovery.

Read the runtime architecture and inspect current worker/execution behavior.

Implement ONLY Phase 23.

Add:
- retries
- exponential backoff
- timeouts
- cancellation
- stale execution detection
- worker crash recovery
- execution recovery

Test process and worker failure scenarios.

Do not implement unrelated performance optimizations.

Stop after Phase 23.
```

----------

## Phase 24 Prompt

```text
Implement Backend Roadmap Phase 24 — Performance + Resource Limits.

Read the architecture and inspect the current runtime.

Implement ONLY Phase 24.

Implement configurable limits for:
- execution duration
- node duration
- loop iterations
- concurrency
- output size
- artifact size
- HTTP response size
- LLM token usage
- retries
- execution depth

Create tests proving limits are enforced.

Do not perform unrelated optimization.

Stop after Phase 24.
```

----------

## Phase 25 Prompt

```text
Implement Backend Roadmap Phase 25 — Security Hardening.

Perform a dedicated security review of the entire backend.

Read:
- /docs/vision.md
- /docs/architecture.md
- /backend/AGENTS.md
- relevant implementation docs

Implement ONLY the security-hardening scope from Phase 25.

Review:
- authentication
- authorization
- credentials
- secrets
- logs
- file access
- HTTP/SSRF
- path traversal
- plugin execution
- expression evaluation
- API limits
- webhooks
- error handling

Add security regression tests.

Do not introduce unrelated features.

Stop after Phase 25.
```

----------

## Phase 26 Prompt

```text
Implement Backend Roadmap Phase 26 — Operations + Deployment.

Read the architecture and deployment requirements.

Implement ONLY Phase 26.

Ensure the backend can be deployed reproducibly with:
- Docker
- Docker Compose
- PostgreSQL
- Redis
- persistent storage
- migrations
- health checks
- readiness checks
- configuration
- startup/shutdown behavior
- backup/restore

Test a clean installation and restart/recovery behavior.

Stop after Phase 26.
```

----------

## Phase 27 Prompt

```text
Implement Backend Roadmap Phase 27 — Backend V1 Integration.

This is the final backend integration phase.

Read all project documentation and inspect the entire backend.

Do NOT start new major architectural features.

Validate the complete Version 1 backend.

Execute the complete flow:
- create Project
- configure graph
- validate
- publish
- configure trigger
- execute
- observe
- inspect
- report
- export
- import
- execute imported Project

Run all available:
- unit tests
- integration tests
- contract tests
- API tests
- runtime tests
- plugin tests
- security tests
- E2E tests

Fix integration defects discovered during this phase.

Do not expand the Version 1 scope.

At the end provide a complete backend readiness report.

Stop after Phase 27.
```

----------

# 38. Final Backend V1 Acceptance Criteria

The backend is considered Version 1 complete when all of the following work:

```text
[ ] Backend starts reproducibly
[ ] Database migrations work
[ ] Authentication works
[ ] Projects can be created
[ ] Drafts can be edited
[ ] Projects can be validated
[ ] Projects can be published
[ ] Published versions are immutable
[ ] Nodes have stable contracts
[ ] Nodes can be registered
[ ] Built-in Nodes work
[ ] Custom Python Nodes work
[ ] Plugins work
[ ] Graphs can execute
[ ] Branching works
[ ] Parallel execution works
[ ] Control flow works where implemented
[ ] Triggers work
[ ] Executions persist
[ ] Node executions persist
[ ] Events persist
[ ] Logs are structured
[ ] Metrics exist
[ ] Credentials are encrypted
[ ] Secrets are redacted
[ ] LLM provider abstraction works
[ ] LLM Node works
[ ] Token usage is tracked
[ ] Cost estimates are tracked
[ ] Reports work
[ ] Projects can be exported
[ ] Projects can be imported
[ ] CLI works
[ ] Long-running execution is decoupled from HTTP
[ ] Retry works
[ ] Timeout works
[ ] Cancellation works
[ ] Recovery works
[ ] Resource limits work
[ ] Security tests pass
[ ] Docker deployment works
[ ] Backup/restore works
[ ] Example Projects work
[ ] Full E2E flow works
```

----------

# 39. Backend Architectural Completion Model

The completed backend must enable this exact developer journey:

```text
Python Developer
       │
       ▼
Create Node
       │
       ▼
Use Node SDK
       │
       ▼
Package Plugin
       │
       ▼
Install Plugin
       │
       ▼
Node Registry
       │
       ▼
Frontend Node Library
       │
       ▼
User adds Node
       │
       ▼
Configure Node
       │
       ▼
Project Draft
       │
       ▼
Validate
       │
       ▼
Publish
       │
       ▼
Project Version
       │
       ▼
Trigger
       │
       ▼
Execution Queue
       │
       ▼
Execution Runtime
       │
       ▼
Node Scheduler
       │
       ▼
Node Execution
       │
       ▼
Events + Logs + Metrics
       │
       ▼
Execution Persistence
       │
       ▼
Report
       │
       ▼
Project Export
```

This flow is the ultimate backend integration target.

The backend implementation must preserve the architectural principles defined in `/docs/vision.md` and `/docs/architecture.md`.