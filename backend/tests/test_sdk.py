"""Python Node SDK (roadmap Phase 16): public surface, boundary, telemetry
limits and the ``run_node`` harness's failure paths.

The happy path of a real SDK node is ``examples/hmac_signer`` (its own
tests) and ``tests/integration/test_sdk_node_runtime.py`` (inside the
engine).
"""

import ast
import math
from pathlib import Path
from typing import Any

import pytest

import akasha.sdk
import akasha.sdk.testing
from akasha.domain.primitives import SEMVER_RE
from akasha.sdk import (
    MAX_EVENTS_PER_ATTEMPT,
    SDK_VERSION,
    Node,
    NodeContractError,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeEvents,
    NodeExecutionContext,
    NodeMetadata,
    NodeMetrics,
    NodeOutput,
    NodeResult,
    NodeValidationError,
    PortType,
)
from akasha.sdk.testing import run_node

BACKEND = Path(__file__).parent.parent

# The SDK compatibility contract: removing or renaming any of these is a
# breaking change and needs a major SDK_VERSION bump (docs/sdk.md).
PUBLIC_API = {
    "CREDENTIAL_REFERENCE_FORMAT",
    "MAX_EVENTS_PER_ATTEMPT",
    "ConfigIssue",
    "SDK_VERSION",
    "CredentialResolver",
    "Node",
    "NodeCapabilities",
    "NodeConfigSchema",
    "NodeConfigurationError",
    "NodeContractError",
    "NodeDefinition",
    "NodeError",
    "NodeErrorKind",
    "NodeEvents",
    "NodeExecutionContext",
    "NodeInput",
    "NodeMetadata",
    "NodeMetrics",
    "NodeOutput",
    "NodeResult",
    "NodeValidationError",
    "NodeWait",
    "PortType",
    "check_node_contract",
}


def test_public_surface_is_stable() -> None:
    assert set(akasha.sdk.__all__) == PUBLIC_API
    assert set(akasha.sdk.testing.__all__) == {"NodeRun", "run_node"}
    for module in (akasha.sdk, akasha.sdk.testing):
        for name in module.__all__:
            assert getattr(module, name) is not None


def test_sdk_version_is_semver() -> None:
    assert SEMVER_RE.match(SDK_VERSION)


def test_examples_import_only_the_sdk() -> None:
    """The acceptance criterion as a guard: example nodes never reach into
    engine internals."""
    violations = []
    for path in (BACKEND / "examples").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                if name.startswith("akasha") and not (
                    name == "akasha.sdk" or name.startswith("akasha.sdk.")
                ):
                    violations.append(f"{path.relative_to(BACKEND)}: {name}")
    assert not violations


# -- declared metrics / events ----------------------------------------------


def definition(**kwargs: Any) -> NodeDefinition:
    return NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.sdk", name="n", description="SDK test node.", version="1.0.0"
        ),
        outputs=(NodeOutput("out", PortType.STRING),),
        **kwargs,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"metrics": ("Bad-Name",)},
        {"metrics": ("a", "a")},
        {"events": ("",)},
        {"events": ("done", "done")},
    ],
)
def test_telemetry_names_are_validated(kwargs: dict[str, Any]) -> None:
    with pytest.raises(NodeContractError):
        definition(**kwargs)


def test_metrics_accumulate_declared_counters_only() -> None:
    metrics = NodeMetrics(("items",))
    metrics.increment("items")
    metrics.increment("items", 2.5)
    assert metrics.totals == {"items": 3.5}
    with pytest.raises(NodeContractError):
        metrics.increment("user_supplied_label")


@pytest.mark.parametrize("amount", [-1, math.nan, math.inf, True, "1"])
def test_metric_increments_must_be_finite_and_non_negative(amount: Any) -> None:
    with pytest.raises(NodeContractError):
        NodeMetrics(("items",)).increment("items", amount)


def test_events_are_declared_and_capped_per_attempt() -> None:
    events = NodeEvents(("tick",))
    with pytest.raises(NodeContractError):
        events.emit("undeclared")
    for _ in range(MAX_EVENTS_PER_ATTEMPT):
        events.emit("tick")
    with pytest.raises(NodeContractError):
        events.emit("tick")
    assert len(events.emitted) == MAX_EVENTS_PER_ATTEMPT


# -- run_node harness: engine checks, engine order -----------------------------


class Echo(Node):
    definition = definition(metrics=("calls",), events=("called",))

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        context.metrics.increment("calls")
        context.events.emit("called")
        return NodeResult({"out": context.config.get("value", "x")})


async def test_run_node_reports_outputs_and_telemetry() -> None:
    run = await run_node(Echo(), config={"value": "hi"})
    assert run.outputs == {"out": "hi"}
    assert run.metrics == {"calls": 1}
    assert run.events == ["called"]


async def test_run_node_rejects_contract_violations() -> None:
    class NoDefinition(Node):
        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult({})

    with pytest.raises(NodeContractError):
        await run_node(NoDefinition())


async def test_run_node_validates_outputs() -> None:
    class Wrong(Echo):
        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult({"out": 1})

    class NotAResult(Echo):
        async def execute(self, context: NodeExecutionContext) -> Any:
            return {"out": "x"}

    for node in (Wrong(), NotAResult()):
        with pytest.raises(NodeValidationError):
            await run_node(node)


async def test_credentials_resolve_only_references_in_the_config() -> None:
    class Sneaky(Echo):
        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult({"out": await context.resolve_credential("credential:b")})

    secrets = {"credential:a": "sa", "credential:b": "sb"}
    with pytest.raises(NodeError) as caught:
        await run_node(Sneaky(), config={"key": "credential:a"}, credentials=secrets)
    assert caught.value.code == "CREDENTIAL_NOT_REFERENCED"
    assert caught.value.kind is NodeErrorKind.CONFIGURATION

    run = await run_node(Sneaky(), config={"key": "credential:b"}, credentials=secrets)
    assert run.outputs == {"out": "sb"}
