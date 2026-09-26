# Phase 02 — Core Domain Model

## 1. Executive Summary

Phase 02 of `/docs/roadmap-backend.md` required a framework-independent core
domain model (`Project`, `ProjectDraft`, `ProjectVersion`, `Graph`,
`NodeInstance`, `Edge`, `Trigger`, `Execution`, `NodeExecution`,
`CredentialReference`, `PluginMetadata`) with explicit invariants, correct
draft/published-version semantics, and no persistence or execution runtime.

All eleven concepts were implemented under `backend/src/akasha/domain/` using
only the Python standard library (`dataclasses`, `enum`, `datetime`, `uuid`,
`re`, `copy`) plus the existing `akasha.errors` module. Drafts are mutable
dataclasses; published `ProjectVersion` objects are frozen and own a deep
copy of the draft graph, so no draft mutation can reach an existing version.
`Execution` pins an immutable `ProjectVersion` object directly and enforces
the execution/node-execution state machines from vision §16 through validated
transition methods.

50 new unit tests (165 total in the backend suite) cover construction,
invariants, invalid states, identity semantics, the complete 9×9 execution
transition matrix, and draft/version snapshot isolation. `pytest`,
`ruff check`, `ruff format --check`, and `mypy` all pass.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap Phase 02 ("Core Domain Model"), source §6 of
`/docs/roadmap-backend.md`:

> Implement the fundamental domain objects: Project, ProjectDraft,
> ProjectVersion, Graph, NodeInstance, Edge, Trigger, Execution,
> NodeExecution, CredentialReference, PluginMetadata. Define IDs, timestamps,
> lifecycle state, value objects, domain validation primitives. Do not
> implement database persistence.

Explicitly NOT in scope for this phase (roadmap dependency graph):

- Persistence / repositories (Phase 05) — and the phase brief additionally
  warned against introducing repository interfaces prematurely.
- Full graph semantic validation: port type compatibility, cycle detection,
  required-input checks (Phase 04).
- Node contract / `NodeDefinition`, `NodeResult`, config schemas (Phase 03).
- Retry/timeout/cancellation policy objects (`RetryPolicy`, `TimeoutPolicy`,
  `CancellationState`) and full execution state machine hardening (Phase 07).
- Any execution runtime / scheduler (Phase 08).
- Events, API endpoints, imports/exports (later phases).

Acceptance criterion: the core domain must be instantiable and validatable
without PostgreSQL, Redis, FastAPI or external services.

---

## 3. Scope Implemented

### Domain Primitives (`domain/primitives.py`)
- `new_id(prefix)` — prefixed UUID4-hex IDs (`proj_<32hex>`); prefix must be
  lowercase identifier.
- `validate_id(value)` — enforces canonical `<prefix>_<hex>` shape in every
  entity constructor.
- `utcnow()` — timezone-aware UTC timestamps; default factory for all
  `created_at` fields.
- `validate_name(value)` — non-blank names.
- `SEMVER_RE` — loose semver pattern (MAJOR.MINOR.PATCH + optional suffix),
  shared by `project.py` and `plugin.py` to avoid a duplicated regex.
- `Entity` — base dataclass with `eq=False`; equality/hash by
  `(type, id)` only, so entities are identified by identity, not field values.
- `Timestamps` — frozen `created_at` / `updated_at` value object.

### Graph (`domain/graph.py`)
- `NodeInstance` — entity with `id`, `node_type` (references a Node
  Definition from Phase 03, e.g. `core.llm`), `config` dict, `label`.
  `NodeInstance.create()` factory generates IDs.
- `Edge` — frozen value object: `source_node_id`, `source_port`,
  `target_node_id`, `target_port`. Rejects empty ports and self-edges.
- `Graph` — mutable container (draft-side). `add_node` (rejects duplicate
  IDs), `remove_node` (rejects unknown, cascades edge removal),
  `add_edge` (rejects unknown endpoints and duplicates).

### Project (`domain/project.py`)
- `ProjectStatus` enum: `DRAFT / PUBLISHED / ACTIVE / DISABLED / ARCHIVED`
  (vision §7; transition rules intentionally deferred, see §5 and §19).
- `Dependency` — frozen value object (`kind: node|plugin`, name,
  semver-ish `version_constraint`, `*` allowed).
- `ProjectDraft` — mutable: `graph`, `triggers`, `dependencies`,
  `description`. `publish(version=, published_at=)` deep-copies graph and
  triggers into a frozen `ProjectVersion` (snapshot isolation, arch §3.7).
- `ProjectVersion` — frozen dataclass: `id`, `version` (≥1),
  `schema_version` (≥1), `graph`, `triggers`, `dependencies`, `description`,
  `published_at`. `_SCHEMA_VERSION = 1` module constant.
- `Project` — entity: `name`, `draft`, `versions`, `status`, `timestamps`;
  `publish_draft()` assigns `current_version + 1`, attaches the snapshot,
  transitions `DRAFT → PUBLISHED`, bumps `updated_at`.

### Trigger (`domain/trigger.py`)
- `TriggerType` StrEnum: `manual / api / cron / interval / webhook / event`
  (vision §13).
- `Trigger` — entity with validated config at construction: CRON requires
  non-empty `cron` string; INTERVAL requires positive numeric
  `interval_seconds` (bool explicitly rejected); EVENT requires non-empty
  `event` name; MANUAL/API/WEBHOOK carry no mandatory config.

### Execution (`domain/execution.py`)
- `ExecutionStatus` (9 states) and `NodeExecutionStatus` (7 states) per
  vision §16.
- Explicit transition tables `_EXECUTION_TRANSITIONS` /
  `_NODE_TRANSITIONS`, terminal states mapped to `frozenset()`.
- `Execution` — entity pinning `project_id` + an immutable `ProjectVersion`
  object (never a draft reference). Transition methods: `enqueue`, `start`,
  `pause`, `wait`, `resume`, `complete`, `fail(error)`, `cancel`,
  `time_out`; all go through `_transition()` which validates against the
  table and raises `InvalidStateTransitionError`. `start`/`fail`/`complete`/
  `cancel`/`time_out` set `started_at`/`finished_at` via an injectable
  `now: datetime | None` parameter (AGENTS.md §25 clock control).
- `NodeExecution` — entity with `node_id` reference, same pattern:
  `mark_ready`, `start`, `complete`, `fail(error)`, `skip`, `cancel`.
- `fail()` validates the transition *before* mutating `.error` (fixed during
  review, see §11).

### Credentials (`domain/credentials.py`)
- `CredentialReference` — frozen value object; non-blank, no whitespace.
  Holds only an opaque `ref` string; no secret material can exist in this
  model by construction.

### Plugins (`domain/plugin.py`)
- `PluginMetadata` — frozen value object: `plugin_id` (lowercase dotted
  pattern), `name`, semver `version`, non-blank `engine_compatibility` and
  `sdk_compatibility` constraints, `dependencies` tuple.

### Errors (`akasha/errors.py`, modified)
- `DomainValidationError` (`DOMAIN_VALIDATION_ERROR`, HTTP 422).
- `InvalidStateTransitionError` (`INVALID_STATE_TRANSITION`, HTTP 409).

### Testing (`backend/tests/domain/`)
- `test_primitives_and_graph.py` (16 tests) — ID shape, entity identity,
  graph structural invariants.
- `test_project.py` (13 tests) — publish lifecycle, frozen versions,
  snapshot isolation, dependency validation.
- `test_execution.py` (16 tests + 81 matrix cases) — lifecycle happy paths,
  pause/resume, cancel from all non-terminal states, explicit TIMEOUT,
  full 9×9 transition matrix, node execution transitions, snapshot pinning.
- `test_trigger_credentials_plugin.py` (19 tests) — trigger config
  invariants, credential reference shape, plugin metadata validation.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/primitives.py` | Created | IDs, UTC clock, entity identity base, shared validators |
| `backend/src/akasha/domain/graph.py` | Created | `NodeInstance`, `Edge`, `Graph` |
| `backend/src/akasha/domain/project.py` | Created | `Project`, `ProjectDraft`, `ProjectVersion`, `ProjectStatus`, `Dependency` |
| `backend/src/akasha/domain/trigger.py` | Created | `TriggerType`, `Trigger` |
| `backend/src/akasha/domain/execution.py` | Created | `Execution`, `NodeExecution`, state machines |
| `backend/src/akasha/domain/credentials.py` | Created | `CredentialReference` |
| `backend/src/akasha/domain/plugin.py` | Created | `PluginMetadata` |
| `backend/src/akasha/errors.py` | Modified | Added `DomainValidationError`, `InvalidStateTransitionError` (+14 lines) |
| `backend/tests/domain/test_primitives_and_graph.py` | Created | Primitives + graph unit tests |
| `backend/tests/domain/test_project.py` | Created | Draft/publish unit tests |
| `backend/tests/domain/test_execution.py` | Created | State machine unit tests |
| `backend/tests/domain/test_trigger_credentials_plugin.py` | Created | Trigger/credential/plugin unit tests |

No configuration, migration, or dependency changes. No documentation outside
this report was modified. New directory: `backend/tests/domain/`.

---

## 5. Architecture Changes

No changes to the documented architecture; several document-level decisions
were made within it and are recorded in §22. Of note:

### Decision: domain layer is pure stdlib (no Pydantic in domain)
- **Context:** backend/AGENTS.md §12 states Pydantic is for system
  boundaries, not domain models; `tests/test_architecture.py` enforces that
  `domain/` never imports FastAPI/SQLAlchemy/Redis/Pydantic-Settings.
- **Chosen Approach:** frozen/mutable stdlib dataclasses with
  `__post_init__` invariant enforcement.
- **Alternatives Considered:** Pydantic models everywhere — rejected as
  boundary-coupling and unnecessary runtime cost.
- **Consequences:** Phase 05 must map dataclasses ↔ ORM rows explicitly;
  Phase 18 will need explicit serialization (dataclasses `asdict` is the
  current tested round-trip mechanism).

### Decision: state machines live in the domain now, policies later
- **Context:** Roadmap lists `Execution`/`NodeExecution` in Phase 02 but the
  full state machine plus `RetryPolicy`/`TimeoutPolicy` in Phase 07.
- **Chosen Approach:** the legal-transition tables and validated transition
  methods were implemented in Phase 02 because an `Execution` without
  transition invariants is a plain data bag (backend/AGENTS.md §18 forbids
  arbitrary status assignment). Retry/timeout/cancellation *policies* remain
  Phase 07.
- **Consequences:** Phase 07 should extend (not rewrite) the transition
  tables; the phase-isolation rule is arguably bent toward Phase 07, but the
  minimal table is what makes Phase-02 artifacts meaningful.

### Decision: errors live in top-level `akasha/errors.py`
- **Context:** Error classes need stable codes and HTTP status mapping
  (backend/AGENTS.md §11) and Phase 01 already created the base hierarchy.
- **Chosen Approach:** `DomainValidationError` / `InvalidStateTransitionError`
  extend `AkashaError` in `akasha/errors.py`; `domain/` imports them —
  permitted by `test_architecture.py` since `errors` is not a layer.
- **Consequences:** single error taxonomy across all layers; the architecture
  test's `INTERNAL_RULES` for `domain` remains satisfied.

---

## 6. Domain / Data Model Changes

New entities (identified by ID, `eq=False` dataclasses):
`Project`, `ProjectDraft` (mutable aggregate member), `NodeInstance`,
`Trigger`, `Execution`, `NodeExecution`.

New frozen value objects: `ProjectVersion`, `Edge`, `Dependency`,
`CredentialReference`, `PluginMetadata`, `Timestamps`.

New enums: `ProjectStatus` (5 values), `TriggerType` (6), `ExecutionStatus`
(9), `NodeExecutionStatus` (7).

State transitions — legal sets (terminal states have none):
- Execution: `CREATED→{QUEUED,CANCELLED}`, `QUEUED→{RUNNING,CANCELLED}`,
  `RUNNING→{PAUSED,WAITING,COMPLETED,FAILED,CANCELLED,TIMEOUT}`,
  `PAUSED→{RUNNING,CANCELLED}`, `WAITING→{RUNNING,CANCELLED}`.
- NodeExecution: `PENDING→{READY,SKIPPED,CANCELLED}`,
  `READY→{RUNNING,SKIPPED,CANCELLED}`, `RUNNING→{COMPLETED,FAILED,CANCELLED}`.

No database tables/migrations (persistence deferred to Phase 05 by design).

Compatibility: this is the first domain model; nothing to stay compatible
with. `ProjectVersion.schema_version = 1` seeds the format-versioning
required by vision §56.

---

## 7. API / Contract Changes

No API, WebSocket, event, or SDK contract changes. No endpoints added or
modified. The `/health` and `/ready` endpoints from Phase 01 are untouched
(verified by the still-passing Phase 01 test suite).

---

## 8. Implementation Details

- **Snapshot isolation.** `ProjectDraft.publish()` uses `copy.deepcopy` for
  the graph and triggers. `ProjectVersion` is `@dataclass(frozen=True)`;
  attribute rebinding raises `FrozenInstanceError` (tested). Note the frozen
  surface is shallow — `project_version.graph` can still be structurally
  mutated by a determined caller; the enforced invariant is that the version
  owns its *own* deep copy, so draft edits never leak (tested via `asdict`
  before/after arbitrary draft mutation).
- **Transition methods only.** Statuses are never assigned directly in
  domain code; every change routes through `_transition()` →
  `_check_transition()` which raises `InvalidStateTransitionError` with
  `{from, to}` details. `fail()` validates first, then records the error —
  a rejected transition leaves the object untouched.
- **Clock.** Transitions accept `now: datetime | None = None`, falling back
  to `utcnow()`. This is the minimal clock seam required by AGENTS.md §25;
  a full `Clock` protocol is not introduced until policies (Phase 07/23)
  need it.
- **Trigger validation** happens in `__post_init__` per type via structural
  `match`, so an invalid trigger cannot be constructed at all (fail at the
  boundary, not at fire time).
- **Identity.** `Entity.__eq__`/`__hash__` use `(type, id)`; subclasses must
  keep `@dataclass(eq=False)` or the dataclass decorator silently regenerates
  field-based equality (discovered via test failure, see §10).
- **IDs** are `<prefix>_<32 lowercase hex>`; `validate_id` runs in every
  entity `__post_init__`, which also catches malformed foreign references
  (`Edge`, `NodeExecution.node_id`, `Execution.project_id`).

---

## 9. Findings

### Finding
`@dataclass(eq=False)` on the `Entity` base does not propagate: each
dataclass subclass regenerates field-based `__eq__`/`__hash__` unless it
also passes `eq=False`.

### Impact
LOW (caught by the identity test immediately; would have broken
identity-by-id semantics for all entities)

### Resolution
FIXED — all five `Entity` subclasses declared `@dataclass(eq=False)`; the
identity test (`test_entity_equality_by_identity_not_fields`) pins this.

### Notes
The Python-3.14 style patch was applied programmatically across the four
affected modules; a future entity that forgets `eq=False` will reintroduce
the bug. There is no static guard for this — candidate for the
`test_architecture.py` AST scan in a later phase.

### Finding
`uv run mypy .` checks `tests/` in strict mode even though
`[tool.mypy] packages = ["akasha"]` only lists the package — passing a path
on the CLI overrides the package list. Test code must therefore also be
strict-clean.

### Impact
LOW

### Resolution
FIXED — tests annotated (`Callable`, explicit loop-variable types, an
`_expect()` helper to avoid narrowing conflicts on repeated
`e.status is ...` assertions).

### Notes
No config change made; keeping tests strict is consistent with §56/§4 of
backend/AGENTS.md.

### Finding
Pre-existing `StarletteDeprecationWarning` ("install httpx2") in the Phase 01
test stack.

### Impact
LOW — warning only, no behavior change.

### Resolution
OPEN (upstream diagnostics from the installed FastAPI/Starlette/httpx
versions; not caused by this phase)

---

## 10. Bugs / Problems Found

All below are introduced-and-fixed within this phase; no pre-existing
product-code bugs were found. The depreciation warning above is a tooling
artifact.

1. **Entity equality silently field-based.**
   - *Root cause:* dataclass `eq` not inherited (see §9).
   - *Fix:* `eq=False` on all `Entity` subclasses.
   - *Regression test:* `TestPrimitives.test_entity_equality_by_identity_not_fields`.
2. **`fail()` mutated `.error` before validating the transition.**
   - *Root cause:* attribute assignment preceded `_transition()`.
   - *Fix:* transition first, then assignment (both `Execution` and
     `NodeExecution`).
   - *Regression test:* covered by the illegal-transition matrix, which
     asserts status is unchanged after a rejected `fail()`.
3. **Transition-matrix test raised `KeyError` instead of asserting.**
   - *Root cause:* `_drive`/`_attempt` helper dicts lacked a `CREATED` entry
     (first test run: 18 failures, all test-side).
   - *Fix:* `_drive` gets `CREATED: []`; `_attempt` raises
     `InvalidStateTransitionError` for target `CREATED` (no inbound
     transitions exist).
4. **`Execution.create(**kwargs: object)` needed `# type: ignore`.**
   - *Root cause:* sloppy generic kwargs on the factory.
   - *Fix:* explicit keyword-only `trigger_type` parameter; no ignores in
     domain source.

---

## 11. Fixes Applied

| Symptom | Root cause | Implementation | Verification |
|---|---|---|---|
| 18 matrix test failures (`KeyError`) | test-helper dicts missing `CREATED` | explicit `CREATED: []` and guarded `_attempt` | 165/165 pass |
| `a == b` failed for same-id entities | subclass dataclass `eq` regeneration | `@dataclass(eq=False)` on all entities | identity test passes |
| rejected `fail()` left `.error` set | mutation before validation | reorder: validate → mutate | matrix illegal-branch asserts unchanged state |
| import-order + line-length lint failures (8) | formatting nits in new files | `ruff check --fix` + manual wraps + `ruff format` | `ruff check`/`format --check` pass |
| 5 mypy errors in tests | strict mode over `tests/`, narrowing conflicts | annotations + `_expect()` helper | `mypy .` clean |

---

## 12. Testing & Verification

### Unit Tests
- 50 new tests in `backend/tests/domain/` across 4 files: primitives/graph
  (16), project lifecycle (13), execution state machines (16 + 81
  parametrized matrix cases), trigger/credential/plugin (19).
- Result: **PASS** — full backend suite 165 passed, 1 unrelated warning.

### Component / Integration / API / E2E / Infrastructure Tests
- **N/A for new scope** — no infrastructure, persistence, or API was added.
- Phase 01 suites (`test_health`, `test_config`, `test_errors`,
  `test_logging`, `test_lifecycle`, `test_architecture`, `test_foundation`)
  re-run as part of the full suite: **PASS**, confirming no regressions and
  that the domain layer satisfies the static dependency-direction guard
  (`test_architecture.py`).

### Security Tests
- Invariant-level only: credential references reject blank/whitespace; no
  secret can exist in the model. Dedicated security tests are Phase 25.

### Performance Tests
- N/A — no benchmarks; nothing performance-sensitive added beyond a deepcopy
  at publish time (documented in §16).

---

## 13. Quality Gates

Commands per AGENTS.md §114 / backend/AGENTS.md §81, run from `backend/`:

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 41 files formatted; 2 new files reformatted during phase, then clean |
| Linter (`uv run ruff check .`) | PASS | 8 initial violations (import order, E501) fixed; final clean |
| Type Checker (`uv run mypy .`) | PASS | 39 files, strict mode, 0 errors; initial 5 test-side errors fixed |
| Unit Tests (`uv run pytest -q`) | PASS | 165 passed, 1 pre-existing upstream deprecation warning |
| Integration Tests | N/A | none in scope |
| API Tests | N/A (regression: Phase 01 API tests pass) | — |
| E2E Tests | N/A | — |
| Build | N/A | no build step configured in this phase |
| Security Checks | N/A | invariant-level secret exclusion covered by unit tests |

---

## 14. Acceptance Criteria Verification

Roadmap Phase 02 acceptance criteria (§6) and phase-brief completion list:

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Core domain objects exist (all 11 concepts) | PASS | `backend/src/akasha/domain/{project,graph,trigger,execution,credentials,plugin}.py` |
| Domain invariants are explicit | PASS | `__post_init__` validation everywhere; `DomainValidationError`/`InvalidStateTransitionError`; 50 tests |
| Draft/Published Version semantics are correct | PASS | frozen `ProjectVersion` + deepcopy snapshot; `test_draft_mutation_after_publish_does_not_change_version`, `test_execution_references_version_not_draft` |
| Domain is infrastructure-independent | PASS | stdlib-only imports; enforced by `test_architecture.py` |
| Unit tests cover important invariants | PASS | incl. full 9×9 execution transition matrix (81 cases) |
| Typing passes (`mypy`, strict) | PASS | 0 errors |
| Linting / formatting pass | PASS | ruff clean |
| No persistence implementation added | PASS | no repository/ORM code; `git status` shows only domain + tests + `errors.py` |
| No execution runtime implemented | PASS | `runtime/` layer untouched (still empty package from Phase 01) |
| Creation / invalid states / equality / state-transition tests (roadmap test list) | PASS | all covered; serialization spot-covered via `asdict` round-trip in the isolation test |
| IDs, timestamps, lifecycle state, value objects, validation primitives defined | PASS | `primitives.py`, `Timestamps`, status enums, frozen value objects |

---

## 15. Security Considerations

- **Secrets:** `CredentialReference` holds only an opaque ID-like string; no
  secret value field exists anywhere in the domain. Node configs are typed
  `dict[str, Any]` with a docstring constraint (references only) — actual
  secret redaction/encryption is Phase 13; structural export exclusion is
  Phase 18.
- **Input validation:** all constructors validate IDs, names, versions,
  trigger configs; invalid input fails at construction with
  `DomainValidationError`, never with a silently-invalid object.
- **Expression evaluation / code execution:** none exists in the domain
  (correctly — `eval`/`exec` are forbidden by architecture §31).
- **Error disclosure:** domain errors carry stable codes + string details
  only; no stack traces or internals are embedded.
- No auth/authz surfaces exist yet (Phase 20); nothing in this phase exposes
  one.

---

## 16. Performance Considerations

- `ProjectDraft.publish()` deep-copies the whole graph — O(nodes + edges +
  config size) per publish. Acceptable: publishing is a rare, explicit user
  action, not a runtime hot path. Revisit only if very large graphs make
  publish latency measurable (Phase 24 is the designated performance phase).
- Entity `__hash__` is `(type-name, id)` tuple hashing — O(1), fine for
  dict/set membership.
- No database queries, no I/O, no caching introduced. No measurements taken
  (nothing measurable to claim).

---

## 17. Documentation Changes

- Created: `docs/reports/backend/phase-02-core-domain-model.md` (this file).
- No changes to `docs/vision.md`, `docs/architecture.md`, roadmaps, or
  AGENTS.md files were required — the implementation matches the documented
  architecture.
- `domain/__init__.py` docstring already describes the layer contract
  (Phase 01) and remains accurate.
- Future: `docs/domain-model.md` is referenced by the roadmap (§2.2) but does
  not exist; writing it is a documentation task for a later phase (suggest
  alongside Phase 04 when graph semantics stabilize).

---

## 18. Dependencies / Infrastructure Changes

None. No new runtime or dev dependencies, no version changes, no environment
variables, no Docker/migrations/Redis changes. The domain layer adds zero
imports beyond the standard library and `akasha.errors`.

---

## 19. Deferred Work

| Item | Why deferred | Target phase | Blocks this phase? |
|---|---|---|---|
| Graph semantic validation (port types, cycles, required inputs, connectivity) | roadmap scope boundary | Phase 04 | No — structural invariants exist |
| `NodeDefinition`, ports, config schemas, `NodeResult` | roadmap scope boundary | Phase 03 | No — `NodeInstance.node_type` + free-form `config` is the Phase-02 seam |
| `RetryPolicy`, `TimeoutPolicy`, `CancellationState`, exhaustive per-transition policy tests | roadmap scope boundary | Phase 07 | No — transition tables already in place |
| Execution runtime / scheduler | roadmap scope boundary | Phase 08 | No |
| `ProjectStatus` transition rules and draft lint/validation before publish | needs application services; currently only `DRAFT→PUBLISHED` via `publish_draft()` | Phase 06 | No |
| Persistence, repositories, migrations | explicit phase constraint | Phase 05 | No |
| Deep immutability of `ProjectVersion.graph` | `frozen` is shallow on dataclasses; deep copy already isolates drafts | only if a real misuse appears | No (limitation, see §20) |
| Dedicated `Clock` protocol | YAGNI until retry/timeout policies need it | Phase 07/23 | No — `now=` parameter seam exists |

---

## 20. Known Limitations

1. `ProjectVersion.graph` is a deep-copied but still *structurally mutable*
   `Graph`; `frozen=True` blocks attribute rebinding, not method calls like
   `version.graph.add_node(...)`. The tested invariant is draft→version
   isolation, not hard tamper-proofing.
2. `ProjectStatus` has no enforced transition table (only
   `DRAFT→PUBLISHED` implicit in `publish_draft()`).
3. `Project.versions` and `ProjectDraft.triggers` are plain lists — no
   guards against duplicate version numbers inserted directly (only
   `publish_draft()` maintains monotonicity).
4. Trigger config validation covers only cron/interval/event; webhook has no
   secret/policy config yet (Phase 10).
5. `Dependency.version_constraint` is validated only as non-blank; full
   specifier semantics (e.g. `packaging.specifiers`) deliberately not pulled
   in as a dependency.
6. No JSON serialization API on domain objects beyond stdlib `asdict()`
   (import/export is Phase 18).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Next action |
|---|---|---|---|---|
| Upstream `StarletteDeprecationWarning` (httpx2) in Phase 01 test stack | cosmetic | LOW | test infra | Pin/upgrade when dependencies are next touched; not this phase |
| No static guard preventing future `Entity` subclasses from forgetting `eq=False` | could silently break identity semantics | LOW | domain | Consider extending `test_architecture.py` AST scan (Phase 03 house-keeping) |
| `docs/domain-model.md` referenced by roadmap but absent | documentation gap | LOW | docs | Write when graph semantics land (Phase 04) |

No blocking open issues.

---

## 22. Architectural / Product Decisions

1. **Draft/publish isolation via construction, not convention.** The only
   way to obtain a `ProjectVersion` with meaningful content is
   `ProjectDraft.publish()` / `Project.publish_draft()`, which deep-copies.
   This makes "executions never see later draft edits" (vision §5.5) a
   structural property rather than a discipline.
2. **State machines are data, not scattered conditionals.** Transition
   legality lives in two module-level tables; methods are thin wrappers.
   Phase 07 extends tables; phase 23's exhaustion/cancellation semantics
   plug into the same single funnel (`_transition`).
3. **Fail-at-construction validation.** Invalid domain objects
   (malformed IDs, blank names, bad trigger configs) cannot be constructed,
   so deeper layers never need defensive re-validation of structural
   invariants. API/DTO-level validation will still exist separately at the
   boundary.
4. **`now` parameter over clock abstraction.** Transitions accept an
   explicit optional timestamp; a full injectable `Clock` is deferred as
   unjustified abstraction until retry/timeout policies require it
   (AGENTS.md §25 intent preserved, §119 avoided).
5. **Error taxonomy anchored in the existing Phase 01 module.** One
   `AkashaError` tree with stable codes; domain adds exactly two subclasses
   instead of a parallel hierarchy.
6. **`NodeExecution.node_id` references the pinned version's instance IDs**,
   keeping node executions joinable to the immutable snapshot — relevant for
   the execution inspector (vision §32).

---

## 23. Repository / Git State

- Branch: `main`.
- Last commit before this phase: `ce2928c backend phase 01 - backend
  application skeleton`.
- Working tree (this phase's work, all uncommitted — no commit was requested):
  - Modified: `backend/src/akasha/errors.py` (+14 lines)
  - Untracked: 7 new domain modules, `backend/tests/domain/` (4 test files),
    `docs/reports/backend/phase-02-core-domain-model.md` (this report)
- No unrelated user changes detected; `.DS_Store` noise pre-exists and is not
  a backend artifact.

---

## 24. Phase Completion Assessment

### Implemented
All eleven domain concepts with explicit invariants; draft/publish snapshot
isolation; execution and node-execution state machines with validated
transitions; shared primitives (IDs, UTC time, entity identity); two new
structured domain errors; 50 unit tests.

### Verified
Full backend suite (165 tests) passes with zero domain-related warnings;
mypy strict, ruff lint, ruff format all clean; static architecture guard
confirms the domain layer imports nothing forbidden; snapshot isolation and
transition legality proven by tests (not by review alone).

### Not Implemented
Nothing from the phase scope.

### Deferred
Graph validation (04), node contract (03), persistence (05), publish
application service + status transition rules (06), retry/timeout policies
(07) — all per roadmap boundaries, none blocking.

### Open
Three low-severity items (§21): upstream deprecation warning, missing AST
guard for `eq=False`, missing `docs/domain-model.md`.

### Blocking Issues
None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase

**Next phase: 03 — Node Contract.**

- Assume available: `Entity` (`eq=False` — keep it on new subclasses),
  `new_id`/`validate_id`, `DomainValidationError`/`InvalidStateTransitionError`,
  `NodeInstance(id, node_type, config, label)` as the placed-instance model
  (extend it, don't fork a parallel "node" concept — AGENTS.md §6),
  `CredentialReference` (node configs must reference credentials, never
  embed secrets), transition tables as the pattern for any new state machine.
- Contracts to build: `NodeDefinition`, metadata, input/output ports,
  config schemas, capabilities, `NodeExecutionContext` seams (careful:
  runtime context is Phase 03 contract + Phase 08 implementation; keep the
  domain contract free of runtime dependencies).
- Constraints that must not be changed casually: frozen `ProjectVersion`;
  deep-copy snapshot in `publish()`; the dependency-direction rules enforced
  by `tests/test_architecture.py` (a NodeDefinition must not import
  FastAPI/SQLAlchemy/Redis either).
- Tests to emulate: full transition-matrix style parametrization worked well
  for exhaustive contract-type checks; new contract tests should follow the
  `tests/domain/` layout and remain strict-mypy clean (`mypy .` covers tests).
