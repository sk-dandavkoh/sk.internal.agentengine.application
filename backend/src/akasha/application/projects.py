"""Project lifecycle use cases (roadmap Phase 06).

Draft → Validate → Publish → immutable Version. Each public method is one
use case and one transaction (one Unit of Work). Every project write is a
compare-and-swap on ``Project.revision`` (see ``ProjectRepository.save``),
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
    ProjectSort,
    UnitOfWork,
)
from akasha.domain.event import Event, EventType
from akasha.domain.graph_validation import ValidationIssue, ValidationResult
from akasha.domain.node import NodeDefinition
from akasha.domain.project import (
    Project,
    ProjectDraft,
    ProjectStatus,
    ProjectVersion,
)
from akasha.errors import AkashaError, DomainValidationError

logger = logging.getLogger("akasha.application.projects")

MAX_PAGE_SIZE = 100

UowFactory = Callable[[], UnitOfWork]


class ProjectNotFoundError(AkashaError):
    code = "PROJECT_NOT_FOUND"
    http_status = 404


class ProjectVersionNotFoundError(AkashaError):
    code = "PROJECT_VERSION_NOT_FOUND"
    http_status = 404


class ProjectHasVersionsError(AkashaError):
    """Published versions are retained history; their project cannot be
    deleted (the FK ``ondelete=RESTRICT`` is the database-side backstop)."""

    code = "PROJECT_HAS_VERSIONS"
    http_status = 409


class ProjectValidationFailedError(AkashaError):
    code = "PROJECT_VALIDATION_FAILED"
    http_status = 422

    def __init__(self, result: ValidationResult) -> None:
        super().__init__(
            "The project draft contains validation errors.",
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


def _check_revision(project: Project, expected: int) -> None:
    if project.revision != expected:
        raise ConcurrentModificationError(
            "The project was modified since it was read; reload and retry.",
            details={
                "project_id": project.id,
                "expected_revision": expected,
                "current_revision": project.revision,
            },
        )


class ProjectService:
    """Application service for the Project lifecycle.

    ``node_definitions`` is the node-type catalog drafts are validated
    against: ``NodeRegistry.definitions`` in production (a live view), any
    mapping in tests. ``plugins`` (enabled plugin id → version) is
    ``PluginRegistry.versions`` in production; empty means none installed.
    """

    def __init__(
        self,
        uow: UowFactory,
        node_definitions: Mapping[str, NodeDefinition],
        events: EventPublisher | None = None,
        plugins: Mapping[str, str] = MappingProxyType({}),
    ) -> None:
        self._uow = uow
        self._definitions = node_definitions
        self._plugins = plugins
        self._events = events or EventPublisher()

    async def create_project(
        self, name: str, *, description: str = "", owner_id: str | None = None
    ) -> Project:
        project = Project.create(name, description=description, owner_id=owner_id)
        async with self._uow() as uow:
            await uow.projects.save(project)
            await uow.commit()
        _log("project_created", project)
        return project

    async def get_project(self, project_id: str) -> Project:
        async with self._uow() as uow:
            return await load_project(uow, project_id)

    async def list_projects(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        owner_id: str | None = None,
        search: str | None = None,
        statuses: Collection[ProjectStatus] = (),
        sort: ProjectSort = ProjectSort.CREATED,
    ) -> Page[Project]:
        """All projects, or only those of ``owner_id``; ``search`` matches
        a case-insensitive substring of the name."""
        check_page(offset, limit)
        async with self._uow() as uow:
            rows = await uow.projects.list(
                offset=offset,
                limit=limit + 1,
                owner_id=owner_id,
                search=search,
                statuses=statuses,
                sort=sort,
            )
        return to_page(rows, offset, limit)

    async def duplicate_project(
        self,
        project_id: str,
        *,
        name: str,
        owner_id: str | None = None,
        version: int | None = None,
    ) -> Project:
        """A new, never-published project holding a copy of the source's
        draft, or of its published ``version`` (graph, triggers,
        dependencies, description, files).

        Published versions are history of the source and are not copied.
        The copy goes through ``replace_draft``, so it obeys every rule a
        draft write does.
        """
        async with self._uow() as uow:
            source = await load_project(uow, project_id)
            draft = (
                deepcopy(source.draft)
                if version is None
                else (await load_version(uow, project_id, version)).to_draft()
            )
            project = Project.create(name, owner_id=owner_id)
            project.replace_draft(draft, name=name)
            await uow.projects.save(project)
            await uow.commit()
        _log(
            "project_duplicated",
            project,
            source_project_id=source.id,
            source_version=version,
        )
        return project

    async def restore_version(
        self, project_id: str, version: int, *, expected_revision: int
    ) -> Project:
        """Replace the draft with a copy of published ``version``.

        The version itself is untouched; publishing the restored draft
        creates a new version. ``expected_revision`` guards the draft being
        replaced, like any draft write: unsaved work elsewhere is a 409.
        """
        async with self._uow() as uow:
            project = await load_project(uow, project_id)
            _check_revision(project, expected_revision)
            snapshot = await load_version(uow, project_id, version)
            project.replace_draft(snapshot.to_draft(), name=project.name)
            await uow.projects.save(project)
            await uow.commit()
        _log("project_version_restored", project, version=version)
        return project

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
        project_id: str,
        *,
        expected_revision: int,
        name: str,
        draft: ProjectDraft,
    ) -> Project:
        """Replace name and draft. Accepts semantically invalid drafts — a
        draft may be work in progress; only publish demands validity.

        Project files are kept, whatever ``draft.files`` holds: the draft
        API does not carry them, and only imports set them (Phase 18).
        """
        async with self._uow() as uow:
            project = await load_project(uow, project_id)
            _check_revision(project, expected_revision)
            project.replace_draft(replace(draft, files=project.draft.files), name=name)
            await uow.projects.save(project)
            await uow.commit()
        _log("project_draft_updated", project)
        return project

    async def delete_project(self, project_id: str) -> None:
        async with self._uow() as uow:
            project = await load_project(uow, project_id)
            if project.current_version > 0:
                raise ProjectHasVersionsError(
                    "A project with published versions cannot be deleted.",
                    details={"project_id": project.id},
                )
            # CAS delete: a publish racing this request makes it a conflict,
            # never a half-deleted project or an FK error surfacing as 500.
            if not await uow.projects.delete(project.id, revision=project.revision):
                raise ConcurrentModificationError(
                    "The project was modified concurrently; reload and retry.",
                    details={"project_id": project.id},
                )
            await uow.commit()
        _log("project_deleted", project)

    async def projects_using_plugin(
        self, plugin_id: str, node_types: Sequence[str]
    ) -> Sequence[str]:
        """Projects whose draft or published history needs the plugin."""
        async with self._uow() as uow:
            return await uow.projects.using_plugin(plugin_id, node_types)

    async def validate_project(
        self, project_id: str, *, expected_revision: int | None = None
    ) -> ValidationResult:
        """Validate the stored draft. ``expected_revision`` pins the result to
        the draft the caller shows, so it never describes a newer one."""
        project = await self.get_project(project_id)
        if expected_revision is not None:
            _check_revision(project, expected_revision)
        return project.draft.validate(self._definitions, self._plugins)

    async def publish_project(
        self,
        project_id: str,
        *,
        expected_revision: int | None = None,
        published_by: str | None = None,
    ) -> ProjectVersion:
        """Validate the draft and freeze it as the next immutable version.

        ``expected_revision`` pins publish to the draft the caller actually
        reviewed. Counter update and version insert share one transaction:
        either both commit or neither does.
        """
        async with self._uow() as uow:
            project = await load_project(uow, project_id)
            if expected_revision is not None:
                _check_revision(project, expected_revision)
            result = project.draft.validate(self._definitions, self._plugins)
            if not result.ok:
                raise ProjectValidationFailedError(result)
            version = project.publish_draft(published_by=published_by)
            published = Event(
                event_type=EventType.PROJECT_PUBLISHED,
                occurred_at=version.published_at,
                project_id=project.id,
                project_version=version.version,
            )
            await uow.projects.save(project)
            await uow.versions.save(project.id, version)
            await uow.events.append([published])
            await uow.commit()
        await self._events.publish([published])
        _log("project_published", project, version=version.version)
        return version

    async def get_version(self, project_id: str, version: int) -> ProjectVersion:
        async with self._uow() as uow:
            return await load_version(uow, project_id, version)

    async def list_versions(
        self, project_id: str, *, offset: int = 0, limit: int = 50
    ) -> Page[ProjectVersion]:
        check_page(offset, limit)
        async with self._uow() as uow:
            await load_project(uow, project_id)
            rows = await uow.versions.list(project_id, offset=offset, limit=limit + 1)
        return to_page(rows, offset, limit)


async def load_project(uow: UnitOfWork, project_id: str) -> Project:
    project = await uow.projects.get(project_id)
    if project is None:
        raise ProjectNotFoundError(
            "Project not found.", details={"project_id": project_id}
        )
    return project


async def load_version(
    uow: UnitOfWork, project_id: str, version: int
) -> ProjectVersion:
    found = await uow.versions.get(project_id, version)
    if found is None:
        raise ProjectVersionNotFoundError(
            "Project version not found.",
            details={"project_id": project_id, "version": version},
        )
    return found


def _log(event: str, project: Project, **extra: object) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "project_id": project.id,
            "revision": project.revision,
            **extra,
        },
    )
