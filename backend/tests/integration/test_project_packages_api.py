"""GET /projects/{id}/export and POST /projects/import through the real HTTP
stack and PostgreSQL (AGENTS §20): content types, status codes and the
structured error body for every rejection."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.packages import ProjectPackageService
from akasha.application.project_package import MAX_PACKAGE_BYTES
from akasha.application.projects import ProjectService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.node import NodeDefinition
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from tests.application.test_project_package import read
from tests.integration.conftest import SOURCE, AuthKit, make_auth_kit
from tests.integration.test_projects_api import (
    assert_error,
    create,
    put_draft,
    valid_draft,
)

pytestmark = pytest.mark.usefixtures("clean_db")

ZIP = {"content-type": "application/zip"}


def client_for(
    database: Database,
    definitions: dict[str, NodeDefinition],
    headers: dict[str, str],
) -> httpx.AsyncClient:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    settings = Settings(environment="test", log_level="CRITICAL")
    container = Container(
        settings=settings,
        database=database,
        projects=ProjectService(uow, definitions),
        packages=ProjectPackageService(uow, definitions),
        **make_auth_kit(database).container_fields(),
    )
    app = create_app(settings=settings, container=container)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
    )


@pytest.fixture
async def admin(auth_kit: AuthKit) -> dict[str, str]:
    return await auth_kit.admin_headers()


@pytest.fixture
async def api(
    database: Database,
    node_definitions: dict[str, NodeDefinition],
    admin: dict[str, str],
) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(database, node_definitions, admin) as client:
        yield client


async def exported(api: httpx.AsyncClient, **params: Any) -> tuple[str, bytes]:
    project = await create(api, "exported")
    await put_draft(api, project["id"], valid_draft(), revision=project["revision"])
    if params.get("version"):
        response = await api.post(f"/api/v1/projects/{project['id']}/publish")
        assert response.status_code == 201, response.text
    response = await api.get(f"/api/v1/projects/{project['id']}/export", params=params)
    assert response.status_code == 200, response.text
    return project["id"], response.content


async def test_export_draft_and_version(api: httpx.AsyncClient) -> None:
    project_id, _ = await exported(api)
    response = await api.get(f"/api/v1/projects/{project_id}/export")
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{project_id}-draft.akasha.zip"'
    )
    assert read(response.content).name == "demo"  # renamed by put_draft

    _, data = await exported(api, version=1)
    assert read(data).source_project_version == 1


async def test_export_errors(api: httpx.AsyncClient) -> None:
    project = await create(api)
    url = f"/api/v1/projects/{project['id']}/export"
    assert_error(
        await api.get(url, params={"version": 3}), 404, "PROJECT_VERSION_NOT_FOUND"
    )
    assert_error(await api.get(url, params={"version": 0}), 422, "VALIDATION_ERROR")
    assert_error(
        await api.get("/api/v1/projects/proj_" + "0" * 32 + "/export"),
        404,
        "PROJECT_NOT_FOUND",
    )


async def test_import_creates_an_unpublished_project(api: httpx.AsyncClient) -> None:
    source_id, data = await exported(api, version=1)
    response = await api.post("/api/v1/projects/import", content=data, headers=ZIP)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["project"]["id"] != source_id
    assert body["project"]["status"] == "draft"
    assert body["project"]["current_version"] == 0
    assert body["project"]["files"] == []
    assert body["validation"] == {"valid": True, "issues": []}
    assert body["missing_credentials"] == []
    source = (await api.get(f"/api/v1/projects/{source_id}")).json()
    assert body["project"]["draft"] == source["draft"]

    publish = await api.post(f"/api/v1/projects/{body['project']['id']}/publish")
    assert publish.status_code == 201, publish.text

    # Ids inside the graph are project-scoped: the same package imports
    # again as a second, independent project.
    again = await api.post("/api/v1/projects/import", content=data, headers=ZIP)
    assert again.status_code == 201, again.text
    assert again.json()["project"]["id"] not in (source_id, body["project"]["id"])
    publish = await api.post(
        f"/api/v1/projects/{again.json()['project']['id']}/publish"
    )
    assert publish.status_code == 201, publish.text


async def test_import_requires_the_zip_media_type(api: httpx.AsyncClient) -> None:
    _, data = await exported(api)
    for headers in ({}, {"content-type": "application/json"}):
        body = assert_error(
            await api.post("/api/v1/projects/import", content=data, headers=headers),
            415,
            "UNSUPPORTED_MEDIA_TYPE",
        )
        assert "application/zip" in body["message"]


async def test_import_rejects_malformed_packages(api: httpx.AsyncClient) -> None:
    body = assert_error(
        await api.post("/api/v1/projects/import", content=b"PK nope", headers=ZIP),
        422,
        "PROJECT_PACKAGE_INVALID",
    )
    assert "Traceback" not in body["message"]
    assert (await api.get("/api/v1/projects")).json()["items"] == []


async def test_import_rejects_oversized_uploads_while_streaming(
    api: httpx.AsyncClient,
) -> None:
    assert_error(
        await api.post(
            "/api/v1/projects/import",
            content=b"\0" * (MAX_PACKAGE_BYTES + 1),
            headers=ZIP,
        ),
        413,
        "PROJECT_PACKAGE_TOO_LARGE",
    )


async def test_import_rejects_incompatible_packages(
    api: httpx.AsyncClient, database: Database, admin: dict[str, str]
) -> None:
    _, data = await exported(api)
    # An installation without test.sink.
    async with client_for(database, {"test.source": SOURCE}, admin) as target:
        body = assert_error(
            await target.post("/api/v1/projects/import", content=data, headers=ZIP),
            422,
            "PROJECT_PACKAGE_INCOMPATIBLE",
        )
    codes = {issue["code"] for issue in body["details"]["issues"]}
    assert codes == {"missing_dependency"}


async def test_openapi_documents_the_package_endpoints(api: httpx.AsyncClient) -> None:
    paths = (await api.get("/openapi.json")).json()["paths"]
    body = paths["/api/v1/projects/import"]["post"]["requestBody"]
    assert body["content"]["application/zip"]["schema"]["format"] == "binary"
    export = paths["/api/v1/projects/{project_id}/export"]["get"]
    assert "application/zip" in export["responses"]["200"]["content"]
