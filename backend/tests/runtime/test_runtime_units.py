"""NodeExecutor and ExecutionPlan unit tests (no database).

Outcome classification is pure logic over one node attempt; readiness and
input resolution are pure functions of the plan and the aggregate.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any

import pytest

from akasha.application.persistence import UnitOfWork
from akasha.domain.execution import Execution, ExecutionError
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import (
    Node,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.domain.primitives import new_id
from akasha.domain.project import ProjectDraft
from akasha.errors import ConfigurationError
from akasha.runtime.executor import execute_node
from akasha.runtime.plan import ExecutionGraphInvalidError, load_plan
from akasha.runtime.runtime import ExecutionRuntime

S = PortType.STRING
Body = Callable[[NodeExecutionContext], Awaitable[Any]]


def make(
    body: Body,
    *,
    inputs: tuple[NodeInput, ...] = (),
    outputs: tuple[NodeOutput, ...] = (NodeOutput("out", S),),
    node_id: str = "test.node",
) -> type[Node]:
    definition_ = NodeDefinition(
        metadata=NodeMetadata(
            node_id=node_id, name="n", description="Test node.", version="1.0.0"
        ),
        inputs=inputs,
        outputs=outputs,
    )

    class N(Node):
        definition = definition_

        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            result: NodeResult = await body(context)
            return result

    return N


def ctx(inputs: Mapping[str, Any] | None = None) -> NodeExecutionContext:
    return NodeExecutionContext(
        execution_id=new_id("exec"),
        node_execution_id=new_id("nexec"),
        node_instance_id=new_id("node"),
        project_id=new_id("proj"),
        project_version="1",
        config={},
        inputs=inputs or {},
        logger=logging.getLogger("test"),
    )


async def ok(c: NodeExecutionContext) -> NodeResult:
    return NodeResult({"out": "x"})


# -- NodeExecutor --------------------------------------------------------------


async def test_success_returns_the_result() -> None:
    outcome = await execute_node(make(ok), ctx(), limit=None)
    assert outcome == NodeResult({"out": "x"})


async def test_node_error_keeps_its_classification() -> None:
    async def body(c: NodeExecutionContext) -> NodeResult:
        raise NodeError("rate limited", kind=NodeErrorKind.TRANSIENT)

    c = ctx()
    outcome = await execute_node(make(body), c, limit=None)
    assert isinstance(outcome, ExecutionError)
    assert (outcome.code, outcome.kind, outcome.node_id) == (
        "NODE_EXECUTION_ERROR",
        NodeErrorKind.TRANSIENT,
        c.node_instance_id,
    )


async def test_invalid_inputs_never_reach_node_code() -> None:
    called: list[bool] = []

    async def body(c: NodeExecutionContext) -> NodeResult:
        called.append(True)
        return NodeResult({"out": "x"})

    node = make(body, inputs=(NodeInput("text", S),))
    outcome = await execute_node(node, ctx({"text": 1}), limit=None)
    assert isinstance(outcome, ExecutionError)
    assert (outcome.code, outcome.kind) == (
        "NODE_INPUT_INVALID",
        NodeErrorKind.VALIDATION,
    )
    assert called == []


@pytest.mark.parametrize(
    "returned",
    [NodeResult({"out": 1}), NodeResult({}), NodeResult({"out": "x", "extra": 1})],
)
async def test_outputs_violating_the_contract_fail(returned: NodeResult) -> None:
    async def body(c: NodeExecutionContext) -> NodeResult:
        return returned

    outcome = await execute_node(make(body), ctx(), limit=None)
    assert isinstance(outcome, ExecutionError)
    assert outcome.code == "NODE_OUTPUT_INVALID"


async def test_returning_something_other_than_a_node_result_fails() -> None:
    async def body(c: NodeExecutionContext) -> Any:
        return {"out": "x"}

    outcome = await execute_node(make(body), ctx(), limit=None)
    assert isinstance(outcome, ExecutionError) and outcome.code == "NODE_OUTPUT_INVALID"


async def test_unexpected_exception_is_contained_without_its_message() -> None:
    async def body(c: NodeExecutionContext) -> NodeResult:
        raise KeyError("password=secret")

    outcome = await execute_node(make(body), ctx(), limit=None)
    assert isinstance(outcome, ExecutionError)
    assert outcome.code == "NODE_UNEXPECTED_ERROR"
    assert outcome.kind is NodeErrorKind.PERMANENT
    assert "secret" not in outcome.message and "KeyError" in outcome.message


async def test_runtime_node_timeout() -> None:
    async def body(c: NodeExecutionContext) -> NodeResult:
        await asyncio.Event().wait()  # never returns
        raise AssertionError

    limit = timedelta(seconds=0.01)
    outcome = await execute_node(make(body), ctx(), limit=limit)
    assert isinstance(outcome, ExecutionError)
    assert (outcome.code, outcome.kind) == ("NODE_TIMEOUT", NodeErrorKind.TIMEOUT)
    assert outcome.message == "node exceeded its timeout of 0.01s"


async def test_node_swallowing_the_timeout_still_times_out() -> None:
    async def body(c: NodeExecutionContext) -> NodeResult:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass  # contract violation under test: swallowed cancellation
        return NodeResult({"out": "late"})

    outcome = await execute_node(make(body), ctx(), limit=timedelta(seconds=0.01))
    assert isinstance(outcome, ExecutionError) and outcome.code == "NODE_TIMEOUT"


async def test_nodes_own_timeout_error_is_a_timeout_kind() -> None:
    async def body(c: NodeExecutionContext) -> NodeResult:
        raise TimeoutError

    outcome = await execute_node(make(body), ctx(), limit=timedelta(hours=1))
    assert isinstance(outcome, ExecutionError)
    assert (outcome.kind, outcome.message) == (
        NodeErrorKind.TIMEOUT,
        "node reported a timeout",
    )


async def test_cancellation_propagates_and_is_never_a_node_failure() -> None:
    started = asyncio.Event()

    async def body(c: NodeExecutionContext) -> NodeResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError

    task = asyncio.create_task(execute_node(make(body), ctx(), limit=None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# -- ExecutionPlan -------------------------------------------------------------


SOURCE = make(ok, node_id="test.source")
LIST_SOURCE = make(
    ok, node_id="test.list", outputs=(NodeOutput("out", S, multiple=True),)
)
COLLECT = make(
    ok,
    node_id="test.collect",
    inputs=(
        NodeInput("items", S, multiple=True),
        NodeInput("opts", PortType.OBJECT, required=False, default={"k": []}),
    ),
)
CATALOG: dict[str, type[Node]] = {
    n.definition.node_id: n for n in (SOURCE, LIST_SOURCE, COLLECT)
}


def diamond() -> tuple[Execution, dict[str, str]]:
    """s1 (scalar) and s2 (list) both feed collect.items."""
    s1, s2 = NodeInstance.create("test.source"), NodeInstance.create("test.list")
    c = NodeInstance.create("test.collect")
    graph = Graph()
    for n in (s1, s2, c):
        graph.add_node(n)
    graph.add_edge(Edge(s2.id, "out", c.id, "items"))
    graph.add_edge(Edge(s1.id, "out", c.id, "items"))
    version = ProjectDraft(graph=graph).publish(version=1)
    execution = Execution.create(new_id("proj"), version)
    execution.enqueue()
    execution.start()
    return execution, {"s1": s1.id, "s2": s2.id, "c": c.id}


def finish(execution: Execution, node_id: str, outputs: dict[str, Any]) -> None:
    execution.mark_node_ready(node_id)
    execution.start_node(node_id)
    execution.complete_node(node_id, outputs=outputs)


def test_plan_roots_and_dependencies() -> None:
    execution, ids = diamond()
    plan = load_plan(execution.project_version, CATALOG)
    assert plan.roots == sorted([ids["s1"], ids["s2"]])
    assert plan.downstream[ids["s1"]] == (ids["c"],)
    assert plan.downstream[ids["c"]] == ()


def test_node_becomes_ready_only_after_every_upstream_completed() -> None:
    execution, ids = diamond()
    plan = load_plan(execution.project_version, CATALOG)
    finish(execution, ids["s1"], {"out": "a"})
    assert plan.ready_after(ids["s1"], execution) == []
    finish(execution, ids["s2"], {"out": ["b", "c"]})
    assert plan.ready_after(ids["s2"], execution) == [ids["c"]]
    execution.mark_node_ready(ids["c"])
    # Already READY: a second completed parent must not re-mark it.
    assert plan.ready_after(ids["s1"], execution) == []


def test_inputs_collect_in_edge_order_and_defaults_are_private_copies() -> None:
    execution, ids = diamond()
    plan = load_plan(execution.project_version, CATALOG)
    finish(execution, ids["s1"], {"out": "a"})
    finish(execution, ids["s2"], {"out": ["b", "c"]})

    first = plan.inputs_for(ids["c"], execution)
    # Edge order: s2 (list, flattened) first, then s1.
    assert first == {"items": ["b", "c", "a"], "opts": {"k": []}}
    first["opts"]["k"].append("mutated")
    first["items"].append("mutated")
    assert plan.inputs_for(ids["c"], execution) == {
        "items": ["b", "c", "a"],
        "opts": {"k": []},
    }
    assert execution.node(ids["s2"]).outputs == {"out": ["b", "c"]}


def test_invalid_graph_is_rejected_with_structured_issues() -> None:
    execution, _ = diamond()
    with pytest.raises(ExecutionGraphInvalidError) as err:
        load_plan(execution.project_version, {"test.source": SOURCE})
    assert {i.code.value for i in err.value.result.issues} == {"unknown_node_type"}
    assert err.value.code == "EXECUTION_GRAPH_INVALID"


def _unused_uow() -> UnitOfWork:
    raise AssertionError("not reached")


def test_concurrency_limit_must_be_positive() -> None:
    with pytest.raises(ConfigurationError):
        ExecutionRuntime(_unused_uow, {}, max_concurrency=0)
