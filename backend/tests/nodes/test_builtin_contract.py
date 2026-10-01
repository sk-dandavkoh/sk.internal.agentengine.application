"""Shared Node Contract suite over every built-in node (AGENTS §28/§58),
plus the settings → registry wiring of the composition root."""

import pytest

from akasha.bootstrap import create_container, create_node_registry
from akasha.config import Settings
from akasha.domain.node import NodeDefinition, NodeError, check_node_contract
from akasha.domain.primitives import SEMVER_RE
from akasha.errors import NodeConfigurationError
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.llm import LlmNode
from akasha.nodes.registry import NodeAlreadyRegisteredError, NodeRegistration
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
    # Phase 27: needed by the example projects (vision §58).
    "core.set",
    "core.pick",
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


def test_capabilities_are_declared_truthfully() -> None:
    def ids(flag: str) -> set[str]:
        return {d.node_id for d in BUILTINS if getattr(d.capabilities, flag)}

    assert ids("is_control_flow") == {"core.if", "core.switch"}
    assert ids("has_side_effects") == {"core.http", "core.smtp"}
    # SMTP's blocking send cannot be interrupted (documented ceiling).
    assert ids("supports_cancellation") == {
        "core.delay",
        "core.http",
        "core.llm",
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


def test_invalid_web_search_url_fails_fast() -> None:
    with pytest.raises(ValueError, match="URL"):
        Settings.model_validate({"web_search_url": "not a url"})
