"""core.transform: every step through the node contract, pipelines, and the
failure semantics of bad data versus bad configuration."""

from typing import Any

import pytest

from akasha.domain.node import NodeError, NodeErrorKind
from akasha.errors import NodeConfigurationError
from akasha.nodes.transform import TransformNode
from tests.support import RunNode

ORDERS = [
    {"id": 3, "status": "open", "total": 30, "owner": {"name": "Bo"}},
    {"id": 1, "status": "paid", "total": 10, "owner": {"name": "Ada"}},
    {"id": 2, "status": "open", "total": 20.5, "owner": {"name": "Ada"}},
]


async def apply(run_node: RunNode, value: Any, *steps: dict[str, Any]) -> Any:
    result = await run_node(
        TransformNode(), config={"steps": list(steps)}, inputs={"value": value}
    )
    return result.outputs["value"]


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (
            [
                {"op": "group", "field": "status"},
                {"op": "pick", "path": "paid.0.owner.name"},
            ],
            "Ada",
        ),
        (
            [
                {
                    "op": "filter",
                    "field": "status",
                    "operator": "equals",
                    "operand": "open",
                }
            ],
            [ORDERS[0], ORDERS[2]],
        ),
        (
            [
                {
                    "op": "filter",
                    "field": "total",
                    "operator": "greater_than",
                    "operand": 15,
                }
            ],
            [ORDERS[0], ORDERS[2]],
        ),
        (
            [{"op": "map", "fields": {"id": "id", "who": "owner.name", "x": "nope"}}],
            [
                {"id": 3, "who": "Bo", "x": None},
                {"id": 1, "who": "Ada", "x": None},
                {"id": 2, "who": "Ada", "x": None},
            ],
        ),
        (
            [{"op": "sort", "field": "id"}, {"op": "map", "fields": {"id": "id"}}],
            [{"id": 1}, {"id": 2}, {"id": 3}],
        ),
        (
            [
                {"op": "sort", "field": "total", "descending": True},
                {"op": "limit", "count": 1},
            ],
            [ORDERS[0]],
        ),
        ([{"op": "unique", "field": "owner.name"}], [ORDERS[0], ORDERS[1]]),
        ([{"op": "aggregate", "function": "sum", "field": "total"}], 60.5),
        ([{"op": "aggregate", "function": "count"}], 3),
        ([{"op": "aggregate", "function": "max", "field": "id"}], 3),
        ([{"op": "aggregate", "function": "avg", "field": "id"}], 2),
        (
            [{"op": "group", "field": "status"}, {"op": "pick", "path": "paid"}],
            [ORDERS[1]],
        ),
        ([{"op": "to_json"}, {"op": "parse_json"}], ORDERS),
    ],
)
async def test_steps(
    run_node: RunNode, steps: list[dict[str, Any]], expected: Any
) -> None:
    assert await apply(run_node, ORDERS, *steps) == expected


async def test_csv_round_trip_and_pipeline(run_node: RunNode) -> None:
    text = "id;name;tags\n1;Ada;\n2;Bo;x\n"
    rows = await apply(run_node, text, {"op": "parse_csv", "delimiter": ";"})
    assert rows == [
        {"id": "1", "name": "Ada", "tags": ""},
        {"id": "2", "name": "Bo", "tags": "x"},
    ]
    csv_text = await apply(
        run_node,
        [{"a": 1, "b": "x"}, {"a": 2, "c": {"n": 1}}],
        {"op": "to_csv"},
    )
    assert csv_text.splitlines() == ["a,b,c", "1,x,", '2,,"{""n"": 1}"']


async def test_template_renders_text_without_evaluating(run_node: RunNode) -> None:
    text = await apply(
        run_node,
        {"name": "Ada", "n": 2},
        {"op": "template", "template": "Hi {{ value.name }} ({{ value.n }})"},
    )
    assert text == "Hi Ada (2)"


async def test_flatten_one_level(run_node: RunNode) -> None:
    assert await apply(run_node, [[1, [2]], 3, []], {"op": "flatten"}) == [1, [2], 3]


async def test_empty_aggregates(run_node: RunNode) -> None:
    assert await apply(run_node, [], {"op": "aggregate", "function": "sum"}) == 0
    assert await apply(run_node, [], {"op": "aggregate", "function": "avg"}) is None


@pytest.mark.parametrize(
    ("value", "step", "code"),
    [
        (
            "not a list",
            {"op": "filter", "operator": "is_null"},
            "TRANSFORM_TYPE_MISMATCH",
        ),
        ([1, "a"], {"op": "sort"}, "TRANSFORM_TYPE_MISMATCH"),
        (["a"], {"op": "aggregate", "function": "sum"}, "TRANSFORM_TYPE_MISMATCH"),
        ("{nope", {"op": "parse_json"}, "TRANSFORM_PARSE_ERROR"),
        ({"a": 1}, {"op": "pick", "path": "b"}, "TRANSFORM_PATH_MISSING"),
        ([1], {"op": "to_csv"}, "TRANSFORM_TYPE_MISMATCH"),
    ],
)
async def test_bad_data_is_a_validation_error(
    run_node: RunNode, value: Any, step: dict[str, Any], code: str
) -> None:
    with pytest.raises(NodeError) as exc:
        await apply(run_node, value, step)
    assert (exc.value.code, exc.value.kind) == (code, NodeErrorKind.VALIDATION)


@pytest.mark.parametrize(
    "step",
    [
        {"op": "pick"},
        {"op": "limit"},
        {"op": "filter", "operator": "equals"},
        {"op": "map"},
    ],
)
async def test_missing_step_parameters_are_configuration_errors(
    run_node: RunNode, step: dict[str, Any]
) -> None:
    with pytest.raises(NodeError) as exc:
        await apply(run_node, [], step)
    assert exc.value.kind is NodeErrorKind.CONFIGURATION


@pytest.mark.parametrize(
    "step",
    [
        {"op": "eval"},  # no such step
        {"op": "pick", "path": "a[0]"},
        {"op": "template", "template": "{{ value.__class__ }}x{{ }}"},
        {"op": "pick", "path": "a", "code": "import os"},  # closed step schema
    ],
)
def test_no_code_or_attribute_access_passes_publish(step: dict[str, Any]) -> None:
    with pytest.raises(NodeConfigurationError):
        TransformNode.definition.validate_config({"steps": [step]})


async def test_dunder_keys_are_data_not_attributes(run_node: RunNode) -> None:
    # A plain path segment only ever indexes mappings and lists.
    assert (
        await apply(run_node, {"__class__": 1}, {"op": "pick", "path": "__class__"})
        == 1
    )
    with pytest.raises(NodeError):
        await apply(run_node, "text", {"op": "pick", "path": "upper"})
