"""core.if and core.switch: routing decisions through the contract."""

from typing import Any

import pytest

from akasha.domain.node import NodeError, NodeErrorKind
from akasha.errors import NodeConfigurationError
from akasha.nodes.control import IfNode, SwitchNode
from tests.support import RunNode


async def branch(run_node: RunNode, value: Any, **config: Any) -> str:
    result = await run_node(IfNode(), config=config, inputs={"value": value})
    (port,) = result.outputs  # exactly one branch is produced
    assert result.outputs[port] == value  # and it carries the value unchanged
    return port


@pytest.mark.parametrize(
    ("value", "op", "operand", "expected"),
    [
        (200, "equals", 200, "true"),
        (200, "equals", 200.0, "true"),
        (True, "equals", 1, "false"),  # booleans are not numbers
        ({"a": [1]}, "equals", {"a": [1]}, "true"),
        ("x", "not_equals", "y", "true"),
        (5, "greater_than", 4, "true"),
        (4, "greater_than", 4, "false"),
        (4, "greater_or_equal", 4, "true"),
        (3, "less_than", 4.5, "true"),
        (5, "less_or_equal", 4, "false"),
        ("hello world", "contains", "world", "true"),
        (["a", 1], "contains", 1.0, "true"),
        ([True], "contains", 1, "false"),
        ({"k": 1}, "contains", "k", "true"),
        ({"k": 1}, "contains", "z", "false"),
    ],
)
async def test_if_operators(
    run_node: RunNode, value: Any, op: str, operand: Any, expected: str
) -> None:
    assert await branch(run_node, value, operator=op, operand=operand) == expected


async def test_if_is_null_needs_no_operand(run_node: RunNode) -> None:
    assert await branch(run_node, None, operator="is_null") == "true"
    assert await branch(run_node, 0, operator="is_null") == "false"


async def test_if_binary_operator_without_operand(run_node: RunNode) -> None:
    with pytest.raises(NodeError) as exc:
        await branch(run_node, 1, operator="equals")
    assert exc.value.kind is NodeErrorKind.CONFIGURATION
    assert exc.value.code == "CONDITION_OPERAND_MISSING"


@pytest.mark.parametrize(
    ("value", "op", "operand"),
    [
        ("5", "greater_than", 4),
        (5, "less_than", "6"),
        (True, "greater_than", 0),
        (5, "contains", 5),
        ("abc", "contains", 1),
    ],
)
async def test_if_type_mismatch_is_a_validation_error(
    run_node: RunNode, value: Any, op: str, operand: Any
) -> None:
    with pytest.raises(NodeError) as exc:
        await branch(run_node, value, operator=op, operand=operand)
    assert (exc.value.kind, exc.value.code) == (
        NodeErrorKind.VALIDATION,
        "CONDITION_TYPE_MISMATCH",
    )


@pytest.mark.parametrize(
    "config",
    [{}, {"operator": "matches", "operand": ".*"}, {"operator": "equals", "x": 1}],
)
async def test_if_rejects_invalid_config(
    run_node: RunNode, config: dict[str, Any]
) -> None:
    """Malicious/unknown operators never reach evaluation: the schema enum
    rejects them at publish time."""
    with pytest.raises(NodeConfigurationError):
        await run_node(IfNode(), config=config, inputs={"value": 1})


async def route(run_node: RunNode, value: Any, cases: list[Any]) -> dict[str, Any]:
    result = await run_node(
        SwitchNode(), config={"cases": cases}, inputs={"value": value}
    )
    return dict(result.outputs)


async def test_switch_routes_to_first_matching_case(run_node: RunNode) -> None:
    cases = ["a", "b", "b", {"k": 1}]
    assert await route(run_node, "b", cases) == {"case_1": "b"}
    assert await route(run_node, {"k": 1.0}, cases) == {"case_3": {"k": 1.0}}


async def test_switch_falls_back_to_default(run_node: RunNode) -> None:
    assert await route(run_node, True, [1, "true"]) == {"default": True}
    assert await route(run_node, None, []) == {"default": None}


@pytest.mark.parametrize("config", [{}, {"cases": "a"}, {"cases": [1, 2, 3, 4, 5, 6]}])
async def test_switch_rejects_invalid_config(
    run_node: RunNode, config: dict[str, Any]
) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(SwitchNode(), config=config, inputs={"value": 1})
