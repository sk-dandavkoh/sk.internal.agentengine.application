# Phase 04 — Project Graph + Validation

## 1. Executive Summary

Phase 04 required making Project Graphs structurally and semantically validatable before
execution: graph construction operations, node/edge/port lookup and resolution, type
compatibility, required-input validation, configuration validation, connectivity and cycle
detection, duplicate/missing/invalid-edge detection, and structured deterministic
validation errors — without implementing execution.

Implemented as one new domain module `akasha/domain/graph_validation.py` (~350 LOC)
containing `ValidationCode`, `ValidationIssue`, `ValidationResult`,
`ports_compatible()`, `resolve_ports()` and `validate_graph()`, plus 11-line additions to
`Graph` (`remove_edge`, `node`) and `ProjectDraft` (`validate`). Validation is
collect-all-issues, never raises on user data, and is deterministic (fixed iteration
order). 38 new tests including two hypothesis property tests; full suite 246 passed;
ruff check/format and mypy strict all pass.

Acceptance criterion "A Project Draft can be fully validated without executing it" is
satisfied via `ProjectDraft.validate(definitions)`.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Roadmap §8 (Phase 04): "Make Projects structurally validatable."

Scope: graph construction, node insertion/removal, edge creation/removal, port
resolution, type compatibility, required input validation, graph connectivity
validation, cycle detection, duplicate node detection, missing node detection,
configuration validation, structured validation errors.

Explicitly NOT in scope (and not implemented): execution (Phase 08), persistence
(Phase 05), the Node Registry (Phase 09), application services / publish gating
(Phase 06), control-flow loop semantics (Phase 09+).

---

## 3. Scope Implemented

### Domain

- **`akasha/domain/graph_validation.py`** (new):
  - `ValidationCode(StrEnum)`: `EMPTY_GRAPH`, `UNKNOWN_NODE_TYPE`,
    `EDGE_UNKNOWN_NODE`, `DUPLICATE_EDGE`, `UNKNOWN_PORT`,
    `INVALID_PORT_CONNECTION`, `MISSING_REQUIRED_INPUT`, `INVALID_CONFIG`,
    `CYCLE_DETECTED`, `DISCONNECTED_NODE`.
  - `ValidationIssue(frozen dataclass)`: `code`, `message`, optional `node_id`,
    `edge`, `port`. Carries locations only — never config payload values.
  - `ValidationResult(frozen dataclass)`: `issues: tuple`, `ok` property,
    `by_code()` lookup.
  - `ports_compatible(source: NodeOutput, target: NodeInput) -> bool`.
  - `resolve_ports(edge, graph, definitions) -> tuple[NodeOutput, NodeInput]` —
    raising variant for consumers that already know the graph validates.
  - `validate_graph(graph, definitions) -> ValidationResult` — the validator,
    collecting all issues in a fixed order.
- **`akasha/domain/graph.py`** (modified): added `Graph.remove_edge(edge)` and
  `Graph.node(node_id)` lookup; both raise `DomainValidationError` on unknown
  references.
- **`akasha/domain/project.py`** (modified): added
  `ProjectDraft.validate(definitions) -> ValidationResult`, a thin delegation to
  `validate_graph`.

### Validation rules implemented

| Check | Trigger |
|---|---|
| `EMPTY_GRAPH` | graph has no nodes |
| `UNKNOWN_NODE_TYPE` | instance's `node_type` not in the definition mapping |
| `INVALID_CONFIG` | `NodeDefinition.validate_config` rejects instance config (incl. non-dict config from malformed construction) |
| `EDGE_UNKNOWN_NODE` | edge references a node id not in the graph |
| `DUPLICATE_EDGE` | identical (src, src_port, tgt, tgt_port) twice — only reachable via malformed construction |
| `UNKNOWN_PORT` | edge port not declared on the node's definition |
| `INVALID_PORT_CONNECTION` | port type incompatibility, or >1 edge into a non-`multiple` input |
| `MISSING_REQUIRED_INPUT` | required input port with no incoming edge |
| `CYCLE_DETECTED` | directed cycle, reported once per distinct cycle with node path |
| `DISCONNECTED_NODE` | node participates in no edge |

### Testing

- `backend/tests/domain/test_graph_validation.py` (new): 38 test functions —
  valid linear/branching/merge/diamond graphs, optional and defaulted inputs,
  unknown node types, dangling edges, duplicate edges, unknown source/target ports,
  multi-edge rejection, `multiple`-input multi-edge acceptance, config errors
  (missing key, wrong type, valid config), cycles (2-node, 3-node, branch-back),
  disconnected nodes, empty graph, all-problems-collected-at-once malformed graph,
  non-dict config robustness, determinism, `ProjectDraft.validate` integration,
  `resolve_ports` and `Graph.remove_edge`/`Graph.node` behavior.
- Two hypothesis property tests (dependency `hypothesis>=6.168.2` added as dev
  dependency): (a) validator never crashes and is deterministic on arbitrary random
  graphs; (b) forward-only edges never yield cycle/port/compatibility issues.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/graph_validation.py` | Created (~350 LOC) | Phase 04 core: validator, issues, compatibility |
| `backend/src/akasha/domain/graph.py` | Modified (+11 LOC) | `remove_edge`, `node()` lookup — required graph operations |
| `backend/src/akasha/domain/project.py` | Modified (+11 LOC) | `ProjectDraft.validate(definitions)` — draft validatable without execution |
| `backend/tests/domain/test_graph_validation.py` | Created (~590 LOC, 38 tests) | Phase 04 test coverage |
| `backend/pyproject.toml` | Modified | dev dependency `hypothesis>=6.168.2` |
| `backend/uv.lock` | Modified | lockfile for hypothesis (+ sortedcontainers) |
| `docs/reports/backend/phase-04-project-graph-validation.md` | Created | this report |

No migrations, no API changes, no configuration changes.

---

## 5. Architecture Changes

No changes to `/docs/architecture.md` — the phase confirms it. Two clarifying
decisions were made inside the documented architecture:

### Decision 1 — Validation collects issues instead of raising

- **Context:** Phase 02/03 domain invariants raise exceptions at construction time.
  Graph validation must report *all* problems of a user-built draft for editor UX.
- **Chosen:** `validate_graph` returns `ValidationResult`; it never raises on user
  data (including malformed-by-construction graphs).
- **Alternatives:** raise first error (matches §9 exceptions but poor UX); exceptions
  as control flow (explicitly forbidden by AGENTS.md §10).
- **Consequences:** API/Application layers (Phase 06) map `ValidationResult` to
  structured error responses; §45 example JSON error shape (`code`, `node_id`,
  `port`, `message`) is directly supported by `ValidationIssue` fields.

### Decision 2 — Node definition lookup is a plain `Mapping[str, NodeDefinition]`

- **Context:** The Node Registry is Phase 09; validation needs definitions now.
- **Chosen:** `validate_graph(graph, definitions: Mapping[...])`.
- **Reason:** avoids building a premature registry; the Phase 09 registry will simply
  provide this mapping.
- **Consequences:** no coupling between domain validation and registry mechanics.

### Decision 3 — Any directed cycle is invalid (for now)

- **Context:** architecture §14 lists Loop as a control-flow concept, but loop nodes
  do not exist yet (Phase 09).
- **Chosen:** `CYCLE_DETECTED` for every directed cycle; `ponytail:` comment names the
  ceiling and upgrade path (marked loop-body exception once control-flow nodes exist).
- **Consequences:** Phase 09 must revisit cycle detection when adding Loop/ForEach.

---

## 6. Domain / Data Model Changes

No changes to existing entities (`Graph`, `NodeInstance`, `Edge`, `ProjectDraft`
behavior unchanged apart from the additive `validate()` method and `Graph.remove_edge`
/`Graph.node`). New types are additive only: `ValidationCode`, `ValidationIssue`,
`ValidationResult`. No serialization format, no schema version bump, no persistence.
Fully backward compatible with Phase 02/03.

---

## 7. API / Contract Changes

No HTTP/API changes (Phase 06 owns the project API). New domain-level contract:

- `validate_graph(graph: Graph, definitions: Mapping[str, NodeDefinition]) -> ValidationResult`
- `ProjectDraft.validate(definitions) -> ValidationResult`
- `ports_compatible(source: NodeOutput, target: NodeInput) -> bool`
- `resolve_ports(edge, graph, definitions) -> tuple[NodeOutput, NodeInput]` (raises
  `DomainValidationError`)

Semantics fixed by this phase: a non-`multiple` input port accepts exactly one
incoming edge; a `multiple` input port accepts several (each edge contributes one list
element). `PortType.JSON` is the wildcard type, compatible with every other type in
either direction.

---

## 8. Implementation Details

- **Determinism:** nodes are iterated `sorted(graph.nodes)`; edges in insertion
  order; issues grouped by check. Two runs over equal graphs yield identical issue
  code sequences (tested). Messages embed node ids, so identical *structure* with
  different random ids yields equal codes but different message text — the determinism
  test therefore compares codes across separate builds, full equality across repeated
  runs on the same object.
- **Cycle detection:** iterative three-color DFS (no recursion — no depth limit on
  large graphs); each distinct cycle (by node set) reported once with its node path.
- **Malformed-input robustness:** a directly-constructed `Graph` can bypass
  `add_edge`/`add_node` invariants (duplicate edges, dangling endpoints, non-dict
  config). The validator reports these as issues instead of crashing (AGENTS.md
  §104); covered by dedicated tests and the random-graph property test.
- **Security:** `ValidationIssue` deliberately carries no config values — locations
  only — so issues are safe to log and return through the API (AGENTS.md §35/§76).
  Credential *references* inside configs are never dereferenced by validation
  (resolution is Phase 13, execution time).

---

## 9. Findings

### Finding 1 — DAG property initially contradicted single-edge-per-input rule

- **Impact:** LOW (test/modeling, caught immediately)
- **Resolution:** FIXED
- **Notes:** The hypothesis DAG test generated two sources feeding one input and
  surfaced the question whether that is invalid. Decision: invalid for scalar inputs,
  valid for `multiple` inputs. This refined `ports_compatible` (originally written as
  symmetric `multiple` shape equality) to the asymmetric rule documented in §7.

### Finding 2 — Messages embed random node ids

- **Impact:** LOW
- **Resolution:** FIXED (test adjusted)
- **Notes:** Cross-build determinism holds for issue *codes/order*, not message text.

No architectural inconsistencies or incorrect roadmap assumptions were discovered.

---

## 10. Bugs / Problems Found

All of the following were introduced during this phase and fixed within it:

1. **Iterative DFS left nodes permanently GRAY** (first draft of `_check_cycles`
   used a broken exit-marker helper). Root cause: half-finished iterative coloring.
   Fix: rewritten as a standard explicit-stack three-color DFS. Regression tests:
   cycle tests (2-node, 3-node, branch-back, diamond) + DAG property test.
2. **`_edge_ports` skip sentinel returned wrong types.** Root cause: convoluted
   placeholder logic. Fix: `_resolve_or_issue` with explicit `tuple | issue | None`
   contract; mypy strict caught the residual typing problem, then fixed.
3. **Two test bugs** (`test_node.domain id shape violated in ghost-id fixtures;
   cross-build message equality). Root cause: `Edge` requires canonical ids; random
   ids differ per build. Fixes: `new_id("node")` ghosts; compare codes not messages.

No pre-existing bugs in Phase 02/03 code were found.

---

## 11. Fixes Applied

| Symptom | Root cause | Fix | Verification |
|---|---|---|---|
| Cycle detection missed/hung drafts | broken exit-marker design | explicit-stack 3-color DFS | cycle/diamond tests + property test |
| `mypy` union return errors in `_resolve_or_issue` | loop-built tuple untypable | two explicit lookups, union return | `mypy` strict pass |
| Validator crash vector on `config="not-a-dict"` | dataclass type hints aren't runtime-enforced | `isinstance` guard → `INVALID_CONFIG` issue | dedicated test |
| `ruff` RUF005/E501/I001 violations | formatting | `--fix` + manual line split | `ruff check`/`format --check` pass |

---

## 12. Testing & Verification

### Unit Tests
- Entire graph validation rule matrix (§3 table), port compatibility, port
  resolution, draft integration: **38 tests, PASS**.

### Property-based Tests
- hypothesis: (a) random graphs (≤6 node types, ≤12 edges) — validator never raises,
  result deterministic, every issue code ∈ `ValidationCode`; (b) forward-only edges
  — no cycle/port/compatibility issues. 200 examples each: **PASS**.

### Integration / API / Infrastructure / E2E / Security / Performance Tests
- **N/A for this phase** — no infrastructure (PostgreSQL/Redis is Phase 05), no API
  (Phase 06), no runtime (Phase 08). Security-relevant behavior (no payload values in
  issues, no crash on malformed input) is covered by unit/property tests.

### Full suite
- `uv run pytest -q`: **246 passed** (208 pre-existing + 38 new), 1 unrelated
  third-party deprecation warning (`starlette.testclient`/`httpx`).

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`ruff format --check .`) | PASS | 3 files initially unformatted, fixed |
| Linter (`ruff check .`) | PASS | 4 initial violations (I001/RUF005/E501), fixed |
| Type Checker (`mypy .`, strict) | PASS | 43 source files, no issues |
| Unit Tests | PASS | 246 passed |
| Integration Tests | N/A | no infrastructure in this phase |
| API Tests | N/A | no API changes |
| E2E Tests | N/A | Phase 27 |
| Build | PASS | `uv sync` resolves with new dep; `akasha` entry unchanged |
| Security Checks | PASS (review) | no secrets, no payload leakage, malformed-input robustness tested |
| Accessibility | N/A | no UI |

---

## 14. Acceptance Criteria Verification

Roadmap §8 acceptance criterion:

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| A Project Draft can be fully validated without executing it | PASS | `ProjectDraft.validate(definitions)` → `validate_graph`; tested in `TestDraftIntegration`; no runtime involvement (execution is Phase 08 and untouched) |

Scope items (all implemented and tested): node insertion/removal ✓, edge
creation/removal ✓, node/port lookup ✓, port resolution ✓, type compatibility ✓,
required input validation ✓, configuration validation ✓, connectivity validation ✓,
cycle detection ✓, duplicate detection ✓ (nodes at construction, edges in malformed
graphs), missing node detection ✓, structured validation errors ✓, deterministic
validation ✓ (tested), no execution ✓.

Test matrix from the session brief — all covered: empty graphs, valid linear,
branching, merge, missing nodes, duplicate edges, missing/unknown ports,
incompatible ports, missing required inputs, invalid configuration, invalid edges,
cycles, malformed graphs; property-based tests included.

---

## 15. Security Considerations

- `ValidationIssue` contains identifiers and port names only — never config values
  — keeping validation output safe for logs and API responses (§35/§76).
- No expression evaluation, no file access, no network access introduced.
- Validation never dereferences credential references; configs may contain only
  references (enforced by the Phase 03 contract), and validation does not change that.
- Malformed-input handling (§104): non-dict config, dangling edges and duplicate
  edges in malformed graphs produce issues, never exceptions.

---

## 16. Performance Considerations

Validation is O(V + E) per check, single pass per rule; cycle detection is iterative
(no recursion limit). No measurements taken — no indication any are needed at current
graph sizes. Large-graph performance testing is Phase 24 scope.

---

## 17. Documentation Changes

- This report. No changes to `/docs/architecture.md`, `/docs/vision.md`, roadmap or
  AGENTS.md were necessary — implementation matches documented architecture.
- `graph.py`'s module docstring ("Semantic validation is Phase 04 and deliberately
  not implemented here") is now outdated in its second clause and should be shortened
  in a future edit (non-blocking; the statement is historical context, not incorrect
  about module responsibilities).

---

## 18. Dependencies / Infrastructure Changes

- Added dev dependency `hypothesis>=6.168.2` (+ transitive `sortedcontainers`):
  AGENTS.md §103 explicitly names graph validation as a property-based-testing
  candidate, and the phase brief requested property-based tests where useful. No new
  runtime dependency. `uv.lock` updated.
- No Docker/PostgreSQL/Redis/environment changes.

---

## 19. Deferred Work

| Item | Why deferred | Target phase | Blocks completion? |
|---|---|---|---|
| Enforce validation before publish (`publish`/`publish_draft` gating) | orchestration belongs to application services (`ValidateProject`/`PublishProject`) | Phase 06 | No — draft is fully validatable, criterion met |
| Node Registry as definition source | explicitly Phase 09 scope | Phase 09 | No — validator takes a `Mapping` |
| Loop-body exception in cycle detection | loop control-flow nodes don't exist yet | Phase 09+ | No — marked with `ponytail:` comment |
| Collecting *all* config violations per node (currently first per node) | current schema subset reports first error; sufficient signal | when schemas gain complexity | No — `ponytail:` comment documents upgrade path |
| Publishing-cycle / draft `VALID` status transitions | project lifecycle state machine is Phase 06 | Phase 06 | No |

---

## 20. Known Limitations

- Config validation reports the first violation per node only.
- Cyclic graphs cannot be expressed even intentionally (no loop construct until
  Phase 09+ control-flow nodes).
- Validation has no notion of triggers/entry points; a graph connected through any
  single component is accepted, whether or not a trigger can reach it (trigger model
  is Phase 10).
- `PortType.JSON` wildcards both directions — a deliberate escape hatch; value-level
  checking happens at execution via `validate_port_value`.

---

## 21. Open Issues

No known open issues resulting from this phase.

---

## 22. Architectural / Product Decisions

Persistent decisions from this phase (details in §5):

1. Graph validation returns collected `ValidationIssue`s; user data never raises.
2. Definition resolution is a plain `Mapping` until the Phase 09 registry exists.
3. Cycles are unconditionally invalid until loop control-flow nodes exist.
4. `multiple` inputs are list-fed and accept many edges; scalar inputs accept one.
5. `PortType.JSON` is the bidirectional compatibility wildcard.
6. Structured issues carry no payload values (security by construction).

---

## 23. Repository / Git State

- Branch: current HEAD at `c6620bd backend phase 03 - node contract`; changes from
  this phase are **uncommitted** (committing was not requested).
- Modified: `backend/pyproject.toml`, `backend/uv.lock`,
  `backend/src/akasha/domain/graph.py`, `backend/src/akasha/domain/project.py`.
- Untracked: `backend/src/akasha/domain/graph_validation.py`,
  `backend/tests/domain/test_graph_validation.py`, this report.
- No unrelated user changes detected in the working tree.

---

## 24. Phase Completion Assessment

### Implemented
Graph construction operations (`remove_edge`, `node`), port lookup/resolution
(`resolve_ports`), type compatibility (`ports_compatible`), and the full validator
`validate_graph` with ten structured issue codes, plus `ProjectDraft.validate`.

### Verified
246 tests pass (38 new incl. 2 property-based); ruff check/format pass; mypy strict
passes on 43 files; determinism and malformed-input robustness explicitly tested.

### Not Implemented
Nothing from the phase scope.

### Deferred
Publish-time enforcement (Phase 06), registry integration (Phase 09), loop-aware
cycle detection (Phase 09+), multi-error config reporting (as needed).

### Open
None.

### Blocking Issues
None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase

**Phase 05 — Persistence Foundation** can assume:

- Domain objects (`Project`, `ProjectDraft`, `ProjectVersion`, `Graph`,
  `NodeInstance`, `Edge`, `Trigger`) are plain dataclasses, free of infrastructure
  imports; `ProjectDraft`/`Graph` are mutable, `ProjectVersion` is frozen and
  receives a deep copy of the graph on publish.
- Validation now exists **before** persistence: repositories that store drafts should
  not re-derive validation rules; use `ProjectDraft.validate(definitions)`.
- `Graph.nodes` is a dict keyed by node id; edges reference node ids — persistable
  as two flat collections keyed by id.
- No repository interfaces exist yet; Phase 05 introduces them (application layer
  must depend on interfaces, infrastructure implements them).
- Do NOT add JSON-schema persistence concerns into the domain; `config` is an opaque
  `dict[str, Any]` at the domain level and should serialize as-is.

**MUST NOT be changed casually:** the `ValidationCode` string values (frontend
contract), the single-edge-per-scalar-input rule, the JSON wildcard rule, and the
never-raise property of `validate_graph`.
