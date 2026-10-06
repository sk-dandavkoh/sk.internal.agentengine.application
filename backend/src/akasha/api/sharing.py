"""Project sharing (``application.sharing``), under /projects/{project_id}:

- ``GET /members``: anyone with access reads who else has it and their own
  access level;
- ``PUT``/``DELETE /members/{username}``, ``PUT /owner``: the owner and
  admins only (``Access.MANAGE``, declared on the routes and checked again
  by the service);
- ``DELETE /membership``: a member leaves (any access).
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, Field

from akasha.api.auth import CurrentIdentity, requires
from akasha.api.schemas import ErrorResponse
from akasha.application.sharing import ProjectSharing, SharingService
from akasha.domain.identity import Access, ProjectMember, ProjectRole, User
from akasha.errors import ConfigurationError


def _sharing(request: Request) -> SharingService:
    service: SharingService | None = request.app.state.container.sharing
    if service is None:
        raise ConfigurationError("Authentication is not configured (database).")
    return service


Sharing = Annotated[SharingService, Depends(_sharing)]
# The same shape as a username (domain.identity.validate_username).
MemberName = Annotated[str, Path(min_length=1, max_length=64)]


class Person(BaseModel):
    user_id: str
    username: str
    display_name: str | None

    @classmethod
    def from_user(cls, user: User) -> "Person":
        return cls(
            user_id=user.id, username=user.username, display_name=user.display_name
        )


class MemberOut(Person):
    role: ProjectRole
    created_at: datetime

    @classmethod
    def from_domain(cls, m: ProjectMember) -> "MemberOut":
        return cls(
            user_id=m.user_id,
            username=m.username,
            display_name=m.display_name,
            role=m.role,
            created_at=m.created_at,
        )


class SharingOut(BaseModel):
    # The caller's access: manage (owner, admin), edit (editor) or view.
    access: Literal["manage", "edit", "view"]
    # Null: an ownerless project (admin-only unless shared).
    owner: Person | None
    # Unpaginated: at most MAX_PROJECT_MEMBERS, by username.
    members: list[MemberOut]

    @classmethod
    def from_domain(cls, s: ProjectSharing) -> "SharingOut":
        return cls(
            access=_ACCESS[s.access],
            owner=None if s.owner is None else Person.from_user(s.owner),
            members=[MemberOut.from_domain(m) for m in s.members],
        )


_ACCESS: dict[Access, Literal["manage", "edit", "view"]] = {
    Access.MANAGE: "manage",
    Access.EDIT: "edit",
    Access.VIEW: "view",
}


class PutMemberRequest(BaseModel):
    role: ProjectRole


class TransferOwnershipRequest(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=64)]


def create_sharing_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422)
    }
    router = APIRouter(
        prefix="/projects/{project_id}", tags=["sharing"], responses=errors
    )

    @router.get("/members")
    async def get_sharing(
        project_id: str, identity: CurrentIdentity, sharing: Sharing
    ) -> SharingOut:
        """The owner, the members and the caller's own access."""
        return SharingOut.from_domain(await sharing.get(identity, project_id))

    @router.put("/members/{username}", **requires(Access.MANAGE))
    async def put_member(
        project_id: str,
        username: MemberName,
        body: PutMemberRequest,
        identity: CurrentIdentity,
        sharing: Sharing,
    ) -> MemberOut:
        """Share the project with a user, or change their role. Takes effect
        on their next request."""
        member = await sharing.put_member(identity, project_id, username, body.role)
        return MemberOut.from_domain(member)

    @router.delete(
        "/members/{username}",
        status_code=status.HTTP_204_NO_CONTENT,
        **requires(Access.MANAGE),
    )
    async def remove_member(
        project_id: str,
        username: MemberName,
        identity: CurrentIdentity,
        sharing: Sharing,
    ) -> Response:
        """Stop sharing with a user; their next request is refused."""
        await sharing.remove_member(identity, project_id, username)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.put("/owner", **requires(Access.MANAGE))
    async def transfer_ownership(
        project_id: str,
        body: TransferOwnershipRequest,
        identity: CurrentIdentity,
        sharing: Sharing,
    ) -> SharingOut:
        """Make another (enabled) user the owner. The previous owner stays
        on as an editor. Answers the sharing as the caller now sees it."""
        result = await sharing.transfer_ownership(identity, project_id, body.username)
        return SharingOut.from_domain(result)

    # Any access suffices: leaving only ever removes one's own.
    @router.delete(
        "/membership",
        status_code=status.HTTP_204_NO_CONTENT,
        **requires(Access.VIEW),
    )
    async def leave_project(
        project_id: str, identity: CurrentIdentity, sharing: Sharing
    ) -> Response:
        """Stop being a member of a project shared with you; the owner
        transfers ownership instead."""
        await sharing.leave(identity, project_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
