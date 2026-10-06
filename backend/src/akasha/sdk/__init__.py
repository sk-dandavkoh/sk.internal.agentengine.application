"""Akasha Python Node SDK — the supported surface for custom Nodes.

Custom node code imports from ``akasha.sdk`` (and ``akasha.sdk.testing`` in
its tests) only. Everything else under ``akasha`` is engine-internal and may
change without notice; the names below follow ``SDK_VERSION`` (semver: a
breaking change to any of them bumps the major version).

A node is a ``Node`` subclass with a ``definition`` and an async
``execute``::

    class Greet(Node):
        definition = NodeDefinition(
            metadata=NodeMetadata(
                node_id="acme.greet", name="Greet",
                description="Greets someone.", version="1.0.0",
            ),
            inputs=(NodeInput("name", PortType.STRING),),
            outputs=(NodeOutput("greeting", PortType.STRING),),
        )

        async def execute(self, context: NodeExecutionContext) -> NodeResult:
            return NodeResult({"greeting": f"Hello, {context.inputs['name']}"})

What the engine guarantees around ``execute`` — config validated at publish,
inputs before and outputs after the call, errors classified, secrets
redacted, timeouts and cancellation enforced — is documented in
``docs/sdk.md``. Custom nodes are trusted code running in the engine
process: the SDK is an API boundary, not a sandbox.
"""

from typing import Final

from akasha.domain.node import (
    CREDENTIAL_REFERENCE_FORMAT,
    MAX_EVENTS_PER_ATTEMPT,
    ConfigIssue,
    CredentialResolver,
    Node,
    NodeCapabilities,
    NodeConfigSchema,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeEvents,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeMetrics,
    NodeOutput,
    NodeResult,
    NodeWait,
    PortType,
    check_node_contract,
)
from akasha.errors import (
    NodeConfigurationError,
    NodeContractError,
    NodeValidationError,
)

# 1.1.0: ConfigIssue / NodeConfigSchema.issues, CREDENTIAL_REFERENCE_FORMAT,
# nullable type lists and additionalProperties schemas (additive).
# 1.2.0: NodeWait, NodeCapabilities.waits_for_input, context.resume (additive).
SDK_VERSION: Final = "1.2.0"

__all__ = [
    "CREDENTIAL_REFERENCE_FORMAT",
    "MAX_EVENTS_PER_ATTEMPT",
    "SDK_VERSION",
    "ConfigIssue",
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
]
