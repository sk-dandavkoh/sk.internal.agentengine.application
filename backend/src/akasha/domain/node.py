"""Node contract: the formal boundary between the Engine and a Node.

A Node is a well-defined executable component (architecture §8/§9), not
arbitrary code. Everything a node type declares about itself lives in an
immutable ``NodeDefinition``; everything the runtime hands to a running node
lives in a ``NodeExecutionContext``; everything a node produces is a
``NodeResult``. Errors cross the boundary only as ``NodeError``.

This module is pure domain: stdlib only, no registry, no runtime, no FastAPI.
Node implementations and the Node Registry live in akasha/nodes (built-ins)
or plugins (Phase 17) — deliberately not here.
"""

import json
import logging
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from akasha.domain.credentials import (
    is_credential_reference,
    plaintext_secret_locations,
)
from akasha.domain.primitives import SEMVER_RE, validate_name
from akasha.errors import (
    AkashaError,
    NodeConfigurationError,
    NodeContractError,
    NodeValidationError,
)

if TYPE_CHECKING:  # type-only: akasha.domain.ai imports this module
    from akasha.domain.ai import LLMClient

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
    # May end an attempt with ``NodeWait`` (outside input, e.g. a person's
    # answer). Only these nodes may; they cannot be agent tools, because a
    # tool call has no way to suspend the agent's attempt.
    waits_for_input: bool = False


# ``format`` of a string holding a credential reference (``credential:<name>``)
# instead of a secret: enforced by validation, and the hint forms use to offer
# a credential picker rather than a text field (architecture §32).
CREDENTIAL_REFERENCE_FORMAT = "credential-reference"

# ``format`` of a string naming an LLM provider: a hint only, so forms offer
# the providers this installation configured (GET /system/llm-providers).
# Not enforced by validation: the configured set is deployment state, not
# part of the node contract; an unknown provider fails when the node runs.
LLM_PROVIDER_FORMAT = "llm-provider"


@dataclass(frozen=True)
class ConfigIssue:
    """One config violation. ``path`` locates it (keys and list indexes, empty
    for the config itself); ``message`` says what is wrong there. Values are
    never echoed, so an issue is safe to show next to a form field."""

    path: tuple[str | int, ...]
    message: str

    @property
    def location(self) -> str:
        """``config.tools[0].name``: the path as messages have always said it."""
        return "config" + "".join(
            f"[{p}]" if isinstance(p, int) else f".{p}" for p in self.path
        )


@dataclass(frozen=True)
class NodeConfigSchema:
    """Machine-readable configuration schema, usable by backend validation,
    frontend forms and workflow import alike (architecture §3.5).

    Expressed as a JSON-Schema object; validation intentionally supports only
    the structural subset checked by ``issues``. ``title``, ``description``
    and ``default`` are annotations for forms; ``default`` documents what the
    node does when the key is absent (validation never inserts it).
    """

    json_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def __post_init__(self) -> None:
        if self.json_schema.get("type") != "object":
            raise NodeContractError("config schema must be a JSON-Schema object")
        properties = self.json_schema.get("properties", {})
        if not isinstance(properties, dict):
            raise NodeContractError("config schema 'properties' must be an object")
        if not isinstance(self.json_schema.get("required", []), list):
            raise NodeContractError("config schema 'required' must be a list")
        # A form advertises the default; one the schema itself rejects would
        # make the form lie about what a valid configuration is.
        for key, prop in properties.items():
            if isinstance(prop, dict) and "default" in prop:
                if found := _issues(prop["default"], prop, (key,)):
                    raise NodeContractError(
                        f"default of config key {key!r} {found[0].message}"
                    )

    def validate_config(self, config: Mapping[str, Any]) -> None:
        """Reject configs violating the schema (the first issue is reported)."""
        if found := self.issues(config):
            first = found[0]
            raise NodeConfigurationError(
                f"{first.location} {first.message}", details={"path": first.location}
            )

    def issues(self, config: Mapping[str, Any]) -> list[ConfigIssue]:
        """Every violation of the schema's supported subset.

        ponytail: enforced keywords are ``type`` (a name or a list such as
        ``["string", "null"]``), ``required``, ``properties``,
        ``additionalProperties`` (``false`` or a schema), ``items``, ``enum``,
        ``minimum``, ``maximum``, ``minLength``, ``maxItems``, ``pattern`` and
        ``format: credential-reference`` — a subset of JSON Schema, applied at
        every depth. Upgrade path: adopt the ``jsonschema`` package when other
        formats or combinators (oneOf/anyOf) become requirements.
        """
        return _issues(config, self.json_schema, ())


def json_equal(a: Any, b: Any) -> bool:
    """Equality with JSON semantics: ``1 == 1.0``, but booleans are not
    numbers (Python's ``True == 1`` would make ``true`` match ``1``)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(json_equal, a, b))
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    return type(a) is type(b) and a == b


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": _is_number,
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, Mapping),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def _issues(
    value: Any, schema: Mapping[str, Any], path: tuple[str | int, ...]
) -> list[ConfigIssue]:
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not types or not all(isinstance(t, str) and t in _JSON_TYPES for t in types):
            location = ConfigIssue(path, "").location
            raise NodeContractError(
                f"unsupported config schema type: {expected!r} at {location}"
            )
        if not any(_JSON_TYPES[t](value) for t in types):
            # Nothing below applies to a value of the wrong type.
            return [ConfigIssue(path, f"must be {' or '.join(types)}")]
    found = [ConfigIssue(path, m) for m in _constraint_messages(value, schema)]
    if isinstance(value, Mapping):
        props: Mapping[str, Any] = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        found += [
            ConfigIssue((*path, key), "is required")
            for key in schema.get("required", [])
            if key not in value
        ]
        for key, item in value.items():
            if key in props:
                found += _issues(item, props[key], (*path, key))
            elif extra is False:
                found.append(ConfigIssue((*path, key), "is not allowed"))
            elif isinstance(extra, Mapping):
                found += _issues(item, extra, (*path, key))
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for i, item in enumerate(value):
            found += _issues(item, schema["items"], (*path, i))
    return found


def _constraint_messages(value: Any, schema: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    if "enum" in schema and not any(json_equal(value, c) for c in schema["enum"]):
        found.append("is not one of the allowed values")
    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            found.append(f"must be <= {schema['maximum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            found.append(f"must have at least {schema['minLength']} characters")
        # Patterns come from node authors (trusted code), never from users.
        if "pattern" in schema and not re.search(schema["pattern"], value):
            found.append("does not match the required format")
        is_reference = schema.get("format") == CREDENTIAL_REFERENCE_FORMAT
        if is_reference and not is_credential_reference(value):
            found.append("must be a credential reference (credential:<name>)")
    if isinstance(value, list) and len(value) > schema.get("maxItems", len(value)):
        found.append(f"must have at most {schema['maxItems']} items")
    return found


@dataclass(frozen=True)
class NodeDefinition:
    """Immutable, complete declaration of a node type."""

    metadata: NodeMetadata
    inputs: tuple[NodeInput, ...] = ()
    outputs: tuple[NodeOutput, ...] = ()
    config_schema: NodeConfigSchema = field(default_factory=NodeConfigSchema)
    capabilities: NodeCapabilities = field(default_factory=NodeCapabilities)
    # Names the node may pass to ``context.metrics`` / ``context.events``.
    # Declared up front so metric labels and event names stay a closed,
    # reviewable set (AGENTS §41) — never derived from workflow data.
    metrics: tuple[str, ...] = ()
    events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for port_in in self.inputs:
            _check_port(port_in)
        for port_out in self.outputs:
            _check_port(port_out)
        _check_unique((p.name for p in self.inputs), "input")
        _check_unique((p.name for p in self.outputs), "output")
        for kind, names in (("metric", self.metrics), ("event", self.events)):
            for name in names:
                if not _PORT_NAME_RE.match(name):
                    raise NodeContractError(f"malformed {kind} name: {name!r}")
            _check_unique(names, kind)

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

    def config_issues(self, config: Mapping[str, Any]) -> list[ConfigIssue]:
        """Everything publish would reject in ``config``, per location:
        plaintext secrets first, then schema violations."""
        secrets = [
            ConfigIssue(path, "contains a secret value; use a credential reference")
            for path in plaintext_secret_locations(config)
        ]
        return secrets + self.config_schema.issues(config)

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
            # ports must be present — a successful node produces all of them —
            # except for control-flow nodes, where an omitted output is an
            # inactive branch: its consumers are skipped by the runtime.
            required = (
                port.required
                if isinstance(port, NodeInput)
                else not self.capabilities.is_control_flow
            )
            if port.name not in values:
                if required:
                    raise NodeValidationError(
                        f"missing required {direction}: {port.name}",
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
    reference becomes a value at execution time, never earlier. Only
    references in the node's own config resolve (``NodeError`` otherwise)."""

    async def resolve(self, reference: str) -> str: ...


def _undeclared(kind: str, name: str) -> NodeContractError:
    return NodeContractError(
        f"{kind} {name!r} is not declared in the node definition",
        details={kind: name},
    )


class NodeMetrics:
    """Counters of one node attempt. Only names declared in
    ``NodeDefinition.metrics`` are accepted; the runtime exports the totals
    as ``akasha_node_metric_total{node_type, metric}`` when the attempt ends.
    """

    def __init__(self, declared: Iterable[str] = ()) -> None:
        self._declared = frozenset(declared)
        self.totals: dict[str, float] = {}

    def increment(self, name: str, amount: float = 1.0) -> None:
        if name not in self._declared:
            raise _undeclared("metric", name)
        if not _is_number(amount) or amount < 0 or not math.isfinite(amount):
            raise NodeContractError(
                f"metric {name!r} increment must be a finite number >= 0"
            )
        self.totals[name] = self.totals.get(name, 0.0) + amount


# Resource limit (AGENTS §43): every emitted name becomes a persisted event.
MAX_EVENTS_PER_ATTEMPT = 100


class NodeEvents:
    """Named events of one node attempt, persisted by the runtime as
    ``node.emitted`` events (payload: ``name``, ``attempt``) when the attempt
    ends. Deliberately name-only: event payloads never carry workflow data
    (see ``akasha.domain.event``)."""

    def __init__(self, declared: Iterable[str] = ()) -> None:
        self._declared = frozenset(declared)
        self.emitted: list[str] = []

    def emit(self, name: str) -> None:
        if name not in self._declared:
            raise _undeclared("event", name)
        if len(self.emitted) >= MAX_EVENTS_PER_ATTEMPT:
            raise NodeContractError(
                f"a node attempt may emit at most {MAX_EVENTS_PER_ATTEMPT} events"
            )
        self.emitted.append(name)


@dataclass(frozen=True)
class NodeExecutionContext:
    """Everything a node is allowed to see while it runs (§21).

    Nodes never touch global application state; ids pin the execution to one
    immutable WorkflowVersion snapshot. ``is_cancelled`` is cooperative:
    nodes doing long work must poll it.
    """

    execution_id: str
    node_execution_id: str
    node_instance_id: str
    workflow_id: str
    workflow_version: str
    config: Mapping[str, Any]
    inputs: Mapping[str, Any]
    logger: logging.Logger
    credentials: CredentialResolver | None = None
    is_cancelled: Callable[[], bool] = lambda: False
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)
    # The payload the execution was started with (manual/API/trigger input).
    # Read by entry nodes such as core.input; other nodes use their ports.
    execution_input: Mapping[str, Any] = field(default_factory=dict)
    metrics: NodeMetrics = field(default_factory=NodeMetrics)
    events: NodeEvents = field(default_factory=NodeEvents)
    # Set on the attempt after a ``NodeWait`` was answered or expired:
    # ``{"response": <validated JSON object> | None, "responded_by": <user
    # id> | None, "timed_out": bool}``. None on every ordinary attempt.
    resume: Mapping[str, Any] | None = None
    # Engine services, the same for built-in and plugin nodes.
    services: "NodeServices" = field(default_factory=lambda: NodeServices())
    # Operator settings of the plugin providing this node (``settings_for``).
    settings: Mapping[str, Any] = field(default_factory=dict)

    async def resolve_credential(self, reference: str) -> str:
        """The secret behind a credential reference from this node's config.

        Raises ``NodeError`` (CONFIGURATION) when the engine runs without a
        credential store, the reference is not in the config, or is unknown.
        """
        if self.credentials is None:
            raise NodeError(
                "credential references cannot be resolved in this engine",
                kind=NodeErrorKind.CONFIGURATION,
                code="CREDENTIALS_UNAVAILABLE",
            )
        return await self.credentials.resolve(reference)


@dataclass(frozen=True)
class NodeResult:
    """Success payload: values keyed by declared output port name.

    There is no failure result: failures cross the contract as ``NodeError``,
    keeping the result type free of error-state ambiguity.
    """

    outputs: Mapping[str, Any]


@dataclass(frozen=True)
class NodeWait:
    """An attempt that needs outside input before the node can produce
    outputs (architecture §9: a node may return a waiting state).

    The runtime parks the node WAITING and, once nothing else can run, the
    execution WAITING — persisted, owned by no worker, no process held. A
    response must satisfy ``response_schema`` (the config-schema subset, so
    forms render it like node config); it reaches the node's next attempt as
    ``NodeExecutionContext.resume``. After ``timeout`` the next attempt runs
    with ``resume["timed_out"]`` instead. Only nodes declaring
    ``capabilities.waits_for_input`` may return it.
    """

    prompt: str
    response_schema: NodeConfigSchema
    timeout: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise NodeContractError("a wait prompt must not be blank")
        if self.timeout is not None and self.timeout <= timedelta(0):
            raise NodeContractError("a wait timeout must be positive")


class NodeErrorKind(StrEnum):
    """Stable error classification for retry/timeout handling (§39)."""

    VALIDATION = auto()
    CONFIGURATION = auto()
    TRANSIENT = auto()  # e.g. rate limit, flaky network
    PERMANENT = auto()  # retrying cannot help
    TIMEOUT = auto()
    CANCELLATION = auto()


RETRYABLE_ERROR_KINDS = frozenset({NodeErrorKind.TRANSIENT, NodeErrorKind.TIMEOUT})


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
        code: str | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.kind = kind
        self.retryable = kind in RETRYABLE_ERROR_KINDS
        if code is not None:  # a node-specific stable code, e.g. HTTP_STATUS
            self.code = code


class Node(ABC):
    """Base contract every node type implements (architecture §33 shape).

    Subclasses declare a ``definition`` class attribute and implement only
    ``execute``. The runtime — never the node — owns execution state
    transitions.
    """

    definition: ClassVar[NodeDefinition]

    @abstractmethod
    async def execute(self, context: NodeExecutionContext) -> "NodeResult | NodeWait":
        """``NodeWait`` only for nodes declaring ``waits_for_input``."""


class NodeFactory(Protocol):
    """What the runtime needs to run a node type: its definition and a way
    to create a fresh instance per attempt. A ``Node`` subclass is one: nodes
    are constructed without arguments and get everything else from their
    ``NodeExecutionContext``.
    """

    @property
    def definition(self) -> NodeDefinition: ...

    def __call__(self) -> Node: ...


@dataclass(frozen=True)
class NetworkPolicy:
    """The operator's outbound policy for user-configured destinations,
    checked with ``check_destination``. ``allowed_hosts``: host names/IP
    literals the operator trusts even though they resolve to private
    addresses (e.g. one internal API), compared case-insensitively."""

    allow_private_networks: bool = False
    allowed_hosts: frozenset[str] = frozenset()


BUILTIN_PLUGIN_ID = "core"


@dataclass(frozen=True)
class NodeServices:
    """What the engine offers every node, built-in or plugin (ADR 0011).

    ``llm``: the engine's LLM client (None: no LLM configured). ``network``:
    the outbound policy. ``workspace_dir``: root of the per-workflow file
    areas (None: not configured); nodes reach it through ``Workspace``,
    never directly. ``nodes``: the node catalog, for nodes that run other
    nodes (core.agent). ``settings``: operator settings per plugin id
    (``core`` for the built-ins); never secrets, which are credentials.
    ``owners``: node type id → plugin id, for ``settings_for``.
    """

    llm: "LLMClient | None" = None
    network: NetworkPolicy = NetworkPolicy()
    workspace_dir: Path | None = None
    nodes: Mapping[str, NodeFactory] = field(default_factory=dict)
    settings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    owners: Mapping[str, str] = field(default_factory=dict)

    def settings_for(self, node_id: str) -> Mapping[str, Any]:
        """The settings of the plugin providing ``node_id``. Looked up by
        owner, not by prefix: plugin ids may contain dots."""
        owner = self.owners.get(node_id)
        if owner is None and node_id.startswith(f"{BUILTIN_PLUGIN_ID}."):
            owner = BUILTIN_PLUGIN_ID
        return {} if owner is None else self.settings.get(owner, {})


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
