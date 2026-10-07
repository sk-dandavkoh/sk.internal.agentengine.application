"""/api/v1/credentials — thin HTTP adapter over ``CredentialService``.

Write-only secrets: requests may carry a value (``SecretStr``, masked in
reprs), responses never do — there is no endpoint that returns plaintext.
Values are resolved only by the runtime, at execution time.

Authorization boundary: like workflows, one router-level dependency secures
all of these in Phase 20.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from akasha.api.schemas import ErrorResponse
from akasha.api.workflow_schemas import PageInfo
from akasha.application.credentials import CredentialService
from akasha.application.workflows import MAX_PAGE_SIZE, Page
from akasha.domain.credentials import (
    MAX_SECRET_LENGTH,
    Credential,
    CredentialKind,
    Secret,
)
from akasha.errors import ConfigurationError

SecretValue = Annotated[SecretStr, Field(min_length=1, max_length=MAX_SECRET_LENGTH)]
Description = Annotated[str, Field(max_length=2000)]


class CreateCredentialRequest(BaseModel):
    name: str
    kind: CredentialKind = CredentialKind.GENERIC
    description: Description = ""
    value: SecretValue


class UpdateCredentialRequest(BaseModel):
    """Omitted fields stay unchanged; ``value`` rotates the secret."""

    description: Description | None = None
    value: SecretValue | None = None


class CredentialDetail(BaseModel):
    name: str
    reference: str
    kind: CredentialKind
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, credential: Credential) -> "CredentialDetail":
        return cls(
            name=credential.name,
            reference=credential.reference,
            kind=credential.kind,
            description=credential.description,
            created_at=credential.timestamps.created_at,
            updated_at=credential.timestamps.updated_at,
        )


class CredentialPage(BaseModel):
    items: list[CredentialDetail]
    page: PageInfo

    @classmethod
    def from_page(cls, page: Page[Credential]) -> "CredentialPage":
        return cls(
            items=[CredentialDetail.from_domain(c) for c in page.items],
            page=PageInfo(offset=page.offset, limit=page.limit, has_more=page.has_more),
        )


def _service(request: Request) -> CredentialService:
    service: CredentialService | None = request.app.state.container.credentials
    if service is None:
        raise ConfigurationError(
            "The credential store is not configured (database and encryption key)."
        )
    return service


Service = Annotated[CredentialService, Depends(_service)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def _secret(value: SecretStr | None) -> Secret | None:
    return None if value is None else Secret(value.get_secret_value())


class CredentialName(BaseModel):
    """What a picker needs: the name to reference and its kind. Not the
    description (admins may write anything there), never a value."""

    name: str
    kind: CredentialKind


class CredentialNamePage(BaseModel):
    items: list[CredentialName]
    page: PageInfo


def create_credential_names_router() -> APIRouter:
    router = APIRouter(
        prefix="/credential-names",
        tags=["credentials"],
        responses={422: {"model": ErrorResponse}},
    )

    @router.get("")
    async def list_credential_names(
        svc: Service, offset: Offset = 0, limit: Limit = MAX_PAGE_SIZE
    ) -> CredentialNamePage:
        page = await svc.list(offset=offset, limit=limit)
        return CredentialNamePage(
            items=[CredentialName(name=c.name, kind=c.kind) for c in page.items],
            page=PageInfo(offset=page.offset, limit=page.limit, has_more=page.has_more),
        )

    return router


def create_credentials_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 422, 500)
    }
    router = APIRouter(prefix="/credentials", tags=["credentials"], responses=errors)

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_credential(
        body: CreateCredentialRequest, svc: Service
    ) -> CredentialDetail:
        credential = await svc.create(
            body.name,
            kind=body.kind,
            value=Secret(body.value.get_secret_value()),
            description=body.description,
        )
        return CredentialDetail.from_domain(credential)

    @router.get("")
    async def list_credentials(
        svc: Service, offset: Offset = 0, limit: Limit = 50
    ) -> CredentialPage:
        return CredentialPage.from_page(await svc.list(offset=offset, limit=limit))

    @router.get("/{name}")
    async def get_credential(name: str, svc: Service) -> CredentialDetail:
        return CredentialDetail.from_domain(await svc.get(name))

    @router.patch("/{name}")
    async def update_credential(
        name: str, body: UpdateCredentialRequest, svc: Service
    ) -> CredentialDetail:
        credential = await svc.update(
            name, description=body.description, value=_secret(body.value)
        )
        return CredentialDetail.from_domain(credential)

    @router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_credential(name: str, svc: Service) -> Response:
        await svc.delete(name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
