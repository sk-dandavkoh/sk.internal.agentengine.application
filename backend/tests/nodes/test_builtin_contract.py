"""Shared Node Contract suite over every built-in node (AGENTS §28/§58),
plus the settings → registry wiring of the composition root."""

from pathlib import Path

import pytest

from akasha.bootstrap import create_container, create_node_services
from akasha.config import Settings
from akasha.domain.node import (
    CREDENTIAL_REFERENCE_FORMAT,
    NodeDefinition,
    NodeError,
    check_node_contract,
)
from akasha.domain.primitives import SEMVER_RE
from akasha.errors import NodeConfigurationError
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.command import CommandNode
from akasha.nodes.files import FileWriteNode
from akasha.nodes.http import HttpNode, WebSearchNode
from akasha.nodes.registry import NodeAlreadyRegisteredError, NodeRegistration
from akasha.nodes.settings import CoreSettings
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
    # Core node portfolio (docs/reports/backend/core-node-portfolio.md):
    # human-in-the-loop, data shaping, databases, browser automation.
    "core.human",
    "core.transform",
    "core.database",
    "core.browser",
    # ADR 0012: the model operates the browser itself.
    "core.browser_agent",
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
def test_every_port_and_setting_is_described(definition: NodeDefinition) -> None:
    """The editor explains ports and settings only with these texts; an
    undescribed one leaves users guessing what to connect or enter."""
    for port in definition.inputs:
        assert port.description.strip(), f"input {port.name}"
    for out in definition.outputs:
        assert out.description.strip(), f"output {out.name}"
    for key, prop in definition.config_schema.json_schema["properties"].items():
        assert str(prop.get("description", "")).strip(), f"setting {key}"


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

    assert ids("is_control_flow") == {"core.human", "core.if", "core.switch"}
    assert ids("waits_for_input") == {"core.human"}
    # core.agent: its tools may have side effects. core.database: writes;
    # core.browser / core.browser_agent: clicks may submit forms.
    assert ids("has_side_effects") == {
        "core.agent",
        "core.browser",
        "core.browser_agent",
        "core.command",
        "core.database",
        "core.file_write",
        "core.http",
        "core.smtp",
    }
    # SMTP's blocking send cannot be interrupted (documented ceiling); file
    # I/O is short, bounded by the file size limit.
    assert ids("supports_cancellation") == {
        "core.agent",
        "core.browser",
        "core.browser_agent",
        "core.command",
        "core.database",
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
    strict = create_node_services(Settings(), llm=None)
    with pytest.raises(NodeError, match="not a public address"):
        await run_node(HttpNode(), config=config, services=strict)
    by_host = create_node_services(
        Settings(network_allowed_hosts=["127.0.0.1"]), llm=None
    )
    result = await run_node(HttpNode(), config=config, services=by_host)
    assert result.outputs["status"] == 200
    assert http_server.state.requests  # really reached the local server


async def test_web_search_url_setting_reaches_the_node(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    settings = Settings.model_validate({"web_search_url": http_server.base_url})
    services = create_node_services(settings, llm=None)
    result = await run_node(WebSearchNode(), inputs={"query": "q"}, services=services)
    assert result.outputs == {"results": []}


def test_container_wires_its_ai_service_into_the_runtime() -> None:
    container = create_container(Settings(environment="test"))
    services = create_node_services(container.settings, llm=container.ai)
    assert services.llm is container.ai


async def test_workspace_and_command_settings_reach_the_nodes(
    run_node: RunNode, tmp_path: Path
) -> None:
    settings = Settings(workspace_dir=tmp_path, command_allowed=["echo"])
    services = create_node_services(settings, llm=None)
    written = await run_node(
        FileWriteNode(), inputs={"path": "a.txt", "content": "hi"}, services=services
    )
    assert written.outputs == {"path": "a.txt", "size": 2}
    echoed = await run_node(
        CommandNode(),
        config={"executable": "echo"},
        inputs={"args": ["hello"]},
        services=services,
    )
    assert echoed.outputs["stdout"] == "hello\n"


def test_plugin_settings_reach_the_owning_plugin_only() -> None:
    settings = Settings.model_validate(
        {"plugin_settings": {"community.web": {"endpoint": "http://intranet"}}}
    )
    # Plugin ids may contain dots: ownership, not the id prefix, decides.
    owners = {"community.web.fetch": "community.web", "community.other.x": "x"}
    services = create_node_services(settings, llm=None, owners=owners)
    assert services.settings_for("community.web.fetch") == {
        "endpoint": "http://intranet"
    }
    assert services.settings_for("community.other.x") == {}
    assert "endpoint" not in services.settings_for("core.http")
    assert services.settings_for("unowned.node") == {}


def test_core_settings_are_typed() -> None:
    services = create_node_services(Settings(command_allowed=["echo"]), llm=None)
    assert CoreSettings(**services.settings_for("core.command")).command_allowed == (
        "echo",
    )
    typo: dict[str, object] = {"command_alowed": ("sh",)}
    with pytest.raises(TypeError):
        CoreSettings(**typo)  # type: ignore[arg-type]  # fails, never ignored


def test_plugin_settings_cannot_configure_core() -> None:
    with pytest.raises(ValueError, match="core"):
        Settings.model_validate(
            {"plugin_settings": {"core": {"command_allowed": ["sh"]}}}
        )


def test_workspace_dir_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="directory"):
        Settings(workspace_dir=tmp_path / "missing")


def test_invalid_web_search_url_fails_fast() -> None:
    with pytest.raises(ValueError, match="URL"):
        Settings.model_validate({"web_search_url": "not a url"})
