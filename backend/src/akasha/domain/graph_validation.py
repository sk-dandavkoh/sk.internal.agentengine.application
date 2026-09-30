"""Project Graph validation (roadmap Phase 04).

Structural integrity already lives on ``Graph``/``Edge`` (Phase 02). This
module adds the semantic layer: given a graph and the Node Definitions its
instances reference, decide — deterministically and without executing
anything — whether the graph may be published/executed.

Validation never raises for user data: it collects every problem it can
find into a ``ValidationResult`` of structured ``ValidationIssue``s, so the
editor can show all errors at once. Iteration order is fixed (nodes sorted
by id, edges in insertion order), so the same graph always yields the same
issues in the same order (architecture §72).

Node definition lookup is a plain ``Mapping[str, NodeDefinition]``; the
Node Registry provides it (``NodeRegistry.definitions``).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, auto

from akasha.domain.credentials import plaintext_secret_paths
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeDefinition, NodeInput, NodeOutput, PortType
from akasha.errors import AkashaError, DomainValidationError


class ValidationCode(StrEnum):
    """Stable machine-readable codes (architecture §45/§46)."""

    EMPTY_GRAPH = auto()
    UNKNOWN_NODE_TYPE = auto()
    EDGE_UNKNOWN_NODE = auto()
    DUPLICATE_EDGE = auto()
    UNKNOWN_PORT = auto()
    INVALID_PORT_CONNECTION = auto()
    MISSING_REQUIRED_INPUT = auto()
    INVALID_CONFIG = auto()
    # Secret material in config (stored before Phase 13 enforced references).
    PLAINTEXT_SECRET = auto()
    CYCLE_DETECTED = auto()
    DISCONNECTED_NODE = auto()
    # Declared project dependencies (architecture §3.8/§144).
    MISSING_DEPENDENCY = auto()
    INCOMPATIBLE_DEPENDENCY = auto()
    INVALID_DEPENDENCY = auto()
    # Triggers (architecture §125): publish gates, not construction errors.
    DUPLICATE_TRIGGER_ID = auto()
    INVALID_TRIGGER_SCHEDULE = auto()


@dataclass(frozen=True)
class ValidationIssue:
    """One structured validation finding. Carries locations only — never
    config payload values — so issues are safe to log and return (§35)."""

    code: ValidationCode
    message: str
    node_id: str | None = None
    edge: Edge | None = None
    port: str | None = None
    trigger_id: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def by_code(self, code: ValidationCode) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.code is code)


def ports_compatible(source: NodeOutput, target: NodeInput) -> bool:
    """Central type-compatibility rule (backend/AGENTS.md §20).

    Exact ``PortType`` match is compatible. ``PortType.JSON`` is the generic
    serializable type and connects to/from any other type — the sanctioned
    escape hatch so pipelines are not forced into bogus casts. A ``multiple``
    (list) output cannot feed a scalar input; a scalar output may feed a
    ``multiple`` input, which collects each incoming edge's value.
    """
    if source.multiple and not target.multiple:
        return False
    return source.port_type is target.port_type or PortType.JSON in (
        source.port_type,
        target.port_type,
    )


def resolve_ports(
    edge: Edge, graph: Graph, definitions: Mapping[str, NodeDefinition]
) -> tuple[NodeOutput, NodeInput]:
    """Resolve an edge to its declared ports.

    Raises ``DomainValidationError`` when any endpoint (node, node type or
    port) cannot be resolved. For consumers that already know the graph
    validates; ``validate_graph`` reports the same conditions as issues.
    """
    source = graph.nodes.get(edge.source_node_id)
    target = graph.nodes.get(edge.target_node_id)
    if source is None or target is None:
        raise DomainValidationError("edge references an unknown node")
    source_def = definitions.get(source.node_type)
    target_def = definitions.get(target.node_type)
    if source_def is None or target_def is None:
        raise DomainValidationError("edge references a node of unknown type")
    try:
        output = source_def.output(edge.source_port)
        input_port = target_def.input(edge.target_port)
    except AkashaError as exc:
        raise DomainValidationError(exc.message) from exc
    return output, input_port


def validate_graph(
    graph: Graph, definitions: Mapping[str, NodeDefinition]
) -> ValidationResult:
    """Fully validate a graph. Never raises on user data; result contains
    every issue found, in deterministic order."""
    issues: list[ValidationIssue] = []
    node_ids = sorted(graph.nodes)

    issues += _check_duplicate_edges(graph)

    # Resolve node instances to their definitions.
    known_defs: dict[str, NodeDefinition] = {}
    for node_id in node_ids:
        instance = graph.nodes[node_id]
        definition = definitions.get(instance.node_type)
        if definition is None:
            issues.append(
                ValidationIssue(
                    ValidationCode.UNKNOWN_NODE_TYPE,
                    f"unknown node type: {instance.node_type!r}",
                    node_id=node_id,
                )
            )
        else:
            known_defs[node_id] = definition
            issues += _check_node_config(node_id, instance, definition)

    # Resolve edges to declared ports; check type compatibility and
    # single-connection-per-input.
    fed_inputs: set[tuple[str, str]] = set()
    for edge in graph.edges:
        if missing := _missing_endpoint(edge, graph):
            issues.append(missing)
            continue
        ports = _resolve_or_issue(edge, graph, known_defs)
        if ports is None:
            continue
        if isinstance(ports, ValidationIssue):
            issues.append(ports)
            continue
        output, input_port = ports
        key = (edge.target_node_id, edge.target_port)
        # A non-multiple input takes exactly one connection; a multiple
        # input is list-fed, so several edges may contribute to it.
        if key in fed_inputs and not input_port.multiple:
            issues.append(
                ValidationIssue(
                    ValidationCode.INVALID_PORT_CONNECTION,
                    f"input port {edge.target_port!r} has more than one incoming edge",
                    node_id=edge.target_node_id,
                    edge=edge,
                    port=edge.target_port,
                )
            )
            continue
        fed_inputs.add(key)
        if not ports_compatible(output, input_port):
            issues.append(
                ValidationIssue(
                    ValidationCode.INVALID_PORT_CONNECTION,
                    f"output type {output.port_type!r} is not compatible with "
                    f"input type {input_port.port_type!r}",
                    node_id=edge.target_node_id,
                    edge=edge,
                    port=edge.target_port,
                )
            )

    # Required inputs of every resolvable node must be fed.
    for node_id in node_ids:
        definition = known_defs.get(node_id)
        if definition is None:
            continue
        for port in definition.inputs:
            if port.required and (node_id, port.name) not in fed_inputs:
                issues.append(
                    ValidationIssue(
                        ValidationCode.MISSING_REQUIRED_INPUT,
                        f"required input {port.name!r} is not connected",
                        node_id=node_id,
                        port=port.name,
                    )
                )

    issues += _check_cycles(graph)
    issues += _check_connectivity(node_ids, graph)
    return ValidationResult(tuple(issues))


def _check_duplicate_edges(graph: Graph) -> list[ValidationIssue]:
    """Graph.add_edge prevents duplicates; a directly-constructed Graph may
    still contain them, and validation must not trust construction (§104)."""
    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str, str, str]] = set()
    for edge in graph.edges:
        key = (
            edge.source_node_id,
            edge.source_port,
            edge.target_node_id,
            edge.target_port,
        )
        if key in seen:
            issues.append(
                ValidationIssue(
                    ValidationCode.DUPLICATE_EDGE, "duplicate edge", edge=edge
                )
            )
        seen.add(key)
    return issues


def _check_node_config(
    node_id: str, instance: NodeInstance, definition: NodeDefinition
) -> list[ValidationIssue]:
    if not isinstance(instance.config, dict):
        # Malformed construction bypassed the dataclass type hint (§104).
        return [
            ValidationIssue(
                ValidationCode.INVALID_CONFIG,
                "configuration must be an object",
                node_id=node_id,
            )
        ]
    if leaks := plaintext_secret_paths(instance.config):
        return [
            ValidationIssue(
                ValidationCode.PLAINTEXT_SECRET,
                "configuration contains a secret value at "
                f"{', '.join(f'config{p}' for p in leaks)}; use a credential reference",
                node_id=node_id,
            )
        ]
    try:
        definition.validate_config(instance.config)
    except AkashaError as exc:
        # ponytail: first config violation per node only. Upgrade path: a
        # collecting NodeConfigSchema when schemas need all-error reporting.
        return [
            ValidationIssue(
                ValidationCode.INVALID_CONFIG,
                f"invalid configuration: {exc.message}",
                node_id=node_id,
            )
        ]
    return []


def _missing_endpoint(edge: Edge, graph: Graph) -> ValidationIssue | None:
    for node_id in (edge.source_node_id, edge.target_node_id):
        if node_id not in graph.nodes:
            return ValidationIssue(
                ValidationCode.EDGE_UNKNOWN_NODE,
                f"edge references unknown node: {node_id}",
                edge=edge,
            )
    return None


def _resolve_or_issue(
    edge: Edge, graph: Graph, known_defs: Mapping[str, NodeDefinition]
) -> tuple[NodeOutput, NodeInput] | ValidationIssue | None:
    """Resolve both edge ports. ``None`` means the node or node type is
    already reported elsewhere; an issue means an undeclared port."""
    source = graph.nodes[edge.source_node_id]
    target = graph.nodes[edge.target_node_id]
    source_def = known_defs.get(source.id)
    target_def = known_defs.get(target.id)
    if source_def is None or target_def is None:
        return None
    try:
        output = source_def.output(edge.source_port)
    except AkashaError:
        return ValidationIssue(
            ValidationCode.UNKNOWN_PORT,
            f"node {source.id} ({source.node_type}) has no output port "
            f"{edge.source_port!r}",
            node_id=source.id,
            edge=edge,
            port=edge.source_port,
        )
    try:
        input_port = target_def.input(edge.target_port)
    except AkashaError:
        return ValidationIssue(
            ValidationCode.UNKNOWN_PORT,
            f"node {target.id} ({target.node_type}) has no input port "
            f"{edge.target_port!r}",
            node_id=target.id,
            edge=edge,
            port=edge.target_port,
        )
    return output, input_port


def _check_cycles(graph: Graph) -> list[ValidationIssue]:
    """Iterative DFS over existing nodes; each distinct cycle reported once.

    ponytail: any directed cycle is invalid. Loop constructs (architecture
    §14) will need a marked loop-body exception once loop nodes exist;
    If/Switch branch without cycles, so an executable graph is still a DAG.
    """
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in graph.nodes}
    for edge in graph.edges:
        if edge.source_node_id in adjacency and edge.target_node_id in adjacency:
            adjacency[edge.source_node_id].append(edge.target_node_id)

    UNVISITED, ON_PATH, DONE = 0, 1, 2
    state = dict.fromkeys(graph.nodes, UNVISITED)
    issues: list[ValidationIssue] = []
    reported: set[frozenset[str]] = set()

    for root in sorted(graph.nodes):
        if state[root] != UNVISITED:
            continue
        state[root] = ON_PATH
        path = [root]
        stack = [(root, iter(adjacency[root]))]
        while stack:
            node, it = stack[-1]
            descended = False
            for nxt in it:
                if state[nxt] == ON_PATH:
                    cycle = [*path[path.index(nxt) :], nxt]
                    key = frozenset(cycle)
                    if key not in reported:
                        reported.add(key)
                        issues.append(
                            ValidationIssue(
                                ValidationCode.CYCLE_DETECTED,
                                "cycle detected: " + " -> ".join(cycle),
                                node_id=nxt,
                            )
                        )
                elif state[nxt] == UNVISITED:
                    state[nxt] = ON_PATH
                    path.append(nxt)
                    stack.append((nxt, iter(adjacency[nxt])))
                    descended = True
                    break
            if not descended:
                state[node] = DONE
                stack.pop()
                path.pop()
    return issues


def _check_connectivity(node_ids: list[str], graph: Graph) -> list[ValidationIssue]:
    """Every node must participate in at least one edge; an executable
    graph with invisible, unreachable nodes is a modeling error."""
    if not node_ids:
        return [ValidationIssue(ValidationCode.EMPTY_GRAPH, "graph has no nodes")]
    connected = {
        node_id
        for edge in graph.edges
        for node_id in (edge.source_node_id, edge.target_node_id)
    }
    return [
        ValidationIssue(
            ValidationCode.DISCONNECTED_NODE,
            "node is not connected to any other node",
            node_id=node_id,
        )
        for node_id in node_ids
        if node_id not in connected
    ]
