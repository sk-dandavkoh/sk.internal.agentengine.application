"""core.human through the node contract: the first attempt asks (NodeWait),
the resumed attempt routes the answer. The persistent WAITING lifecycle is
tested against PostgreSQL in tests/integration/test_human_in_the_loop.py."""

from datetime import timedelta
from typing import Any

import pytest

from akasha.domain.node import NodeError, NodeResult, NodeWait
from akasha.nodes.human import HumanNode
from tests.support import RunNode, make_context

LEAD = {"name": "Ada", "score": 91}


async def ask(config: dict[str, Any], value: Any = LEAD) -> NodeWait:
    node = HumanNode()
    node.definition.validate_config(config)
    result = await node.execute(
        make_context(node, config=config, inputs={"value": value})
    )
    assert isinstance(result, NodeWait)
    return result


def answered(
    response: dict[str, Any] | None, *, timed_out: bool = False
) -> dict[str, Any]:
    return {"response": response, "responded_by": "usr_1", "timed_out": timed_out}


async def test_first_attempt_waits_with_the_rendered_prompt() -> None:
    wait = await ask({"prompt": "Contact {{ value.name }} (score {{ value.score }})?"})
    assert wait.prompt == "Contact Ada (score 91)?"
    assert wait.timeout is None
    schema = wait.response_schema
    assert schema.json_schema["required"] == ["decision"]
    assert not schema.issues({"decision": "approve", "comment": "ok"})
    assert schema.issues({"decision": "maybe"})  # not an allowed value
    assert schema.issues({"decision": "approve", "extra": 1})  # closed


async def test_fields_and_timeout_shape_the_request() -> None:
    wait = await ask(
        {
            "prompt": "Fill in the offer",
            "approval": False,
            "fields": {
                "amount": {"type": "number", "minimum": 0},
                "note": {"type": "string"},
            },
            "required_fields": ["amount"],
            "timeout_seconds": 3600,
        }
    )
    assert wait.timeout == timedelta(hours=1)
    schema = wait.response_schema
    assert "decision" not in schema.json_schema["properties"]
    assert not schema.issues({"amount": 5})
    assert schema.issues({"note": "x"})  # amount is required
    assert schema.issues({"amount": -1})


@pytest.mark.parametrize(
    ("decision", "port"), [("approve", "approved"), ("reject", "rejected")]
)
async def test_resumed_attempt_routes_the_decision(
    run_node: RunNode, decision: str, port: str
) -> None:
    result = await run_node(
        HumanNode(),
        config={"prompt": "ok?"},
        inputs={"value": LEAD},
        resume=answered({"decision": decision, "comment": "why"}),
    )
    assert result.outputs == {
        port: LEAD,
        "response": {"decision": decision, "comment": "why", "responded_by": "usr_1"},
    }


async def test_submission_without_approval_continues_on_approved(
    run_node: RunNode,
) -> None:
    result = await run_node(
        HumanNode(),
        config={"prompt": "data?", "approval": False},
        inputs={"value": None},
        resume=answered({"comment": "here"}),
    )
    assert set(result.outputs) == {"approved", "response"}


async def test_expired_wait_routes_to_timed_out_only(run_node: RunNode) -> None:
    result = await run_node(
        HumanNode(),
        config={"prompt": "ok?", "timeout_seconds": 60},
        inputs={"value": LEAD},
        resume=answered(None, timed_out=True),
    )
    assert result.outputs == {"timed_out": LEAD}


@pytest.mark.parametrize(
    "config",
    [
        {"prompt": "x", "fields": {"decision": {"type": "string"}}},  # reserved
        {"prompt": "x", "fields": {"when": {"type": "date"}}},  # unsupported type
        {"prompt": "x", "required_fields": ["missing"]},
        {"prompt": "x", "fields": {"n": {"type": "integer", "default": "a"}}},
    ],
)
async def test_invalid_field_definitions_are_configuration_errors(
    config: dict[str, Any],
) -> None:
    with pytest.raises(NodeError) as exc:
        await ask(config)
    assert exc.value.code == "HUMAN_INPUT_INVALID_FIELDS"


async def test_missing_prompt_variable_fails_instead_of_asking_blank() -> None:
    with pytest.raises(NodeError) as exc:
        await ask({"prompt": "Contact {{ value.email }}?"})
    assert exc.value.code == "TEMPLATE_VARIABLE_MISSING"


async def test_overlong_prompts_are_cut() -> None:
    wait = await ask({"prompt": "{{ value }}"}, value="x" * 20_000)
    assert len(wait.prompt) < 10_100 and wait.prompt.endswith("[truncated]")


def test_declares_waiting_and_routing() -> None:
    caps = HumanNode.definition.capabilities
    assert caps.waits_for_input and caps.is_control_flow
    assert not caps.has_side_effects  # asking twice is harmless (recovery)


async def test_a_result_is_returned_only_after_resume(run_node: RunNode) -> None:
    node = HumanNode()
    first = await node.execute(make_context(node, config={"prompt": "ok?"}))
    assert not isinstance(first, NodeResult)
