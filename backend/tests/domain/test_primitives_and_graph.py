"""Domain primitive and graph structural invariants."""

import pytest

from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.primitives import (
    new_id,
    semver_key,
    utcnow,
    validate_id,
    version_satisfies,
)
from akasha.errors import DomainValidationError


class TestSemver:
    def test_ordering_is_numeric_and_prerelease_first(self) -> None:
        ordered = ["0.9.9", "1.0.0-alpha", "1.0.0-beta", "1.0.0", "1.0.1", "1.10.0"]
        assert sorted(ordered, key=semver_key) == ordered
        assert semver_key("1.0.0+build.7") == semver_key("1.0.0")

    def test_constraints(self) -> None:
        assert version_satisfies("1.2.3", "*")
        assert version_satisfies("1.2.3", ">=1.2.0, <2.0.0")
        assert version_satisfies("1.2.3", "=1.2.3")
        assert not version_satisfies("2.0.0", ">=1.2.0,<2.0.0")
        assert not version_satisfies("1.0.0-rc", ">=1.0.0")

    def test_malformed_input_rejected(self) -> None:
        for version, constraint in [
            ("1.2", "*"),
            ("1.2.3", "~1.2.0"),
            ("1.2.3", ">= 1.2"),
            ("1.2.3", ""),
            ("1.2.3", ">=1.0.0 <2.0.0"),
        ]:
            with pytest.raises(DomainValidationError):
                version_satisfies(version, constraint)


class TestPrimitives:
    def test_new_id_shape(self) -> None:
        validate_id(new_id("wf"))

    def test_new_id_rejects_bad_prefix(self) -> None:
        with pytest.raises(ValueError, match="prefix"):
            new_id("NotAPrefix!")

    def test_validate_id_rejects_garbage(self) -> None:
        for bad in ["", "wf", "wf_xyz", "WF_" + "a" * 32]:
            with pytest.raises(ValueError, match="malformed"):
                validate_id(bad)

    def test_utcnow_is_timezone_aware(self) -> None:
        assert utcnow().tzinfo is not None

    def test_entity_equality_by_identity_not_fields(self) -> None:
        a = NodeInstance(id=new_id("node"), node_type="core.log", label="a")
        b = NodeInstance(id=a.id, node_type="core.log", label="different")
        c = NodeInstance.create(node_type="core.log", label="a")
        assert a == b
        assert hash(a) == hash(b)
        assert a != c


class TestGraph:
    def _node(self) -> NodeInstance:
        return NodeInstance.create(node_type="core.log")

    def test_add_and_remove_node(self) -> None:
        g = Graph()
        n = self._node()
        g.add_node(n)
        g.remove_node(n.id)
        assert g.nodes == {}

    def test_duplicate_node_rejected(self) -> None:
        g = Graph()
        n = self._node()
        g.add_node(n)
        with pytest.raises(DomainValidationError, match="duplicate"):
            g.add_node(n)

    def test_remove_unknown_node_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="unknown node"):
            Graph().remove_node(new_id("node"))

    def test_valid_edge(self) -> None:
        g = Graph()
        a, b = self._node(), self._node()
        g.add_node(a)
        g.add_node(b)
        edge = Edge(
            source_node_id=a.id,
            source_port="out",
            target_node_id=b.id,
            target_port="in",
        )
        g.add_edge(edge)
        assert g.edges == [edge]

    def test_edge_to_unknown_node_rejected(self) -> None:
        g = Graph()
        g.add_node(self._node())
        edge = Edge(
            source_node_id=next(iter(g.nodes)),
            source_port="out",
            target_node_id=new_id("node"),
            target_port="in",
        )
        with pytest.raises(DomainValidationError, match="unknown node"):
            g.add_edge(edge)

    def test_self_edge_rejected(self) -> None:
        n = self._node()
        with pytest.raises(DomainValidationError, match="self"):
            Edge(
                source_node_id=n.id,
                source_port="o",
                target_node_id=n.id,
                target_port="i",
            )

    def test_empty_port_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="ports"):
            Edge(
                source_node_id=new_id("node"),
                source_port="",
                target_node_id=new_id("node"),
                target_port="in",
            )

    def test_duplicate_edge_rejected(self) -> None:
        g = Graph()
        a, b = self._node(), self._node()
        g.add_node(a)
        g.add_node(b)
        edge = Edge(
            source_node_id=a.id,
            source_port="out",
            target_node_id=b.id,
            target_port="in",
        )
        g.add_edge(edge)
        with pytest.raises(DomainValidationError, match="duplicate"):
            g.add_edge(edge)

    def test_remove_node_removes_its_edges(self) -> None:
        g = Graph()
        a, b = self._node(), self._node()
        g.add_node(a)
        g.add_node(b)
        g.add_edge(
            Edge(
                source_node_id=a.id,
                source_port="out",
                target_node_id=b.id,
                target_port="in",
            )
        )
        g.remove_node(a.id)
        assert g.edges == []

    def test_node_instance_requires_type(self) -> None:
        with pytest.raises(DomainValidationError, match="node type"):
            NodeInstance.create(node_type="  ")
