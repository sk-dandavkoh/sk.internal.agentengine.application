"""/api/v1/plugins through the real HTTP stack: listing, upload install,
enable/disable and removal, admin only (architecture §22, §36, ADR 0001).
Real packages in a temporary plugin directory; sessions and projects in
PostgreSQL."""

import io
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.projects import ProjectService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.project import Dependency, ProjectDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.registry import NodeRegistry
from akasha.plugins.registry import PluginRegistry
from tests.integration.fixtures import AuthKit, make_auth_kit
from tests.integration.test_cli import echo_graph, set_draft
from tests.integration.test_triggers_api import assert_error
from tests.test_plugins import SAMPLE, write_plugin

pytestmark = pytest.mark.usefixtures("clean_db")

ZIP = {"content-type": "application/zip"}


def zipped(package: Path, *, top_level: bool = True) -> bytes:
    """The package as `zip -r` makes it: inside one top-level directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = path.relative_to(package).as_posix()
                name = f"{package.name}/{relative}" if top_level else relative
                archive.write(path, name)
    return buffer.getvalue()


@asynccontextmanager
async def _client(
    database: Database, plugins: PluginRegistry | None, *, http_install: bool = True
) -> AsyncIterator[tuple[httpx.AsyncClient, AuthKit, dict[str, str]]]:
    settings = Settings(
        environment="test", log_level="CRITICAL", plugins_http_install=http_install
    )
    kit = make_auth_kit(database)
    container = Container(
        settings=settings,
        nodes=NodeRegistry(),
        database=database,
        plugins=plugins,
        projects=ProjectService(
            lambda: SqlAlchemyUnitOfWork(database),
            create_builtin_registry().definitions,
        ),
        **kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    admin = await kit.admin_headers()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, kit, admin


def _installed(tmp_path: Path) -> Path:
    path = tmp_path / "installed"
    path.mkdir()
    return path


def _registry(directory: Path) -> PluginRegistry:
    registry = PluginRegistry(directory, NodeRegistry())
    registry.load_installed()
    return registry


async def test_lists_installed_plugins_with_state_and_metadata(
    database: Database, tmp_path: Path
) -> None:
    PluginRegistry(tmp_path, NodeRegistry()).install(SAMPLE)
    # An unreadable package is listed as FAILED without metadata.
    (tmp_path / "broken").mkdir()
    registry = _registry(tmp_path)  # as at startup

    async with _client(database, registry, http_install=False) as (client, _, admin):
        response = await client.get("/api/v1/plugins", headers=admin)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "install_enabled": False,
        "items": [
            {
                "plugin_id": "broken",
                "state": "failed",
                "error": "PLUGIN_MANIFEST_INVALID",
                "metadata": None,
            },
            {
                "plugin_id": "text_tools",
                "state": "enabled",
                "error": None,
                "metadata": {
                    "name": "Text Tools",
                    "version": "1.0.0",
                    "description": "Small text transformations for workflows.",
                    "engine_compatibility": ">=0.1.0,<1.0.0",
                    "sdk_compatibility": ">=1.0.0,<2.0.0",
                    "dependencies": [],
                    "node_types": ["text_tools.slugify"],
                },
            },
        ],
    }


async def test_no_plugin_directory_lists_nothing_and_refuses_changes(
    database: Database,
) -> None:
    async with _client(database, None) as (client, _kit, admin):
        listed = await client.get("/api/v1/plugins", headers=admin)
        upload = await client.post(
            "/api/v1/plugins", content=b"x", headers={**admin, **ZIP}
        )
        enable = await client.post("/api/v1/plugins/x/enable", headers=admin)

    assert listed.json() == {"items": [], "install_enabled": False}
    assert_error(upload, 403, "PLUGIN_INSTALL_DISABLED")
    assert_error(enable, 500, "CONFIGURATION_ERROR")


async def test_installs_an_uploaded_package(database: Database, tmp_path: Path) -> None:
    installed = _installed(tmp_path)
    registry = _registry(installed)

    async with _client(database, registry) as (client, _kit, admin):
        response = await client.post(
            "/api/v1/plugins", content=zipped(SAMPLE), headers={**admin, **ZIP}
        )
        listed = await client.get("/api/v1/plugins", headers=admin)

    assert response.status_code == 201, response.text
    assert response.json()["plugin_id"] == "text_tools"
    assert response.json()["state"] == "enabled"
    assert listed.json()["install_enabled"] is True
    assert [p["plugin_id"] for p in listed.json()["items"]] == ["text_tools"]
    assert (installed / "text_tools" / "akasha-plugin.json").is_file()
    # A restart sees the same installation.
    assert _registry(installed).get("text_tools").state == "enabled"


async def test_package_at_the_archive_root_installs_too(
    database: Database, tmp_path: Path
) -> None:
    registry = _registry(_installed(tmp_path))
    package = write_plugin(tmp_path, "acme")

    async with _client(database, registry) as (client, _kit, admin):
        response = await client.post(
            "/api/v1/plugins",
            content=zipped(package, top_level=False),
            headers={**admin, **ZIP},
        )

    assert response.status_code == 201, response.text
    assert registry.get("acme").state == "enabled"


async def test_upload_disabled_by_default_is_refused_before_reading(
    database: Database, tmp_path: Path
) -> None:
    installed = _installed(tmp_path)
    async with _client(database, _registry(installed), http_install=False) as (
        client,
        _kit,
        admin,
    ):
        response = await client.post(
            "/api/v1/plugins", content=zipped(SAMPLE), headers={**admin, **ZIP}
        )

    assert_error(response, 403, "PLUGIN_INSTALL_DISABLED")
    assert response.json()["details"] == {"setting": "AKASHA_PLUGINS_HTTP_INSTALL"}
    assert list(installed.iterdir()) == []


@pytest.mark.parametrize(
    ("body", "headers", "status", "code"),
    [
        (b"not a zip", ZIP, 422, "PLUGIN_PACKAGE_INVALID"),
        (b"{}", {"content-type": "application/json"}, 415, "UNSUPPORTED_MEDIA_TYPE"),
    ],
)
async def test_rejects_malformed_uploads(
    database: Database,
    tmp_path: Path,
    body: bytes,
    headers: dict[str, str],
    status: int,
    code: str,
) -> None:
    installed = _installed(tmp_path)
    async with _client(database, _registry(installed)) as (client, _kit, admin):
        response = await client.post(
            "/api/v1/plugins", content=body, headers={**admin, **headers}
        )

    assert_error(response, status, code)
    assert list(installed.iterdir()) == []


async def test_invalid_manifest_is_rejected_with_field_locations(
    database: Database, tmp_path: Path
) -> None:
    installed = _installed(tmp_path)
    package = write_plugin(tmp_path, "acme", surprise="<script>alert(1)</script>")

    async with _client(database, _registry(installed)) as (client, _kit, admin):
        response = await client.post(
            "/api/v1/plugins", content=zipped(package), headers={**admin, **ZIP}
        )

    assert_error(response, 422, "PLUGIN_MANIFEST_INVALID")
    assert response.json()["details"] == {"fields": ["surprise"]}
    # Manifest values never come back in the error.
    assert "<script>" not in response.text
    assert list(installed.iterdir()) == []


async def test_incompatible_package_is_rejected_with_issues(
    database: Database, tmp_path: Path
) -> None:
    installed = _installed(tmp_path)
    package = write_plugin(tmp_path, "acme", engine=">=99.0.0")

    async with _client(database, _registry(installed)) as (client, _kit, admin):
        response = await client.post(
            "/api/v1/plugins", content=zipped(package), headers={**admin, **ZIP}
        )

    assert_error(response, 409, "PLUGIN_INCOMPATIBLE")
    issues = response.json()["details"]["issues"]
    assert len(issues) == 1
    assert issues[0].startswith("engine ")
    assert list(installed.iterdir()) == []


async def test_installing_twice_conflicts(database: Database, tmp_path: Path) -> None:
    registry = _registry(_installed(tmp_path))
    async with _client(database, registry) as (client, _kit, admin):
        first = await client.post(
            "/api/v1/plugins", content=zipped(SAMPLE), headers={**admin, **ZIP}
        )
        second = await client.post(
            "/api/v1/plugins", content=zipped(SAMPLE), headers={**admin, **ZIP}
        )

    assert first.status_code == 201, first.text
    assert_error(second, 409, "PLUGIN_ALREADY_INSTALLED")


async def test_disable_and_enable_round_trip(
    database: Database, tmp_path: Path
) -> None:
    installed = _installed(tmp_path)
    registry = _registry(installed)
    registry.install(write_plugin(tmp_path, "acme"))

    async with _client(database, registry) as (client, _kit, admin):
        disabled = await client.post("/api/v1/plugins/acme/disable", headers=admin)
        again = await client.post("/api/v1/plugins/acme/disable", headers=admin)
        restarted = _registry(installed).get("acme").state
        enabled = await client.post("/api/v1/plugins/acme/enable", headers=admin)
        twice = await client.post("/api/v1/plugins/acme/enable", headers=admin)
        missing = await client.post("/api/v1/plugins/nope/enable", headers=admin)

    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["state"] == "disabled"
    assert_error(again, 409, "INVALID_STATE_TRANSITION")
    assert restarted == "disabled"  # persisted in state.json
    assert enabled.json()["state"] == "enabled"
    assert_error(twice, 409, "INVALID_STATE_TRANSITION")
    assert_error(missing, 404, "PLUGIN_NOT_FOUND")


async def test_disable_is_blocked_while_enabled_plugins_depend_on_it(
    database: Database, tmp_path: Path
) -> None:
    registry = _registry(_installed(tmp_path))
    registry.install(write_plugin(tmp_path, "base"))
    registry.install(
        write_plugin(tmp_path, "addon", dependencies=[{"kind": "plugin", "id": "base"}])
    )

    async with _client(database, registry) as (client, _kit, admin):
        response = await client.post("/api/v1/plugins/base/disable", headers=admin)

    assert_error(response, 409, "PLUGIN_IN_USE")
    assert response.json()["details"]["dependents"] == ["addon"]
    assert registry.get("base").state == "enabled"


async def test_remove_is_blocked_while_projects_use_it_unless_forced(
    database: Database, tmp_path: Path
) -> None:
    installed = _installed(tmp_path)
    registry = _registry(installed)
    registry.install(write_plugin(tmp_path, "acme"))
    projects = ProjectService(
        lambda: SqlAlchemyUnitOfWork(database), create_builtin_registry().definitions
    )
    user = await projects.create_project("uses acme")
    await set_draft(
        projects,
        user.id,
        ProjectDraft(
            graph=echo_graph(), dependencies=[Dependency("plugin", "acme", "*")]
        ),
    )

    async with _client(database, registry) as (client, _kit, admin):
        blocked = await client.delete("/api/v1/plugins/acme", headers=admin)
        forced = await client.delete(
            "/api/v1/plugins/acme", params={"force": "true"}, headers=admin
        )
        gone = await client.delete("/api/v1/plugins/acme", headers=admin)

    assert_error(blocked, 409, "PLUGIN_IN_USE")
    assert blocked.json()["details"]["projects"] == [user.id]
    assert forced.status_code == 200, forced.text
    assert forced.json() == {"plugin_id": "acme", "affected_projects": [user.id]}
    assert not (installed / "acme").exists()
    assert_error(gone, 404, "PLUGIN_NOT_FOUND")


async def test_removes_an_unused_plugin(database: Database, tmp_path: Path) -> None:
    installed = _installed(tmp_path)
    registry = _registry(installed)
    registry.install(write_plugin(tmp_path, "acme"))

    async with _client(database, registry) as (client, _kit, admin):
        response = await client.delete("/api/v1/plugins/acme", headers=admin)
        listed = await client.get("/api/v1/plugins", headers=admin)

    assert response.json() == {"plugin_id": "acme", "affected_projects": []}
    assert listed.json()["items"] == []


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/api/v1/plugins", {}),
        ("POST", "/api/v1/plugins", {"content": b"x", "headers": ZIP}),
        ("POST", "/api/v1/plugins/acme/enable", {}),
        ("POST", "/api/v1/plugins/acme/disable", {}),
        ("DELETE", "/api/v1/plugins/acme", {}),
    ],
)
async def test_every_operation_requires_an_admin_session(
    database: Database,
    tmp_path: Path,
    method: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    installed = _installed(tmp_path)
    registry = _registry(installed)
    registry.install(write_plugin(tmp_path, "acme"))

    async with _client(database, registry) as (client, kit, admin):
        headers = kwargs.pop("headers", {})
        anonymous = await client.request(method, path, headers=headers, **kwargs)
        member = await kit.member_headers(admin, "member")
        forbidden = await client.request(
            method, path, headers={**headers, **member}, **kwargs
        )

    assert_error(anonymous, 401, "AUTHENTICATION_REQUIRED")
    assert_error(forbidden, 403, "PERMISSION_DENIED")
    assert registry.get("acme").state == "enabled"
    assert (installed / "acme").is_dir()
