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

from akasha.domain.ai import (
    CostEstimate,
    FinishReason,
    LLMClient,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from akasha.domain.credentials import credential_references
from akasha.domain.node import (
    CREDENTIAL_REFERENCE_FORMAT,
    LLM_PROVIDER_FORMAT,
    MAX_EVENTS_PER_ATTEMPT,
    NO_DEFAULT,
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
    NodeFactory,
    NodeInput,
    NodeMetadata,
    NodeMetrics,
    NodeOutput,
    NodeResult,
    NodeServices,
    NodeWait,
    PortType,
    check_node_contract,
    json_equal,
)
from akasha.errors import (
    NodeConfigurationError,
    NodeContractError,
    NodeValidationError,
)
from akasha.security.network import NetworkPolicy, check_destination
from akasha.security.paths import Workspace
from akasha.security.redaction import mask_known_secrets, redact_text

# 1.1.0: ConfigIssue / NodeConfigSchema.issues, CREDENTIAL_REFERENCE_FORMAT,
# nullable type lists and additionalProperties schemas (additive).
# 1.2.0: NodeWait, NodeCapabilities.waits_for_input, context.resume (additive).
# 1.3.0: NodeServices / context.services / context.settings: the engine's
# LLM client, network policy, workspace, node catalog and operator settings
# reach every node through its context (ADR 0011); the LLM, network,
# workspace and redaction names below (additive).
SDK_VERSION: Final = "1.3.0"

__all__ = [
    "CREDENTIAL_REFERENCE_FORMAT",
    "LLM_PROVIDER_FORMAT",
    "MAX_EVENTS_PER_ATTEMPT",
    "NO_DEFAULT",
    "SDK_VERSION",
    "ConfigIssue",
    "CostEstimate",
    "CredentialResolver",
    "FinishReason",
    "LLMClient",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "NetworkPolicy",
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
    "NodeFactory",
    "NodeInput",
    "NodeMetadata",
    "NodeMetrics",
    "NodeOutput",
    "NodeResult",
    "NodeServices",
    "NodeValidationError",
    "NodeWait",
    "PortType",
    "ProviderError",
    "ProviderErrorKind",
    "Role",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "Workspace",
    "check_destination",
    "check_node_contract",
    "credential_references",
    "json_equal",
    "mask_known_secrets",
    "redact_text",
]
