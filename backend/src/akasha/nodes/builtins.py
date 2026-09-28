"""The built-in node set (roadmap Phase 09) and its registration.

Stateless nodes register as classes; nodes with deployment dependencies
(network policy, search endpoint) register with a factory closure. The
composition root calls ``create_builtin_registry`` once at startup.
"""

from akasha.nodes.control import IfNode, SwitchNode
from akasha.nodes.core import DelayNode, InputNode, LogNode, MergeNode, OutputNode
from akasha.nodes.http import DEFAULT_MAX_RESPONSE_BYTES, HttpNode, WebSearchNode
from akasha.nodes.registry import NodeRegistry
from akasha.nodes.smtp import SmtpNode
from akasha.security.network import NetworkPolicy


def create_builtin_registry(
    *,
    network: NetworkPolicy | None = None,
    web_search_url: str | None = None,
    http_max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> NodeRegistry:
    """A registry with every ``core.*`` node. ``network`` governs the
    user-configured destinations of core.http and core.smtp."""
    policy = network or NetworkPolicy()
    registry = NodeRegistry()
    for node_cls in (
        InputNode,
        OutputNode,
        LogNode,
        IfNode,
        SwitchNode,
        DelayNode,
        MergeNode,
    ):
        registry.register(node_cls)
    registry.register(
        HttpNode, lambda: HttpNode(policy, max_response_bytes=http_max_response_bytes)
    )
    registry.register(
        WebSearchNode,
        lambda: WebSearchNode(
            web_search_url, max_response_bytes=http_max_response_bytes
        ),
    )
    registry.register(SmtpNode, lambda: SmtpNode(policy))
    return registry
