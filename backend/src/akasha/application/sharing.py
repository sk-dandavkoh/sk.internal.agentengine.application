"""Workflow sharing: who besides the owner may view or edit a workflow.

The owner and admins manage sharing (``Access.MANAGE``); members get the
access of their role (``domain.identity.workflow_access``). Every check
goes through ``AuthorizationService``, so the REST guard, MCP and this
service apply one rule. Changes take effect on the member's next request,
because access is evaluated per request.

Members are named by username: owners share with people, and usernames
are immutable. Any user who manages a workflow can therefore tell whether a
username exists; on a self-hosted installation that is the point of
sharing, and it needs a session.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from akasha.application.auth import (
    AuthorizationService,
    Clock,
    UserNotFoundError,
)
from akasha.application.persistence import UnitOfWork
from akasha.application.workflows import UowFactory, load_workflow
from akasha.domain.identity import (
    MAX_WORKFLOW_MEMBERS,
    Access,
    Identity,
    User,
    WorkflowMember,
    WorkflowRole,
)
from akasha.domain.primitives import utcnow
from akasha.errors import AkashaError

logger = logging.getLogger("akasha.application.sharing")


class WorkflowOwnerError(AkashaError):
    """The workflow's owner always manages it; they cannot be a member."""

    code = "WORKFLOW_OWNER"
    http_status = 409


class MemberNotFoundError(AkashaError):
    code = "MEMBER_NOT_FOUND"
    http_status = 404


class MemberLimitError(AkashaError):
    code = "MEMBER_LIMIT"
    http_status = 409


class UserDisabledError(AkashaError):
    """A disabled user cannot sign in, so cannot manage a workflow."""

    code = "USER_DISABLED"
    http_status = 409


@dataclass(frozen=True)
class WorkflowSharing:
    """Who can reach a workflow, and what the caller may do with it."""

    access: Access
    owner: User | None  # None: an ownerless (admin-only) workflow
    members: Sequence[WorkflowMember]


class SharingService:
    def __init__(
        self,
        uow: UowFactory,
        authorization: AuthorizationService,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self._uow = uow
        self._authz = authorization
        self._clock = clock

    async def get(self, identity: Identity, workflow_id: str) -> WorkflowSharing:
        """Readable by anyone with access: collaborators see each other."""
        access = await self._authz.require_workflow(identity, workflow_id)
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            owner = (
                None
                if workflow.owner_id is None
                else await uow.users.get(workflow.owner_id)
            )
            members = await uow.members.list(workflow_id)
        return WorkflowSharing(access=access, owner=owner, members=members)

    async def put_member(
        self, identity: Identity, workflow_id: str, username: str, role: WorkflowRole
    ) -> WorkflowMember:
        """Share with ``username``, or change their role."""
        await self._authz.require_workflow(identity, workflow_id, Access.MANAGE)
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            user = await self._user(uow, username)
            if user.id == workflow.owner_id:
                raise WorkflowOwnerError("The owner already manages this workflow.")
            current = await uow.members.role(workflow_id, user.id)
            if current is None and await uow.members.count(workflow_id) >= (
                MAX_WORKFLOW_MEMBERS
            ):
                raise MemberLimitError(
                    f"A workflow can be shared with at most {MAX_WORKFLOW_MEMBERS}"
                    " users.",
                    details={"limit": MAX_WORKFLOW_MEMBERS},
                )
            await uow.members.put(workflow_id, user.id, role, self._clock())
            await uow.commit()
            # Re-read: a role change keeps the original created_at.
            member = next(
                m for m in await uow.members.list(workflow_id) if m.user_id == user.id
            )
        _audit(
            "workflow_member_set",
            identity,
            workflow_id=workflow_id,
            member_id=user.id,
            role=role,
            previous=current,
        )
        return member

    async def remove_member(
        self, identity: Identity, workflow_id: str, username: str
    ) -> None:
        await self._authz.require_workflow(identity, workflow_id, Access.MANAGE)
        async with self._uow() as uow:
            user = await self._user(uow, username)
            if not await uow.members.delete(workflow_id, user.id):
                raise MemberNotFoundError(
                    "The workflow is not shared with this user.",
                    details={"username": username},
                )
            await uow.commit()
        _audit(
            "workflow_member_removed",
            identity,
            workflow_id=workflow_id,
            member_id=user.id,
        )

    async def transfer_ownership(
        self, identity: Identity, workflow_id: str, username: str
    ) -> WorkflowSharing:
        """Make ``username`` the owner. The previous owner stays on as an
        editor (they can leave, or be removed, afterwards); the new owner's
        member row goes, since owning includes it. Owner-run event triggers
        now fire only for sources the new owner can see."""
        await self._authz.require_workflow(identity, workflow_id, Access.MANAGE)
        async with self._uow() as uow:
            # Locked, so concurrent transfers serialize: each sees the owner
            # the other one left, and no previous owner loses access.
            await load_workflow(uow, workflow_id)  # not found → 404
            previous = await uow.workflows.lock_owner(workflow_id)
            user = await self._user(uow, username)
            if user.id == previous:
                raise WorkflowOwnerError(
                    "This user already owns the workflow.",
                    details={"username": username},
                )
            if user.disabled:
                raise UserDisabledError(
                    "A disabled user cannot own a workflow.",
                    details={"username": username},
                )
            await uow.members.delete(workflow_id, user.id)
            if previous is not None:
                if await uow.members.count(workflow_id) >= MAX_WORKFLOW_MEMBERS:
                    raise MemberLimitError(
                        "The previous owner cannot stay on as a member: the"
                        f" workflow is shared with {MAX_WORKFLOW_MEMBERS} users.",
                        details={"limit": MAX_WORKFLOW_MEMBERS},
                    )
                await uow.members.put(
                    workflow_id, previous, WorkflowRole.EDITOR, self._clock()
                )
            await uow.workflows.set_owner(workflow_id, user.id)
            await uow.commit()
        _audit(
            "workflow_owner_transferred",
            identity,
            workflow_id=workflow_id,
            previous_owner_id=previous,
            owner_id=user.id,
        )
        return await self.get(identity, workflow_id)

    async def leave(self, identity: Identity, workflow_id: str) -> None:
        """A member removes their own access. The owner cannot: someone
        must manage the workflow, so they transfer ownership first."""
        await self._authz.require_workflow(identity, workflow_id)
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            if workflow.owner_id == identity.user.id:
                raise WorkflowOwnerError(
                    "The owner cannot leave the workflow; transfer ownership first."
                )
            if not await uow.members.delete(workflow_id, identity.user.id):
                raise MemberNotFoundError(
                    "You are not a member of this workflow (administrators"
                    " reach every workflow)."
                )
            await uow.commit()
        _audit("workflow_member_left", identity, workflow_id=workflow_id)

    @staticmethod
    async def _user(uow: UnitOfWork, username: str) -> User:
        user = await uow.users.get_by_username(username.lower())
        if user is None:
            raise UserNotFoundError("User not found.", details={"username": username})
        return user


def _audit(event: str, identity: Identity, **extra: object) -> None:
    # Ids only, like the account audit events (application.auth).
    logger.info(event, extra={"event": event, "actor_id": identity.user.id, **extra})
