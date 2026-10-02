"""/api/v1/projects — thin HTTP adapter over ``ProjectService``.

Handlers parse, delegate, serialize. No domain rules, no persistence here.

Authorization: the v1 router authenticates every request and checks access
to the ``{project_id}`` in the path (``akasha.api.auth``). Handlers without
one scope by the caller: new projects are owned by them, listings show
only what they may see.
"""

from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)

from akasha.api.auth import CurrentIdentity
from akasha.api.project_schemas import (
    CreateProjectRequest,
    DuplicateProjectRequest,
    ImportProjectResponse,
    Name,
    ProjectDetail,
    ProjectPage,
    ProjectVersionDetail,
    ProjectVersionPage,
    PublishProjectRequest,
    UpdateProjectRequest,
    ValidateProjectRequest,
    ValidationResponse,
)
from akasha.api.schemas import ErrorResponse, read_limited_body
from akasha.application.auth import AuthorizationService
from akasha.application.packages import ProjectPackageService
from akasha.application.persistence import ProjectSort
from akasha.application.project_package import (
    MAX_PACKAGE_BYTES,
    MEDIA_TYPE,
    ProjectPackageTooLargeError,
)
from akasha.application.projects import MAX_PAGE_SIZE, ProjectService
from akasha.domain.project import ProjectStatus
from akasha.errors import ConfigurationError


def _service(request: Request) -> ProjectService:
    service: ProjectService | None = request.app.state.container.projects
    if service is None:
        raise ConfigurationError("Project persistence is not configured.")
    return service


def _packages(request: Request) -> ProjectPackageService:
    service: ProjectPackageService | None = request.app.state.container.packages
    if service is None:
        raise ConfigurationError("Project persistence is not configured.")
    return service


async def _package_body(request: Request) -> bytes:
    """The raw request body, read with a cap: an oversized upload is
    rejected while streaming, before it is buffered whole."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip()
    if media_type != MEDIA_TYPE:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"A project package must be sent as {MEDIA_TYPE}.",
        )
    return await read_limited_body(
        request,
        MAX_PACKAGE_BYTES,
        ProjectPackageTooLargeError(
            f"A project package must not exceed {MAX_PACKAGE_BYTES} bytes."
        ),
    )


Service = Annotated[ProjectService, Depends(_service)]
Packages = Annotated[ProjectPackageService, Depends(_packages)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
VersionNumber = Annotated[int, Path(ge=1)]


def create_projects_router() -> APIRouter:
    # Declared so the OpenAPI contract (and generated clients) show the real
    # error body instead of FastAPI's default validation schema.
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 413, 415, 422, 500)
    }
    router = APIRouter(prefix="/projects", tags=["projects"], responses=errors)

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_project(
        body: CreateProjectRequest, svc: Service, identity: CurrentIdentity
    ) -> ProjectDetail:
        project = await svc.create_project(
            body.name, description=body.description, owner_id=identity.user.id
        )
        return ProjectDetail.from_domain(project)

    @router.post(
        "/import",
        status_code=status.HTTP_201_CREATED,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
    )
    async def import_project(
        svc: Packages,
        identity: CurrentIdentity,
        body: Annotated[bytes, Depends(_package_body)],
    ) -> ImportProjectResponse:
        result = await svc.import_project(body, owner_id=identity.user.id)
        return ImportProjectResponse.from_domain(result)

    @router.get("")
    async def list_projects(
        svc: Service,
        identity: CurrentIdentity,
        offset: Offset = 0,
        limit: Limit = 50,
        q: Annotated[Name | None, Query(description="Name contains (any case)")] = None,
        status: Annotated[list[ProjectStatus] | None, Query()] = None,
        sort: ProjectSort = ProjectSort.CREATED,
    ) -> ProjectPage:
        owner = AuthorizationService.owner_filter(identity)
        page = await svc.list_projects(
            offset=offset,
            limit=limit,
            owner_id=owner,
            search=q,
            statuses=status or (),
            sort=sort,
        )
        return ProjectPage.from_page(page)

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

    @router.post("/{project_id}/duplicate", status_code=status.HTTP_201_CREATED)
    async def duplicate_project(
        project_id: str,
        body: DuplicateProjectRequest,
        svc: Service,
        identity: CurrentIdentity,
    ) -> ProjectDetail:
        """A new draft-only project with a copy of this project's draft,
        owned by the caller."""
        project = await svc.duplicate_project(
            project_id, name=body.name, owner_id=identity.user.id
        )
        return ProjectDetail.from_domain(project)

    @router.post("/{project_id}/validate")
    async def validate_project(
        project_id: str, svc: Service, body: ValidateProjectRequest | None = None
    ) -> ValidationResponse:
        expected = body.expected_revision if body else None
        result = await svc.validate_project(project_id, expected_revision=expected)
        return ValidationResponse.from_domain(result)

    @router.post("/{project_id}/publish", status_code=status.HTTP_201_CREATED)
    async def publish_project(
        project_id: str, svc: Service, body: PublishProjectRequest | None = None
    ) -> ProjectVersionDetail:
        expected = body.expected_revision if body else None
        version = await svc.publish_project(project_id, expected_revision=expected)
        return ProjectVersionDetail.from_domain(version)

    @router.get(
        "/{project_id}/export",
        response_class=Response,
        responses={200: {"content": {MEDIA_TYPE: {}}}},
    )
    async def export_project(
        project_id: str,
        svc: Packages,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> Response:
        """The project package of published ``version``, or of the draft."""
        data = await svc.export_project(project_id, version=version)
        suffix = "draft" if version is None else f"v{version}"
        return Response(
            content=data,
            media_type=MEDIA_TYPE,
            # The id, not the name: user text never reaches a header.
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{project_id}-{suffix}.akasha.zip"'
                )
            },
        )

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
