"""Shared Node Contract suite over every built-in node (AGENTS §28/§58),
plus the settings → registry wiring of the composition root."""

from pathlib import Path

import pytest

from akasha.bootstrap import create_container, create_node_registry
from akasha.config import Settings
from akasha.domain.node import (
    CREDENTIAL_REFERENCE_FORMAT,
    NodeDefinition,
    NodeError,
    check_node_contract,
)
from akasha.domain.primitives import SEMVER_RE
from akasha.errors import NodeConfigurationError
from akasha.nodes.agent import AgentNode
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.llm import LlmNode
from akasha.nodes.registry import NodeAlreadyRegisteredError, NodeRegistration
from akasha.security.redaction import is_sensitive_key
from tests.support import LocalHttpServer, RunNode

ROADMAP_NODES = {
    "core.input",
    "core.output",
    "core.log",
    "core.if",
    "core.switch",
    "core.delay",
    "core.merge",
    "core.http",
    "core.web_search",
    "core.smtp",
    "core.llm",
    # Phase 27: needed by the example workflows (vision §58).
    "core.set",
    "core.pick",
    # Agents (ADR 0005): the tool loop and the tools it needs.
    "core.agent",
    "core.web_fetch",
    "core.file_read",
    "core.file_write",
    "core.file_list",
    "core.command",
}
REGISTRY = create_builtin_registry()
BUILTINS = REGISTRY.list_nodes()


def test_registry_contains_exactly_the_roadmap_set() -> None:
    assert {d.node_id for d in BUILTINS} == ROADMAP_NODES
    assert [d.node_id for d in BUILTINS] == sorted(ROADMAP_NODES)


@pytest.mark.parametrize("definition", BUILTINS, ids=lambda d: d.node_id)
def test_builtin_contract(definition: NodeDefinition) -> None:
    registration = REGISTRY.get(definition.node_id)
    node = registration()
    check_node_contract(type(node))
    assert node.definition is definition
    assert registration() is not node  # fresh instance per attempt
    metadata = definition.metadata
    assert metadata.node_id.startswith("core.")
    assert SEMVER_RE.match(metadata.version)
    assert metadata.description.strip() and metadata.name.strip()
    assert definition.outputs, "every built-in produces something"
    # Closed schemas: a typo in a config key fails at publish, not at run.
    assert definition.config_schema.json_schema["additionalProperties"] is False
    with pytest.raises(NodeConfigurationError):
        definition.validate_config({"__typo__": 1})


@pytest.mark.parametrize("definition", BUILTINS, ids=lambda d: d.node_id)
def test_secret_config_keys_declare_credential_references(
    definition: NodeDefinition,
) -> None:
    """Forms render a credential picker only for the declared format, so a
    sensitive key without it would invite typing a secret (§35/§36)."""
    for key, prop in definition.config_schema.json_schema["properties"].items():
        if is_sensitive_key(key):
            leaf = prop.get("additionalProperties", prop)
            assert leaf.get("format") == CREDENTIAL_REFERENCE_FORMAT, key


def test_capabilities_are_declared_truthfully() -> None:
    def ids(flag: str) -> set[str]:
        return {d.node_id for d in BUILTINS if getattr(d.capabilities, flag)}

    assert ids("is_control_flow") == {"core.if", "core.switch"}
    # core.agent: its tools may have side effects.
    assert ids("has_side_effects") == {
        "core.agent",
        "core.command",
        "core.file_write",
        "core.http",
        "core.smtp",
    }
    # SMTP's blocking send cannot be interrupted (documented ceiling); file
    # I/O is short, bounded by the file size limit.
    assert ids("supports_cancellation") == {
        "core.agent",
        "core.command",
        "core.delay",
        "core.http",
        "core.llm",
        "core.web_fetch",
        "core.web_search",
    }


def test_builtins_can_register_only_once() -> None:
    registry = create_builtin_registry()
    registration = registry.get("core.http")
    assert isinstance(registration, NodeRegistration)
    with pytest.raises(NodeAlreadyRegisteredError):
        registry.register(type(registration()))


# -- composition root -------------------------------------------------------------


def test_container_exposes_the_builtin_registry() -> None:
    container = create_container(Settings(environment="test"))
    assert {d.node_id for d in container.nodes.list_nodes()} == ROADMAP_NODES


async def test_network_settings_reach_the_http_node(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    config = {"url": f"{http_server.base_url}/echo"}
    strict = create_node_registry(Settings()).get("core.http")()
    with pytest.raises(NodeError, match="not a public address"):
        await run_node(strict, config=config)
    by_host = create_node_registry(Settings(network_allowed_hosts=["127.0.0.1"])).get(
        "core.http"
    )()
    assert (await run_node(by_host, config=config)).outputs["status"] == 200
    assert http_server.state.requests  # really reached the local server


async def test_web_search_url_setting_reaches_the_node(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    settings = Settings.model_validate({"web_search_url": http_server.base_url})
    node = create_node_registry(settings).get("core.web_search")()
    result = await run_node(node, inputs={"query": "q"})
    assert result.outputs == {"results": []}


def test_container_wires_its_ai_service_into_core_llm() -> None:
    container = create_container(Settings(environment="test"))
    node = container.nodes.get("core.llm")()
    assert isinstance(node, LlmNode)
    assert node._llm is container.ai


def test_container_wires_ai_service_and_live_catalog_into_core_agent() -> None:
    container = create_container(Settings(environment="test"))
    node = container.nodes.get("core.agent")()
    assert isinstance(node, AgentNode)
    assert node._llm is container.ai
    assert node._catalog is container.nodes.catalog


async def test_workspace_and_command_settings_reach_the_nodes(
    run_node: RunNode, tmp_path: Path
) -> None:
    settings = Settings(workspace_dir=tmp_path, command_allowed=["echo"])
    registry = create_node_registry(settings)
    written = await run_node(
        registry.get("core.file_write")(), inputs={"path": "a.txt", "content": "hi"}
    )
    assert written.outputs == {"path": "a.txt", "size": 2}
    echoed = await run_node(
        registry.get("core.command")(),
        config={"executable": "echo"},
        inputs={"args": ["hello"]},
    )
    assert echoed.outputs["stdout"] == "hello\n"


def test_workspace_dir_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="directory"):
        Settings(workspace_dir=tmp_path / "missing")


def test_invalid_web_search_url_fails_fast() -> None:
    with pytest.raises(ValueError, match="URL"):
        Settings.model_validate({"web_search_url": "not a url"})
