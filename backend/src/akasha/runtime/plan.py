"""GraphLoader, GraphValidator integration and DependencyResolver.

``load_plan`` turns a pinned WorkflowVersion into an ``ExecutionPlan``: it
re-validates the snapshot (graph and declared node-version dependencies)
against the *runtime's* node catalog (the catalog may have changed since
publish — a removed node type, a stricter config schema or an incompatible
node version must stop the run before any node executes) and precomputes the
dependency structure. The plan is immutable; all mutable state lives in the
``Execution`` aggregate.

Readiness is generic — no node type is special-cased. An edge is *live* when
its source COMPLETED and produced the edge's port; it is *dead* when the
source was SKIPPED or (control-flow nodes only) omitted that port. Once
every upstream node is decided, a node runs if at least one incoming edge is
live and every required input is fed by a live edge; otherwise it is
SKIPPED, which in turn kills its outgoing edges. So an ``if`` branch that
was not taken is skipped transitively, while a node joining both branches
(e.g. ``core.merge``) runs with the values of the live one.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from akasha.application.workflows import issue_payload
from akasha.domain.execution import Execution, NodeExecutionStatus
from akasha.domain.graph import Edge, Graph
from akasha.domain.graph_validation import ValidationResult
from akasha.domain.node import NO_DEFAULT, NodeDefinition, NodeFactory
from akasha.domain.workflow import WorkflowVersion
from akasha.errors import AkashaError

# node type id (e.g. "core.http") -> implementation; supplied by the
# NodeRegistry (``NodeRegistry.catalog``) or injected by tests.
NodeCatalog = Mapping[str, NodeFactory]

_DECIDED = frozenset({NodeExecutionStatus.COMPLETED, NodeExecutionStatus.SKIPPED})


class ExecutionGraphInvalidError(AkashaError):
    code = "EXECUTION_GRAPH_INVALID"
    http_status = 422

    def __init__(self, result: ValidationResult) -> None:
        super().__init__(
            "The pinned workflow version is not executable with the "
            "available node types.",
            details={"issues": [issue_payload(i) for i in result.issues]},
        )
        self.result = result


@dataclass(frozen=True)
class ExecutionPlan:
    graph: Graph
    implementations: Mapping[str, NodeFactory]  # node instance id -> factory
    incoming: Mapping[str, tuple[Edge, ...]]  # in graph edge order
    downstream: Mapping[str, tuple[str, ...]]  # sorted, de-duplicated

    def definition(self, node_id: str) -> NodeDefinition:
        return self.implementations[node_id].definition

    @property
    def roots(self) -> list[str]:
        return sorted(n for n, edges in self.incoming.items() if not edges)

    @property
    def node_versions(self) -> dict[str, str]:
        """Resolved version per node type used by the graph (sorted keys)."""
        versions = {
            self.graph.nodes[n].node_type: f.definition.version
            for n, f in self.implementations.items()
        }
        return dict(sorted(versions.items()))

    def readiness(
        self, node_id: str, execution: Execution
    ) -> NodeExecutionStatus | None:
        """READY or SKIPPED once every upstream node is decided (COMPLETED
        or SKIPPED); ``None`` while some upstream node is still open."""
        edges = self.incoming[node_id]
        if any(execution.node(e.source_node_id).status not in _DECIDED for e in edges):
            return None
        fed = {e.target_port for e in edges if _live(e, execution)}
        required = {p.name for p in self.definition(node_id).inputs if p.required}
        if fed and required <= fed:
            return NodeExecutionStatus.READY
        return NodeExecutionStatus.SKIPPED

    def inputs_for(self, node_id: str, execution: Execution) -> dict[str, Any]:
        """Resolve input values from upstream outputs (output propagation).

        Only live edges contribute. A ``multiple`` input collects one value
        per live incoming edge in graph edge order (a ``multiple`` source
        contributes its items). Values are deep-copied: sibling branches
        share an upstream output, and one branch mutating it must not
        change what another branch sees.
        """
        definition = self.definition(node_id)
        values: dict[str, Any] = {}
        for edge in self.incoming[node_id]:
            if not _live(edge, execution):
                continue
            outputs = execution.node(edge.source_node_id).outputs or {}
            value = deepcopy(outputs[edge.source_port])
            if not definition.input(edge.target_port).multiple:
                values[edge.target_port] = value
                continue
            bucket = values.setdefault(edge.target_port, [])
            source = self.definition(edge.source_node_id).output(edge.source_port)
            if source.multiple and isinstance(value, list):
                bucket.extend(value)
            else:
                bucket.append(value)
        for port in definition.inputs:
            if port.name not in values and port.default is not NO_DEFAULT:
                values[port.name] = deepcopy(port.default)
        return values


def _live(edge: Edge, execution: Execution) -> bool:
    source = execution.node(edge.source_node_id)
    return source.status is NodeExecutionStatus.COMPLETED and edge.source_port in (
        source.outputs or {}
    )


def load_plan(
    version: WorkflowVersion,
    catalog: NodeCatalog,
    plugins: Mapping[str, str] = MappingProxyType({}),
) -> ExecutionPlan:
    """``plugins`` (enabled plugin id → version): the snapshot's pinned
    plugin versions must be exactly these, or nothing runs."""
    graph = version.graph
    result = version.validate(
        {node_type: factory.definition for node_type, factory in catalog.items()},
        plugins,
    )
    if not result.ok:
        raise ExecutionGraphInvalidError(result)
    incoming: dict[str, list[Edge]] = {n: [] for n in graph.nodes}
    downstream: dict[str, set[str]] = {n: set() for n in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target_node_id].append(edge)
        downstream[edge.source_node_id].add(edge.target_node_id)
    return ExecutionPlan(
        graph=graph,
        implementations={n: catalog[i.node_type] for n, i in graph.nodes.items()},
        incoming={n: tuple(edges) for n, edges in incoming.items()},
        downstream={n: tuple(sorted(t)) for n, t in downstream.items()},
    )
