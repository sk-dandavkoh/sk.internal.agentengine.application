"""Graph model: NodeInstance, Edge and the (draft-side mutable) Graph.

Structural integrity only: node ids unique, edges reference existing nodes.
Semantic validation (port types, cycles, required inputs) is Phase 04 and
deliberately not implemented here.
"""

from dataclasses import dataclass, field
from typing import Any

from akasha.domain.primitives import Entity, new_id, validate_id
from akasha.errors import DomainValidationError


@dataclass(eq=False)
class NodeInstance(Entity):
    """One configured occurrence of a node type inside a project graph.

    ``node_type`` references a Node Definition in the registry (Phase 03+),
    e.g. ``core.llm``. ``config`` holds node-defined settings and must never
    contain plaintext secrets — only CredentialReferences.
    """

    node_type: str
    config: dict[str, Any] = field(default_factory=dict)
    label: str = ""

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        if not self.node_type.strip():
            raise DomainValidationError("node instance requires a node type")

    @classmethod
    def create(cls, node_type: str, **kwargs: Any) -> "NodeInstance":
        return cls(id=new_id("node"), node_type=node_type, **kwargs)


@dataclass(frozen=True)
class Edge:
    """Directed connection: source node output port → target node input port."""

    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str

    def __post_init__(self) -> None:
        validate_id(self.source_node_id)
        validate_id(self.target_node_id)
        if not self.source_port or not self.target_port:
            raise DomainValidationError("edge ports must be non-empty")
        if self.source_node_id == self.target_node_id:
            raise DomainValidationError("self-edges are not allowed")


@dataclass
class Graph:
    """Nodes plus edges. Mutable: drafts evolve until published.

    Snapshot isolation for publishing is done by ProjectDraft.publish(), which
    deep-copies this graph into the immutable ProjectVersion.
    """

    nodes: dict[str, NodeInstance] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: NodeInstance) -> None:
        if node.id in self.nodes:
            raise DomainValidationError(f"duplicate node id: {node.id}")
        self.nodes[node.id] = node

    def remove_node(self, node_id: str) -> None:
        if node_id not in self.nodes:
            raise DomainValidationError(f"unknown node: {node_id}")
        del self.nodes[node_id]
        self.edges = [
            e for e in self.edges if node_id not in (e.source_node_id, e.target_node_id)
        ]

    def add_edge(self, edge: Edge) -> None:
        for node_id in (edge.source_node_id, edge.target_node_id):
            if node_id not in self.nodes:
                raise DomainValidationError(f"edge references unknown node: {node_id}")
        if edge in self.edges:
            raise DomainValidationError("duplicate edge")
        self.edges.append(edge)

    def remove_edge(self, edge: Edge) -> None:
        if edge not in self.edges:
            raise DomainValidationError("unknown edge")
        self.edges.remove(edge)

    def node(self, node_id: str) -> NodeInstance:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise DomainValidationError(f"unknown node: {node_id}") from None
