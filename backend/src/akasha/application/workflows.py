"""Workflow lifecycle use cases (roadmap Phase 06).

Draft → Validate → Publish → immutable Version. Each public method is one
use case and one transaction (one Unit of Work). Every workflow write is a
compare-and-swap on ``Workflow.revision`` (see ``WorkflowRepository.save``),
so concurrent editors or publishers get ``ConcurrentModificationError``
instead of silently overwriting each other.

HTTP-free by design: the API, the CLI (Phase 21) and tests drive the same
service.
"""

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType

from akasha.application.events import EventPublisher
from akasha.application.persistence import (
    ConcurrentModificationError,
    UnitOfWork,
    WorkflowSort,
)
from akasha.domain.event import Event, EventType
from akasha.domain.graph_validation import ValidationIssue, ValidationResult
from akasha.domain.node import NodeDefinition
from akasha.domain.workflow import (
    Workflow,
    WorkflowDraft,
    WorkflowStatus,
    WorkflowVersion,
    plugins_in_use,
)
from akasha.errors import AkashaError, DomainValidationError

logger = logging.getLogger("akasha.application.workflows")

MAX_PAGE_SIZE = 100

UowFactory = Callable[[], UnitOfWork]


class WorkflowNotFoundError(AkashaError):
    code = "WORKFLOW_NOT_FOUND"
    http_status = 404


class WorkflowVersionNotFoundError(AkashaError):
    code = "WORKFLOW_VERSION_NOT_FOUND"
    http_status = 404


class WorkflowHasVersionsError(AkashaError):
    """Published versions are retained history; their workflow cannot be
    deleted (the FK ``ondelete=RESTRICT`` is the database-side backstop)."""

    code = "WORKFLOW_HAS_VERSIONS"
    http_status = 409


class WorkflowArchivedError(AkashaError):
    code = "WORKFLOW_ARCHIVED"
    http_status = 409


class WorkflowValidationFailedError(AkashaError):
    code = "WORKFLOW_VALIDATION_FAILED"
    http_status = 422

    def __init__(self, result: ValidationResult) -> None:
        super().__init__(
            "The workflow draft contains validation errors.",
            details={"issues": [issue_payload(i) for i in result.issues]},
        )
        self.result = result


def issue_payload(issue: ValidationIssue) -> dict[str, object]:
    """The one wire shape of a validation issue (locations only, no config
    values — safe to return and log)."""
    edge = issue.edge
    return {
        "code": issue.code.value,
        "message": issue.message,
        "node_id": issue.node_id,
        "port": issue.port,
        "edge": None
        if edge is None
        else {
            "source_node_id": edge.source_node_id,
            "source_port": edge.source_port,
            "target_node_id": edge.target_node_id,
            "target_port": edge.target_port,
        },
        "trigger_id": issue.trigger_id,
    }


@dataclass(frozen=True)
class Page[T]:
    items: Sequence[T]
    offset: int
    limit: int
    has_more: bool


def check_page(offset: int, limit: int) -> None:
    if offset < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
        raise DomainValidationError(
            f"offset must be >= 0 and limit between 1 and {MAX_PAGE_SIZE}"
        )


def to_page[T](rows: Sequence[T], offset: int, limit: int) -> Page[T]:
    # Callers fetch limit + 1 rows: the extra row only signals has_more.
    return Page(rows[:limit], offset, limit, has_more=len(rows) > limit)


def _check_revision(workflow: Workflow, expected: int) -> None:
    if workflow.revision != expected:
        raise ConcurrentModificationError(
            "The workflow was modified since it was read; reload and retry.",
            details={
                "workflow_id": workflow.id,
                "expected_revision": expected,
                "current_revision": workflow.revision,
            },
        )


class WorkflowService:
    """Application service for the Workflow lifecycle.

    ``node_definitions`` is the node-type catalog drafts are validated
    against: ``NodeRegistry.definitions`` in production (a live view), any
    mapping in tests. ``plugins`` (enabled plugin id → version) is
    ``PluginRegistry.versions`` in production; empty means none installed.
    ``plugin_nodes`` (node type → plugin id) is ``PluginRegistry.node_plugins``:
    publishing pins the versions of the plugins a draft uses.
    """

    def __init__(
        self,
        uow: UowFactory,
        node_definitions: Mapping[str, NodeDefinition],
        events: EventPublisher | None = None,
        plugins: Mapping[str, str] = MappingProxyType({}),
        plugin_nodes: Mapping[str, str] = MappingProxyType({}),
    ) -> None:
        self._uow = uow
        self._definitions = node_definitions
        self._plugins = plugins
        self._plugin_nodes = plugin_nodes
        self._events = events or EventPublisher()

    async def create_workflow(
        self, name: str, *, description: str = "", owner_id: str | None = None
    ) -> Workflow:
        workflow = Workflow.create(name, description=description, owner_id=owner_id)
        async with self._uow() as uow:
            await uow.workflows.save(workflow)
            await uow.commit()
        _log("workflow_created", workflow)
        return workflow

    async def get_workflow(self, workflow_id: str) -> Workflow:
        async with self._uow() as uow:
            return await load_workflow(uow, workflow_id)

    async def list_workflows(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        visible_to: str | None = None,
        search: str | None = None,
        statuses: Collection[WorkflowStatus] = (),
        sort: WorkflowSort = WorkflowSort.CREATED,
    ) -> Page[Workflow]:
        """All workflows, or only those ``visible_to`` that user; ``search`` matches
        a case-insensitive substring of the name. Archived workflows are left
        out unless ``statuses`` asks for them."""
        check_page(offset, limit)
        statuses = statuses or [
            s for s in WorkflowStatus if s is not WorkflowStatus.ARCHIVED
        ]
        async with self._uow() as uow:
            rows = await uow.workflows.list(
                offset=offset,
                limit=limit + 1,
                visible_to=visible_to,
                search=search,
                statuses=statuses,
                sort=sort,
            )
        return to_page(rows, offset, limit)

    async def duplicate_workflow(
        self,
        workflow_id: str,
        *,
        name: str,
        owner_id: str | None = None,
        version: int | None = None,
    ) -> Workflow:
        """A new, never-published workflow holding a copy of the source's
        draft, or of its published ``version`` (graph, triggers,
        dependencies, description, files).

        Published versions are history of the source and are not copied.
        The copy goes through ``replace_draft``, so it obeys every rule a
        draft write does.
        """
        async with self._uow() as uow:
            source = await load_workflow(uow, workflow_id)
            draft = (
                deepcopy(source.draft)
                if version is None
                else (await load_version(uow, workflow_id, version)).to_draft()
            )
            workflow = Workflow.create(name, owner_id=owner_id)
            workflow.replace_draft(draft, name=name)
            await uow.workflows.save(workflow)
            await uow.commit()
        _log(
            "workflow_duplicated",
            workflow,
            source_workflow_id=source.id,
            source_version=version,
        )
        return workflow

    async def restore_version(
        self, workflow_id: str, version: int, *, expected_revision: int
    ) -> Workflow:
        """Replace the draft with a copy of published ``version``.

        The version itself is untouched; publishing the restored draft
        creates a new version. ``expected_revision`` guards the draft being
        replaced, like any draft write: unsaved work elsewhere is a 409.
        """
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            _check_revision(workflow, expected_revision)
            snapshot = await load_version(uow, workflow_id, version)
            workflow.replace_draft(snapshot.to_draft(), name=workflow.name)
            await uow.workflows.save(workflow)
            await uow.commit()
        _log("workflow_version_restored", workflow, version=version)
        return workflow

    async def usernames(self, user_ids: Collection[str | None]) -> dict[str, str]:
        """Usernames of the given users, for display (version authors).
        Unknown ids are left out."""
        names: dict[str, str] = {}
        async with self._uow() as uow:
            for user_id in sorted({u for u in user_ids if u is not None}):
                user = await uow.users.get(user_id)
                if user is not None:
                    names[user_id] = user.username
        return names

    async def update_draft(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        name: str,
        draft: WorkflowDraft,
    ) -> Workflow:
        """Replace name and draft. Accepts semantically invalid drafts — a
        draft may be work in progress; only publish demands validity.

        Workflow files are kept, whatever ``draft.files`` holds: the draft
        API does not carry them, and only imports set them (Phase 18).
        """
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            _check_revision(workflow, expected_revision)
            workflow.replace_draft(
                replace(draft, files=workflow.draft.files), name=name
            )
            await uow.workflows.save(workflow)
            await uow.commit()
        _log("workflow_draft_updated", workflow)
        return workflow

    async def edit_draft(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        edit: Callable[[WorkflowDraft], None],
        name: str | None = None,
    ) -> Workflow:
        """Apply ``edit`` (e.g. one ``Graph.add_node``) to a copy of the
        stored draft and save it, in one transaction.

        The same rules as ``update_draft``: ``expected_revision`` guards
        against overwriting newer work, and the edited draft passes
        ``Workflow.replace_draft``. ``edit`` raises to reject the change;
        nothing is stored then. ``name`` renames the workflow as well.
        """
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            _check_revision(workflow, expected_revision)
            draft = deepcopy(workflow.draft)
            edit(draft)
            workflow.replace_draft(draft, name=workflow.name if name is None else name)
            await uow.workflows.save(workflow)
            await uow.commit()
        _log("workflow_draft_updated", workflow)
        return workflow

    async def delete_workflow(self, workflow_id: str) -> None:
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            if workflow.current_version > 0:
                raise WorkflowHasVersionsError(
                    "A workflow with published versions cannot be deleted.",
                    details={"workflow_id": workflow.id},
                )
            # CAS delete: a publish racing this request makes it a conflict,
            # never a half-deleted workflow or an FK error surfacing as 500.
            if not await uow.workflows.delete(workflow.id, revision=workflow.revision):
                raise ConcurrentModificationError(
                    "The workflow was modified concurrently; reload and retry.",
                    details={"workflow_id": workflow.id},
                )
            await uow.commit()
        _log("workflow_deleted", workflow)

    async def set_archived(self, workflow_id: str, archived: bool) -> Workflow:
        """Archive (or restore) a workflow: the way to retire one that has
        published versions, which ``delete_workflow`` refuses."""
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            if archived:
                workflow.archive()
            else:
                workflow.unarchive()
            await uow.workflows.save(workflow)
            await uow.commit()
        _log("workflow_archived" if archived else "workflow_unarchived", workflow)
        return workflow

    async def workflows_using_plugin(
        self, plugin_id: str, node_types: Sequence[str]
    ) -> Sequence[str]:
        """Workflows whose draft or published history needs the plugin."""
        async with self._uow() as uow:
            return await uow.workflows.using_plugin(plugin_id, node_types)

    async def validate_workflow(
        self, workflow_id: str, *, expected_revision: int | None = None
    ) -> ValidationResult:
        """Validate the stored draft. ``expected_revision`` pins the result to
        the draft the caller shows, so it never describes a newer one."""
        workflow = await self.get_workflow(workflow_id)
        if expected_revision is not None:
            _check_revision(workflow, expected_revision)
        return workflow.draft.validate(self._definitions, self._plugins)

    async def publish_workflow(
        self,
        workflow_id: str,
        *,
        expected_revision: int | None = None,
        published_by: str | None = None,
    ) -> WorkflowVersion:
        """Validate the draft and freeze it as the next immutable version.

        ``expected_revision`` pins publish to the draft the caller actually
        reviewed. Counter update and version insert share one transaction:
        either both commit or neither does.
        """
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            if expected_revision is not None:
                _check_revision(workflow, expected_revision)
            result = workflow.draft.validate(self._definitions, self._plugins)
            if not result.ok:
                raise WorkflowValidationFailedError(result)
            draft = workflow.draft
            pins = plugins_in_use(
                draft.graph, draft.dependencies, self._plugin_nodes, self._plugins
            )
            version = workflow.publish_draft(
                published_by=published_by, plugin_versions=pins
            )
            published = Event(
                event_type=EventType.WORKFLOW_PUBLISHED,
                occurred_at=version.published_at,
                workflow_id=workflow.id,
                workflow_version=version.version,
            )
            await uow.workflows.save(workflow)
            await uow.versions.save(workflow.id, version)
            await uow.events.append([published])
            await uow.commit()
        await self._events.publish([published])
        _log("workflow_published", workflow, version=version.version)
        return version

    async def get_version(self, workflow_id: str, version: int) -> WorkflowVersion:
        async with self._uow() as uow:
            return await load_version(uow, workflow_id, version)

    async def list_versions(
        self, workflow_id: str, *, offset: int = 0, limit: int = 50
    ) -> Page[WorkflowVersion]:
        check_page(offset, limit)
        async with self._uow() as uow:
            await load_workflow(uow, workflow_id)
            rows = await uow.versions.list(workflow_id, offset=offset, limit=limit + 1)
        return to_page(rows, offset, limit)


async def load_workflow(uow: UnitOfWork, workflow_id: str) -> Workflow:
    workflow = await uow.workflows.get(workflow_id)
    if workflow is None:
        raise WorkflowNotFoundError(
            "Workflow not found.", details={"workflow_id": workflow_id}
        )
    return workflow


async def load_version(
    uow: UnitOfWork, workflow_id: str, version: int
) -> WorkflowVersion:
    found = await uow.versions.get(workflow_id, version)
    if found is None:
        raise WorkflowVersionNotFoundError(
            "Workflow version not found.",
            details={"workflow_id": workflow_id, "version": version},
        )
    return found


def _log(event: str, workflow: Workflow, **extra: object) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "workflow_id": workflow.id,
            "revision": workflow.revision,
            **extra,
        },
    )
