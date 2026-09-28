"""/api/v1/projects — thin HTTP adapter over ``ProjectService``.

Handlers parse, delegate, serialize. No domain rules, no persistence here.

Authorization boundary: every project endpoint lives on this one router,
so Phase 20 secures them all by adding a single router-level dependency;
the service stays HTTP-free and gains a principal argument then.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from akasha.api.project_schemas import (
    CreateProjectRequest,
    ProjectDetail,
    ProjectPage,
    ProjectVersionDetail,
    ProjectVersionPage,
    PublishProjectRequest,
    UpdateProjectRequest,
    ValidationResponse,
)
from akasha.api.schemas import ErrorResponse
from akasha.application.projects import MAX_PAGE_SIZE, ProjectService
from akasha.errors import ConfigurationError


def _service(request: Request) -> ProjectService:
    service: ProjectService | None = request.app.state.container.projects
    if service is None:
        raise ConfigurationError("Project persistence is not configured.")
    return service


Service = Annotated[ProjectService, Depends(_service)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
VersionNumber = Annotated[int, Path(ge=1)]


def create_projects_router() -> APIRouter:
    # Declared so the OpenAPI contract (and generated clients) show the real
    # error body instead of FastAPI's default validation schema.
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 422, 500)
    }
    router = APIRouter(prefix="/projects", tags=["projects"], responses=errors)

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_project(body: CreateProjectRequest, svc: Service) -> ProjectDetail:
        project = await svc.create_project(body.name, description=body.description)
        return ProjectDetail.from_domain(project)

    @router.get("")
    async def list_projects(
        svc: Service, offset: Offset = 0, limit: Limit = 50
    ) -> ProjectPage:
        return ProjectPage.from_page(
            await svc.list_projects(offset=offset, limit=limit)
        )

    @router.get("/{project_id}")
    async def get_project(project_id: str, svc: Service) -> ProjectDetail:
        return ProjectDetail.from_domain(await svc.get_project(project_id))

    @router.put("/{project_id}")
    async def update_project(
        project_id: str, body: UpdateProjectRequest, svc: Service
    ) -> ProjectDetail:
        project = await svc.update_draft(
            project_id,
            expected_revision=body.expected_revision,
            name=body.name,
            draft=body.draft.to_domain(body.description),
        )
        return ProjectDetail.from_domain(project)

    @router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_project(project_id: str, svc: Service) -> Response:
        await svc.delete_project(project_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/{project_id}/validate")
    async def validate_project(project_id: str, svc: Service) -> ValidationResponse:
        return ValidationResponse.from_domain(await svc.validate_project(project_id))

    @router.post("/{project_id}/publish", status_code=status.HTTP_201_CREATED)
    async def publish_project(
        project_id: str, svc: Service, body: PublishProjectRequest | None = None
    ) -> ProjectVersionDetail:
        expected = body.expected_revision if body else None
        version = await svc.publish_project(project_id, expected_revision=expected)
        return ProjectVersionDetail.from_domain(version)

    @router.get("/{project_id}/versions")
    async def list_versions(
        project_id: str, svc: Service, offset: Offset = 0, limit: Limit = 50
    ) -> ProjectVersionPage:
        page = await svc.list_versions(project_id, offset=offset, limit=limit)
        return ProjectVersionPage.from_page(page)

    @router.get("/{project_id}/versions/{version}")
    async def get_version(
        project_id: str, version: VersionNumber, svc: Service
    ) -> ProjectVersionDetail:
        return ProjectVersionDetail.from_domain(
            await svc.get_version(project_id, version)
        )

    return router
