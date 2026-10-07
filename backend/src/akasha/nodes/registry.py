"""Node Registry: the single place node type ids resolve to definitions and
runtime implementations (architecture §37, backend AGENTS §23).

    node type id → NodeRegistration (definition + factory) → fresh Node

Roadmap names map onto one class on purpose — three objects sharing one
dict would only add ways to disagree:

- NodeRegistry         ``register`` / ``unregister`` (lifecycle)
- NodeResolver         ``get`` / ``resolve`` (lookup + version check)
- NodeMetadataRegistry ``list_nodes`` / ``definitions`` (node library)

``definitions`` and ``catalog`` are live read-only views, so publish-time
validation (``WorkflowService``) and run-time validation
(``ExecutionRuntime``) always see the same set of node types.

One version per node type id: ``NodeInstance`` references a type id only,
so side-by-side versions would make resolution ambiguous. Workflows pin
compatible versions through ``Dependency`` constraints instead.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from akasha.domain.node import (
    Node,
    NodeDefinition,
    NodeFactory,
    check_node_contract,
)
from akasha.domain.primitives import version_satisfies
from akasha.errors import AkashaError, NodeContractError


class NodeNotFoundError(AkashaError):
    code = "NODE_NOT_FOUND"
    http_status = 404


class NodeAlreadyRegisteredError(AkashaError):
    code = "NODE_ALREADY_REGISTERED"
    http_status = 409


class NodeVersionIncompatibleError(AkashaError):
    code = "NODE_VERSION_INCOMPATIBLE"
    http_status = 409


@dataclass(frozen=True)
class NodeRegistration:
    """A registered node type; satisfies ``NodeFactory`` for the runtime."""

    definition: NodeDefinition
    factory: Callable[[], Node]

    def __call__(self) -> Node:
        return self.factory()


class NodeRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, NodeRegistration] = {}
        self._definitions: dict[str, NodeDefinition] = {}
        self.catalog: Mapping[str, NodeFactory] = MappingProxyType(self._entries)
        self.definitions: Mapping[str, NodeDefinition] = MappingProxyType(
            self._definitions
        )

    def register(self, node_cls: type[Node]) -> NodeRegistration:
        """Validate the contract and make the node type available.

        Built-in or plugin, a node is constructed without arguments; its
        dependencies arrive through the execution context (ADR 0011).
        Constructed once here, so a wiring mistake fails at startup instead
        of mid-execution.
        """
        check_node_contract(node_cls)
        definition = node_cls.definition
        try:
            node_cls()
        except TypeError as exc:
            raise NodeContractError(
                f"{node_cls.__name__} must be constructible without arguments"
            ) from exc
        if definition.node_id in self._entries:
            raise NodeAlreadyRegisteredError(
                "A node type with this id is already registered.",
                details={
                    "node_id": definition.node_id,
                    "registered_version": self._definitions[definition.node_id].version,
                },
            )
        registration = NodeRegistration(definition, node_cls)
        self._entries[definition.node_id] = registration
        self._definitions[definition.node_id] = definition
        return registration

    def unregister(self, node_id: str) -> None:
        """Remove a node type. Running executions keep the implementation
        they resolved at start; new runs and publishes no longer see it."""
        self.get(node_id)
        del self._entries[node_id]
        del self._definitions[node_id]

    def get(self, node_id: str) -> NodeRegistration:
        try:
            return self._entries[node_id]
        except KeyError:
            raise NodeNotFoundError(
                "Unknown node type.", details={"node_id": node_id}
            ) from None

    def resolve(self, node_id: str, constraint: str = "*") -> NodeRegistration:
        """Look up a node type whose version satisfies ``constraint``
        (grammar: ``akasha.domain.primitives.version_satisfies``)."""
        registration = self.get(node_id)
        version = registration.definition.version
        if not version_satisfies(version, constraint):
            raise NodeVersionIncompatibleError(
                "The registered node version does not satisfy the constraint.",
                details={
                    "node_id": node_id,
                    "version": version,
                    "constraint": constraint,
                },
            )
        return registration

    def list_nodes(self) -> list[NodeDefinition]:
        """All definitions, sorted by node type id (deterministic order for
        the node library and API listings)."""
        return [self._definitions[k] for k in sorted(self._definitions)]
