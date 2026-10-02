"""GET /api/v1/plugins through the real HTTP stack: installed plugins with
their state, admin only (architecture §22). Real packages in a temporary
plugin directory; sessions in PostgreSQL."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from akasha.api.app import create_app
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.infrastructure.database import Database
from akasha.nodes.registry import NodeRegistry
from akasha.plugins.registry import PluginRegistry
from tests.integration.conftest import AuthKit, make_auth_kit
from tests.integration.test_triggers_api import assert_error
from tests.test_plugins import SAMPLE

pytestmark = pytest.mark.usefixtures("clean_db")


@asynccontextmanager
async def _client(
    database: Database, plugins: PluginRegistry | None
) -> AsyncIterator[tuple[httpx.AsyncClient, AuthKit, dict[str, str]]]:
    settings = Settings(environment="test", log_level="CRITICAL")
    kit = make_auth_kit(database)
    container = Container(
        settings=settings,
        nodes=NodeRegistry(),
        database=database,
        plugins=plugins,
        **kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    admin = await kit.admin_headers()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, kit, admin


async def test_lists_installed_plugins_with_state_and_metadata(
    database: Database, tmp_path: Path
) -> None:
    PluginRegistry(tmp_path, NodeRegistry()).install(SAMPLE)
    # An unreadable package is listed as FAILED without metadata.
    (tmp_path / "broken").mkdir()
    registry = PluginRegistry(tmp_path, NodeRegistry())  # as at startup
    registry.load_installed()

    async with _client(database, registry) as (client, _kit, admin):
        response = await client.get("/api/v1/plugins", headers=admin)

    assert response.status_code == 200, response.text
    assert response.json() == {
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
        ]
    }


async def test_no_plugin_directory_lists_nothing(database: Database) -> None:
    async with _client(database, None) as (client, _kit, admin):
        response = await client.get("/api/v1/plugins", headers=admin)

    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


async def test_requires_an_admin_session(database: Database) -> None:
    async with _client(database, None) as (client, kit, admin):
        anonymous = await client.get("/api/v1/plugins")
        member = await kit.member_headers(admin, "member")
        forbidden = await client.get("/api/v1/plugins", headers=member)

    assert_error(anonymous, 401, "AUTHENTICATION_REQUIRED")
    assert_error(forbidden, 403, "PERMISSION_DENIED")
