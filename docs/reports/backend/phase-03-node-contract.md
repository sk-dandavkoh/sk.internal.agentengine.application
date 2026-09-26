# Phase 03 — Node Contract

## 1. Executive Summary

Phase 03 defined the formal contract between the Engine and Nodes: the set of types a node type must declare about itself (`NodeDefinition`), the types the runtime hands to a running node (`NodeExecutionContext`), the type a node returns (`NodeResult`), and the error type a node may surface (`NodeError`).

The implementation is a single new domain module `backend/src/akasha/domain/node.py` (stdlib only), three new error classes in `backend/src/akasha/errors.py`, a docstring update in the domain package, and a contract test suite with a minimal example node in `backend/tests/domain/test_node_contract.py` (43 tests, all passing).

The phase acceptance criterion — *"A Node can be defined independently from the runtime and later executed through the standardized contract"* — is satisfied and verified by contract tests that both define and execute example nodes through the contract. No registry (Phase 09) and no production built-in node (Phase 09) was implemented, per scope discipline.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap Phase 03 (docs/roadmap-backend.md §7):

> Implement the foundational Node system. Implement: NodeDefinition, NodeInstance, NodeMetadata, NodeInput, NodeOutput, NodeConfigurationSchema, NodeCapabilities, NodeExecutionContext, NodeResult, NodeError. Define the base Node contract. Support: identity, version, metadata, configuration schema, input ports, output ports, validation, execution, documentation metadata.

`NodeInstance` was already implemented in Phase 02 (`akasha/domain/graph.py`) and was deliberately not duplicated; the domain docstring in graph.py already cross-references Phase 03.

The phase was explicitly NOT responsible for:

- the Node Registry (Phase 09)
- any production Built-in Node (Phase 09)
- graph-level port compatibility / required-input validation at graph level (Phase 04)
- the Python Node SDK (Phase 16)
- runtime scheduling/execution of graphs (Phase 08)

---

## 3. Scope Implemented

### Domain (`backend/src/akasha/domain/node.py`, 474 lines)

- `PortType` (StrEnum): `string, number, boolean, json, object, list, binary, file, artifact, secret_reference` — exactly the port types required by the task.
- `NodeInput` (frozen dataclass): `name, port_type, required, nullable, multiple, schema, default, description`.
- `NodeOutput` (frozen dataclass): `name, port_type, nullable, multiple, schema, description`. Outputs have no `required` — a successful node produces all declared outputs (enforced in validation).
- `NodeMetadata` (frozen dataclass): `node_id` (validated against `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, e.g. `core.llm`), `name, description, version` (validated against the existing `SEMVER_RE` from `domain/primitives.py`), `category`, `documentation_url`.
- `NodeCapabilities` (frozen dataclass): `supports_cancellation, has_side_effects, is_control_flow, streams_output` (all bool, default False). Chosen minimal; `has_side_effects` exists because it drives retry/idempotency semantics required by roadmap §44/§46.
- `NodeConfigSchema` (frozen dataclass): wraps a JSON-Schema dict; `__post_init__` requires `type: "object"`; `validate_config()` enforces a structural subset (see §9, Finding 1).
- `NodeDefinition` (frozen dataclass): `metadata, inputs, outputs, config_schema, capabilities`. Validates itself fully in `__post_init__`: port name syntax, unique port names per direction, scalar ports must not declare value schemas, defaults only on non-required inputs, defaults must match their port type. Convenience: `node_id`, `version` properties; `input(name)`/`output(name)` lookup; `validate_config()`, `validate_inputs()`, `validate_outputs()`.
- `validate_port_value(port, value)`: standalone runtime value validation used by both definition-time default checking and runtime input/output validation.
- `CredentialResolver` (Protocol): `resolve(reference: str) -> str` — the only credential-facing surface of the contract; keeps the contract free of credential-store dependencies (store is Phase 13).
- `NodeExecutionContext` (frozen dataclass): `execution_id, node_execution_id, node_instance_id, project_id, project_version, config, inputs, logger, credentials (optional), is_cancelled (callable, default `lambda: False`), runtime_metadata`.
- `NodeResult` (frozen dataclass): `outputs` mapping only. No failure field — failures cross the contract as `NodeError` exceptions.
- `NodeErrorKind` (StrEnum): `validation, configuration, transient, permanent, timeout, cancellation`.
- `NodeError` (subclass of `AkashaError`): carries `kind`; `retryable` is derived (`transient`/`timeout` → retryable), not settable ad hoc.
- `Node` (ABC): `definition: ClassVar[NodeDefinition]`, abstract `async execute(self, context) -> NodeResult`.
- `check_node_contract(node_cls)`: reusable class-level contract check (definition present and well-formed, `execute` actually overridden) for future built-in/plugin contract test suites (backend/AGENTS.md §58).

### Errors (`backend/src/akasha/errors.py`)

- `NodeContractError` — `code="NODE_CONTRACT_ERROR"`, HTTP 500 (developer error).
- `NodeConfigurationError` — `code="NODE_CONFIGURATION_ERROR"`, HTTP 422.
- `NodeValidationError` — `code="NODE_VALIDATION_ERROR"`, HTTP 422.

### Testing (`backend/tests/domain/test_node_contract.py`, 43 tests)

- Example nodes `UpperCaseNode` and `FailOnDemandNode` defined in the test file (not production code) and executed through the contract to prove a future built-in can implement it without violation.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/node.py` | Created | Node contract (this phase) |
| `backend/tests/domain/test_node_contract.py` | Created | Contract test suite + example nodes |
| `backend/src/akasha/errors.py` | Modified | Added `NodeContractError`, `NodeConfigurationError`, `NodeValidationError` |
| `backend/src/akasha/domain/__init__.py` | Modified | Docstring updated: lists node contract concepts |

No migrations, no configuration changes, no new dependencies, no API changes, no generated files. No new directories.

---

## 5. Architecture Changes

Two decisions worth recording (both clarifications within existing architecture, not changes to it):

### Decision 1: Contract lives in the domain layer

- **Context:** The roadmap lists `NodeExecutionContext`/`NodeResult`/`NodeError` among contract concepts; one could argue they're runtime concerns.
- **Chosen approach:** Everything in `akasha/domain/node.py`. `backend/src/akasha/nodes/__init__.py` (Phase 01) already states built-ins "depend on the node contracts defined by the domain... layers", confirming this placement.
- **Consequences:** The contract is dependency-free and importable by `nodes`, `runtime`, `plugins`, future CLI and the Phase 16 SDK. The architecture-layer guard test (`tests/test_architecture.py`) requires no changes; `domain` imports remain stdlib + `akasha.errors` only.

### Decision 2: No failure result type — failures are `NodeError` exceptions

- **Context:** Architecture §9 says a node "may return: success, failure, retryable error, waiting state". A discriminated-union result was considered.
- **Chosen approach:** Success-only `NodeResult(outputs)`; failure as `NodeError` with `kind`. "Waiting state" is deferred (no runtime exists yet to consume it).
- **Reason:** A result type that can be either success or failure invites error-state ambiguity (both fields half-populated). Retry semantics are classification (`NodeErrorKind`), not wording — the runtime (Phase 08) maps `kind`/`retryable` to retry policy (Phase 23).
- **Consequences:** Phase 08 must translate `NodeError` into `NodeExecutionStatus.FAILED` (or retry), and Phase 07's state machines stay untouched. If "waiting" turns out to be a real runtime need, it must be added explicitly with its own result variant and architecture note.

---

## 6. Domain / Data Model Changes

New domain types only; no existing Phase 02 types were modified. Not persisted in this phase (persistence is Phase 05).

| Type | Kind | Notes |
|---|---|---|
| `PortType` | Enum (StrEnum) | 10 port types as required |
| `NodeInput` / `NodeOutput` | Frozen dataclasses (value objects) | Port shape contracts |
| `NodeMetadata` | Frozen dataclass (value object) | Identity: `node_id` + `version` |
| `NodeCapabilities` | Frozen dataclass (value object) | 4 boolean flags |
| `NodeConfigSchema` | Frozen dataclass (value object) | JSON-Schema subset wrapper |
| `NodeDefinition` | Frozen dataclass (value object) | Self-validating aggregate of the above |
| `NodeExecutionContext` | Frozen dataclass | Runtime hand-off boundary |
| `NodeResult` | Frozen dataclass | Success payload |
| `NodeErrorKind` | Enum (StrEnum) | Error classification |
| `NodeError` | Exception (extends `AkashaError`) | `retryable` derived from `kind` |
| `CredentialResolver` | Protocol | Late secret resolution seam |
| `Node` | ABC | Implementation base class |

Compatibility: strictly additive. No serialization format defined yet — that becomes relevant in Phase 05 (persistence) and Phase 18 (import/export).

---

## 7. API / Contract Changes

No HTTP API changes (no endpoints added/changed/removed).

The Node contract itself is the deliverable. Machine-readable contract surface that later phases depend on:

- `NodeDefinition` construction raises `NodeContractError` for any structural violation; valid definitions are therefore invariant-clean by construction.
- `NodeDefinition.validate_config(config)` → raises `NodeConfigurationError`.
- `NodeDefinition.validate_inputs(values)` / `validate_outputs(values)` → raise `NodeValidationError`. Inputs: only `required` ports must be present; unknown ports rejected; `None` only on `nullable` ports; `multiple` ports require lists of correctly typed items. Outputs: all declared outputs must be present on success.
- `check_node_contract(node_cls)` → raises `NodeContractError`; intended as the shared check for Phase 09 built-in node tests and Phase 17 plugin node tests.

Error codes are stable machine-readable strings (`NODE_CONTRACT_ERROR`, `NODE_CONFIGURATION_ERROR`, `NODE_VALIDATION_ERROR`, `NODE_EXECUTION_ERROR`) per the error architecture (architecture §46).

---

## 8. Implementation Details

Matters-for-maintenance items only:

- **Config validation is a deliberate JSON-Schema subset.** `NodeConfigSchema.validate_config` enforces `type`, `required`, `properties` (recursively into objects and `array.items`), and `additionalProperties: false`. Anything else in the schema (formats, enums, `oneOf`, ...) is silently *not* validated at definition time — unknown `type` values inside properties do raise `NodeContractError` when validating a config. A `ponytail:` comment in the code names the ceiling and the upgrade path (`jsonschema` package) per project convention.
- **Port value typing:** `bool` is explicitly rejected where `number` is expected (Python's `bool` is an `int` subclass); `number` accepts `int | float`. `json` = round-trippable via `json.dumps`. `file`/`artifact`/`secret_reference` values are validated as non-empty opaque reference *strings*; resolution is delegated to the artifact/credential services (later phases). Plaintext secrets or raw path payloads never appear at the contract boundary.
- **Immutability:** all contract types are frozen dataclasses; `NodeDefinition` can be shared by reference across graph validation, registry, and execution without defensive copies.
- **`default` sentinel:** `dataclasses.MISSING` cannot be used as a field default (dataclass interprets it as "no default provided"), so the module defines its own `_NoDefault` sentinel exported as `NO_DEFAULT`. This was a real implementation bug caught by the first test run; see §11.
- **`check_node_contract`** exists so Phase 09/17 don't invent divergent contract checks; definition-level invariants fire at `NodeDefinition` construction time, the function only verifies class wiring (`definition` present, `execute` overridden).
- **Cancellation** is a zero-arg callable `is_cancelled()` on the context (cooperative, §41), not an injected framework object — nodes poll it.

---

## 9. Findings

### Finding 1
`NodeConfigurationSchema` (roadmap wording) implies full JSON Schema, but no schema library is in the dependency set and the project rules discourage adding one for convenience.

- **Impact:** LOW — the structural subset covers type/required/closed-object validation, which is what built-in nodes need now.
- **Resolution:** DECIDED (subset; documented in code and §22). Upgrade path (`jsonschema`) is a one-module change localized to `NodeConfigSchema`.
- **Notes:** Frontend form generation (roadmap-frontend) consumes schemas — it must be fed the same subset or the subset must be documented as the contract.

### Finding 2
`NodeInstance` was listed under Phase 03 scope in the roadmap but was already fully implemented in Phase 02 (`domain/graph.py`).

- **Impact:** LOW.
- **Resolution:** No duplicate concept created (AGENTS.md §6 forbids near-duplicate concepts). Cross-reference already present in graph.py.

### Finding 3
Pre-existing deprecation warning from the tool environment: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` (emitted on every test run, unrelated to this phase).

- **Impact:** LOW.
- **Resolution:** OPEN — belongs to a dependency-maintenance pass, not this phase.

---

## 10. Bugs / Problems Found

### Bug A (introduced and fixed within this phase)

- **Problem:** `TypeError: non-default argument 'default' follows default argument 'schema'` at import of the domain module.
- **Root cause:** `dataclasses.MISSING` was used as the default value of `NodeInput.default`; dataclass treats `MISSING` as "no default", making it a non-default field after defaulted fields.
- **Fix:** Custom `_NoDefault` sentinel (`NO_DEFAULT`).
- **Regression test:** all definition-construction tests exercise the field; specifically `test_required_input_must_not_declare_default` and `test_default_must_match_port_type` exercise both branches of the sentinel check.

### Bug B (introduced and fixed within this phase)

- **Problem:** `test_output_validation` failed — `validate_outputs({})` passed though a declared output was missing.
- **Root cause:** `_validate_ports` treated "required" as input-only; outputs had no presence requirement.
- **Fix:** Outputs are always required to be present on success (`required = port.required if isinstance(port, NodeInput) else direction == "output"`).
- **Regression test:** `test_output_validation` covers present / wrong-type / missing.

No pre-existing bugs were found. No infrastructure/environment problems (pure domain work).

---

## 11. Fixes Applied

Both fixes are Bugs A and B above; additionally:

- **Symptom:** ruff E501 (4 lines) + F401 (unused import) on first gate run.
- **Root cause:** long lines introduced in the new files; leftover import after test cleanup.
- **Fix:** `ruff check --fix`, `ruff format`, manual wrap; removed `AkashaError` import from the test.
- **Verification:** all quality gates green (§13).

- **Symptom:** three mypy strict errors.
- **Root cause:** heterogeneous tuple concatenation inferred as `tuple[object, ...]`; passing an intentionally-abstract class where `type[Node]` expected; intentional invalid write to a frozen field flagged.
- **Fix:** split the port loop by direction; two `cast()` calls in tests with comments explaining the intentional type violation.
- **Verification:** `mypy .` — "Success: no issues found in 41 source files".

---

## 12. Testing & Verification

### Unit Tests (contract-level, deterministic, no infrastructure)

`tests/domain/test_node_contract.py`, **43 tests, all pass**:

- Definition/metadata validity: dotted node-id syntax, semver versions, description/name non-blank, duplicate ports, malformed port names, default-on-required rejection, default-type mismatch, definition immutability, `check_node_contract` positive and both negative paths.
- Port values: valid + invalid case for all 10 port types (incl. `bool`≠`number`, non-JSON-serializable rejection, blank secret reference rejection).
- Required / optional / nullable / multiple inputs; unknown-port rejection; output presence/typing.
- Config validation: non-object schema rejected at definition; required/unknown keys; wrong primitive types (incl. `bool`≠`integer`); nested objects; typed arrays.
- Execution through the contract: `UpperCaseNode` happy path, config-affected behavior, config-error path; `FailOnDemandNode` `NodeError` kind classification and derived `retryable`; determinism (repeated identical validation outcomes).

### Integration / API / Infrastructure / E2E / Security / Accessibility / Performance Tests

- **Not applicable / not performed.** This phase is pure domain logic with no infrastructure, no HTTP surface, and no external services. Per backend/AGENTS.md §52 the test level must match the risk; heavy contract coverage at unit level is the correct level here. Security-relevant contract points (secret references stay references; no eval/exec anywhere) are covered by design and by the secret-reference port tests.

Full suite: **208 passed** (43 new + 165 pre-existing), 1 pre-existing third-party deprecation warning.

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`ruff format --check .`) | PASS | 2 files reformatted during development, then stable |
| Linter (`ruff check .`) | PASS | 5 findings during development (E501/F401), all fixed |
| Type Checker (`mypy .`, strict) | PASS | 3 errors during development, all fixed; final: no issues in 41 files |
| Unit Tests (`uv run pytest`) | PASS | 208 passed |
| Integration Tests | N/A | no infrastructure in this phase |
| API Tests | N/A | no API changes in this phase |
| E2E Tests | N/A | deferred to later phases (E2E is Phase 27 acceptance) |
| Build | N/A | no distributable build step defined / changed |
| Security Checks | PASS (design-level) | no secrets, no eval/exec, reference-only credentials at contract boundary; dedicated security suite is Phase 25 |
| Accessibility | N/A | backend-only phase |

---

## 14. Acceptance Criteria Verification

Roadmap Phase 03 acceptance criterion:

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| "A Node can be defined independently from the runtime and later executed through the standardized contract." | PASS | `akasha/domain/node.py` imports nothing from `runtime`/`nodes` (enforced by `test_architecture.py`); contract tests define example nodes and execute them through `Node.execute(NodeExecutionContext) -> NodeResult` (`test_node_executes_via_contract`, `test_node_honours_validated_config`, `test_node_error_carries_kind_and_retryable`). |

Task-implied criteria:

| Criterion | Status | Evidence |
|---|---|---|
| Node contract is explicit | PASS | `NodeDefinition`/`Node` ABC/`NodeExecutionContext`/`NodeResult`/`NodeError` are distinct typed classes |
| Contracts are strongly typed | PASS | mypy strict clean; port/config values validated against declared types |
| Validation semantics are clear | PASS | documented in §7/§8; encoded in `validate_*` methods; tested (required/nullable/multiple/unknown/config) |
| Tests verify the contract | PASS | 43 contract tests |
| No registry implementation prematurely added | PASS | no registry code; `nodes/__init__.py` unchanged |
| No built-in Node implementation beyond contract-test necessity | PASS | example nodes live only in the test file |

---

## 15. Security Considerations

- **Secrets:** the contract accepts secret material only via `CredentialResolver` and `SECRET_REFERENCE` port values, which are validated as opaque non-empty reference strings. No secret value can be declared in a `NodeDefinition` or `NodeConfigSchema`. Redaction services are Phase 13.
- **Code execution:** no `eval`/`exec` anywhere in the contract; config validation is a structural schema check, not expression evaluation (expression system is Phase-explicit later; AGENTS.md §38 boundary preserved).
- **Error disclosure:** all node-side errors are `NodeError`/`NodeValidationError`/`NodeConfigurationError` with stable codes; no stack traces or internals leak into message construction.
- **Global state:** `NodeExecutionContext` is per-execution, frozen, and injected; no singletons (§21).

No authentication/authorization/API-exposure surface exists yet (Phase 20); nothing here creates one.

---

## 16. Performance Considerations

- All contract objects are frozen dataclasses; construction-time validation runs once per node type, not per execution.
- `validate_port_value` is O(value size) for `multiple`/`json` checks; no graph traversal, no I/O.
- No measurements taken — nothing measurable at this layer yet. Performance work is Phase 24.

---

## 17. Documentation Changes

- `backend/src/akasha/domain/__init__.py` — module docstring now lists the node contract concepts.
- This report: `docs/reports/backend/phase-03-node-contract.md` (new).

Should be updated in a future phase: `docs/architecture.md` §33 SDK snippet remains aspirational until Phase 16; the config-schema subset should be documented for frontend/authoring tooling when the schema-driven Node Inspector is built (roadmap-frontend).

---

## 18. Dependencies / Infrastructure Changes

None. No new dependencies, no version changes, no configuration/environment changes, no Docker/database/Redis changes, no test-infrastructure changes.

---

## 19. Deferred Work

| What | Why | Target phase | Blocking? |
|---|---|---|---|
| Node Registry, resolver | roadmap scope discipline | Phase 09 | No |
| Built-in nodes (core.*) | roadmap scope discipline | Phase 09 | No |
| Graph-level port compatibility | belongs to graph validation | Phase 04 | No |
| Full JSON Schema (enums, formats, combinators) | not needed yet; avoids a dependency | when first real node needs it | No |
| "Waiting" node result variant | no runtime consumer exists yet | Phase 07/08 | No |
| Credential store backing `CredentialResolver` | Phase 13 scope | Phase 13 | No |
| Persistence of node definitions | Phase 05 scope | Phase 05 | No |

---

## 20. Known Limitations

- Config validation is a JSON-Schema subset; unsupported schema features are not validated (documented in code).
- `file`/`artifact`/`secret_reference` port values are only validated as non-empty strings; semantic resolution belongs to later services.
- `NodeCapabilities` has four flags; control-flow-specific semantics (branch conditions, loop bounds) are intentionally unresolved until the runtime exists.
- `check_node_contract` verifies class wiring, not behavior; behavioral conformance is demonstrated by per-node tests reusing the same validators.

---

## 21. Open Issues

1. **Starlette/httpx deprecation warning in test output** (pre-existing). Impact: noise only. Severity: LOW. Recommended action: dependency maintenance (`httpx2` or updated starlette) outside a feature phase. Target: any maintenance window.

No other known open issues resulting from this phase.

---

## 22. Architectural / Product Decisions

1. **The Node contract is domain code, not runtime code.** Future phases (registry, runtime, SDK, plugins) import it; they must not redefine port/config/result/error types.
2. **Node failure == `NodeError` exception with `NodeErrorKind`; retryability is derived, never free-text.** The runtime decides retry policy from `kind`.
3. **Outputs are all-or-nothing on success.** A node returning `NodeResult` has produced every declared output; partial success is modeled as an error or as control flow, not as a half-filled result.
4. **Config schemas use a validated JSON-Schema subset chosen to avoid a dependency.** Upgrade path is explicit (ponytail comment in code).
5. **`NodeInstance` (Phase 02) was not duplicated/reworked.** Its docstring already pins `node_type` → `NodeDefinition.metadata.node_id` as the linkage the Phase 09 registry will resolve.
6. **IDs of the running context are plain validated strings pinned to the execution snapshot** — the context deliberately carries no object references into domain entities, keeping nodes decoupled from persistence/lifecycle internals.

---

## 23. Repository / Git State

- Branch: `main`
- Last commit before this phase: `6b53237 backend phase 02 - core domain model`
- Working tree after this phase (uncommitted, by design — no commit was requested):
  - Modified: `backend/src/akasha/domain/__init__.py`, `backend/src/akasha/errors.py`
  - Created (untracked): `backend/src/akasha/domain/node.py`, `backend/tests/domain/test_node_contract.py`
  - Report file created under `docs/reports/backend/`
- No unrelated changes detected; no user work touched.

---

## 24. Phase Completion Assessment

### Implemented
Complete node contract (definitions, metadata, ports, config schema, capabilities, execution context, result, error, base class, contract checker) + 43 contract tests + three error classes.

### Verified
Full test suite (208 passed), ruff check, ruff format --check, mypy strict — all green. Architecture dependency guard unchanged and passing.

### Not Implemented
Nothing within phase scope.

### Deferred
Registry, built-ins, graph port compatibility, full JSON Schema, credential store, persistence — each mapped to its roadmap phase (§19).

### Open
One pre-existing third-party deprecation warning in test output (§21).

### Blocking Issues
None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase (Phase 04 — Project Graph + Validation)

- **Available now:** `NodeDefinition` (self-validating), `PortType`, `validate_port_value`, `NodeConfigSchema.validate_config`, `NodeValidationError`/`NodeContractError`/`NodeConfigurationError` with stable codes. Graph-side `NodeInstance` stores `node_type` (== `NodeDefinition.metadata.node_id`) and `config`.
- **Phase 04 validation must** resolve node types to definitions (temporarily via a small lookup; the real registry is Phase 09 — use an injectable mapping keyed by `node_id`), then apply: port existence via `definition.input/output(name)`, required-input presence via `validate_inputs`-style rules adapted to graph edges, config validity via `validate_config`, plus structural rules (cycles, connectivity, duplicates) in `domain/graph.py`.
- **Must not be changed casually:** the frozen-ness and self-validation of `NodeDefinition` (it is the invariant anchor), the `NodeErrorKind`→`retryable` mapping (Phase 23 will consume it), and the outputs-all-present rule (the runtime will rely on it for output propagation).
- **Compatibility rule placeholder:** there is intentionally no `PortType` compatibility function yet — define it centrally in Phase 04 (backend/AGENTS.md §20: "Type Compatibility wird zentral validiert"); do not let it leak into per-node code or the frontend.
- **Prerequisite reading (unchanged):** /AGENTS.md, /backend/AGENTS.md, /docs/vision.md, /docs/architecture.md, /docs/roadmap-backend.md.
