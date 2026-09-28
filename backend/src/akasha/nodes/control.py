"""Control-flow built-ins: core.if and core.switch.

Both route their input to exactly one output port and omit the others.
They declare ``is_control_flow``; the runtime then treats an omitted port as
an inactive branch and skips its consumers (``ExecutionPlan.readiness``).
Nothing in the runtime knows these node types.

Conditions are declarative config (operator + operand), not expressions:
there is no expression engine yet, and user input must never reach eval.
"""

import operator
from collections.abc import Callable
from typing import Any

from akasha.domain.node import (
    Node,
    NodeCapabilities,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
    json_equal,
)
from akasha.nodes.core import VERSION, closed_schema, value_in

J = PortType.JSON
_CONTROL_FLOW = NodeCapabilities(is_control_flow=True)

# ponytail: five fixed case ports — node definitions (and so ports) are
# per type, not per instance. More cases need instance-defined ports, i.e.
# a Node contract change; until then chain switches.
SWITCH_CASES = 5


def _number(value: object, name: str) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    raise NodeError(
        f"the {name} must be a number for this operator",
        kind=NodeErrorKind.VALIDATION,
        code="CONDITION_TYPE_MISMATCH",
    )


def _compare(op: Callable[[float, float], bool]) -> Callable[[Any, Any], bool]:
    return lambda value, operand: op(
        _number(value, "value"), _number(operand, "operand")
    )


def _contains(value: Any, operand: Any) -> bool:
    if isinstance(value, str) and isinstance(operand, str):
        return operand in value
    if isinstance(value, list):
        return any(json_equal(item, operand) for item in value)
    if isinstance(value, dict) and isinstance(operand, str):
        return operand in value
    raise NodeError(
        "contains needs a string, list or object value (and a string operand "
        "for strings and objects)",
        kind=NodeErrorKind.VALIDATION,
        code="CONDITION_TYPE_MISMATCH",
    )


_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "equals": json_equal,
    "not_equals": lambda value, operand: not json_equal(value, operand),
    "greater_than": _compare(operator.gt),
    "greater_or_equal": _compare(operator.ge),
    "less_than": _compare(operator.lt),
    "less_or_equal": _compare(operator.le),
    "contains": _contains,
    "is_null": lambda value, _: value is None,
}
_UNARY = frozenset({"is_null"})


class IfNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.if",
            name="If",
            description="Routes the value to 'true' or 'false' by a condition.",
            version=VERSION,
            category="control_flow",
        ),
        inputs=(value_in(),),
        outputs=(
            NodeOutput("true", J, nullable=True),
            NodeOutput("false", J, nullable=True),
        ),
        config_schema=closed_schema(
            {
                "operator": {"type": "string", "enum": sorted(_OPERATORS)},
                "operand": {},  # any JSON value
            },
            "operator",
        ),
        capabilities=_CONTROL_FLOW,
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        op = context.config["operator"]
        if op not in _UNARY and "operand" not in context.config:
            raise NodeError(
                f"operator {op!r} needs an operand",
                kind=NodeErrorKind.CONFIGURATION,
                code="CONDITION_OPERAND_MISSING",
            )
        value = context.inputs["value"]
        taken = _OPERATORS[op](value, context.config.get("operand"))
        return NodeResult({"true" if taken else "false": value})


class SwitchNode(Node):
    """Routes the value to ``case_<i>`` for the first ``cases[i]`` equal to
    it (JSON equality), else to ``default``."""

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.switch",
            name="Switch",
            description="Routes the value to the first matching case.",
            version=VERSION,
            category="control_flow",
        ),
        inputs=(value_in(),),
        outputs=(
            *(NodeOutput(f"case_{i}", J, nullable=True) for i in range(SWITCH_CASES)),
            NodeOutput("default", J, nullable=True),
        ),
        config_schema=closed_schema(
            {"cases": {"type": "array", "maxItems": SWITCH_CASES}}, "cases"
        ),
        capabilities=_CONTROL_FLOW,
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        value = context.inputs["value"]
        for i, case in enumerate(context.config["cases"]):
            if json_equal(value, case):
                return NodeResult({f"case_{i}": value})
        return NodeResult({"default": value})
