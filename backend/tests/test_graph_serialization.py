"""Codec round-trip tests for graph/trigger/dependency persistence.

JSON is the portability contract (architecture §80); these tests pin the
exact document shape so a silent serialization change cannot corrupt
persisted drafts or versions.
"""

import json

import pytest

from akasha.domain.graph import Edge, Graph, NodeInstance, Position
from akasha.domain.trigger import Trigger, TriggerType
from akasha.domain.workflow import Dependency
from akasha.errors import DomainValidationError
from akasha.infrastructure.graph_serialization import (
    deserialize_dependency,
    deserialize_graph,
    deserialize_trigger,
    serialize_dependency,
    serialize_graph,
    serialize_trigger,
)


def _graph() -> Graph:
    a = NodeInstance.create(
        "core.input", config={"q": "ü"}, label="input", position=Position(1.5, -3)
    )
    b = NodeInstance.create("core.llm", config={"model": "x", "opts": {"n": 1.5}})
    graph = Graph(nodes={a.id: a, b.id: b})
    graph.add_edge(
        Edge(
            source_node_id=a.id,
            source_port="out",
            target_node_id=b.id,
            target_port="prompt",
        )
    )
    return graph


def test_graph_roundtrip_is_exact_and_json_serializable() -> None:
    graph = _graph()
    document = serialize_graph(graph)
    assert json.loads(json.dumps(document)) == document  # truly JSON-safe
    restored = deserialize_graph(json.loads(json.dumps(document)))
    assert restored.nodes == graph.nodes
    assert restored.edges == graph.edges
    for node_id, node in graph.nodes.items():
        assert restored.nodes[node_id].label == node.label
        assert restored.nodes[node_id].config == node.config
        assert restored.nodes[node_id].position == node.position


def test_graph_without_positions_loads_unplaced_nodes() -> None:
    # Graphs persisted before node positions existed carry no "position".
    document = serialize_graph(_graph())
    for raw in document["nodes"]:
        raw.pop("position")
    assert all(n.position is None for n in deserialize_graph(document).nodes.values())


def test_graph_rejects_non_finite_positions() -> None:
    document = serialize_graph(_graph())
    document["nodes"][0]["position"] = {"x": float("nan"), "y": 0}
    with pytest.raises(DomainValidationError, match="finite"):
        deserialize_graph(document)


def test_graph_node_order_is_deterministic() -> None:
    graph = _graph()
    ids = [n["id"] for n in serialize_graph(graph)["nodes"]]
    assert ids == sorted(ids)


def test_graph_rejects_malformed_documents() -> None:
    with pytest.raises(KeyError):  # required node fields are mandatory
        deserialize_graph({"nodes": [{"id": "node_" + "0" * 32}]})
    with pytest.raises(ValueError, match="malformed domain id"):
        deserialize_graph(
            {"nodes": [{"id": "not-a-domain-id", "node_type": "core.input"}]}
        )
    with pytest.raises(DomainValidationError):
        deserialize_graph(
            {
                "nodes": [],
                "edges": [
                    {
                        "source_node_id": "node_" + "0" * 32,
                        "source_port": "out",
                        "target_node_id": "node_" + "0" * 32,
                        "target_port": "in",  # self-edge is a domain violation
                    }
                ],
            }
        )


def test_trigger_roundtrip() -> None:
    trigger = Trigger.create(TriggerType.CRON, {"cron": "* * * * *"})
    restored = deserialize_trigger(json.loads(json.dumps(serialize_trigger(trigger))))
    assert restored.id == trigger.id
    assert restored.trigger_type is TriggerType.CRON
    assert restored.config == trigger.config
    assert restored.enabled is True


def test_trigger_validation_still_applies_on_load() -> None:
    with pytest.raises(DomainValidationError):
        deserialize_trigger(
            {
                "id": "trig_" + "0" * 32,
                "trigger_type": "cron",
                "config": {},  # cron trigger without a cron expression
                "enabled": True,
            }
        )


def test_dependency_roundtrip() -> None:
    dep = Dependency(kind="plugin", name="ext.search", version_constraint="^2.0")
    restored = deserialize_dependency(json.loads(json.dumps(serialize_dependency(dep))))
    assert restored == dep
