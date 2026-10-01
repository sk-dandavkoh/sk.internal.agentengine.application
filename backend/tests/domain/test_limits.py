"""ResourceLimits: every cap at and one beyond its boundary (Phase 24)."""

from datetime import timedelta

import pytest

from akasha.domain.execution import RetryPolicy, TimeoutPolicy
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.limits import (
    ExecutionCapacityError,
    Limit,
    ResourceLimitError,
    ResourceLimits,
    graph_depth,
    json_size,
)
from akasha.domain.node import NodeErrorKind
from akasha.domain.primitives import new_id
from akasha.errors import DomainValidationError

LIMITS = ResourceLimits(
    execution_timeout=timedelta(seconds=60),
    node_timeout=timedelta(seconds=10),
    max_node_attempts=3,
    max_graph_nodes=4,
    max_graph_depth=3,
    max_active_executions_per_project=2,
    max_node_output_bytes=100,
    max_execution_output_bytes=250,
)
TICK = timedelta(microseconds=1)
NODE = new_id("node")


def chain(n: int) -> Graph:
    graph = Graph()
    previous: NodeInstance | None = None
    for _ in range(n):
        node = NodeInstance.create("test.node")
        graph.add_node(node)
        if previous is not None:
            graph.add_edge(Edge(previous.id, "out", node.id, "in"))
        previous = node
    return graph


def parallel(n: int) -> Graph:
    graph = Graph()
    for _ in range(n):
        graph.add_node(NodeInstance.create("test.node"))
    return graph


# -- construction ---------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"execution_timeout": timedelta(0)},
        {"node_timeout": timedelta(seconds=-1)},
        {"node_timeout": timedelta(hours=2)},  # > execution_timeout
        {"max_node_attempts": 0},
        {"max_graph_nodes": 0},
        {"max_graph_depth": 0},
        {"max_active_executions_per_project": 0},
        {"max_node_output_bytes": 0},
        {"max_node_output_bytes": 2, "max_execution_output_bytes": 1},
    ],
)
def test_contradictory_limits_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        ResourceLimits(**kwargs)  # type: ignore[arg-type]


def test_defaults_are_finite() -> None:
    limits = ResourceLimits()
    timeout, retry = limits.policies(None, None)
    assert timeout.execution_timeout == limits.execution_timeout
    assert timeout.node_timeout == limits.node_timeout
    assert retry == RetryPolicy()


# -- durations and attempts (admission) -----------------------------------------


def test_missing_timeouts_take_the_caps() -> None:
    timeout, _ = LIMITS.policies(TimeoutPolicy(), None)
    assert timeout == TimeoutPolicy(timedelta(seconds=60), timedelta(seconds=10))


def test_lower_timeouts_are_kept() -> None:
    asked = TimeoutPolicy(timedelta(seconds=5), timedelta(seconds=1))
    assert LIMITS.policies(asked, None)[0] == asked


def test_timeouts_exactly_at_the_caps_are_allowed() -> None:
    asked = TimeoutPolicy(timedelta(seconds=60), timedelta(seconds=10))
    assert LIMITS.policies(asked, None)[0] == asked


@pytest.mark.parametrize(
    ("asked", "limit"),
    [
        (
            TimeoutPolicy(execution_timeout=timedelta(seconds=60) + TICK),
            Limit.EXECUTION_DURATION,
        ),
        (TimeoutPolicy(node_timeout=timedelta(seconds=10) + TICK), Limit.NODE_DURATION),
    ],
)
def test_timeouts_one_beyond_the_caps_are_rejected(
    asked: TimeoutPolicy, limit: Limit
) -> None:
    with pytest.raises(ResourceLimitError) as caught:
        LIMITS.policies(asked, None)
    assert caught.value.limit is limit
    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"
    assert caught.value.http_status == 422


def test_attempts_at_and_beyond_the_cap() -> None:
    at = RetryPolicy(max_attempts=3)
    assert LIMITS.policies(None, at)[1] is at
    with pytest.raises(ResourceLimitError) as caught:
        LIMITS.policies(None, RetryPolicy(max_attempts=4))
    assert caught.value.limit is Limit.NODE_ATTEMPTS
    assert caught.value.details == {
        "limit": "node_attempts",
        "maximum": 3,
        "requested": 4,
    }


# -- graph ------------------------------------------------------------------------


def test_graph_size_at_and_beyond_the_cap() -> None:
    LIMITS.check_graph(parallel(4))
    with pytest.raises(ResourceLimitError) as caught:
        LIMITS.check_graph(parallel(5))
    assert caught.value.limit is Limit.GRAPH_NODES


def test_graph_depth_at_and_beyond_the_cap() -> None:
    LIMITS.check_graph(chain(3))
    with pytest.raises(ResourceLimitError) as caught:
        LIMITS.check_graph(chain(4))
    # 4 nodes is within the size cap: the depth cap is what fired.
    assert caught.value.limit is Limit.GRAPH_DEPTH


def test_graph_depth_is_the_longest_path() -> None:
    assert graph_depth(Graph()) == 0
    assert graph_depth(parallel(3)) == 1
    assert graph_depth(chain(5)) == 5
    # Diamond plus a shortcut: a→b→c→d and a→d. The longest path wins.
    graph = chain(4)
    ids = list(graph.nodes)
    graph.add_edge(Edge(ids[0], "out", ids[3], "in2"))
    assert graph_depth(graph) == 4


def test_graph_depth_ignores_cycles() -> None:
    """Cycles are a validation error, not a limit; measuring must not hang."""
    graph = chain(2)
    a, b = list(graph.nodes)
    graph.add_edge(Edge(b, "out", a, "in2"))
    assert graph_depth(graph) == 0


# -- capacity -------------------------------------------------------------------


def test_active_executions_at_and_beyond_the_cap() -> None:
    LIMITS.check_capacity(1)  # admitting the 2nd of 2
    with pytest.raises(ExecutionCapacityError) as caught:
        LIMITS.check_capacity(2)  # the 3rd
    assert caught.value.limit is Limit.ACTIVE_EXECUTIONS
    assert (caught.value.code, caught.value.http_status) == (
        "EXECUTION_CAPACITY_EXCEEDED",
        429,
    )
    assert isinstance(caught.value, ResourceLimitError)


# -- output -----------------------------------------------------------------------


def test_node_output_at_and_beyond_the_cap() -> None:
    assert LIMITS.check_output(NODE, 100, 0) is None
    exceeded = LIMITS.check_output(NODE, 101, 0)
    assert exceeded is not None
    limit, error = exceeded
    assert limit is Limit.NODE_OUTPUT
    assert (error.code, error.kind, error.node_id) == (
        "NODE_OUTPUT_TOO_LARGE",
        NodeErrorKind.PERMANENT,
        NODE,
    )
    assert error.details == {"size": "101", "limit": "100"}


def test_execution_output_at_and_beyond_the_cap() -> None:
    assert LIMITS.check_output(NODE, 50, 200) is None  # exactly 250
    exceeded = LIMITS.check_output(NODE, 51, 200)
    assert exceeded is not None
    limit, error = exceeded
    assert limit is Limit.EXECUTION_OUTPUT
    assert error.code == "EXECUTION_OUTPUT_TOO_LARGE"
    assert error.details == {"size": "251", "limit": "250"}


def test_node_cap_is_reported_before_the_execution_cap() -> None:
    """Both broken at once: the node's own cap names the culprit."""
    exceeded = LIMITS.check_output(NODE, 101, 249)
    assert exceeded is not None
    assert exceeded[0] is Limit.NODE_OUTPUT


def test_json_size_counts_utf8_bytes() -> None:
    assert json_size({"a": "x"}) == len('{"a":"x"}')
    assert json_size("é") == len('"é"'.encode())  # 2-byte character
    assert json_size("\U0001f600") == 6  # 4-byte character plus quotes
    assert json_size("\ud800") == 5  # unpaired surrogate: measured, not raised
    assert json_size({"b": b"ab"}) == len('{"b":"b\'ab\'"}')  # str() fallback
    assert json_size(float("nan")) == 3
