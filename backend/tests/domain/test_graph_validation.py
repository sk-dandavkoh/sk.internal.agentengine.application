"""Project Graph validation (roadmap Phase 04).

Covers the acceptance matrix: valid graphs (linear/branching/merge),
missing/duplicate nodes and edges, unknown ports, incompatible ports,
missing required inputs, invalid config, invalid edges, cycles,
disconnected/malformed graphs — plus determinism and property-based
robustness of the validator itself.
"""

import string
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.graph_validation import (
    ValidationCode,
    ports_compatible,
    resolve_ports,
    validate_graph,
)
from akasha.domain.node import (
    NodeConfigSchema,
    NodeDefinition,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    PortType,
)
from akasha.domain.primitives import new_id
from akasha.domain.project import ProjectDraft
from akasha.errors import DomainValidationError

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def make_definition(
    node_type: str,
    *,
    inputs: tuple[NodeInput, ...] = (),
    outputs: tuple[NodeOutput, ...] = (),
    schema: dict[str, Any] | None = None,
) -> NodeDefinition:
    return NodeDefinition(
        metadata=NodeMetadata(
            node_id=node_type,
            name=node_type.replace(".", " ").title(),
            description=f"Test node {node_type}.",
            version="1.0.0",
        ),
        inputs=inputs,
        outputs=outputs,
        config_schema=(
            NodeConfigSchema(schema) if schema is not None else NodeConfigSchema()
        ),
    )


SOURCE_DEF = make_definition(
    "test.source", outputs=(NodeOutput(name="out", port_type=PortType.STRING),)
)
SINK_DEF = make_definition(
    "test.sink", inputs=(NodeInput(name="inp", port_type=PortType.STRING),)
)
PASS_DEF = make_definition(
    "test.passthrough",
    inputs=(NodeInput(name="inp", port_type=PortType.STRING),),
    outputs=(NodeOutput(name="out", port_type=PortType.STRING),),
)
NUMBER_SINK_DEF = make_definition(
    "test.number_sink",
    inputs=(NodeInput(name="inp", port_type=PortType.NUMBER),),
)
CONFIGURED_DEF = make_definition(
    "test.configured",
    inputs=(NodeInput(name="inp", port_type=PortType.STRING),),
    schema={
        "type": "object",
        "properties": {"retries": {"type": "integer"}},
        "required": ["retries"],
        "additionalProperties": False,
    },
)
CATALOG = {
    d.node_id: d
    for d in (SOURCE_DEF, SINK_DEF, PASS_DEF, NUMBER_SINK_DEF, CONFIGURED_DEF)
}


def add(
    graph: Graph, node_type: str, config: dict[str, Any] | None = None
) -> NodeInstance:
    node = NodeInstance.create(node_type=node_type, config=config or {})
    graph.add_node(node)
    return node


def connect(graph: Graph, a: NodeInstance, b: NodeInstance) -> Edge:
    edge = Edge(
        source_node_id=a.id, source_port="out", target_node_id=b.id, target_port="inp"
    )
    graph.add_edge(edge)
    return edge


def linear_graph() -> Graph:
    g = Graph()
    a, b = add(g, "test.source"), add(g, "test.sink")
    connect(g, a, b)
    return g


# ---------------------------------------------------------------------------
# Valid graphs
# ---------------------------------------------------------------------------


class TestValidGraphs:
    def test_linear_graph_is_valid(self) -> None:
        assert validate_graph(linear_graph(), CATALOG).ok

    def test_branching_graph_is_valid(self) -> None:
        g = Graph()
        a, b, c = add(g, "test.source"), add(g, "test.sink"), add(g, "test.sink")
        connect(g, a, b)
        connect(g, a, c)
        assert validate_graph(g, CATALOG).ok

    def test_merge_graph_is_valid(self) -> None:
        # A merge node with two optional inputs fed by two sources.
        merge_def = make_definition(
            "test.merge",
            inputs=(
                NodeInput(name="left", port_type=PortType.STRING, required=False),
                NodeInput(name="right", port_type=PortType.STRING, required=False),
            ),
        )
        catalog = {**CATALOG, "test.merge": merge_def}
        g = Graph()
        a, b, m = add(g, "test.source"), add(g, "test.source"), add(g, "test.merge")
        g.add_edge(Edge(a.id, "out", m.id, "left"))
        g.add_edge(Edge(b.id, "out", m.id, "right"))
        assert validate_graph(g, catalog).ok

    def test_non_required_input_may_stay_unconnected(self) -> None:
        defn = make_definition(
            "test.defaults",
            inputs=(
                NodeInput(
                    name="query",
                    port_type=PortType.STRING,
                    required=False,
                    default="*",
                ),
            ),
            outputs=(NodeOutput("out", PortType.STRING),),
        )
        catalog = {**CATALOG, "test.defaults": defn}
        g = Graph()
        a, b = add(g, "test.defaults"), add(g, "test.sink")
        connect(g, a, b)  # defaults.query stays unconnected: allowed
        assert validate_graph(g, catalog).ok


# ---------------------------------------------------------------------------
# Missing / duplicate nodes and edges
# ---------------------------------------------------------------------------


class TestNodesAndEdges:
    def test_empty_graph_is_invalid(self) -> None:
        result = validate_graph(Graph(), CATALOG)
        assert result.by_code(ValidationCode.EMPTY_GRAPH)

    def test_unknown_node_type(self) -> None:
        g = Graph()
        n = NodeInstance.create(node_type="ghost.node")
        g.add_node(n)
        result = validate_graph(g, CATALOG)
        issue = result.by_code(ValidationCode.UNKNOWN_NODE_TYPE)[0]
        assert issue.node_id == n.id
        assert "ghost.node" in issue.message

    def test_edge_to_missing_node(self) -> None:
        g = linear_graph()
        ghost = NodeInstance.create(node_type="test.sink")  # never added
        edge = Edge(
            source_node_id=next(iter(g.nodes)),
            source_port="out",
            target_node_id=ghost.id,
            target_port="inp",
        )
        g.edges.append(edge)  # bypass add_edge's structural check
        result = validate_graph(g, CATALOG)
        assert result.by_code(ValidationCode.EDGE_UNKNOWN_NODE)

    def test_duplicate_edge_detected_in_malformed_graph(self) -> None:
        g = linear_graph()
        g.edges.append(g.edges[0])
        result = validate_graph(g, CATALOG)
        assert result.by_code(ValidationCode.DUPLICATE_EDGE)

    def test_unknown_source_port(self) -> None:
        g = Graph()
        a, b = add(g, "test.source"), add(g, "test.sink")
        g.edges.append(Edge(a.id, "nope", b.id, "inp"))
        result = validate_graph(g, CATALOG)
        issue = result.by_code(ValidationCode.UNKNOWN_PORT)[0]
        assert issue.port == "nope"
        assert issue.node_id == a.id

    def test_unknown_target_port(self) -> None:
        g = Graph()
        a, b = add(g, "test.source"), add(g, "test.sink")
        g.edges.append(Edge(a.id, "out", b.id, "nope"))
        result = validate_graph(g, CATALOG)
        issue = result.by_code(ValidationCode.UNKNOWN_PORT)[0]
        assert issue.port == "nope"
        assert issue.node_id == b.id

    def test_two_edges_into_one_input_rejected(self) -> None:
        g = Graph()
        a, b, c = add(g, "test.source"), add(g, "test.source"), add(g, "test.sink")
        connect(g, a, c)
        connect(g, b, c)
        result = validate_graph(g, CATALOG)
        issues = result.by_code(ValidationCode.INVALID_PORT_CONNECTION)
        assert any("more than one" in i.message for i in issues)

    def test_multiple_input_accepts_many_edges(self) -> None:
        multi_def = make_definition(
            "test.collector",
            inputs=(
                NodeInput("items", PortType.STRING, multiple=True, required=False),
            ),
        )
        catalog = {**CATALOG, "test.collector": multi_def}
        g = Graph()
        a, b, c = add(g, "test.source"), add(g, "test.source"), add(g, "test.collector")
        g.add_edge(Edge(a.id, "out", c.id, "items"))
        g.add_edge(Edge(b.id, "out", c.id, "items"))
        assert validate_graph(g, catalog).ok

    def test_node_lookup(self) -> None:
        g = linear_graph()
        node_id = next(iter(g.nodes))
        assert g.node(node_id) is g.nodes[node_id]
        with pytest.raises(DomainValidationError, match="unknown node"):
            g.node(new_id("node"))

    def test_remove_edge(self) -> None:
        g = linear_graph()
        edge = g.edges[0]
        g.remove_edge(edge)
        assert g.edges == []
        with pytest.raises(DomainValidationError, match="unknown edge"):
            g.remove_edge(edge)


# ---------------------------------------------------------------------------
# Ports and types
# ---------------------------------------------------------------------------


class TestPortCompatibility:
    def test_incompatible_types_rejected(self) -> None:
        g = Graph()
        a, b = add(g, "test.source"), add(g, "test.number_sink")
        connect(g, a, b)
        result = validate_graph(g, CATALOG)
        issue = result.by_code(ValidationCode.INVALID_PORT_CONNECTION)[0]
        assert issue.node_id == b.id
        assert issue.port == "inp"
        assert "string" in issue.message and "number" in issue.message

    def test_json_connects_to_anything(self) -> None:
        json_out = make_definition(
            "test.json_out", outputs=(NodeOutput("out", PortType.JSON),)
        )
        json_in = make_definition(
            "test.json_in", inputs=(NodeInput("inp", PortType.JSON),)
        )
        catalog = {**CATALOG, "test.json_out": json_out, "test.json_in": json_in}
        g = Graph()
        a, b = add(g, "test.json_out"), add(g, "test.number_sink")
        c = add(g, "test.json_in")
        connect(g, a, b)  # JSON -> NUMBER: allowed (escape hatch)
        g.edges.append(Edge(a.id, "out", c.id, "inp"))  # JSON -> JSON
        assert validate_graph(g, catalog).ok

    def test_multiple_shape_mismatch_rejected(self) -> None:
        out_list = make_definition(
            "test.list_out",
            outputs=(NodeOutput("out", PortType.STRING, multiple=True),),
        )
        catalog = {**CATALOG, "test.list_out": out_list}
        g = Graph()
        a, b = add(g, "test.list_out"), add(g, "test.sink")
        connect(g, a, b)
        assert validate_graph(g, catalog).by_code(
            ValidationCode.INVALID_PORT_CONNECTION
        )

    def test_ports_compatible_directly(self) -> None:
        out = NodeOutput("o", PortType.STRING)
        assert ports_compatible(out, NodeInput("i", PortType.STRING))
        assert not ports_compatible(out, NodeInput("i", PortType.NUMBER))
        assert ports_compatible(out, NodeInput("i", PortType.JSON))
        assert not ports_compatible(
            NodeOutput("o", PortType.STRING, multiple=True),
            NodeInput("i", PortType.STRING),
        )


class TestResolvePorts:
    def test_resolves_declared_ports(self) -> None:
        g = linear_graph()
        out, inp = resolve_ports(g.edges[0], g, CATALOG)
        assert out.name == "out" and inp.name == "inp"

    def test_unknown_node_raises(self) -> None:
        g = linear_graph()
        bad = Edge(g.edges[0].source_node_id, "out", new_id("node"), "inp")
        with pytest.raises(DomainValidationError):
            resolve_ports(bad, g, CATALOG)

    def test_unknown_type_raises(self) -> None:
        g = Graph()
        a = add(g, "ghost.node")
        b = add(g, "test.sink")
        edge = Edge(a.id, "out", b.id, "inp")
        with pytest.raises(DomainValidationError):
            resolve_ports(edge, g, CATALOG)

    def test_unknown_port_raises(self) -> None:
        g = linear_graph()
        bad = Edge(g.edges[0].source_node_id, "nope", g.edges[0].target_node_id, "inp")
        with pytest.raises(DomainValidationError):
            resolve_ports(bad, g, CATALOG)


# ---------------------------------------------------------------------------
# Required inputs and configuration
# ---------------------------------------------------------------------------


class TestInputsAndConfig:
    def test_missing_required_input(self) -> None:
        g = Graph()
        a, b = add(g, "test.source"), add(g, "test.number_sink")
        connect(g, a, b)  # number_sink.inp stays fed; add an unfed passthrough
        c = add(g, "test.passthrough")
        g.add_edge(Edge(a.id, "out", c.id, "inp"))
        g.add_edge(Edge(c.id, "out", b.id, "inp"))  # b becomes double-fed
        d = add(g, "test.passthrough")  # never fed
        result = validate_graph(g, CATALOG)
        issues = result.by_code(ValidationCode.MISSING_REQUIRED_INPUT)
        assert [i.node_id for i in issues] == [d.id]
        assert result.by_code(ValidationCode.INVALID_PORT_CONNECTION)

    def test_invalid_config_missing_key(self) -> None:
        g = Graph()
        a, b = add(g, "test.source"), add(g, "test.configured", config={"x": 1})
        connect(g, a, b)
        result = validate_graph(g, CATALOG)
        issues = result.by_code(ValidationCode.INVALID_CONFIG)
        assert issues and issues[0].node_id == b.id
        assert "retries" in issues[0].message

    def test_invalid_config_wrong_type(self) -> None:
        g = Graph()
        a, b = (
            add(g, "test.source"),
            add(g, "test.configured", config={"retries": "many"}),
        )
        connect(g, a, b)
        result = validate_graph(g, CATALOG)
        assert result.by_code(ValidationCode.INVALID_CONFIG)

    def test_valid_config_accepted(self) -> None:
        g = Graph()
        a, b = add(g, "test.source"), add(g, "test.configured", config={"retries": 3})
        connect(g, a, b)
        assert validate_graph(g, CATALOG).ok


# ---------------------------------------------------------------------------
# Cycles and connectivity
# ---------------------------------------------------------------------------


class TestCyclesAndConnectivity:
    def test_simple_cycle_detected(self) -> None:
        g = Graph()
        a, b = add(g, "test.passthrough"), add(g, "test.passthrough")
        connect(g, a, b)
        connect(g, b, a)
        result = validate_graph(g, CATALOG)
        assert result.by_code(ValidationCode.CYCLE_DETECTED)

    def test_longer_cycle_detected(self) -> None:
        g = Graph()
        nodes = [add(g, "test.passthrough") for _ in range(3)]
        for i in range(3):
            connect(g, nodes[i], nodes[(i + 1) % 3])
        result = validate_graph(g, CATALOG)
        assert len(result.by_code(ValidationCode.CYCLE_DETECTED)) == 1

    def test_branch_out_and_back_is_a_cycle(self) -> None:
        g = Graph()
        a = add(g, "test.source")
        b, c = add(g, "test.passthrough"), add(g, "test.passthrough")
        connect(g, a, b)
        connect(g, b, c)
        connect(g, c, b)
        assert validate_graph(g, CATALOG).by_code(ValidationCode.CYCLE_DETECTED)

    def test_diamond_is_not_a_cycle(self) -> None:
        # A(passthrough) -> B, A -> C, B -> D, C -> D with source feeding A.
        merge_def = make_definition(
            "test.merge2",
            inputs=(
                NodeInput("in1", PortType.STRING, required=False),
                NodeInput("in2", PortType.STRING, required=False),
            ),
        )
        catalog = {**CATALOG, "test.merge2": merge_def}
        g = Graph()
        s = add(g, "test.source")
        a = add(g, "test.passthrough")
        b, c = add(g, "test.passthrough"), add(g, "test.passthrough")
        d = add(g, "test.merge2")
        connect(g, s, a)
        connect(g, a, b)
        connect(g, a, c)
        g.add_edge(Edge(b.id, "out", d.id, "in1"))
        g.add_edge(Edge(c.id, "out", d.id, "in2"))
        result = validate_graph(g, catalog)
        assert not result.by_code(ValidationCode.CYCLE_DETECTED)
        assert result.ok

    def test_disconnected_node_detected(self) -> None:
        g = linear_graph()
        orphan = add(g, "test.sink")
        result = validate_graph(g, CATALOG)
        issue = result.by_code(ValidationCode.DISCONNECTED_NODE)[0]
        assert issue.node_id == orphan.id


# ---------------------------------------------------------------------------
# Malformed graphs must not crash the validator
# ---------------------------------------------------------------------------


class TestMalformedGraphs:
    def test_all_problems_collected_at_once(self) -> None:
        # Ghost-type node + dangling edge + orphan + cycle.
        g = Graph()
        ghost = add(g, "ghost.node")
        a, b = add(g, "test.passthrough"), add(g, "test.passthrough")
        connect(g, a, b)
        connect(g, b, a)  # cycle
        g.edges.append(Edge(a.id, "out", new_id("node"), "inp"))
        orphan = add(g, "test.sink")  # disconnected AND its input unfed
        result = validate_graph(g, CATALOG)
        codes = {i.code for i in result.issues}
        assert {
            ValidationCode.UNKNOWN_NODE_TYPE,
            ValidationCode.EDGE_UNKNOWN_NODE,
            ValidationCode.CYCLE_DETECTED,
            ValidationCode.DISCONNECTED_NODE,
            ValidationCode.MISSING_REQUIRED_INPUT,
        } <= codes
        assert ghost.id in {i.node_id for i in result.issues}
        assert orphan.id in {i.node_id for i in result.issues}

    def test_non_dict_config_reported_not_crashing(self) -> None:
        g = linear_graph()
        sink = next(n for n in g.nodes.values() if n.node_type == "test.sink")
        # malformed on purpose:
        sink.config = "not-a-dict"  # type: ignore[assignment]
        result = validate_graph(g, CATALOG)
        assert result.by_code(ValidationCode.INVALID_CONFIG)

    def test_graph_with_only_dangling_edges(self) -> None:
        g = Graph()
        g.edges.append(Edge(new_id("node"), "o", new_id("node"), "i"))
        result = validate_graph(g, CATALOG)
        assert result.by_code(ValidationCode.EDGE_UNKNOWN_NODE)
        assert result.by_code(ValidationCode.EMPTY_GRAPH)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_graph_same_issues_in_same_order(self) -> None:
        g = Graph()
        a, b = add(g, "test.passthrough"), add(g, "test.passthrough")
        connect(g, a, b)
        connect(g, b, a)
        add(g, "ghost.node")
        first = validate_graph(g, CATALOG)
        # Rebuild an equal graph from scratch: identical result.
        g2 = Graph()
        a2, b2 = add(g2, "test.passthrough"), add(g2, "test.passthrough")
        connect(g2, a2, b2)
        connect(g2, b2, a2)
        add(g2, "ghost.node")
        second = validate_graph(g2, CATALOG)
        # Ids differ between builds; codes and their order must not.
        assert [i.code for i in first.issues] == [i.code for i in second.issues]
        # Validate twice over the same object: identical.
        assert validate_graph(g, CATALOG) == first


# ---------------------------------------------------------------------------
# Draft integration + property-based robustness
# ---------------------------------------------------------------------------


class TestDraftIntegration:
    def test_draft_validate_delegates(self) -> None:
        draft = ProjectDraft(graph=linear_graph())
        assert draft.validate(CATALOG).ok
        draft.graph.add_node(NodeInstance.create(node_type="test.sink"))
        result = draft.validate(CATALOG)
        assert result.by_code(ValidationCode.DISCONNECTED_NODE)
        assert result.by_code(ValidationCode.MISSING_REQUIRED_INPUT)


_PORT_TYPES = list(PortType)
_node_type = st.builds(
    lambda s: f"test.{s}",
    st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8),
).filter(lambda t: len(t) > 5)


def _build_random_graph(types: list[str], edge_specs: list[tuple[int, int]]) -> Graph:
    """Graph from generated parts; defs auto-declare ports o0/i0 as JSON."""
    g = Graph()
    nodes = [NodeInstance.create(node_type=t) for t in types]
    for n in nodes:
        g.add_node(n)
    for src, tgt in edge_specs:
        if src != tgt and src < len(nodes) and tgt < len(nodes):
            try:
                g.add_edge(Edge(nodes[src].id, "o0", nodes[tgt].id, "i0"))
            except DomainValidationError:
                pass
    return g


_random_defs = st.lists(_node_type, min_size=0, max_size=6, unique=True)
_random_edges = st.lists(st.tuples(st.integers(0, 5), st.integers(0, 5)), max_size=12)


@given(types=_random_defs, edge_specs=_random_edges)
@settings(max_examples=200)
def test_validator_never_crashes_and_is_deterministic(
    types: list[str], edge_specs: list[tuple[int, int]]
) -> None:
    g = _build_random_graph(types, edge_specs)
    defs = {
        t: make_definition(
            t,
            inputs=(NodeInput("i0", PortType.JSON, required=False),),
            outputs=(NodeOutput("o0", PortType.JSON),),
        )
        for t in set(types)
    }
    first = validate_graph(g, defs)
    assert validate_graph(g, defs) == first
    for issue in first.issues:
        assert issue.code in ValidationCode
        assert issue.message


_random_dag = st.lists(
    st.tuples(st.integers(0, 6), st.integers(0, 6)), max_size=20
).map(lambda pairs: [(a, b) for a, b in pairs if a < b])


@given(edge_specs=_random_dag)
@settings(max_examples=200)
def test_forward_only_edges_form_valid_dags(
    edge_specs: list[tuple[int, int]],
) -> None:
    types = ["test.a", "test.b", "test.c", "test.d", "test.e", "test.f", "test.g"]
    g = _build_random_graph(types, edge_specs)
    defs = {
        t: make_definition(
            t,
            inputs=(NodeInput("i0", PortType.JSON, required=False, multiple=True),),
            outputs=(NodeOutput("o0", PortType.JSON),),
        )
        for t in set(types)
    }
    result = validate_graph(g, defs)
    assert not result.by_code(ValidationCode.CYCLE_DETECTED)
    assert not result.by_code(ValidationCode.UNKNOWN_PORT)
    assert not result.by_code(ValidationCode.INVALID_PORT_CONNECTION)
