"""Values PostgreSQL cannot store (NaN, Infinity, NUL) are rejected at every
domain write gate, so bad input is a validation error, never a database
error (500) at commit time."""

import math
from typing import Any

import pytest

from akasha.domain.credentials import Credential, CredentialKind
from akasha.domain.execution import ExecutionError
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeErrorKind
from akasha.domain.primitives import is_storable, validate_name
from akasha.domain.trigger import Trigger, TriggerType
from akasha.domain.workflow import Dependency, Workflow, WorkflowDraft
from akasha.errors import DomainValidationError

UNSTORABLE: list[Any] = [
    math.nan,
    math.inf,
    -math.inf,
    "nul\0",
    "\\\0",  # literal backslash followed by a real NUL
    {"key\0": 1},
    "lone \ud800 surrogate",
    {"\udfff": 1},
    [1, {"x": math.nan}],
    b"bytes",
    object(),
]


@pytest.mark.parametrize("value", UNSTORABLE)
def test_unstorable_values(value: Any) -> None:
    assert not is_storable(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        1.5,
        "ünïcode 😀",
        {"a": [1, None, "x"]},
        [],
        # literal backslash text that merely looks like a NUL escape
        "\\u0000",
        {"\\u0000": None},
        "\\\\u0000",
    ],
)
def test_storable_values(value: Any) -> None:
    assert is_storable(value)


def test_names_and_descriptions_reject_nul() -> None:
    with pytest.raises(DomainValidationError, match="NUL"):
        validate_name("a\0b")
    with pytest.raises(DomainValidationError, match="surrogates"):
        validate_name("a\ud800b")
    with pytest.raises(DomainValidationError, match="NUL"):
        Workflow.create("p", description="d\0")
    with pytest.raises(DomainValidationError, match="NUL"):
        Credential(name="crm", kind=CredentialKind.GENERIC, description="d\0")


def _draft(**overrides: Any) -> WorkflowDraft:
    graph = Graph()
    a = NodeInstance(id="node_" + "a" * 32, node_type="t.a", config={"x": 1})
    b = NodeInstance(id="node_" + "b" * 32, node_type="t.b")
    graph.add_node(a)
    graph.add_node(b)
    graph.add_edge(Edge(a.id, "out", b.id, "in"))
    draft = WorkflowDraft(
        graph=graph,
        triggers=[Trigger(id="trig_" + "c" * 32, trigger_type=TriggerType.MANUAL)],
        dependencies=[Dependency("node", "t.a")],
    )
    for key, value in overrides.items():
        setattr(draft, key, value)
    return draft


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (
            lambda d: d.graph.nodes["node_" + "a" * 32].config.update(x=math.nan),
            "nodes.node_" + "a" * 32,
        ),
        (
            lambda d: d.graph.nodes["node_" + "a" * 32].config.update(x=math.inf),
            "nodes.node_" + "a" * 32,
        ),
        (
            lambda d: d.graph.nodes["node_" + "a" * 32].config.update({"k\0": 1}),
            "nodes.node_" + "a" * 32,
        ),
        (
            lambda d: setattr(d.graph.nodes["node_" + "b" * 32], "label", "l\0"),
            "nodes.node_" + "b" * 32,
        ),
        (
            lambda d: d.graph.edges.__setitem__(
                0, Edge("node_" + "a" * 32, "o\0", "node_" + "b" * 32, "in")
            ),
            "edges[0]",
        ),
        (lambda d: d.triggers[0].config.update(p="\0"), "triggers.trig_" + "c" * 32),
        (
            lambda d: d.dependencies.__setitem__(
                0, Dependency("node", "t.a", ">=1.0.0\0")
            ),
            "dependencies[0]",
        ),
        (lambda d: setattr(d, "description", "d\0"), "description"),
    ],
)
def test_replace_draft_names_unstorable_locations(mutate: Any, path: str) -> None:
    workflow = Workflow.create("p")
    draft = _draft()
    mutate(draft)
    with pytest.raises(DomainValidationError, match="NaN, Infinity or NUL") as info:
        workflow.replace_draft(draft, name="p")
    assert info.value.details == {"paths": [path]}
    assert workflow.draft.graph.nodes == {}  # unchanged


def test_valid_draft_is_accepted() -> None:
    workflow = Workflow.create("p")
    workflow.replace_draft(_draft(), name="p")
    assert len(workflow.draft.graph.nodes) == 2


def test_execution_errors_replace_nul_instead_of_failing() -> None:
    """A failing node must still be able to record its failure."""
    error = ExecutionError(
        code="NODE_EXECUTION_ERROR",
        message="bad\0message\ud800",
        kind=NodeErrorKind.PERMANENT,
        details={"k\0": "v\0"},
    )
    assert error.message == "bad\ufffdmessage\ufffd"
    assert error.details == {"k\ufffd": "v\ufffd"}
    assert is_storable([error.message, dict(error.details)])
