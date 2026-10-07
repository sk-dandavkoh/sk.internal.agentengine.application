"""core.transform: reshape data with a fixed pipeline of declarative steps.

One node for the data operations workflows need between I/O nodes (core
node portfolio): parse/serialize JSON and CSV, pick, filter, map, sort,
limit, unique, flatten, group, aggregate and text templates. Steps run in
order, each on the previous step's result:

    {"op": "parse_csv"} → {"op": "filter", "field": "status",
    "operator": "equals", "operand": "open"} → {"op": "map", "fields":
    {"id": "id", "owner": "owner.name"}} → {"op": "sort", "field": "id"}

Like core.if, nothing is evaluated: paths are plain dotted lookups
(``lead.contacts.0``), conditions reuse core.if's operators, templates the
``{{ path }}`` substitution of core.llm. No expression engine, no eval.

ponytail: per-step parameters are checked when the step runs, not at
publish (the config-schema subset has no oneOf). Upgrade path: per-op
schemas once the validator supports combinators.
"""

import csv
import io
import json
from collections.abc import Callable, Mapping
from typing import Any

from akasha.nodes.control import OPERATORS, UNARY_OPERATORS
from akasha.nodes.core import (
    MISSING,
    PATH_SCHEMA,
    TEMPLATE,
    VERSION,
    closed_schema,
    lookup,
    render,
    value_in,
    value_out,
)
from akasha.sdk import (
    Node,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeMetadata,
    NodeResult,
)

MAX_STEPS = 20
_AGGREGATES = ("avg", "count", "max", "min", "sum")


def _error(
    message: str, code: str, kind: NodeErrorKind = NodeErrorKind.VALIDATION
) -> NodeError:
    return NodeError(message, kind=kind, code=code)


def _param(step: Mapping[str, Any], name: str) -> Any:
    if name not in step:
        raise _error(
            f"step {step['op']!r} needs {name!r}",
            "TRANSFORM_STEP_INVALID",
            NodeErrorKind.CONFIGURATION,
        )
    return step[name]


def _items(value: Any, op: str) -> list[Any]:
    if not isinstance(value, list):
        raise _error(f"step {op!r} needs a list", "TRANSFORM_TYPE_MISMATCH")
    return value


def _field(item: Any, step: Mapping[str, Any]) -> Any:
    """The tested/sorted part of an item: ``field`` of it, or the item."""
    if "field" not in step:
        return item
    found = lookup(item, step["field"])
    return None if found is MISSING else found


def _key(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _pick(value: Any, step: Mapping[str, Any]) -> Any:
    found = lookup(value, _param(step, "path"))
    if found is MISSING:
        raise _error(f"no value at {step['path']!r}", "TRANSFORM_PATH_MISSING")
    return found


def _filter(value: Any, step: Mapping[str, Any]) -> Any:
    op = _param(step, "operator")
    if op not in UNARY_OPERATORS:
        _param(step, "operand")
    test = OPERATORS[op]
    return [
        i for i in _items(value, "filter") if test(_field(i, step), step.get("operand"))
    ]


def _map(value: Any, step: Mapping[str, Any]) -> Any:
    fields: Mapping[str, str] = _param(step, "fields")

    def project(item: Any) -> dict[str, Any]:
        out = {}
        for name, path in fields.items():
            found = lookup(item, path)
            out[name] = None if found is MISSING else found
        return out

    return [project(i) for i in _items(value, "map")]


def _sort(value: Any, step: Mapping[str, Any]) -> Any:
    try:
        # None sorts first; a mix of other types is a data error.
        return sorted(
            _items(value, "sort"),
            key=lambda i: (_field(i, step) is not None, _field(i, step)),
            reverse=step.get("descending", False),
        )
    except TypeError:
        raise _error(
            "sort values are not comparable", "TRANSFORM_TYPE_MISMATCH"
        ) from None


def _unique(value: Any, step: Mapping[str, Any]) -> Any:
    seen: set[str] = set()
    out = []
    for item in _items(value, "unique"):
        key = _key(_field(item, step))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _flatten(value: Any, step: Mapping[str, Any]) -> Any:
    out: list[Any] = []
    for item in _items(value, "flatten"):
        out.extend(item if isinstance(item, list) else [item])
    return out


def _group(value: Any, step: Mapping[str, Any]) -> Any:
    _param(step, "field")
    groups: dict[str, list[Any]] = {}
    for item in _items(value, "group"):
        groups.setdefault(_key(_field(item, step)), []).append(item)
    return groups


def _aggregate(value: Any, step: Mapping[str, Any]) -> Any:
    function = _param(step, "function")
    values = [_field(i, step) for i in _items(value, "aggregate")]
    if function == "count":
        return len(values)
    numbers = [v for v in values if v is not None]
    if any(isinstance(v, bool) or not isinstance(v, int | float) for v in numbers):
        raise _error("aggregate needs numbers", "TRANSFORM_TYPE_MISMATCH")
    if not numbers:
        return 0 if function == "sum" else None
    match function:
        case "sum":
            return sum(numbers)
        case "min":
            return min(numbers)
        case "max":
            return max(numbers)
    return sum(numbers) / len(numbers)


def _text(value: Any, op: str) -> str:
    if not isinstance(value, str):
        raise _error(f"step {op!r} needs text", "TRANSFORM_TYPE_MISMATCH")
    return value


def _parse_json(value: Any, step: Mapping[str, Any]) -> Any:
    try:
        return json.loads(_text(value, "parse_json"))
    except ValueError:
        raise _error("the text is not valid JSON", "TRANSFORM_PARSE_ERROR") from None


def _to_json(value: Any, step: Mapping[str, Any]) -> Any:
    return json.dumps(value, ensure_ascii=False)


def _parse_csv(value: Any, step: Mapping[str, Any]) -> Any:
    reader = csv.DictReader(
        io.StringIO(_text(value, "parse_csv")), delimiter=step.get("delimiter", ",")
    )
    try:
        # Short rows fill with None; extra cells land under "" (restkey).
        return [dict(row) for row in reader]
    except csv.Error:
        raise _error("the text is not valid CSV", "TRANSFORM_PARSE_ERROR") from None


def _to_csv(value: Any, step: Mapping[str, Any]) -> Any:
    rows = _items(value, "to_csv")
    if not all(isinstance(r, dict) for r in rows):
        raise _error("to_csv needs a list of objects", "TRANSFORM_TYPE_MISMATCH")
    columns = list(dict.fromkeys(k for r in rows for k in r))  # first-seen order
    out = io.StringIO()
    writer = csv.DictWriter(out, columns, delimiter=step.get("delimiter", ","))
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                k: v if isinstance(v, str) or v is None else json.dumps(v)
                for k, v in row.items()
            }
        )
    return out.getvalue()


def _template(value: Any, step: Mapping[str, Any]) -> Any:
    return render(_param(step, "template"), {"value": value})


_STEPS: dict[str, Callable[[Any, Mapping[str, Any]], Any]] = {
    "aggregate": _aggregate,
    "filter": _filter,
    "flatten": _flatten,
    "group": _group,
    "limit": lambda v, s: _items(v, "limit")[: _param(s, "count")],
    "map": _map,
    "parse_csv": _parse_csv,
    "parse_json": _parse_json,
    "pick": _pick,
    "sort": _sort,
    "template": _template,
    "to_csv": _to_csv,
    "to_json": _to_json,
    "unique": _unique,
}


class TransformNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.transform",
            name="Transform",
            description="Reshapes data: parse, filter, map, sort, group, format.",
            version=VERSION,
            category="transform",
        ),
        inputs=(value_in("The data to reshape."),),
        outputs=(value_out("The result after the last step."),),
        config_schema=closed_schema(
            {
                "steps": {
                    "type": "array",
                    "maxItems": MAX_STEPS,
                    "description": "Applied in order, each to the previous result.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": sorted(_STEPS)},
                            "path": {
                                **PATH_SCHEMA,
                                "description": "pick: dotted path.",
                            },
                            "field": {
                                **PATH_SCHEMA,
                                "description": "filter/sort/unique/group/"
                                "aggregate: dotted path of each item.",
                            },
                            "operator": {"type": "string", "enum": sorted(OPERATORS)},
                            "operand": {"description": "filter: compared value."},
                            "fields": {
                                "type": "object",
                                "additionalProperties": PATH_SCHEMA,
                                "description": "map: output field to path.",
                            },
                            "descending": {"type": "boolean"},
                            "count": {"type": "integer", "minimum": 0},
                            "function": {"type": "string", "enum": list(_AGGREGATES)},
                            "delimiter": {
                                "type": "string",
                                "enum": [",", ";", "\t", "|"],
                            },
                            "template": {
                                **TEMPLATE,
                                "description": "template: text with"
                                " {{ value }} / {{ value.name }}.",
                            },
                        },
                        "required": ["op"],
                        "additionalProperties": False,
                    },
                }
            },
            "steps",
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        value = context.inputs["value"]
        for step in context.config["steps"]:
            value = _STEPS[step["op"]](value, step)
        return NodeResult({"value": value})
