"""NodeRegistry: registration lifecycle, lookup, metadata, versions."""

from typing import cast

import pytest

from akasha.domain.node import (
    Node,
    NodeDefinition,
    NodeExecutionContext,
    NodeMetadata,
    NodeResult,
)
from akasha.errors import DomainValidationError, NodeContractError
from akasha.nodes.registry import (
    NodeAlreadyRegisteredError,
    NodeNotFoundError,
    NodeRegistry,
    NodeVersionIncompatibleError,
)


def _node(node_id: str, version: str = "1.2.0") -> type[Node]:
    class Impl(Node):
        definition = NodeDefinition(
            metadata=NodeMetadata(
                node_id=node_id, name=node_id, description="D", version=version
            )
        )

        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult({})

    return Impl


class Configured(Node):
    """A node that wants a constructor argument (dependencies belong in the
    execution context instead)."""

    definition = _node("test.configured").definition

    def __init__(self, setting: str) -> None:
        self.setting = setting

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({})


def test_register_then_get_and_resolve() -> None:
    registry = NodeRegistry()
    impl = _node("test.a")
    registration = registry.register(impl)

    assert registry.get("test.a") is registration
    assert registration.definition is impl.definition
    assert isinstance(registration(), impl)
    assert registration() is not registration()  # fresh instance per call
    assert registry.resolve("test.a", ">=1.0.0,<2.0.0") is registration


def test_duplicate_registration_is_rejected_and_keeps_the_original() -> None:
    registry = NodeRegistry()
    first = registry.register(_node("test.a", "1.0.0"))
    with pytest.raises(NodeAlreadyRegisteredError) as exc:
        registry.register(_node("test.a", "2.0.0"))
    assert exc.value.details["registered_version"] == "1.0.0"
    assert registry.get("test.a") is first


def test_unknown_node_type() -> None:
    registry = NodeRegistry()
    for call in (registry.get, registry.resolve, registry.unregister):
        with pytest.raises(NodeNotFoundError) as exc:
            call("test.missing")
        assert exc.value.code == "NODE_NOT_FOUND"
        assert exc.value.details == {"node_id": "test.missing"}


def test_unregister_removes_from_every_view() -> None:
    registry = NodeRegistry()
    registry.register(_node("test.a"))
    registry.unregister("test.a")
    assert "test.a" not in registry.definitions
    assert "test.a" not in registry.catalog
    assert registry.list_nodes() == []
    registry.register(_node("test.a", "3.0.0"))  # id is free again
    assert registry.get("test.a").definition.version == "3.0.0"


def test_views_are_live_and_read_only() -> None:
    registry = NodeRegistry()
    definitions, catalog = registry.definitions, registry.catalog
    registry.register(_node("test.a"))
    assert set(definitions) == set(catalog) == {"test.a"}
    with pytest.raises(TypeError):
        # cast: the static type already forbids this; prove the runtime does.
        cast("dict[str, NodeDefinition]", definitions)["test.b"] = _node(
            "test.b"
        ).definition


def test_metadata_listing_is_sorted() -> None:
    registry = NodeRegistry()
    for node_id in ("test.c", "test.a", "test.b"):
        registry.register(_node(node_id))
    assert [d.node_id for d in registry.list_nodes()] == ["test.a", "test.b", "test.c"]


def test_version_compatibility() -> None:
    registry = NodeRegistry()
    registry.register(_node("test.a", "1.2.0"))
    for ok in ("*", "1.2.0", ">=1.2.0", "<2.0.0"):
        registry.resolve("test.a", ok)
    for incompatible in (">=2.0.0", "<1.2.0", "1.2.1"):
        with pytest.raises(NodeVersionIncompatibleError) as exc:
            registry.resolve("test.a", incompatible)
        assert exc.value.details["version"] == "1.2.0"
    with pytest.raises(DomainValidationError):
        registry.resolve("test.a", "latest")


def test_contract_is_checked_at_registration() -> None:
    class NoDefinition(Node):
        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult({})

    registry = NodeRegistry()
    with pytest.raises(NodeContractError):
        registry.register(NoDefinition)
    assert registry.list_nodes() == []


def test_nodes_must_be_constructible_without_arguments() -> None:
    registry = NodeRegistry()
    with pytest.raises(NodeContractError, match="without arguments"):
        registry.register(Configured)
    assert "test.configured" not in registry.definitions
