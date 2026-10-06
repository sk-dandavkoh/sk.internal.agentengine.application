"""The built-in node set (roadmap Phase 09) and its registration.

Stateless nodes register as classes; nodes with deployment dependencies
(network policy, search endpoint) register with a factory closure. The
composition root calls ``create_builtin_registry`` once at startup.
"""

from akasha.domain.ai import LLMClient
from akasha.nodes.agent import AgentNode
from akasha.nodes.browser import BrowserNode
from akasha.nodes.command import DEFAULT_MAX_OUTPUT_BYTES, CommandNode
from akasha.nodes.control import IfNode, SwitchNode
from akasha.nodes.core import (
    DelayNode,
    InputNode,
    LogNode,
    MergeNode,
    OutputNode,
    PickNode,
    SetNode,
)
from akasha.nodes.database import DatabaseNode
from akasha.nodes.files import (
    DEFAULT_MAX_FILE_BYTES,
    FileListNode,
    FileReadNode,
    FileWriteNode,
)
from akasha.nodes.http import (
    DEFAULT_MAX_RESPONSE_BYTES,
    HttpNode,
    WebFetchNode,
    WebSearchNode,
)
from akasha.nodes.human import HumanNode
from akasha.nodes.llm import LlmNode
from akasha.nodes.registry import NodeRegistry
from akasha.nodes.smtp import SmtpNode
from akasha.nodes.transform import TransformNode
from akasha.security.network import NetworkPolicy
from akasha.security.paths import Workspace


def create_builtin_registry(
    *,
    network: NetworkPolicy | None = None,
    web_search_url: str | None = None,
    http_max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    llm: LLMClient | None = None,
    workspace: Workspace | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    allowed_commands: frozenset[str] = frozenset(),
    command_max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> NodeRegistry:
    """A registry with every ``core.*`` node. ``network`` governs the
    user-configured destinations of core.http, core.web_fetch, core.smtp,
    core.database (PostgreSQL) and core.browser; ``llm`` serves core.llm and
    core.agent (without it they fail with LLM_NOT_CONFIGURED); ``workspace``
    hosts the file nodes, SQLite databases, browser screenshots and
    core.command, which runs only ``allowed_commands``. core.agent offers
    any node of this registry as a tool — plugin nodes included."""
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
        SetNode,
        PickNode,
        HumanNode,
        TransformNode,
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
    registry.register(
        WebFetchNode,
        lambda: WebFetchNode(policy, max_response_bytes=http_max_response_bytes),
    )
    registry.register(SmtpNode, lambda: SmtpNode(policy))
    registry.register(LlmNode, lambda: LlmNode(llm))
    registry.register(
        FileReadNode, lambda: FileReadNode(workspace, max_file_bytes=max_file_bytes)
    )
    registry.register(
        FileWriteNode, lambda: FileWriteNode(workspace, max_file_bytes=max_file_bytes)
    )
    registry.register(
        FileListNode, lambda: FileListNode(workspace, max_file_bytes=max_file_bytes)
    )
    registry.register(DatabaseNode, lambda: DatabaseNode(workspace, policy))
    registry.register(
        BrowserNode,
        lambda: BrowserNode(
            policy,
            workspace,
            max_response_bytes=http_max_response_bytes,
            max_file_bytes=max_file_bytes,
        ),
    )
    registry.register(
        CommandNode,
        lambda: CommandNode(
            workspace,
            allowed_executables=allowed_commands,
            max_output_bytes=command_max_output_bytes,
        ),
    )
    # The live catalog view: tools resolve against whatever is registered
    # when the agent runs, the same set publish and the runtime see.
    registry.register(AgentNode, lambda: AgentNode(llm, registry.catalog))
    return registry
