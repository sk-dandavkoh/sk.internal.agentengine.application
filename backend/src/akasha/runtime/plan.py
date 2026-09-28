"""GraphLoader, GraphValidator integration and DependencyResolver.

``load_plan`` turns a pinned ProjectVersion into an ``ExecutionPlan``: it
re-validates the snapshot against the *runtime's* node catalog (the catalog
may have changed since publish — a removed node type or a stricter config
schema must stop the run before any node executes) and precomputes the
dependency structure. The plan is immutable; all mutable state lives in the
``Execution`` aggregate.

Without control-flow nodes (Phase 09+) every edge is a hard dependency: a
node is ready exactly when every upstream node COMPLETED.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from akasha.application.projects import issue_payload
from akasha.domain.execution import Execution, NodeExecutionStatus
from akasha.domain.graph import Edge, Graph
from akasha.domain.graph_validation import ValidationResult, validate_graph
from akasha.domain.node import NO_DEFAULT, Node, NodeDefinition
from akasha.domain.project import ProjectVersion
from akasha.errors import AkashaError

# node type id (e.g. "core.llm") -> implementation. The Node Registry
# (Phase 09) will provide this mapping; until then it is injected.
NodeCatalog = Mapping[str, type[Node]]


class ExecutionGraphInvalidError(AkashaError):
    code = "EXECUTION_GRAPH_INVALID"
    http_status = 422

    def __init__(self, result: ValidationResult) -> None:
        super().__init__(
            "The pinned project version is not executable with the "
            "available node types.",
            details={"issues": [issue_payload(i) for i in result.issues]},
        )
        self.result = result


@dataclass(frozen=True)
class ExecutionPlan:
    graph: Graph
    implementations: Mapping[str, type[Node]]  # node instance id -> class
    incoming: Mapping[str, tuple[Edge, ...]]  # in graph edge order
    downstream: Mapping[str, tuple[str, ...]]  # sorted, de-duplicated

    def definition(self, node_id: str) -> NodeDefinition:
        return self.implementations[node_id].definition

    @property
    def roots(self) -> list[str]:
        return sorted(n for n, edges in self.incoming.items() if not edges)

    def ready_after(self, node_id: str, execution: Execution) -> list[str]:
        """Downstream nodes of ``node_id`` that just became runnable: still
        PENDING and every upstream node COMPLETED."""
        done = NodeExecutionStatus.COMPLETED
        return [
            target
            for target in self.downstream[node_id]
            if execution.node(target).status is NodeExecutionStatus.PENDING
            and all(
                execution.node(e.source_node_id).status is done
                for e in self.incoming[target]
            )
        ]

    def inputs_for(self, node_id: str, execution: Execution) -> dict[str, Any]:
        """Resolve input values from upstream outputs (output propagation).

        A ``multiple`` input collects one value per incoming edge in graph
        edge order (a ``multiple`` source contributes its items). Values are
        deep-copied: sibling branches share an upstream output, and one
        branch mutating it must not change what another branch sees.
        """
        definition = self.definition(node_id)
        values: dict[str, Any] = {}
        for edge in self.incoming[node_id]:
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


def load_plan(version: ProjectVersion, catalog: NodeCatalog) -> ExecutionPlan:
    graph = version.graph
    result = validate_graph(
        graph, {node_type: cls.definition for node_type, cls in catalog.items()}
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
