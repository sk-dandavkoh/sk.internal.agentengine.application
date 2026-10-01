"""core.input, core.output, core.log, core.delay, core.merge, core.set,
core.pick."""

import asyncio
import logging

import pytest

from akasha.domain.node import NodeError, NodeErrorKind
from akasha.errors import NodeConfigurationError, NodeValidationError
from akasha.nodes.core import (
    DelayNode,
    InputNode,
    LogNode,
    MergeNode,
    OutputNode,
    PickNode,
    SetNode,
)
from tests.support import RunNode

# -- core.input -----------------------------------------------------------------


async def test_input_emits_the_whole_execution_input(run_node: RunNode) -> None:
    payload = {"lead": {"name": "Ada"}, "n": 1}
    result = await run_node(InputNode(), execution_input=payload)
    assert result.outputs == {"value": payload}


async def test_input_emits_one_field(run_node: RunNode) -> None:
    result = await run_node(
        InputNode(), config={"field": "lead"}, execution_input={"lead": None}
    )
    assert result.outputs == {"value": None}


async def test_input_missing_field_is_a_validation_error(run_node: RunNode) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(InputNode(), config={"field": "lead"}, execution_input={})
    assert (exc.value.kind, exc.value.code) == (
        NodeErrorKind.VALIDATION,
        "INPUT_FIELD_MISSING",
    )
    assert exc.value.details == {"field": "lead"}


@pytest.mark.parametrize("config", [{"field": ""}, {"field": 1}, {"key": "x"}])
async def test_input_rejects_invalid_config(
    run_node: RunNode, config: dict[str, object]
) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(InputNode(), config=config)


# -- core.output ----------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "x", {"a": [1]}])
async def test_output_records_its_input(run_node: RunNode, value: object) -> None:
    result = await run_node(OutputNode(), inputs={"value": value})
    assert result.outputs == {"value": value}


async def test_output_requires_a_value(run_node: RunNode) -> None:
    with pytest.raises(NodeValidationError, match="missing required input"):
        await run_node(OutputNode(), inputs={})


# -- core.log -------------------------------------------------------------------


async def test_log_writes_structured_line_without_the_value(
    run_node: RunNode, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="akasha.nodes")
    result = await run_node(
        LogNode(),
        config={"message": "lead scored", "level": "warning"},
        inputs={"value": {"email": "ada@example.com"}},
    )
    assert result.outputs == {"value": {"email": "ada@example.com"}}
    (record,) = caplog.records
    assert (record.levelno, record.getMessage()) == (logging.WARNING, "lead scored")
    assert record.__dict__["event"] == "node_log"
    assert record.__dict__["value_type"] == "dict"
    assert record.__dict__["execution_id"].startswith("exec_")
    assert "value" not in record.__dict__
    assert "ada@example.com" not in caplog.text


async def test_log_includes_the_value_only_when_opted_in(
    run_node: RunNode, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="akasha.nodes")
    await run_node(
        LogNode(),
        config={"message": "m", "include_value": True},
        inputs={"value": [1, 2]},
    )
    (record,) = caplog.records
    assert record.levelno == logging.INFO  # default level
    assert record.__dict__["value"] == [1, 2]


@pytest.mark.parametrize(
    "config", [{}, {"message": ""}, {"message": "m", "level": "fatal"}]
)
async def test_log_rejects_invalid_config(
    run_node: RunNode, config: dict[str, object]
) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(LogNode(), config=config, inputs={"value": 1})


# -- core.delay -----------------------------------------------------------------


async def test_delay_passes_the_value_on(run_node: RunNode) -> None:
    result = await run_node(DelayNode(), config={"seconds": 0}, inputs={"value": 7})
    assert result.outputs == {"value": 7}
    unconnected = await run_node(DelayNode(), config={"seconds": 0})
    assert unconnected.outputs == {"value": None}


@pytest.mark.parametrize("seconds", [-1, 3600.5, "5"])
async def test_delay_bounds_are_enforced(run_node: RunNode, seconds: object) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(DelayNode(), config={"seconds": seconds})


async def test_delay_is_interrupted_by_cancellation(run_node: RunNode) -> None:
    task = asyncio.create_task(run_node(DelayNode(), config={"seconds": 3600}))
    await asyncio.sleep(0)  # one loop turn: the node is now inside its wait
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_delay_is_interrupted_by_a_timeout(run_node: RunNode) -> None:
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await run_node(DelayNode(), config={"seconds": 3600})


# -- core.merge -----------------------------------------------------------------


async def test_merge_collects_values_in_order(run_node: RunNode) -> None:
    result = await run_node(MergeNode(), inputs={"items": [{"a": 1}, None, "x"]})
    assert result.outputs == {"items": [{"a": 1}, None, "x"]}


async def test_merge_requires_a_list(run_node: RunNode) -> None:
    with pytest.raises(NodeValidationError):
        await run_node(MergeNode(), inputs={"items": "not-a-list"})


# -- core.set -------------------------------------------------------------------


async def test_set_emits_static_fields_without_an_input(run_node: RunNode) -> None:
    result = await run_node(SetNode(), config={"fields": {"query": "q", "n": 2}})
    assert result.outputs == {"value": {"query": "q", "n": 2}}


async def test_set_wraps_the_input_under_key(run_node: RunNode) -> None:
    result = await run_node(
        SetNode(),
        config={"fields": {"topic": "t", "results": "static"}, "key": "results"},
        inputs={"value": [{"url": "u"}]},
    )
    # The input wins over a static field of the same name.
    assert result.outputs == {"value": {"topic": "t", "results": [{"url": "u"}]}}


async def test_set_rejects_a_value_without_key(run_node: RunNode) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(SetNode(), inputs={"value": 1})
    assert (exc.value.kind, exc.value.code) == (
        NodeErrorKind.CONFIGURATION,
        "SET_KEY_MISSING",
    )


@pytest.mark.parametrize("config", [{"fields": []}, {"key": ""}, {"other": 1}])
async def test_set_rejects_invalid_config(
    run_node: RunNode, config: dict[str, object]
) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(SetNode(), config=config)


# -- core.pick ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [("score", 8), ("lead.contacts.1", "b"), ("lead.none", None)],
)
async def test_pick_follows_the_path(
    run_node: RunNode, path: str, expected: object
) -> None:
    value = {"score": 8, "lead": {"contacts": ["a", "b"], "none": None}}
    result = await run_node(PickNode(), config={"path": path}, inputs={"value": value})
    assert result.outputs == {"value": expected}


@pytest.mark.parametrize("path", ["missing", "score.x", "lead.contacts.2"])
async def test_pick_missing_path_is_a_validation_error(
    run_node: RunNode, path: str
) -> None:
    value = {"score": 8, "lead": {"contacts": ["a", "b"]}}
    with pytest.raises(NodeError) as exc:
        await run_node(PickNode(), config={"path": path}, inputs={"value": value})
    assert (exc.value.kind, exc.value.code) == (
        NodeErrorKind.VALIDATION,
        "PICK_PATH_MISSING",
    )


@pytest.mark.parametrize(
    "config", [{}, {"path": ""}, {"path": "__class__.x y"}, {"path": "a..b"}]
)
async def test_pick_rejects_invalid_paths(
    run_node: RunNode, config: dict[str, object]
) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(PickNode(), config=config, inputs={"value": {}})
