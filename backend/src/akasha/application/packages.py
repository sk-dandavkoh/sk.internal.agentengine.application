"""Project import / export use cases (roadmap Phase 18).

- ProjectExporter      ``export_project``: draft or published version →
                       package bytes (``project_package.write_package``)
- ProjectImporter      ``import_project``: package bytes → a new project
                       whose draft is the package content
- DependencyValidator  ``_requirement_issues`` plus the project dependency
                       rules (``validate_dependencies``)

Secrets never leave: configs hold credential references only, and export
re-checks the content with the same rule drafts are saved under
(``plaintext_secret_locations``); it refuses rather than strips. Credential
values, the encryption key and any session state are never read here.

Import is all-or-nothing and strict about what the installation must
provide: an unsupported format/schema version, a missing or incompatible
node type, or an unmet declared dependency (node or plugin) rejects the
package. Other graph findings (e.g. a missing edge in an exported
work-in-progress draft) do not: the imported draft is returned with its
validation result, like any draft, and publishing stays gated as usual.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from akasha import __version__ as ENGINE_VERSION
from akasha.application.events import EventPublisher
from akasha.application.project_package import (
    NodeRequirement,
    ProjectPackage,
    ProjectPackageIncompatibleError,
    Requirements,
    credential_names,
    read_package,
    write_package,
)
from akasha.application.projects import (
    ProjectVersionNotFoundError,
    UowFactory,
    load_project,
)
from akasha.domain.credentials import PlaintextSecretError
from akasha.domain.event import Event, EventType
from akasha.domain.graph_validation import (
    ValidationCode,
    ValidationIssue,
    ValidationResult,
)
from akasha.domain.node import NodeDefinition
from akasha.domain.primitives import semver_key, utcnow, version_satisfies
from akasha.domain.project import (
    Project,
    ProjectDraft,
    plaintext_secret_locations,
    validate_dependencies,
)
from akasha.errors import DomainValidationError

logger = logging.getLogger("akasha.application.packages")


@dataclass(frozen=True)
class ImportResult:
    project: Project
    validation: ValidationResult
    # Referenced credentials this installation does not have yet (names
    # only). Not an error: the operator creates them; until then a run
    # fails with a structured credential error.
    missing_credentials: tuple[str, ...]


class ProjectPackageService:
    """``node_definitions`` and ``plugins`` are the same live views
    ``ProjectService`` validates against."""

    def __init__(
        self,
        uow: UowFactory,
        node_definitions: Mapping[str, NodeDefinition],
        events: EventPublisher | None = None,
        plugins: Mapping[str, str] = MappingProxyType({}),
        engine_version: str = ENGINE_VERSION,
    ) -> None:
        self._uow = uow
        self._definitions = node_definitions
        self._plugins = plugins
        self._events = events or EventPublisher()
        self._engine_version = engine_version

    async def export_project(
        self, project_id: str, *, version: int | None = None
    ) -> bytes:
        """The package of published ``version``, or of the draft if None."""
        async with self._uow() as uow:
            project = await load_project(uow, project_id)
            if version is None:
                draft = project.draft
            else:
                found = await uow.versions.get(project_id, version)
                if found is None:
                    raise ProjectVersionNotFoundError(
                        "Project version not found.",
                        details={"project_id": project_id, "version": version},
                    )
                draft = ProjectDraft(
                    graph=found.graph,
                    triggers=list(found.triggers),
                    dependencies=list(found.dependencies),
                    description=found.description,
                    files=dict(found.files),
                )
            data = write_package(
                ProjectPackage(
                    name=project.name,
                    draft=draft,
                    requires=self._requirements(draft),
                    engine_version=self._engine_version,
                    source_project_version=version,
                )
            )
            exported = Event(
                event_type=EventType.PROJECT_EXPORTED,
                occurred_at=utcnow(),
                project_id=project.id,
                project_version=version,
            )
            await uow.events.append([exported])
            await uow.commit()
        await self._events.publish([exported])
        logger.info(
            "project_exported",
            extra={
                "event": "project_exported",
                "project_id": project.id,
                "project_version": version,
                "package_bytes": len(data),
            },
        )
        return data

    async def import_project(self, data: bytes) -> ImportResult:
        package = read_package(data, engine_version=self._engine_version)
        draft = package.draft
        issues = self._requirement_issues(package.requires) + validate_dependencies(
            draft.dependencies, self._definitions, self._plugins
        )
        if issues:
            raise ProjectPackageIncompatibleError(
                "This installation does not provide what the project requires.",
                issues=issues,
            )
        project = Project.create(package.name, description=draft.description)
        # The same write gate as every draft: file rules, no secret values.
        project.replace_draft(draft, name=package.name)
        async with self._uow() as uow:
            missing = tuple(
                [
                    name
                    for name in package.requires.credentials
                    if await uow.credentials.get(name) is None
                ]
            )
            imported = Event(
                event_type=EventType.PROJECT_IMPORTED,
                occurred_at=utcnow(),
                project_id=project.id,
            )
            await uow.projects.save(project)
            await uow.events.append([imported])
            await uow.commit()
        await self._events.publish([imported])
        logger.info(
            "project_imported",
            extra={
                "event": "project_imported",
                "project_id": project.id,
                "revision": project.revision,
                "source_engine_version": package.engine_version,
            },
        )
        return ImportResult(
            project=project,
            validation=project.draft.validate(self._definitions, self._plugins),
            missing_credentials=missing,
        )

    def _requirements(self, draft: ProjectDraft) -> Requirements:
        leaks = plaintext_secret_locations(draft.graph, draft.triggers, draft.files)
        if leaks:
            # Refuse, never strip: a silently altered export would import
            # as a different project. Reachable only for content stored
            # before Phase 13 enforced references.
            raise PlaintextSecretError(
                "The project contains secret values and cannot be exported;"
                " replace them with credential references first.",
                details={"paths": leaks},
            )
        nodes = []
        for node_type in sorted({n.node_type for n in draft.graph.nodes.values()}):
            definition = self._definitions.get(node_type)
            if definition is None:
                raise ProjectPackageIncompatibleError(
                    "The project uses a node type that is not installed, so its"
                    " requirements cannot be recorded.",
                    details={"node_type": node_type},
                )
            nodes.append(
                NodeRequirement(node_type=node_type, version=definition.version)
            )
        configs = [n.config for n in draft.graph.nodes.values()]
        configs += [t.config for t in draft.triggers]
        return Requirements(nodes=nodes, credentials=sorted(credential_names(configs)))

    def _requirement_issues(
        self, requires: Requirements
    ) -> tuple[ValidationIssue, ...]:
        """Every node type the package needs must be installed in a
        SemVer-compatible version: same major, at least the exported one."""
        issues = []
        for req in requires.nodes:
            definition = self._definitions.get(req.node_type)
            if definition is None:
                issues.append(
                    ValidationIssue(
                        ValidationCode.MISSING_DEPENDENCY,
                        f"required node type {req.node_type!r} is not installed",
                    )
                )
                continue
            try:
                major = semver_key(req.version)[0]
                ok = version_satisfies(
                    definition.version, f">={req.version},<{major + 1}.0.0"
                )
            except DomainValidationError:
                ok = None
            if ok is None:
                issues.append(
                    ValidationIssue(
                        ValidationCode.INVALID_DEPENDENCY,
                        f"malformed version for node type {req.node_type!r}",
                    )
                )
            elif not ok:
                issues.append(
                    ValidationIssue(
                        ValidationCode.INCOMPATIBLE_DEPENDENCY,
                        f"node type {req.node_type!r} {definition.version} is not"
                        f" compatible with {req.version}",
                    )
                )
        return tuple(issues)
