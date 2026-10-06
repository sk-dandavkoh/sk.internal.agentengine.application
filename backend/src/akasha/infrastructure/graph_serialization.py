"""Domain graph <-> portable dict conversion for persistence.

JSON is the documented, schema-stable serialization for graph data
(architecture §80 "internal Python object serialization must not be used").
Structure mirrors the domain model exactly; semantic validation belongs to
``graph_validation``, not to this codec. Loading fails loudly with
``DomainValidationError``/``ValueError`` on malformed or unknown enumerations
— persisting garbage must be harder than persisting a valid graph.
"""

import base64
from collections.abc import Mapping
from typing import Any

from akasha.domain.graph import Edge, Graph, NodeInstance, Position
from akasha.domain.trigger import Trigger, TriggerType
from akasha.domain.workflow import Dependency


def serialize_graph(graph: Graph) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": node.id,
                "node_type": node.node_type,
                "config": node.config,
                "label": node.label,
                "position": _position(node.position),
            }
            # Deterministic ordering: persistence must not depend on dict
            # iteration order (architecture §72).
            for node in sorted(graph.nodes.values(), key=lambda n: n.id)
        ],
        "edges": [
            {
                "source_node_id": edge.source_node_id,
                "source_port": edge.source_port,
                "target_node_id": edge.target_node_id,
                "target_port": edge.target_port,
            }
            for edge in graph.edges
        ],
    }


def deserialize_graph(data: dict[str, Any]) -> Graph:
    nodes = {
        raw["id"]: NodeInstance(
            id=raw["id"],
            node_type=raw["node_type"],
            config=dict(raw.get("config", {})),
            label=raw.get("label", ""),
            # Absent in graphs stored before positions existed.
            position=_load_position(raw.get("position")),
        )
        for raw in data.get("nodes", [])
    }
    edges = [Edge(**raw) for raw in data.get("edges", [])]
    return Graph(nodes=nodes, edges=edges)


def _position(position: Position | None) -> dict[str, float] | None:
    return None if position is None else {"x": position.x, "y": position.y}


def _load_position(data: Mapping[str, Any] | None) -> Position | None:
    return None if data is None else Position(x=float(data["x"]), y=float(data["y"]))


def serialize_trigger(trigger: Trigger) -> dict[str, Any]:
    return {
        "id": trigger.id,
        "trigger_type": trigger.trigger_type.value,
        "config": trigger.config,
        "enabled": trigger.enabled,
    }


def deserialize_trigger(data: dict[str, Any]) -> Trigger:
    return Trigger(
        id=data["id"],
        trigger_type=TriggerType(data["trigger_type"]),
        config=dict(data.get("config", {})),
        enabled=data.get("enabled", True),
    )


def serialize_dependency(dep: Dependency) -> dict[str, Any]:
    return {
        "kind": dep.kind,
        "name": dep.name,
        "version_constraint": dep.version_constraint,
    }


def deserialize_dependency(data: dict[str, Any]) -> Dependency:
    return Dependency(
        kind=data["kind"],
        name=data["name"],
        version_constraint=data.get("version_constraint", "*"),
    )


def serialize_files(files: Mapping[str, bytes]) -> dict[str, str]:
    return {
        path: base64.b64encode(files[path]).decode("ascii") for path in sorted(files)
    }


def deserialize_files(data: Mapping[str, str]) -> dict[str, bytes]:
    return {
        path: base64.b64decode(content, validate=True) for path, content in data.items()
    }
