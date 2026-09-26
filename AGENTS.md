````markdown
# AGENTS.md

# Agent Orchestration Engine — Engineering Guidelines

## 1. Purpose

This repository contains a self-hosted AI Agent Orchestration Engine.

The system allows users to:

- create modular AI workflows
- compose Nodes into Projects
- configure and connect Nodes visually
- publish immutable Project Versions
- execute Projects through a runtime
- monitor executions
- inspect logs, events, metrics and errors
- manage credentials and plugins
- create custom Python Nodes
- import and export Projects
- integrate different LLM providers
- operate the complete system in a self-hosted environment

This repository must be treated as a production-grade software system.

The objective is not merely to make the application "work".

The objective is to build a system that is:

- maintainable
- testable
- observable
- secure
- deterministic where possible
- reproducible
- extensible
- operationally understandable
- architecturally coherent
- suitable for long-term development by multiple engineers

Agents working in this repository are expected to behave like senior software engineers.

---

# 2. Non-Negotiable Engineering Principles

## 2.1 Correctness over speed

Do not optimize for the number of implemented features.

Prefer:

```text
correct architecture
>
correct domain model
>
correct tests
>
correct error handling
>
correct observability
>
feature completeness
````

A feature that appears to work but cannot be reliably tested, observed, migrated, recovered or maintained is not considered complete.

---

## 2.2 Read before modifying

Before changing code, inspect:

* repository structure
* relevant source files
* existing tests
* configuration
* migrations
* dependency configuration
* API contracts
* architecture documentation
* relevant roadmap phase
* existing implementation patterns

Never assume that a component does not exist because it is not immediately visible.

---

## 2.3 Respect the architecture

The architecture documented in:

```text
/docs/vision.md
/docs/architecture.md
```

is authoritative unless an explicit architectural decision changes it.

Do not introduce shortcuts that undermine the architecture.

If a change genuinely requires architectural modification:

1. identify the conflict
2. explain the reason
3. update the appropriate documentation
4. create an ADR if the decision is architecturally significant
5. then implement it

Do not silently redefine the architecture inside implementation code.

---

# 3. Scope Discipline

## 3.1 Implement only the requested phase

When working from:

```text
/docs/roadmap-backend.md
/docs/roadmap-frontend.md
```

implement only the current phase.

Do not silently implement future phases.

If future functionality is required as a small technical prerequisite, implement the smallest stable foundation necessary and document it.

Do not use a current phase as an excuse to build half of a future subsystem.

---

## 3.2 Do not rewrite working systems unnecessarily

Before replacing an existing implementation:

* understand why it exists
* inspect its tests
* inspect its callers
* determine whether it is actually incorrect
* determine whether the replacement improves the architecture

Avoid broad rewrites when an incremental change is sufficient.

---

# 4. Source of Truth

The backend is authoritative for domain state.

The frontend is authoritative only for transient UI state.

The following must not be independently reimplemented in the frontend:

* graph validity
* execution state transitions
* project publication rules
* credential authorization
* Node compatibility rules
* runtime scheduling
* retry semantics
* execution lifecycle
* permission enforcement

Frontend validation may improve UX, but backend validation remains authoritative.

---

# 5. Backend Architecture

The backend should maintain clear boundaries between:

```text
API
Application
Domain
Infrastructure
Runtime
Nodes
Providers
Plugins
Security
Observability
```

A typical dependency direction is:

```text
API
 ↓
Application
 ↓
Domain

Infrastructure ───────→ Domain
Runtime ───────────────→ Domain / Application
Nodes ─────────────────→ Domain contracts
Providers ─────────────→ Provider abstractions
Plugins ───────────────→ Node contracts
```

The domain must not depend on:

* FastAPI
* SQLAlchemy
* Redis
* PostgreSQL
* HTTP clients
* provider SDKs
* filesystem implementations
* infrastructure-specific frameworks

unless explicitly justified by the architecture.

---

# 6. Domain Model Rules

The fundamental concepts are:

```text
Engine
Project
ProjectDraft
ProjectVersion
Graph
Node
NodeInstance
Edge
Trigger
Execution
NodeExecution
Event
CredentialReference
Plugin
```

Do not introduce duplicate concepts with overlapping responsibilities.

For example, do not create:

```text
Workflow
Pipeline
Flow
GraphDefinition
ProjectGraph
```

if they are merely alternate names for the same domain concept.

Prefer one explicit domain concept with clear semantics.

---

# 7. Project Drafts and Published Versions

A Project Draft is mutable.

A published Project Version is immutable.

The expected lifecycle is:

```text
Project
  ↓
Draft
  ↓
Validate
  ↓
Publish
  ↓
Immutable Project Version
  ↓
Execution Snapshot
```

An Execution must never accidentally execute a later modified Draft.

Execution must operate against a deterministic project/version snapshot.

---

# 8. Backend API Rules

API endpoints must be:

* explicit
* versioned
* typed
* documented
* testable
* consistent

The API version should follow:

```text
/api/v1/...
```

Do not expose domain entities directly as database models.

Use explicit API schemas/DTOs where appropriate.

Do not leak:

* database implementation details
* internal stack traces
* secrets
* provider credentials
* filesystem paths
* internal exception structures

---

# 9. Error Handling

Errors must be intentional and structured.

Avoid:

```python
except Exception:
    return {"error": "something went wrong"}
```

Prefer domain/application-specific errors.

Examples:

```text
ValidationError
ProjectNotFoundError
ProjectVersionNotFoundError
NodeNotFoundError
NodeConfigurationError
ExecutionNotFoundError
ExecutionStateError
CredentialNotFoundError
ProviderError
PluginCompatibilityError
ResourceLimitError
```

API error responses should contain:

* stable error code
* human-readable message
* optional structured details
* correlation/request identifier where applicable

Do not expose internal stack traces to normal API consumers.

Stack traces belong in secure server-side logs.

---

# 10. Exceptions and Failure Semantics

Do not use exceptions as ordinary control flow.

Exceptions should represent exceptional conditions.

Do not silently swallow exceptions.

Bad:

```python
try:
    ...
except Exception:
    pass
```

If an exception is intentionally ignored, document why.

---

# 11. Testing Philosophy

Testing is part of implementation.

A feature without appropriate tests is incomplete.

The project must use multiple levels of tests.

```text
Unit Tests
    ↓
Component Tests
    ↓
Integration Tests
    ↓
Contract Tests
    ↓
API Tests
    ↓
Infrastructure Tests
    ↓
End-to-End Tests
```

Not every function requires every test type.

The test level must match the risk.

---

# 12. Unit Tests

Unit tests should test deterministic business logic in isolation.

Examples:

* graph validation
* port compatibility
* state transitions
* retry classification
* resource limit calculations
* project versioning
* expression parsing
* configuration validation
* Node contract validation

Unit tests should not require:

* PostgreSQL
* Redis
* external internet
* real LLM providers
* real SMTP servers

unless the test is explicitly not a unit test.

Unit tests should be fast and deterministic.

---

# 13. Integration Tests

Integration tests must test real component interaction.

Examples:

```text
Application Service
    ↓
Repository
    ↓
PostgreSQL
```

or:

```text
Execution Runtime
    ↓
Node Registry
    ↓
Node
    ↓
Persistence
```

Integration tests should use real infrastructure whenever behavior depends on that infrastructure.

Do not mock PostgreSQL when testing PostgreSQL behavior.

Do not mock Redis when testing Redis behavior.

Do not mock transaction semantics when transaction correctness is the thing being tested.

---

# 14. Testcontainers

When infrastructure behavior matters, prefer ephemeral real infrastructure.

Use Testcontainers or an equivalent container-based testing approach.

Typical services:

```text
PostgreSQL
Redis
```

Potentially later:

```text
MinIO / S3-compatible storage
SMTP test server
OpenSearch
```

depending on actual project requirements.

Tests should create their infrastructure dynamically.

Example lifecycle:

```text
pytest starts
    ↓
Container created
    ↓
Service becomes ready
    ↓
Migrations executed
    ↓
Tests run
    ↓
Container destroyed
```

Containers must not depend on manually pre-existing infrastructure.

---

# 15. Testcontainer Rules

Testcontainers must:

* use explicit image versions
* wait for readiness
* expose only required ports
* use isolated databases
* clean themselves up
* avoid shared mutable state
* support parallel test execution where practical

Do not use:

```text
latest
```

for test infrastructure images.

Prefer pinned versions.

Example:

```text
postgres:17.x
redis:8.x
```

with versions chosen according to the supported production environment.

---

# 16. No Test Pollution

Tests must not depend on:

* previous test execution
* developer machine state
* manually created database records
* local Redis state
* local environment variables unless explicitly configured
* external services
* test execution order

Each test must establish its required state.

Prefer:

```text
Arrange
Act
Assert
Cleanup
```

with automated cleanup.

---

# 17. Database Testing

Database tests must verify:

* migrations
* schema constraints
* foreign keys
* unique constraints
* indexes where behaviorally relevant
* transactions
* rollback behavior
* optimistic/concurrent updates where applicable
* repository behavior
* serialization/deserialization

Do not test only that an ORM object can be instantiated.

The actual database must be exercised for persistence-critical behavior.

---

# 18. Migration Testing

Every schema migration must be tested.

At minimum:

```text
empty database
    ↓
apply all migrations
    ↓
database is usable
```

Also verify, where practical:

```text
previous supported schema
    ↓
new migration
    ↓
application works
```

Migrations must be deterministic and repeatable.

Never modify an already-applied migration merely to make tests pass.

Create a new migration instead.

---

# 19. Transaction Testing

Transaction boundaries must be explicit.

Test:

* successful transaction
* rollback on failure
* partial operation failure
* nested application operations where relevant
* concurrent access where relevant

If a Project publish operation must be atomic, there should be an integration test proving that a failure cannot leave an invalid half-published state.

---

# 20. API Testing

API tests must test the actual HTTP layer.

Test:

* authentication
* authorization
* validation
* successful responses
* invalid requests
* missing resources
* conflicts
* domain errors
* unexpected infrastructure errors
* pagination
* filtering
* malformed input
* content types

Do not test only the underlying service and assume the HTTP layer works.

---

# 21. Contract Testing

Frontend/backend contracts are critical.

When appropriate, use contract tests to verify:

* request schemas
* response schemas
* error formats
* enum values
* Node definitions
* execution states
* event payloads

If OpenAPI is generated by the backend, the generated schema should be validated as part of CI.

Frontend types should not silently diverge from backend contracts.

---

# 22. Execution Runtime Testing

The execution engine is one of the highest-risk components.

It requires extensive tests.

At minimum test:

```text
linear graph
branching graph
parallel graph
merge
conditional execution
loop
retry
timeout
failure
cancellation
dependency failure
missing input
invalid configuration
missing node
unknown node
unknown port
execution persistence
event emission
```

Also test:

```text
execution starts
→ node runs
→ node completes
→ output propagates
→ dependent node becomes ready
→ final execution completes
```

---

# 23. Execution State Machine Tests

Every legal transition must have a test.

Every illegal transition should also have a test.

For example:

```text
CREATED → QUEUED       valid
QUEUED → RUNNING       valid
RUNNING → COMPLETED    valid
RUNNING → FAILED       valid
RUNNING → CANCELLED    valid
COMPLETED → RUNNING    invalid
FAILED → RUNNING       invalid
CANCELLED → RUNNING    invalid
```

Do not rely on UI logic to prevent invalid state transitions.

The backend must enforce them.

---

# 24. Retry Testing

Retries must be deterministic and testable.

Test:

* transient error
* permanent error
* retry count
* exponential backoff
* maximum delay
* final failure
* successful retry
* cancellation during retry
* timeout during retry

Do not make tests wait for real long backoff periods.

Inject a clock or backoff strategy where appropriate.

---

# 25. Time Handling

Time-dependent code must be testable.

Avoid scattering:

```python
datetime.now()
```

through business logic.

Prefer an injectable clock abstraction where meaningful.

Tests must be able to control time for:

* retries
* timeouts
* schedules
* execution durations
* token expiry
* credential expiry
* trigger evaluation

Use UTC internally unless there is a documented reason otherwise.

---

# 26. External Services

Do not make ordinary tests depend on:

* OpenAI
* Anthropic
* Google
* external web search
* public websites
* SMTP providers
* arbitrary APIs

External provider tests should be separated into:

```text
mocked/unit/integration tests
```

and optionally:

```text
explicit external smoke tests
```

External smoke tests must never be required for normal CI unless intentionally configured.

---

# 27. LLM Testing

LLM output is inherently nondeterministic.

Do not write fragile tests such as:

```python
assert response == "exact generated sentence"
```

unless the provider is a deterministic test double.

Instead test:

* request construction
* provider selection
* model selection
* prompt rendering
* variables
* token usage parsing
* structured output validation
* provider error mapping
* retry behavior
* timeout handling
* cost calculation
* response normalization

Use deterministic fake providers.

Example:

```text
FakeLLMProvider
    ↓
known request
    ↓
known response
```

---

# 28. Node Contract Testing

Every Node must conform to the Node Contract.

Tests should verify:

* metadata
* version
* inputs
* outputs
* config schema
* validation
* execution behavior
* error handling

A reusable Node contract test suite should be created where possible.

A new Node should be able to opt into the common contract tests.

---

# 29. Built-in Node Testing

Each built-in Node needs behavior tests.

Examples:

```text
HTTP Node
Web Search Node
SMTP Node
If Node
Switch Node
Delay Node
Merge Node
Log Node
Input Node
Output Node
LLM Node
```

Tests should cover:

* valid inputs
* invalid inputs
* configuration errors
* runtime errors
* timeout
* cancellation where supported
* output schema
* side effects

For network-related Nodes, use controlled local test services rather than the public internet.

---

# 30. HTTP Node Testing

HTTP Node tests should use a local test HTTP server.

Test:

* GET
* POST
* headers
* body
* timeout
* HTTP 4xx
* HTTP 5xx
* redirects according to policy
* oversized responses
* malformed responses
* SSRF protections
* blocked destinations

Never rely on `google.com` or another public website to test HTTP Node behavior.

---

# 31. SMTP Testing

SMTP functionality should use an ephemeral local SMTP test service.

Possible approaches:

* Mailpit
* MailHog
* another isolated SMTP test container

Tests should verify:

```text
message sent
recipient
sender
subject
body
attachments
failure
timeout
```

No real email provider should be required for automated tests.

---

# 32. Web Search Testing

Web Search Nodes should not rely on live search engines in normal CI.

Use:

* mocked HTTP responses
* local fixtures
* controlled test servers

Tests should verify:

* query construction
* result normalization
* empty result handling
* malformed result handling
* timeout
* rate limiting
* errors

---

# 33. File System Testing

File-related features must use isolated temporary directories.

Never write tests into:

```text
/
~/Documents
project root
developer home
```

unless explicitly intended.

Use temporary directories provided by the test framework.

Test:

* path traversal
* invalid paths
* permissions
* missing files
* large files
* cleanup
* concurrent access where relevant

---

# 34. Security Testing

Security is a functional requirement.

Tests should cover:

```text
authentication
authorization
credential isolation
secret redaction
SSRF protection
path traversal
unsafe file access
malicious plugin metadata
unsafe expressions
oversized payloads
request limits
webhook authentication
invalid tokens
expired sessions
```

Security tests should run in CI.

---

# 35. Secrets

Secrets must never be:

* committed to Git
* printed in logs
* included in reports
* included in Project exports
* included in exception messages
* included in telemetry
* returned through normal API responses
* stored in frontend localStorage
* embedded in URLs

Tests must explicitly verify secret redaction.

Example:

```text
input:
sk-secret-value

log:
sk-***REDACTED***

report:
[REDACTED]
```

The exact redaction format may differ, but plaintext secrets must never escape.

---

# 36. Credential References

Project configuration should contain references, not plaintext secrets.

Example:

```json
{
  "credential_id": "cred_123"
}
```

not:

```json
{
  "api_key": "secret-value"
}
```

Tests must ensure Project export cannot accidentally contain secret material.

---

# 37. Plugin Security

Plugins are executable code.

Treat plugin installation and loading as a security boundary.

Validate:

* manifest
* plugin version
* engine compatibility
* SDK compatibility
* dependencies
* identifiers
* package structure

Do not execute arbitrary plugin code merely because a manifest claims it is valid.

The trusted-code model must be explicit.

---

# 38. Expressions

User expressions must never be evaluated through unrestricted:

```python
eval(...)
exec(...)
```

Do not introduce arbitrary Python evaluation into user-configurable workflows.

Expressions must use a controlled parser/evaluator.

Tests must include malicious expressions.

Examples:

```text
__import__
filesystem access
process spawning
network access
attribute traversal
object introspection
```

These must be rejected.

---

# 39. Logging

Use structured logging.

Logs should contain useful context such as:

```text
timestamp
level
service
environment
request_id
correlation_id
execution_id
project_id
node_id
event_type
message
```

Do not log sensitive payloads by default.

Avoid:

```python
logger.info("request=%s", request)
```

when the request can contain secrets.

---

# 40. Observability

Observability is part of the product.

Important operations should be traceable through:

```text
request
→ application service
→ execution
→ node execution
→ provider
→ persistence
```

Use correlation identifiers.

A production failure should be diagnosable without reproducing the problem locally.

---

# 41. Metrics

Important metrics include:

```text
execution_count
execution_success_count
execution_failure_count
execution_duration
node_execution_duration
active_executions
queue_depth
retry_count
token_usage
estimated_cost
provider_errors
```

Metrics should have bounded cardinality.

Do not create labels from arbitrary user input.

---

# 42. Performance

Do not optimize blindly.

Use measurement.

Potential performance risks:

* N+1 database queries
* huge graph serialization
* excessive WebSocket events
* unbounded execution logs
* large API payloads
* repeated Node registry lookups
* inefficient graph traversal
* uncontrolled concurrency

Performance-sensitive code should have tests or benchmarks where appropriate.

---

# 43. Resource Limits

The engine must not assume infinite resources.

Potential limits:

```text
maximum execution duration
maximum node duration
maximum graph depth
maximum loop iterations
maximum concurrency
maximum payload size
maximum artifact size
maximum HTTP response size
maximum LLM tokens
maximum retries
maximum execution nesting depth
```

Limits must be enforced server-side.

Tests must verify both:

```text
within limit → allowed
over limit → rejected/terminated safely
```

---

# 44. Concurrency

Concurrency must be explicit.

Do not introduce background tasks without understanding:

* lifecycle
* cancellation
* shutdown behavior
* exception propagation
* resource ownership
* persistence semantics

Every long-running task needs a clear owner.

---

# 45. Background Workers

Worker processes must support:

* startup
* graceful shutdown
* heartbeat
* job acquisition
* failure handling
* retry
* cancellation
* stale job detection

A worker crash must not leave executions permanently invisible.

Recovery behavior must be tested.

---

# 46. Queue Semantics

Queue processing must be designed around at-least-once execution assumptions unless a stronger guarantee is explicitly established.

Therefore Nodes with external side effects must consider idempotency.

Examples:

```text
SMTP
HTTP POST
external API calls
database writes
```

Tests should verify behavior around retries and duplicate delivery where applicable.

---

# 47. Idempotency

Where an operation can safely be repeated, implement idempotency.

For externally visible side effects, consider:

* idempotency keys
* execution IDs
* node execution IDs
* provider request IDs

Do not assume that retrying a failed HTTP request is harmless.

---

# 48. Graceful Shutdown

Services must handle termination correctly.

Shutdown should:

1. stop accepting new work
2. finish or safely interrupt active work according to policy
3. persist necessary state
4. close connections
5. stop background tasks
6. exit cleanly

Tests should verify shutdown behavior where practical.

---

# 49. Docker

Docker images must be:

* reproducible
* minimal where practical
* non-root where practical
* health-checkable
* configurable through environment variables
* free of unnecessary build artifacts

Pin important base images.

Avoid:

```text
FROM python:latest
```

Prefer a deliberate supported version.

---

# 50. Docker Compose

Local infrastructure should be reproducible.

A developer should be able to start required services without manually installing:

* PostgreSQL
* Redis
* object storage
* SMTP test service

where those services are required by the current environment.

However, tests should preferably create isolated ephemeral infrastructure instead of relying on a long-running developer Compose environment.

---

# 51. Health Checks

Provide appropriate endpoints such as:

```text
/health
/readiness
```

Health must distinguish:

```text
process alive
```

from:

```text
service ready to process requests
```

Readiness should consider critical dependencies where appropriate.

---

# 52. Configuration

Configuration must be explicit.

Do not scatter environment access throughout business logic.

Prefer:

```text
configuration
    ↓
application startup
    ↓
dependency injection
```

rather than:

```python
os.getenv(...)
```

throughout arbitrary modules.

Validate configuration during startup.

Fail fast for invalid critical configuration.

---

# 53. Dependency Management

Dependencies must be intentional.

Before adding a dependency:

1. determine whether the functionality already exists
2. evaluate maintenance quality
3. evaluate security
4. evaluate license compatibility
5. evaluate project activity
6. evaluate package size
7. evaluate whether the dependency creates architectural coupling

Do not add a library for trivial functionality.

---

# 54. Version Pinning

Production dependencies should have reproducible versions.

Avoid unconstrained:

```text
package>=1.0
```

when reproducibility would be compromised.

Use the project's chosen dependency management mechanism consistently.

---

# 55. Formatting and Linting

Code must pass:

* formatter
* linter
* type checker

before a phase is considered complete.

Do not suppress lint rules without a documented reason.

Avoid:

```python
# noqa
```

as a shortcut.

---

# 56. Type Safety

Use static typing aggressively.

Avoid:

```python
Any
```

unless genuinely necessary.

Avoid untyped dictionaries for domain structures when typed models are appropriate.

Prefer:

```python
TypedDict
dataclass
Pydantic model
Protocol
Enum
explicit domain type
```

depending on context.

---

# 57. Frontend Type Safety

Frontend TypeScript must use strict mode.

Avoid:

```typescript
any
```

as an escape hatch.

Prefer generated or explicitly maintained API types.

Runtime validation should be used where data crosses trust boundaries.

---

# 58. React Architecture

Frontend components should be:

* focused
* composable
* testable
* accessible

Avoid enormous components containing:

* API calls
* business logic
* graph logic
* validation
* rendering
* persistence

all in one file.

Prefer feature-oriented composition.

---

# 59. Frontend State

Separate:

```text
Server State
UI State
Editor State
Authentication State
```

Do not put all state into one global store merely for convenience.

Use the simplest appropriate mechanism.

---

# 60. Frontend API Usage

Components should not contain arbitrary:

```typescript
fetch(...)
axios(...)
```

calls.

Use the centralized API layer.

Normalize API errors.

Handle:

* loading
* success
* empty
* error
* stale
* unauthorized
* forbidden
* conflict

states.

---

# 61. Graph Editor

The graph editor is a core product feature.

Do not treat it as a generic drawing surface.

It must respect:

* Node Contracts
* Ports
* Data Types
* Project Draft semantics
* Backend validation
* Node versions
* execution state
* immutable published versions

Editor interactions must remain deterministic.

---

# 62. Frontend Secrets

Never store secrets in:

```text
localStorage
sessionStorage
URL parameters
query strings
analytics events
console.log
error telemetry
React state longer than necessary
```

The exact credential architecture must follow the backend security model.

---

# 63. Accessibility

Accessibility is part of correctness.

UI must support:

* keyboard navigation
* visible focus
* semantic HTML
* labels
* accessible dialogs
* accessible error messages
* sufficient contrast
* reduced motion

Do not use color as the only indicator of state.

---

# 64. Browser Security

Avoid:

* unsafe HTML rendering
* arbitrary script injection
* dynamic code execution
* untrusted iframe embedding
* open redirects

Any use of:

```text
dangerouslySetInnerHTML
innerHTML
eval
Function(...)
```

requires explicit architectural justification.

---

# 65. Git Hygiene

Commits should be focused.

Avoid mixing:

```text
feature
formatting
unrelated refactor
dependency upgrade
```

into one change.

Do not commit:

* secrets
* local databases
* build artifacts
* `.env` files containing secrets
* test credentials
* temporary files

---

# 66. Generated Files

Do not manually edit generated files unless the repository explicitly requires it.

Instead modify their source and regenerate.

Generated artifacts must be deterministic.

---

# 67. Database Access

Repositories should encapsulate persistence behavior.

Application services should not contain raw SQL unless explicitly justified.

Domain objects should not know about ORM sessions.

Avoid leaking ORM models into API responses.

---

# 68. Dependency Injection

Dependencies should be explicit.

Prefer constructor/function injection over global mutable state.

Avoid hidden singletons.

The application should be testable by replacing:

* repositories
* clocks
* queues
* providers
* credential stores
* event publishers
* external clients

with test implementations.

---

# 69. Test Doubles

Use the right test double:

```text
Fake
Stub
Mock
Spy
```

Do not mock everything.

Especially avoid mocking the component whose real behavior is what you need to verify.

For persistence and infrastructure behavior, prefer real ephemeral services.

---

# 70. Contract Tests for Plugins and Nodes

The repository should provide reusable contract test utilities.

A custom Node should be testable against:

```text
Node Contract
Input validation
Output validation
Config validation
Execution context
Error semantics
Version metadata
```

A plugin should be testable against:

```text
Manifest
Compatibility
Registration
Loading
Node discovery
Uninstallation
```

---

# 71. Import/Export Testing

Project packages must be tested extensively.

At minimum:

```text
create project
→ export
→ import
→ validate
→ publish
→ execute
```

Also test:

* missing dependencies
* incompatible versions
* malformed package
* unknown Node
* invalid schema
* credential references
* malicious files
* path traversal
* corrupted archive

Secrets must never survive export.

---

# 72. Determinism

Where possible, operations should be deterministic.

Examples:

* graph serialization
* project exports
* validation errors
* migrations
* configuration parsing

If ordering matters, define it explicitly.

Do not rely on:

* dictionary iteration order for business semantics
* database row order without `ORDER BY`
* filesystem ordering
* network response ordering

---

# 73. Reproducibility

An execution should record enough information to understand what happened.

Relevant metadata may include:

```text
project_id
project_version
node_versions
plugin_versions
engine_version
SDK_version
provider
model
configuration snapshot
trigger
runtime metadata
```

Do not claim deterministic AI output merely because metadata is recorded.

---

# 74. API Pagination

Large collections must not be returned unbounded.

Apply pagination to:

* Projects
* Executions
* Events
* Logs
* Reports
* Plugins
* potentially Nodes

Frontend must handle pagination correctly.

---

# 75. Large Payloads

Do not return enormous payloads through ordinary API responses.

Use:

* pagination
* streaming
* artifact storage
* downloads
* summaries

where appropriate.

Execution outputs can become very large.

---

# 76. Logging Large Payloads

Never automatically log:

* complete LLM prompts
* complete LLM responses
* arbitrary HTTP bodies
* uploaded files
* binary artifacts

unless explicitly configured and safe.

Prefer summaries and references.

---

# 77. Privacy by Design

Assume workflow data may contain sensitive business information.

Therefore:

* minimize logged payloads
* redact secrets
* control access
* avoid unnecessary retention
* make exports explicit
* avoid sending data to external providers unintentionally

Provider configuration must make external data transmission understandable.

---

# 78. LLM Provider Abstraction

The domain must not become coupled to one provider.

Expected architecture:

```text
LLM Node
    ↓
AI Service
    ↓
Provider Abstraction
    ↓
Provider Adapter / LiteLLM
    ↓
Provider
```

Provider-specific details must remain behind the abstraction.

---

# 79. Provider Error Mapping

Different providers have different errors.

Normalize them into stable internal categories.

Examples:

```text
AuthenticationError
RateLimitError
TimeoutError
ContextLengthError
InvalidRequestError
ProviderUnavailableError
ContentPolicyError
UnknownProviderError
```

Do not make application logic depend on raw provider exception classes.

---

# 80. Event System

Events describe what happened.

They are not necessarily the canonical source of state.

The system should distinguish:

```text
Current State
```

from:

```text
Historical Events
```

Events should be:

* structured
* versionable
* traceable
* persistable where required

---

# 81. Event Schema Evolution

Event payloads may evolve.

Do not silently change event semantics.

When compatibility matters:

* version events
* maintain backward compatibility
* document changes

Frontend consumers must not break merely because an unrelated optional field was added.

---

# 82. WebSocket Reliability

WebSocket consumers must tolerate:

* disconnect
* reconnect
* duplicate events
* missed events
* out-of-order events where applicable
* stale connections

The frontend should be able to recover by refetching authoritative state.

Do not assume a WebSocket connection is perfectly reliable.

---

# 83. Caching

Cache only data that can safely be cached.

Never cache sensitive credential values.

Cache invalidation must be deliberate.

After mutations, invalidate or update relevant queries.

---

# 84. Autosave

Autosave must not silently overwrite newer server state.

Where concurrent editing is possible, consider:

* version numbers
* revision IDs
* optimistic concurrency
* conflict detection

Do not blindly overwrite.

---

# 85. Optimistic Updates

Use optimistic updates only when rollback is reliable.

For destructive or complex operations such as:

* publishing
* deleting projects
* credential changes
* plugin installation

prefer authoritative server confirmation.

---

# 86. API Compatibility

Do not change API contracts casually.

Breaking changes require:

* documentation
* migration plan
* versioning where appropriate
* frontend impact analysis
* tests

---

# 87. Backward Compatibility

Project packages and persisted data may outlive application versions.

When changing schemas:

* introduce migrations
* support compatibility where required
* explicitly reject unsupported versions
* provide useful errors

Never silently reinterpret incompatible data.

---

# 88. Documentation

Important architecture decisions must be documented.

Relevant documentation:

```text
/docs/vision.md
/docs/architecture.md
/docs/roadmap-backend.md
/docs/roadmap-frontend.md
```

For significant decisions use:

```text
/docs/adr/
```

An ADR should explain:

```text
Context
Decision
Alternatives
Consequences
```

---

# 89. Comments

Comments should explain why, not what.

Bad:

```python
# Increment counter
counter += 1
```

Good:

```python
# Increment only after successful persistence so retries cannot
# report a completed execution that was never committed.
counter += 1
```

Delete stale comments.

---

# 90. TODO Policy

Do not leave vague TODOs.

Bad:

```text
TODO fix this later
```

Prefer:

```text
TODO(#issue): explain concrete missing behavior
```

If no issue tracker exists, document the reason and intended scope clearly.

Do not use TODOs to hide incomplete required functionality.

---

# 91. Feature Flags

Feature flags must have:

* clear owner
* purpose
* default behavior
* removal plan

Do not create permanent undocumented flags.

---

# 92. Environment Separation

Distinguish:

```text
development
test
staging
production
```

Tests must not accidentally use production resources.

---

# 93. CI Requirements

CI should run at minimum:

```text
format check
lint
type check
unit tests
integration tests
API tests
build
```

As the project matures:

```text
contract tests
security tests
E2E tests
migration tests
dependency audit
container build
```

should also run.

---

# 94. Parallel Test Execution

Tests should be safe for parallel execution where practical.

Avoid global shared resources.

Use isolated:

* databases
* schemas
* temporary directories
* ports
* queues

Testcontainers can provide isolated infrastructure.

---

# 95. Flaky Tests

Flaky tests are defects.

Do not "fix" flakiness by:

```text
sleep(5)
retry test indefinitely
ignore failure
mark everything xfail
```

Investigate the race condition.

Use:

* deterministic clocks
* readiness checks
* proper synchronization
* event-driven waits
* isolated state

---

# 96. Async Testing

Async code must be tested asynchronously.

Avoid blocking event loops.

Be especially careful with:

* HTTP clients
* database sessions
* Redis
* WebSockets
* background tasks
* execution runtime

Tests should verify cancellation and timeout semantics.

---

# 97. Cancellation

Cancellation must be cooperative and explicit.

A cancelled execution must not later report itself as successfully completed.

Test:

```text
cancel before execution
cancel while queued
cancel while running
cancel during node execution
cancel during retry
cancel during provider request
```

---

# 98. Timeout Handling

Timeouts must produce explicit states/errors.

Do not convert a timeout into a generic failure without preserving the reason.

Test:

```text
node timeout
execution timeout
provider timeout
HTTP timeout
queue timeout
```

---

# 99. Recovery

Recovery is part of execution correctness.

Test scenarios such as:

```text
worker crashes
database connection temporarily unavailable
Redis unavailable
execution remains RUNNING
process restarts
stale execution detected
execution recovered or marked appropriately
```

Recovery behavior must be deterministic and documented.

---

# 100. Backup and Restore

Backup functionality must be tested by actually restoring data.

Do not consider:

```text
backup command exits 0
```

sufficient.

The real test is:

```text
create data
→ backup
→ destroy environment
→ restore
→ verify data
→ execute restored project
```

---

# 101. Operational Testing

Production-like tests should verify:

* startup
* migrations
* readiness
* graceful shutdown
* restart
* recovery
* backup
* restore
* worker restart
* database restart where practical

---

# 102. Test Data

Prefer factories/builders over large static fixture blobs.

Example:

```python
project = ProjectFactory.create(...)
```

Factories should generate valid domain objects by default.

Invalid objects should be created explicitly for negative tests.

---

# 103. Property-Based Testing

For complex deterministic algorithms, property-based testing should be considered.

Potential candidates:

* graph validation
* graph serialization/deserialization
* expression parsing
* project import/export
* state transitions
* configuration validation

Examples of useful properties:

```text
export(import(project)) ≈ project
```

and:

```text
valid graph → validator accepts graph
```

where appropriate.

---

# 104. Fuzz Testing

Security-sensitive parsers should be candidates for fuzzing.

Potential targets:

* Project package parser
* manifest parser
* expression parser
* API payload validation
* configuration parser

Malformed input must fail safely.

---

# 105. Dependency and Supply Chain Security

Regularly check dependencies for:

* known vulnerabilities
* malicious packages
* abandoned dependencies
* incompatible licenses

Lockfiles must be committed where appropriate.

Do not blindly upgrade all dependencies without testing.

---

# 106. Container Security

Container builds should be checked for:

* unnecessary packages
* root execution
* leaked secrets
* vulnerable base images
* writable sensitive paths

Secrets must not be baked into Docker images.

---

# 107. Secrets in CI

CI secrets must be provided through CI secret mechanisms.

Never place credentials in:

```text
Git
source code
test fixtures
Dockerfiles
logs
command-line arguments
```

---

# 108. Test Environment Isolation

A test may create:

```text
temporary PostgreSQL
temporary Redis
temporary SMTP
temporary object storage
```

but must destroy them automatically.

The developer should never need to manually clean containers after a failed test run.

---

# 109. Realistic Integration Testing

Prefer this:

```text
pytest
 ↓
Testcontainers
 ↓
real PostgreSQL
 ↓
real Redis
 ↓
real application
```

over this:

```text
pytest
 ↓
20 mocks
 ↓
everything passes
```

Mocks are useful, but they must not replace integration testing.

---

# 110. Testing the Tests

Test infrastructure itself must be reliable.

If a fixture claims to create:

```text
PostgreSQL + migrations
```

there should be tests or CI validation demonstrating that the fixture actually provides that environment.

Do not allow broken fixtures to create false confidence.

---

# 111. Mutation Testing

For high-value domain logic, mutation testing may be introduced later.

Candidates:

* state machine
* graph validation
* credential authorization
* resource limits
* retry policy

The purpose is to detect tests that pass despite meaningful logic changes.

---

# 112. Coverage

Coverage is a signal, not the objective.

Do not optimize for:

```text
100% coverage
```

by writing meaningless tests.

Prefer high coverage of:

* domain logic
* execution runtime
* security
* persistence
* API contracts
* critical workflows

Critical paths should have substantially stronger coverage than incidental code.

---

# 113. Code Review Standard

Before considering a change complete, ask:

```text
Is the abstraction correct?
Is the failure behavior correct?
Is the persistence behavior correct?
Is concurrency safe?
Are secrets protected?
Is the code observable?
Is it testable?
Is the API stable?
Does it introduce unnecessary coupling?
Can this be maintained in two years?
```

---

# 114. Before Declaring a Phase Complete

Run the relevant quality gates.

At minimum:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

Use the actual project commands if they differ.

For frontend:

```bash
npm test
npm run lint
npm run build
npx tsc --noEmit
```

Again, use the repository's actual package manager and scripts.

Do not claim a command passed if it was not actually executed.

---

# 115. Phase Completion Report

At the end of every roadmap phase, report:

```text
Phase:
Status:

Implemented:
- ...

Tests:
- ...

Integration Tests:
- ...

Infrastructure Tests:
- ...

Security Tests:
- ...

Typecheck:
PASS / FAIL

Lint:
PASS / FAIL

Format:
PASS / FAIL

Build:
PASS / FAIL

Known Limitations:
- ...

Technical Debt:
- ...

Architectural Decisions:
- ...

Future Work Explicitly Not Implemented:
- ...
```

Never hide failures.

---

# 116. If Tests Fail

Do not immediately weaken the test.

First determine whether:

```text
implementation is wrong
test is wrong
test environment is wrong
architecture is wrong
requirement is ambiguous
```

Then fix the underlying issue.

Do not modify a test simply because the implementation does not satisfy it.

---

# 117. If Infrastructure Is Unavailable

If an integration test requires Docker and Docker is unavailable:

* do not replace the integration test with a mock merely to make CI pass
* report the infrastructure limitation
* run all tests that can legitimately run
* keep the real integration test intact

Never falsely claim full test coverage.

---

# 118. If a Requirement Is Ambiguous

Do not invent complex behavior.

Prefer:

1. inspect architecture
2. inspect existing implementation
3. inspect tests
4. inspect roadmap
5. choose the smallest behavior consistent with the documented architecture

If ambiguity materially affects architecture, stop and document the ambiguity rather than silently choosing a major design.

---

# 119. Avoid Premature Abstraction

Do not create abstractions merely because something could theoretically be reused.

Abstractions should be justified by:

* actual reuse
* domain semantics
* architectural boundary
* testability
* replaceability

Prefer simple code until the abstraction has a real reason to exist.

---

# 120. Avoid God Objects

Do not create classes such as:

```text
EngineManager
ApplicationManager
SystemManager
WorkflowManager
```

that eventually contain dozens of unrelated responsibilities.

Prefer focused services.

---

# 121. Avoid God Components

The same applies to React.

Avoid:

```text
ProjectEditor.tsx
```

becoming a 3,000-line component.

Split by responsibility:

```text
EditorCanvas
NodeLibrary
NodeInspector
ValidationPanel
Toolbar
ProjectHeader
ExecutionOverlay
```

or equivalent feature boundaries.

---

# 122. Explicit Ownership

Every resource should have clear ownership.

Examples:

```text
Database session → request/application scope
Execution → execution runtime
Background job → worker
WebSocket → connection manager
Temporary file → owning operation
Container → test fixture
```

Ambiguous ownership causes leaks and race conditions.

---

# 123. Resource Cleanup

Every resource must have a cleanup strategy.

Especially:

* database connections
* Redis connections
* HTTP clients
* WebSocket connections
* temporary files
* background tasks
* subprocesses
* containers

Tests must verify cleanup where relevant.

---

# 124. No Hidden Network Calls

Tests must never unexpectedly call the public internet.

Network access should be explicit.

For test suites, prefer:

```text
local server
mock server
container
fake provider
```

This ensures:

* deterministic tests
* faster CI
* privacy
* reproducibility

---

# 125. Reproducible Development Environment

A new developer should be able to clone the repository and reach a working development environment with documented steps.

Required infrastructure should be clearly documented.

Avoid undocumented "works on my machine" dependencies.

---

# 126. Final Engineering Standard

The system should be developed under the following principle:

```text
Every feature must be:

understood
    ↓
architecturally placed
    ↓
implemented
    ↓
validated
    ↓
tested
    ↓
observed
    ↓
secured
    ↓
documented
    ↓
operationally reproducible
```

A feature is not complete merely because its happy path works.

The expected standard is:

```text
Happy Path
+ Failure Path
+ Validation
+ Persistence
+ Concurrency
+ Cancellation
+ Timeout
+ Security
+ Observability
+ Recovery
+ Tests
+ Documentation
```

---

# 127. Engineering North Star

When making a technical decision, prefer the solution that best satisfies:

```text
Correctness
    >
Security
    >
Maintainability
    >
Testability
    >
Observability
    >
Operational Reliability
    >
Performance
    >
Convenience
```

Performance optimizations must not compromise correctness or security.

Convenience must not compromise architecture.

Mocks must not replace meaningful integration tests.

Passing tests must not be confused with correct software.

The goal is not to produce the largest amount of code.

The goal is to produce a system that remains understandable, testable, secure and reliable as the Agent Orchestration Engine grows.

```
```
