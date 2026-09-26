"""Node contract: the formal boundary between the Engine and a Node.

A Node is a well-defined executable component (architecture §8/§9), not
arbitrary code. Everything a node type declares about itself lives in an
immutable ``NodeDefinition``; everything the runtime hands to a running node
lives in a ``NodeExecutionContext``; everything a node produces is a
``NodeResult``. Errors cross the boundary only as ``NodeError``.

This module is pure domain: stdlib only, no registry, no runtime, no FastAPI.
Node implementations live in akasha/nodes (built-ins, Phase 09) or plugins
(Phase 17); the registry is Phase 09 — deliberately not here.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Any, ClassVar, Protocol

from akasha.domain.primitives import SEMVER_RE, validate_name
from akasha.errors import (
    AkashaError,
    NodeConfigurationError,
    NodeContractError,
    NodeValidationError,
)

# Node type ids are globally unambiguous, e.g. "core.llm", "plugin_name.node".
_NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_PORT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class _NoDefault:
    """Sentinel: the input declares no default (dataclasses.MISSING cannot be
    used as a field default because dataclass interprets it as 'no default')."""


NO_DEFAULT: Any = _NoDefault()


class PortType(StrEnum):
    """The supported value kinds a port can carry (backend/AGENTS.md §19)."""

    STRING = auto()
    NUMBER = auto()
    BOOLEAN = auto()
    JSON = auto()
    OBJECT = auto()
    LIST = auto()
    BINARY = auto()
    FILE = auto()
    ARTIFACT = auto()
    SECRET_REFERENCE = auto()


@dataclass(frozen=True)
class NodeInput:
    """One input port of a node definition.

    ``schema`` is an optional JSON-Schema fragment constraining structured
    values (OBJECT/LIST/JSON). A ``default`` implies the input may be left
    unconnected, so it is only legal on non-required inputs.
    """

    name: str
    port_type: PortType
    required: bool = True
    nullable: bool = False
    multiple: bool = False
    schema: dict[str, Any] | None = None
    default: Any = NO_DEFAULT
    description: str = ""


@dataclass(frozen=True)
class NodeOutput:
    """One output port. Outputs are always produced on success, so they have
    no ``required`` — only value-shape attributes."""

    name: str
    port_type: PortType
    nullable: bool = False
    multiple: bool = False
    schema: dict[str, Any] | None = None
    description: str = ""


@dataclass(frozen=True)
class NodeMetadata:
    """Human- and tooling-facing facts about a node type."""

    node_id: str  # "core.llm" — NOT the id of a NodeInstance
    name: str  # display name
    description: str
    version: str  # semver
    category: str = "utility"  # organizational only (architecture §8.1)
    documentation_url: str | None = None

    def __post_init__(self) -> None:
        if not _NODE_ID_RE.match(self.node_id):
            raise NodeContractError(f"malformed node id: {self.node_id!r}")
        validate_name(self.name)
        if not self.description.strip():
            raise NodeContractError("node description must not be blank")
        if not SEMVER_RE.match(self.version):
            raise NodeContractError(f"node version must be semver: {self.version!r}")
        validate_name(self.category)


@dataclass(frozen=True)
class NodeCapabilities:
    """What the runtime may expect from / must provide to the node."""

    supports_cancellation: bool = False
    has_side_effects: bool = False  # retry/idempotency relevance (§44/§46)
    is_control_flow: bool = False  # if/switch/loop-style runtime semantics
    streams_output: bool = False


@dataclass(frozen=True)
class NodeConfigSchema:
    """Machine-readable configuration schema, usable by backend validation,
    frontend forms and project import alike (architecture §3.5).

    Expressed as a JSON-Schema object; validation intentionally supports only
    the structural subset checked by ``validate_config``.
    """

    json_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def __post_init__(self) -> None:
        if self.json_schema.get("type") != "object":
            raise NodeContractError("config schema must be a JSON-Schema object")
        for fname in ("properties", "required", "additionalProperties"):
            if fname == "properties" and not isinstance(
                self.json_schema.get(fname, {}), dict
            ):
                raise NodeContractError("config schema 'properties' must be an object")
            if fname == "required" and not isinstance(
                self.json_schema.get(fname, []), list
            ):
                raise NodeContractError("config schema 'required' must be a list")

    def validate_config(self, config: Mapping[str, Any]) -> None:
        """Reject configs violating the schema's structural subset.

        ponytail: only ``type``, ``required`, ``properties`` (recursively) and
        ``additionalProperties`` are enforced — a subset of JSON Schema.
        Upgrade path: adopt the ``jsonschema`` package when formats, enums or
        combinators (oneOf/anyOf) become requirements.
        """
        props: dict[str, Any] = self.json_schema.get("properties", {})
        for key in self.json_schema.get("required", []):
            if key not in config:
                raise NodeConfigurationError(
                    f"missing required config key: {key}",
                    details={"key": key},
                )
        closed = self.json_schema.get("additionalProperties", True) is False
        for key, value in config.items():
            if key not in props:
                if closed:
                    raise NodeConfigurationError(
                        f"unknown config key: {key}",
                        details={"key": key},
                    )
                continue
            _check_json_type(value, props[key], f"config.{key}")


def _check_json_type(value: Any, schema: Mapping[str, Any], path: str) -> None:
    expected = schema.get("type")
    if expected is None:
        return
    ok = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
    }.get(expected)
    if ok is None:
        raise NodeContractError(
            f"unsupported config schema type: {expected!r} at {path}"
        )
    if not ok:
        raise NodeConfigurationError(
            f"{path} must be {expected}", details={"path": path, "expected": expected}
        )
    if expected == "object":
        for key, item in value.items():
            _check_json_type(
                item, schema.get("properties", {}).get(key, {}), f"{path}.{key}"
            )
    if expected == "array" and "items" in schema:
        for i, item in enumerate(value):
            _check_json_type(item, schema["items"], f"{path}[{i}]")


@dataclass(frozen=True)
class NodeDefinition:
    """Immutable, complete declaration of a node type."""

    metadata: NodeMetadata
    inputs: tuple[NodeInput, ...] = ()
    outputs: tuple[NodeOutput, ...] = ()
    config_schema: NodeConfigSchema = field(default_factory=NodeConfigSchema)
    capabilities: NodeCapabilities = field(default_factory=NodeCapabilities)

    def __post_init__(self) -> None:
        for port_in in self.inputs:
            _check_port(port_in)
        for port_out in self.outputs:
            _check_port(port_out)
        _check_unique((p.name for p in self.inputs), "input")
        _check_unique((p.name for p in self.outputs), "output")

    @property
    def node_id(self) -> str:
        return self.metadata.node_id

    @property
    def version(self) -> str:
        return self.metadata.version

    def input(self, name: str) -> NodeInput:
        return _find(self.inputs, name)

    def output(self, name: str) -> NodeOutput:
        return _find(self.outputs, name)

    def validate_config(self, config: Mapping[str, Any]) -> None:
        self.config_schema.validate_config(config)

    def validate_inputs(self, values: Mapping[str, Any]) -> None:
        self._validate_ports(self.inputs, values, "input")

    def validate_outputs(self, values: Mapping[str, Any]) -> None:
        self._validate_ports(self.outputs, values, "output")

    def _validate_ports(
        self,
        ports: tuple[NodeInput, ...] | tuple[NodeOutput, ...],
        values: Mapping[str, Any],
        direction: str,
    ) -> None:
        declared = {p.name for p in ports}
        unknown = set(values) - declared
        if unknown:
            raise NodeValidationError(
                f"unknown {direction} ports: {sorted(unknown)}",
                details={"ports": ",".join(sorted(unknown))},
            )
        for port in ports:
            # Inputs: only required ones must be present. Outputs: all declared
            # ports must be present — a successful node produces all of them.
            required = (
                port.required if isinstance(port, NodeInput) else direction == "output"
            )
            if port.name not in values:
                if required:
                    raise NodeValidationError(
                        f"missing required input: {port.name}",
                        details={"port": port.name},
                    )
                continue
            validate_port_value(port, values[port.name])


def _check_port(port: NodeInput | NodeOutput) -> None:
    if not _PORT_NAME_RE.match(port.name):
        raise NodeContractError(f"malformed port name: {port.name!r}")
    if port.schema is not None and port.port_type in (
        PortType.STRING,
        PortType.NUMBER,
        PortType.BOOLEAN,
    ):
        raise NodeContractError(
            f"scalar port {port.name!r} must not declare a value schema"
        )
    if isinstance(port, NodeInput) and not isinstance(port.default, _NoDefault):
        if port.required:
            raise NodeContractError(
                f"required input {port.name!r} must not declare a default"
            )
        try:
            validate_port_value(port, port.default)
        except NodeValidationError as exc:
            raise NodeContractError(
                f"default of input {port.name!r} violates its own port type"
            ) from exc


def _check_unique(names: Any, direction: str) -> None:
    seen: list[str] = []
    for name in names:
        if name in seen:
            raise NodeContractError(f"duplicate {direction} port: {name}")
        seen.append(name)


def _find[T: (NodeInput, NodeOutput)](ports: tuple[T, ...], name: str) -> T:
    for port in ports:
        if port.name == name:
            return port
    raise NodeContractError(f"{name!r} is not a declared port")


def validate_port_value(port: NodeInput | NodeOutput, value: Any) -> None:
    """Check one concrete runtime value against a port's declared shape."""
    if value is None:
        if port.nullable:
            return
        raise NodeValidationError(
            f"port {port.name!r} is not nullable", details={"port": port.name}
        )
    if port.multiple:
        if not isinstance(value, list):
            raise NodeValidationError(
                f"port {port.name!r} expects a list of {port.port_type}",
                details={"port": port.name},
            )
        for item in value:
            _check_single(port, item)
    else:
        _check_single(port, value)


def _check_single(port: NodeInput | NodeOutput, value: Any) -> None:
    t, name = port.port_type, port.name

    def fail(reason: str) -> None:
        raise NodeValidationError(
            f"port {name!r} {reason}: got {type(value).__name__}",
            details={"port": name},
        )

    match t:
        case PortType.STRING:
            if not isinstance(value, str):
                fail("expects a string")
        case PortType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, int | float):
                fail("expects a number")
        case PortType.BOOLEAN:
            if not isinstance(value, bool):
                fail("expects a boolean")
        case PortType.OBJECT:
            if not isinstance(value, dict):
                fail("expects an object")
        case PortType.LIST:
            if not isinstance(value, list):
                fail("expects a list")
        case PortType.BINARY:
            if not isinstance(value, bytes):
                fail("expects bytes")
        case PortType.JSON:
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                fail("expects a JSON-serializable value")
        case PortType.FILE | PortType.ARTIFACT | PortType.SECRET_REFERENCE:
            # Opaque references: a string id/URI resolved later by the artifact
            # resp. credential service. Never a plaintext secret or raw path
            # payload at the contract boundary.
            if not isinstance(value, str) or not value.strip():
                fail("expects a non-empty reference string")


class CredentialResolver(Protocol):
    """Late secret resolution (Phase 13). Config stores references only; the
    reference becomes a value at execution time, never earlier."""

    def resolve(self, reference: str) -> str: ...


@dataclass(frozen=True)
class NodeExecutionContext:
    """Everything a node is allowed to see while it runs (§21).

    Nodes never touch global application state; ids pin the execution to one
    immutable ProjectVersion snapshot. ``is_cancelled`` is cooperative:
    nodes doing long work must poll it.
    """

    execution_id: str
    node_execution_id: str
    node_instance_id: str
    project_id: str
    project_version: str
    config: Mapping[str, Any]
    inputs: Mapping[str, Any]
    logger: logging.Logger
    credentials: CredentialResolver | None = None
    is_cancelled: Callable[[], bool] = lambda: False
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeResult:
    """Success payload: values keyed by declared output port name.

    There is no failure result: failures cross the contract as ``NodeError``,
    keeping the result type free of error-state ambiguity.
    """

    outputs: Mapping[str, Any]


class NodeErrorKind(StrEnum):
    """Stable error classification for retry/timeout handling (§39)."""

    VALIDATION = auto()
    CONFIGURATION = auto()
    TRANSIENT = auto()  # e.g. rate limit, flaky network
    PERMANENT = auto()  # retrying cannot help
    TIMEOUT = auto()
    CANCELLATION = auto()


_RETRYABLE = frozenset({NodeErrorKind.TRANSIENT, NodeErrorKind.TIMEOUT})


class NodeError(AkashaError):
    """The only failure a node may surface. Retry semantics are derived from
    the kind, not left to individual nodes' wording."""

    code = "NODE_EXECUTION_ERROR"
    http_status = 500

    def __init__(
        self,
        message: str,
        *,
        kind: NodeErrorKind,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.kind = kind
        self.retryable = kind in _RETRYABLE


class Node(ABC):
    """Base contract every node type implements (architecture §33 shape).

    Subclasses declare a ``definition`` class attribute and implement only
    ``execute``. The runtime — never the node — owns execution state
    transitions.
    """

    definition: ClassVar[NodeDefinition]

    @abstractmethod
    async def execute(self, context: NodeExecutionContext) -> NodeResult: ...


def check_node_contract(node_cls: type[Node]) -> None:
    """Reusable contract check for built-in and custom node tests (§58).

    Definition-level invariants already fail at ``NodeDefinition``
    construction time (frozen dataclass); this additionally proves the class
    is wired correctly: a definition exists and ``execute`` is implemented.
    """
    if not isinstance(getattr(node_cls, "definition", None), NodeDefinition):
        raise NodeContractError(
            f"{node_cls.__name__} must declare a NodeDefinition as 'definition'"
        )
    if node_cls.execute is Node.execute:
        raise NodeContractError(f"{node_cls.__name__} must implement execute()")
