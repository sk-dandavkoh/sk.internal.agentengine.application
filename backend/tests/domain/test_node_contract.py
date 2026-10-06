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
    ConfigIssue,
    Node,
    NodeCapabilities,
    NodeConfigSchema,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeFactory,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
    check_node_contract,
    json_equal,
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
        workflow_id="wf_" + "a" * 32,
        workflow_version="1.0.0",
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
    with pytest.raises(NodeConfigurationError, match=r"config\.url is required"):
        schema.validate_config({})
    with pytest.raises(NodeConfigurationError, match=r"config\.extra is not allowed"):
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


# ---------------------------------------------------------------------------
# Phase 09 contract additions: schema keywords, control-flow outputs,
# JSON equality, factories
# ---------------------------------------------------------------------------


def test_config_constraint_keywords() -> None:
    schema = NodeConfigSchema(
        {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST"]},
                "seconds": {"type": "number", "minimum": 0, "maximum": 10},
                "url": {"type": "string", "minLength": 1, "pattern": "^https?://"},
                "cases": {"type": "array", "maxItems": 2},
                "flag": {"enum": [True]},
            },
        }
    )
    schema.validate_config(
        {"method": "GET", "seconds": 10, "url": "http://x", "cases": [1, 2]}
    )
    bad: list[dict[str, Any]] = [
        {"method": "get"},
        {"seconds": -0.1},
        {"seconds": 10.5},
        {"url": ""},
        {"url": "file:///etc/passwd"},
        {"cases": [1, 2, 3]},
        {"flag": 1},  # 1 is not the boolean true
    ]
    for config in bad:
        with pytest.raises(NodeConfigurationError):
            schema.validate_config(config)


def test_config_issues_collects_every_violation_with_its_path() -> None:
    schema = NodeConfigSchema(
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "pattern": "^https?://"},
                "tools": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string", "minLength": 1}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["method"],
            "additionalProperties": False,
        }
    )
    issues = schema.issues(
        {"url": "ftp://x", "tools": [{"name": ""}, {"extra": 1}], "typo": 1}
    )
    assert issues == [
        ConfigIssue(("method",), "is required"),
        ConfigIssue(("url",), "does not match the required format"),
        ConfigIssue(("tools", 0, "name"), "must have at least 1 characters"),
        ConfigIssue(("tools", 1, "name"), "is required"),
        ConfigIssue(("tools", 1, "extra"), "is not allowed"),
        ConfigIssue(("typo",), "is not allowed"),
    ]
    assert issues[2].location == "config.tools[0].name"
    with pytest.raises(NodeConfigurationError, match=r"^config\.method is required$"):
        schema.validate_config({"url": "ftp://x"})


def test_config_issues_never_echo_values() -> None:
    schema = NodeConfigSchema(
        {"type": "object", "properties": {"mode": {"enum": ["a", "b"]}}}
    )
    (issue,) = schema.issues({"mode": "sk-live-secret-value"})
    assert "sk-live" not in issue.message


def test_nullable_type_lists() -> None:
    schema = NodeConfigSchema(
        {
            "type": "object",
            "properties": {
                "limit": {"type": ["integer", "null"], "minimum": 1},
            },
        }
    )
    assert schema.issues({"limit": None}) == []
    assert schema.issues({"limit": 3}) == []
    assert schema.issues({"limit": 0}) == [ConfigIssue(("limit",), "must be >= 1")]
    assert schema.issues({"limit": "3"}) == [
        ConfigIssue(("limit",), "must be integer or null")
    ]


@pytest.mark.parametrize("bad_type", [[], ["string", "date"], [["string"]], "date"])
def test_unsupported_types_are_contract_errors(bad_type: object) -> None:
    schema = NodeConfigSchema(
        {"type": "object", "properties": {"x": {"type": bad_type}}}
    )
    with pytest.raises(NodeContractError, match="unsupported config schema type"):
        schema.issues({"x": "v"})


def test_additional_properties_schema_checks_map_values() -> None:
    schema = NodeConfigSchema(
        {
            "type": "object",
            "properties": {
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                }
            },
        }
    )
    assert schema.issues({"headers": {"Accept": "text/plain"}}) == []
    assert schema.issues({"headers": {"X-Count": 3}}) == [
        ConfigIssue(("headers", "X-Count"), "must be string")
    ]


def test_credential_reference_format() -> None:
    schema = NodeConfigSchema(
        {
            "type": "object",
            "properties": {
                "credential": {"type": "string", "format": "credential-reference"}
            },
        }
    )
    assert schema.issues({"credential": "credential:smtp-main"}) == []
    for value in ("hunter2", "credential:", "credential:UPPER", "smtp-main"):
        (issue,) = schema.issues({"credential": value})
        assert issue.message == "must be a credential reference (credential:<name>)"


def test_annotations_are_not_validated_and_defaults_must_be_valid() -> None:
    NodeConfigSchema(
        {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["info", "debug"],
                    "default": "info",
                    "title": "Level",
                    "description": "Log level.",
                }
            },
        }
    )
    with pytest.raises(NodeContractError, match="default of config key 'level'"):
        NodeConfigSchema(
            {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["info"], "default": "loud"}
                },
            }
        )


def test_json_equal_is_strict_about_booleans() -> None:
    assert json_equal(1, 1.0)
    assert json_equal({"a": [1, "x"]}, {"a": [1.0, "x"]})
    assert not json_equal(True, 1)
    assert not json_equal(0, False)
    assert not json_equal([True], [1])
    assert not json_equal({"a": 1}, {"a": 1, "b": 2})
    assert not json_equal("1", 1)
    assert json_equal(None, None)


def _branching(*, control_flow: bool) -> NodeDefinition:
    return NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.branch", name="Branch", description="D", version="1.0.0"
        ),
        outputs=(NodeOutput("yes", PortType.JSON), NodeOutput("no", PortType.JSON)),
        capabilities=NodeCapabilities(is_control_flow=control_flow),
    )


def test_control_flow_node_may_omit_outputs() -> None:
    _branching(control_flow=True).validate_outputs({"yes": 1})
    _branching(control_flow=True).validate_outputs({})
    with pytest.raises(NodeValidationError, match="missing required output: no"):
        _branching(control_flow=False).validate_outputs({"yes": 1})
    with pytest.raises(NodeValidationError, match="unknown output"):
        _branching(control_flow=True).validate_outputs({"maybe": 1})


def test_node_class_is_a_node_factory() -> None:
    factory: NodeFactory = UpperCaseNode
    assert factory.definition is UpperCaseNode.definition
    assert isinstance(factory(), UpperCaseNode)
