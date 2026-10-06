"""MCP control plane units: configuration, fail-closed wiring, error
mapping, plugin version pinning rules, plugin packaging (no database; the
protocol and integration tests are tests/integration/test_mcp*.py)."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.application.plugins import PluginService
from akasha.config import Settings
from akasha.domain.graph import Graph, NodeInstance
from akasha.domain.workflow import Dependency, pinned_plugin_issues, plugins_in_use
from akasha.errors import AkashaError, DomainValidationError
from akasha.mcp.authoring import manifest_schema, scaffold
from akasha.mcp.server import error_payload
from akasha.nodes.registry import NodeRegistry
from akasha.observability.logging import configure_logging
from akasha.plugins.archive import pack_files, unpack_package
from akasha.plugins.registry import PluginRegistry


def test_mcp_is_off_by_default_and_code_needs_its_own_opt_in() -> None:
    settings = Settings()
    assert settings.mcp_enabled is False
    assert settings.mcp_plugin_code is False


def test_a_disabled_engine_builds_no_mcp_server(settings: Settings) -> None:
    app = create_app(settings=settings)
    assert app.state.mcp is None
    assert not [r for r in app.routes if getattr(r, "path", "") == "/mcp"]
    with TestClient(app) as client:
        assert client.post("/mcp", json={}).status_code == 404


def test_enabled_without_authentication_backing_fails_closed() -> None:
    """No database means no sessions: nobody gets in, not everybody."""
    app = create_app(settings=Settings(environment="test", mcp_enabled=True))
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "Bearer anything"}):
            response = client.post("/mcp", json={}, headers=headers)
            assert response.status_code == 500
            assert response.json()["code"] == "CONFIGURATION_ERROR"


def test_tool_errors_are_structured_redacted_and_classified() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    error = DomainValidationError(
        f"bad value {secret}", details={"field": "x", "value": secret}
    )
    payload = error_payload(error)
    assert payload["code"] == "DOMAIN_VALIDATION_ERROR"
    assert payload["retryable"] is False
    assert secret not in str(payload)

    class Stale(AkashaError):
        code = "CONCURRENT_MODIFICATION"

    assert error_payload(Stale("reload"))["retryable"] is True


def test_publish_pins_the_plugins_a_draft_uses() -> None:
    graph = Graph()
    graph.add_node(NodeInstance.create("acme.shout"))
    graph.add_node(NodeInstance.create("core.input"))
    used = plugins_in_use(
        graph,
        [Dependency("plugin", "beta"), Dependency("plugin", "absent")],
        {"acme.shout": "acme", "other.node": "other"},
        {"acme": "1.2.3", "beta": "0.1.0", "other": "9.9.9"},
    )
    # Owners of used node types and declared plugins; unused plugins and
    # plugins that are not enabled are not pinned.
    assert used == {"acme": "1.2.3", "beta": "0.1.0"}


def test_pins_are_exact() -> None:
    pinned = {"acme": "1.2.3"}
    assert pinned_plugin_issues(pinned, {"acme": "1.2.3"}) == ()
    assert [i.code.value for i in pinned_plugin_issues(pinned, {"acme": "1.2.4"})] == [
        "incompatible_dependency"
    ]
    assert [i.code.value for i in pinned_plugin_issues(pinned, {})] == [
        "missing_dependency"
    ]


def test_packing_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    files = scaffold("acme", "Acme", "shout", "Shouts.")
    data = pack_files(files)
    assert pack_files(dict(reversed(files.items()))) == data
    package = unpack_package(data, tmp_path)
    assert {
        p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file()
    } == set(files)


def test_the_scaffold_is_a_valid_package(tmp_path: Path) -> None:
    service = PluginService(PluginRegistry(tmp_path, NodeRegistry()), None)
    check = service.check_archive(pack_files(scaffold("acme", "A", "shout", "")))
    assert check.ok, check.issues
    assert check.metadata is not None
    assert [n.node_id for n in check.metadata.nodes] == ["acme.shout"]
    assert check.installed is None


def test_the_manifest_schema_is_the_validating_model() -> None:
    schema = manifest_schema()
    assert schema["additionalProperties"] is False
    assert {"manifest_version", "id", "components"} <= set(schema["required"])


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING"])
def test_sdk_frame_logging_never_goes_below_info(level: str) -> None:
    root = logging.getLogger()
    handlers, old = list(root.handlers), root.level
    try:
        configure_logging(level)
        assert logging.getLogger("mcp").level >= logging.INFO
        assert not logging.getLogger("mcp").isEnabledFor(logging.DEBUG)
    finally:
        root.handlers[:] = handlers
        root.setLevel(old)
        logging.getLogger("mcp").setLevel(logging.NOTSET)
