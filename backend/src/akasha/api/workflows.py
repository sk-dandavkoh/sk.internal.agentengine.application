"""/api/v1/workflows — thin HTTP adapter over ``WorkflowService``.

Handlers parse, delegate, serialize. No domain rules, no persistence here.

Authorization: the v1 router authenticates every request and checks access
to the ``{workflow_id}`` in the path (``akasha.api.auth``). Handlers without
one scope by the caller: new workflows are owned by them, listings show
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

from akasha.api.auth import Authorization, CurrentIdentity, requires
from akasha.api.schemas import ErrorResponse, read_limited_body
from akasha.api.workflow_schemas import (
    CreateWorkflowRequest,
    DuplicateWorkflowRequest,
    ImportWorkflowResponse,
    Name,
    PublishWorkflowRequest,
    RestoreVersionRequest,
    UpdateWorkflowRequest,
    ValidateWorkflowRequest,
    ValidationResponse,
    WorkflowDetail,
    WorkflowPage,
    WorkflowVersionDetail,
    WorkflowVersionPage,
)
from akasha.application.auth import AuthorizationService
from akasha.application.packages import WorkflowPackageService
from akasha.application.persistence import WorkflowSort
from akasha.application.workflow_package import (
    MAX_PACKAGE_BYTES,
    MEDIA_TYPE,
    WorkflowPackageTooLargeError,
)
from akasha.application.workflows import MAX_PAGE_SIZE, WorkflowService
from akasha.domain.identity import Access
from akasha.domain.workflow import WorkflowStatus
from akasha.errors import ConfigurationError


def _service(request: Request) -> WorkflowService:
    service: WorkflowService | None = request.app.state.container.workflows
    if service is None:
        raise ConfigurationError("Workflow persistence is not configured.")
    return service


def _packages(request: Request) -> WorkflowPackageService:
    service: WorkflowPackageService | None = request.app.state.container.packages
    if service is None:
        raise ConfigurationError("Workflow persistence is not configured.")
    return service


async def _package_body(request: Request) -> bytes:
    """The raw request body, read with a cap: an oversized upload is
    rejected while streaming, before it is buffered whole."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip()
    if media_type != MEDIA_TYPE:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"A workflow package must be sent as {MEDIA_TYPE}.",
        )
    return await read_limited_body(
        request,
        MAX_PACKAGE_BYTES,
        WorkflowPackageTooLargeError(
            f"A workflow package must not exceed {MAX_PACKAGE_BYTES} bytes."
        ),
    )


Service = Annotated[WorkflowService, Depends(_service)]
Packages = Annotated[WorkflowPackageService, Depends(_packages)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
VersionNumber = Annotated[int, Path(ge=1)]


def create_workflows_router() -> APIRouter:
    # Declared so the OpenAPI contract (and generated clients) show the real
    # error body instead of FastAPI's default validation schema.
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 413, 415, 422, 500)
    }
    router = APIRouter(prefix="/workflows", tags=["workflows"], responses=errors)

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_workflow(
        body: CreateWorkflowRequest, svc: Service, identity: CurrentIdentity
    ) -> WorkflowDetail:
        workflow = await svc.create_workflow(
            body.name, description=body.description, owner_id=identity.user.id
        )
        return WorkflowDetail.from_domain(workflow)

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
    async def import_workflow(
        svc: Packages,
        identity: CurrentIdentity,
        body: Annotated[bytes, Depends(_package_body)],
    ) -> ImportWorkflowResponse:
        result = await svc.import_workflow(body, owner_id=identity.user.id)
        return ImportWorkflowResponse.from_domain(result)

    @router.get("")
    async def list_workflows(
        svc: Service,
        identity: CurrentIdentity,
        offset: Offset = 0,
        limit: Limit = 50,
        q: Annotated[Name | None, Query(description="Name contains (any case)")] = None,
        status: Annotated[list[WorkflowStatus] | None, Query()] = None,
        sort: WorkflowSort = WorkflowSort.CREATED,
    ) -> WorkflowPage:
        visible = AuthorizationService.visible_to(identity)
        page = await svc.list_workflows(
            offset=offset,
            limit=limit,
            visible_to=visible,
            search=q,
            statuses=status or (),
            sort=sort,
        )
        return WorkflowPage.from_page(page)

    @router.get("/{workflow_id}")
    async def get_workflow(workflow_id: str, svc: Service) -> WorkflowDetail:
        return WorkflowDetail.from_domain(await svc.get_workflow(workflow_id))

    @router.put("/{workflow_id}")
    async def update_workflow(
        workflow_id: str,
        body: UpdateWorkflowRequest,
        svc: Service,
        identity: CurrentIdentity,
        authz: Authorization,
    ) -> WorkflowDetail:
        draft = body.draft.to_domain(body.description)
        current = await svc.get_workflow(workflow_id)
        await authz.require_trigger_sources(
            identity, current.draft.triggers, draft.triggers
        )
        workflow = await svc.update_draft(
            workflow_id,
            expected_revision=body.expected_revision,
            name=body.name,
            draft=draft,
        )
        return WorkflowDetail.from_domain(workflow)

    @router.delete(
        "/{workflow_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        **requires(Access.MANAGE),
    )
    async def delete_workflow(workflow_id: str, svc: Service) -> Response:
        await svc.delete_workflow(workflow_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/{workflow_id}/archive", **requires(Access.MANAGE))
    async def archive_workflow(workflow_id: str, svc: Service) -> WorkflowDetail:
        """Retire the workflow: versions and history stay, its triggers stop,
        new executions are refused (409 WORKFLOW_ARCHIVED) and it leaves the
        default listing."""
        return WorkflowDetail.from_domain(await svc.set_archived(workflow_id, True))

    @router.post("/{workflow_id}/unarchive", **requires(Access.MANAGE))
    async def unarchive_workflow(workflow_id: str, svc: Service) -> WorkflowDetail:
        return WorkflowDetail.from_domain(await svc.set_archived(workflow_id, False))

    # Reading is enough: the copy belongs to the caller.
    @router.post(
        "/{workflow_id}/duplicate",
        status_code=status.HTTP_201_CREATED,
        **requires(Access.VIEW),
    )
    async def duplicate_workflow(
        workflow_id: str,
        body: DuplicateWorkflowRequest,
        svc: Service,
        identity: CurrentIdentity,
    ) -> WorkflowDetail:
        """A new draft-only workflow with a copy of this workflow's draft (or
        of published ``version``), owned by the caller."""
        workflow = await svc.duplicate_workflow(
            workflow_id,
            name=body.name,
            owner_id=identity.user.id,
            version=body.version,
        )
        return WorkflowDetail.from_domain(workflow)

    # A check without side effects.
    @router.post("/{workflow_id}/validate", **requires(Access.VIEW))
    async def validate_workflow(
        workflow_id: str, svc: Service, body: ValidateWorkflowRequest | None = None
    ) -> ValidationResponse:
        expected = body.expected_revision if body else None
        result = await svc.validate_workflow(workflow_id, expected_revision=expected)
        return ValidationResponse.from_domain(result)

    @router.post("/{workflow_id}/publish", status_code=status.HTTP_201_CREATED)
    async def publish_workflow(
        workflow_id: str,
        svc: Service,
        identity: CurrentIdentity,
        body: PublishWorkflowRequest | None = None,
    ) -> WorkflowVersionDetail:
        expected = body.expected_revision if body else None
        version = await svc.publish_workflow(
            workflow_id, expected_revision=expected, published_by=identity.user.id
        )
        names = {identity.user.id: identity.user.username}
        return WorkflowVersionDetail.from_domain(version, names)

    @router.get(
        "/{workflow_id}/export",
        response_class=Response,
        responses={200: {"content": {MEDIA_TYPE: {}}}},
    )
    async def export_workflow(
        workflow_id: str,
        svc: Packages,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> Response:
        """The workflow package of published ``version``, or of the draft."""
        data = await svc.export_workflow(workflow_id, version=version)
        suffix = "draft" if version is None else f"v{version}"
        return Response(
            content=data,
            media_type=MEDIA_TYPE,
            # The id, not the name: user text never reaches a header.
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{workflow_id}-{suffix}.akasha.zip"'
                )
            },
        )

    @router.get("/{workflow_id}/versions")
    async def list_versions(
        workflow_id: str, svc: Service, offset: Offset = 0, limit: Limit = 50
    ) -> WorkflowVersionPage:
        page = await svc.list_versions(workflow_id, offset=offset, limit=limit)
        names = await svc.usernames([v.published_by for v in page.items])
        return WorkflowVersionPage.from_page(page, names)

    @router.get("/{workflow_id}/versions/{version}")
    async def get_version(
        workflow_id: str, version: VersionNumber, svc: Service
    ) -> WorkflowVersionDetail:
        found = await svc.get_version(workflow_id, version)
        names = await svc.usernames([found.published_by])
        return WorkflowVersionDetail.from_domain(found, names)

    @router.post("/{workflow_id}/versions/{version}/restore")
    async def restore_version(
        workflow_id: str,
        version: VersionNumber,
        body: RestoreVersionRequest,
        svc: Service,
    ) -> WorkflowDetail:
        """Replace the draft with a copy of ``version``; the version itself
        stays as it is. Publishing the draft afterwards makes a new version."""
        workflow = await svc.restore_version(
            workflow_id, version, expected_revision=body.expected_revision
        )
        return WorkflowDetail.from_domain(workflow)

    return router
