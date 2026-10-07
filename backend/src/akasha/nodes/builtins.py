"""The built-in node set (roadmap Phase 09) and its registration.

Every built-in registers as a plain class, exactly like a plugin node: what
a node needs from the deployment (LLM client, network policy, workspace,
operator settings) reaches it through ``context.services`` and
``context.settings`` (ADR 0011). The composition root calls
``create_builtin_registry`` once at startup.
"""

from akasha.nodes.agent import AgentNode
from akasha.nodes.browser import BrowserNode
from akasha.nodes.command import CommandNode
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
from akasha.nodes.files import FileListNode, FileReadNode, FileWriteNode
from akasha.nodes.http import HttpNode, WebFetchNode, WebSearchNode
from akasha.nodes.human import HumanNode
from akasha.nodes.llm import LlmNode
from akasha.nodes.registry import NodeRegistry
from akasha.nodes.smtp import SmtpNode
from akasha.nodes.transform import TransformNode
from akasha.sdk import Node

BUILTIN_NODES: tuple[type[Node], ...] = (
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
    HttpNode,
    WebSearchNode,
    WebFetchNode,
    SmtpNode,
    LlmNode,
    FileReadNode,
    FileWriteNode,
    FileListNode,
    DatabaseNode,
    BrowserNode,
    CommandNode,
    AgentNode,
)


def create_builtin_registry() -> NodeRegistry:
    """A registry with every ``core.*`` node."""
    registry = NodeRegistry()
    for node_cls in BUILTIN_NODES:
        registry.register(node_cls)
    return registry
