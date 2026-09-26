"""Contract tests for the Node system (roadmap Phase 03, backend/AGENTS.md §58).

One minimal example node (UpperCaseNode) stands in for any future built-in:
it must pass the same contract checks every production node will face.
"""

import asyncio
import dataclasses
import logging
from typing import Any, cast

import pytest

from akasha.domain.node import (
    Node,
    NodeConfigSchema,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
    check_node_contract,
    validate_port_value,
)
from akasha.errors import (
    NodeConfigurationError,
    NodeContractError,
    NodeValidationError,
)

# ---------------------------------------------------------------------------
# A minimal example node used to prove the contract is implementable.
# ---------------------------------------------------------------------------


class UpperCaseNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.upper_case",
            name="Upper Case",
            description="Uppercases the input text.",
            version="1.0.0",
            category="utility",
        ),
        inputs=(NodeInput(name="text", port_type=PortType.STRING),),
        outputs=(NodeOutput(name="upper", port_type=PortType.STRING),),
        config_schema=NodeConfigSchema(
            {
                "type": "object",
                "properties": {"strip": {"type": "boolean"}},
                "additionalProperties": False,
            }
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        self.definition.validate_config(context.config)
        self.definition.validate_inputs(context.inputs)
        text = str(context.inputs["text"])
        if context.config.get("strip"):
            text = text.strip()
        context.logger.info("node.execute")
        return NodeResult(outputs={"upper": text.upper()})


class FailOnDemandNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.fail_on_demand",
            name="Fail On Demand",
            description="Fails with a configurable error kind.",
            version="0.1.0",
        ),
        inputs=(NodeInput(name="kind", port_type=PortType.STRING),),
        outputs=(NodeOutput(name="never", port_type=PortType.STRING, nullable=True),),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        raise NodeError("boom", kind=NodeErrorKind(context.inputs["kind"]))


def make_context(
    *,
    inputs: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> NodeExecutionContext:
    return NodeExecutionContext(
        execution_id="exec_" + "a" * 32,
        node_execution_id="nexec_" + "a" * 32,
        node_instance_id="node_" + "a" * 32,
        project_id="proj_" + "a" * 32,
        project_version="1.0.0",
        config=config or {},
        inputs=inputs or {"text": "hello"},
        logger=logging.getLogger("test"),
    )


# ---------------------------------------------------------------------------
# Metadata and definition-level invariants
# ---------------------------------------------------------------------------


def test_valid_definition_passes_contract_check() -> None:
    check_node_contract(UpperCaseNode)


def test_metadata_requires_dotted_lowercase_node_id() -> None:
    for bad in ("UpperCase", "core.", "single", "no-namespace"):
        with pytest.raises(NodeContractError):
            NodeMetadata(node_id=bad, name="N", description="D", version="1.0.0")


def test_metadata_requires_semver_version() -> None:
    for bad in ("1.0", "v1", "latest", ""):
        with pytest.raises(NodeContractError):
            NodeMetadata(node_id="test.x", name="N", description="D", version=bad)
    ok = NodeMetadata(node_id="test.x", name="N", description="D", version="1.2.3-rc.1")
    assert ok.version == "1.2.3-rc.1"


def test_definition_rejects_duplicate_ports() -> None:
    meta = NodeMetadata(node_id="test.dup", name="N", description="D", version="1.0.0")
    ports = (
        NodeInput(name="a", port_type=PortType.STRING),
        NodeInput(name="a", port_type=PortType.STRING, required=False),
    )
    with pytest.raises(NodeContractError, match="duplicate input"):
        NodeDefinition(metadata=meta, inputs=ports)


def test_definition_rejects_malformed_port_name() -> None:
    meta = NodeMetadata(node_id="test.bad", name="N", description="D", version="1.0.0")
    with pytest.raises(NodeContractError, match="malformed port name"):
        NodeDefinition(
            metadata=meta,
            inputs=(NodeInput(name="not a port", port_type=PortType.STRING),),
        )


def test_required_input_must_not_declare_default() -> None:
    meta = NodeMetadata(node_id="test.d", name="N", description="D", version="1.0.0")
    with pytest.raises(NodeContractError, match="must not declare a default"):
        NodeDefinition(
            metadata=meta,
            inputs=(NodeInput(name="a", port_type=PortType.STRING, default="x"),),
        )


def test_default_must_match_port_type() -> None:
    meta = NodeMetadata(node_id="test.d2", name="N", description="D", version="1.0.0")
    with pytest.raises(NodeContractError, match="violates its own port type"):
        NodeDefinition(
            metadata=meta,
            inputs=(
                NodeInput(
                    name="a",
                    port_type=PortType.NUMBER,
                    required=False,
                    default="not-a-number",
                ),
            ),
        )
    # a matching default is fine
    NodeDefinition(
        metadata=meta,
        inputs=(
            NodeInput(name="a", port_type=PortType.NUMBER, required=False, default=3),
        ),
    )


def test_check_contract_rejects_incomplete_classes() -> None:
    class NoDefinition(Node):
        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult(outputs={})

    with pytest.raises(NodeContractError, match="must declare a NodeDefinition"):
        check_node_contract(NoDefinition)

    class NoExecute(Node):
        definition = UpperCaseNode.definition
        # execute inherited but not implemented

    with pytest.raises(NodeContractError, match="must implement execute"):
        # NoExecute is intentionally abstract; cast to smuggle it past mypy —
        # being non-instantiable is exactly what this check must reject.
        check_node_contract(cast("type[Node]", NoExecute))


def test_definition_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        # Intentional illegal write to prove runtime immutability — mypy
        # rejects it statically, which is precisely the contract itself.
        UpperCaseNode.definition.metadata = cast(  # type: ignore[misc]
            "NodeMetadata", None
        )


# ---------------------------------------------------------------------------
# Port value validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("port_type", "good"),
    [
        (PortType.STRING, "text"),
        (PortType.NUMBER, 3),
        (PortType.NUMBER, 3.5),
        (PortType.BOOLEAN, True),
        (PortType.OBJECT, {"k": "v"}),
        (PortType.LIST, [1, 2]),
        (PortType.BINARY, b"\x00\x01"),
        (PortType.JSON, {"nested": [1, "a", None]}),
        (PortType.FILE, "file_abc123"),
        (PortType.ARTIFACT, "artifact_abc123"),
        (PortType.SECRET_REFERENCE, "credential:openai-prod"),
    ],
)
def test_valid_port_values(port_type: PortType, good: Any) -> None:
    port = NodeInput(name="p", port_type=port_type)
    validate_port_value(port, good)


@pytest.mark.parametrize(
    ("port_type", "bad"),
    [
        (PortType.STRING, 7),
        (PortType.NUMBER, True),  # bool is not a number
        (PortType.NUMBER, "3"),
        (PortType.BOOLEAN, 1),
        (PortType.OBJECT, []),
        (PortType.LIST, {}),
        (PortType.BINARY, "bytes-as-str"),
        (PortType.JSON, object()),
        (PortType.SECRET_REFERENCE, ""),
    ],
)
def test_invalid_port_values(port_type: PortType, bad: Any) -> None:
    port = NodeInput(name="p", port_type=port_type)
    with pytest.raises(NodeValidationError):
        validate_port_value(port, bad)


def test_required_input_missing() -> None:
    with pytest.raises(NodeValidationError, match="missing required input"):
        UpperCaseNode.definition.validate_inputs({})


def test_optional_input_may_be_omitted() -> None:
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.opt", name="N", description="D", version="1.0.0"
        ),
        inputs=(NodeInput(name="maybe", port_type=PortType.STRING, required=False),),
    )
    definition.validate_inputs({})


def test_nullable_port_accepts_none() -> None:
    port = NodeInput(name="p", port_type=PortType.STRING, nullable=True)
    validate_port_value(port, None)
    with pytest.raises(NodeValidationError):
        validate_port_value(NodeInput(name="p", port_type=PortType.STRING), None)


def test_multiple_port_requires_list_of_typed_items() -> None:
    port = NodeInput(name="p", port_type=PortType.STRING, multiple=True)
    validate_port_value(port, ["a", "b"])
    with pytest.raises(NodeValidationError):
        validate_port_value(port, "not-a-list")
    with pytest.raises(NodeValidationError):
        validate_port_value(port, ["a", 1])


def test_unknown_values_rejected() -> None:
    with pytest.raises(NodeValidationError, match="unknown input ports"):
        UpperCaseNode.definition.validate_inputs({"text": "x", "nope": 1})


def test_output_validation() -> None:
    UpperCaseNode.definition.validate_outputs({"upper": "X"})
    with pytest.raises(NodeValidationError):
        UpperCaseNode.definition.validate_outputs({"upper": 42})
    with pytest.raises(NodeValidationError):
        UpperCaseNode.definition.validate_outputs({})  # outputs always produced


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_config_schema_must_be_object() -> None:
    with pytest.raises(NodeContractError):
        NodeConfigSchema({"type": "array"})


def test_config_required_and_unknown_keys() -> None:
    schema = NodeConfigSchema(
        {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        }
    )
    schema.validate_config({"url": "https://x"})
    with pytest.raises(NodeConfigurationError, match="missing required"):
        schema.validate_config({})
    with pytest.raises(NodeConfigurationError, match="unknown config key"):
        schema.validate_config({"url": "https://x", "extra": 1})
    with pytest.raises(NodeConfigurationError, match="must be string"):
        schema.validate_config({"url": 42})


def test_config_nested_objects_and_enum_free_primitives() -> None:
    schema = NodeConfigSchema(
        {
            "type": "object",
            "properties": {
                "retries": {"type": "integer"},
                "opts": {
                    "type": "object",
                    "properties": {"deep": {"type": "boolean"}},
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        }
    )
    schema.validate_config({"retries": 3, "opts": {"deep": True}, "tags": ["a"]})
    with pytest.raises(NodeConfigurationError):
        schema.validate_config({"retries": True})  # bool is not an integer
    with pytest.raises(NodeConfigurationError):
        schema.validate_config({"tags": ["a", 1]})
    with pytest.raises(NodeConfigurationError):
        schema.validate_config({"opts": {"deep": "yes"}})


# ---------------------------------------------------------------------------
# Execution through the standardized contract
# ---------------------------------------------------------------------------


def test_node_executes_via_contract() -> None:
    result = asyncio.run(UpperCaseNode().execute(make_context()))
    assert result == NodeResult(outputs={"upper": "HELLO"})
    UpperCaseNode.definition.validate_outputs(result.outputs)


def test_node_honours_validated_config() -> None:
    result = asyncio.run(
        UpperCaseNode().execute(
            make_context(inputs={"text": "  hi "}, config={"strip": True})
        )
    )
    assert result.outputs == {"upper": "HI"}


def test_invalid_config_fails_inside_contract_shape() -> None:
    with pytest.raises(NodeConfigurationError):
        asyncio.run(UpperCaseNode().execute(make_context(config={"unknown": True})))


def test_node_error_carries_kind_and_retryable() -> None:
    with pytest.raises(NodeError) as excinfo:
        asyncio.run(
            FailOnDemandNode().execute(make_context(inputs={"kind": "transient"}))
        )
    assert excinfo.value.kind is NodeErrorKind.TRANSIENT
    assert excinfo.value.retryable is True
    assert excinfo.value.code == "NODE_EXECUTION_ERROR"
    with pytest.raises(NodeError) as excinfo2:
        asyncio.run(
            FailOnDemandNode().execute(make_context(inputs={"kind": "permanent"}))
        )
    assert excinfo2.value.retryable is False


def test_deterministic_contract_behavior() -> None:
    """Same input -> same validation outcome, every time."""
    for _ in range(3):
        UpperCaseNode.definition.validate_inputs({"text": "x"})
        with pytest.raises(NodeValidationError):
            UpperCaseNode.definition.validate_inputs({"text": 1})
