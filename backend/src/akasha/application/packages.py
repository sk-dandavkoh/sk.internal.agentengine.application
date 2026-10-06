"""Workflow import / export use cases (roadmap Phase 18).

- WorkflowExporter      ``export_workflow``: draft or published version →
                       package bytes (``workflow_package.write_package``)
- WorkflowImporter      ``import_workflow``: package bytes → a new workflow
                       whose draft is the package content
- DependencyValidator  ``_requirement_issues`` plus the workflow dependency
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
from akasha.application.workflow_package import (
    NodeRequirement,
    Requirements,
    WorkflowPackage,
    WorkflowPackageIncompatibleError,
    credential_names,
    read_package,
    write_package,
)
from akasha.application.workflows import (
    UowFactory,
    WorkflowVersionNotFoundError,
    load_workflow,
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
from akasha.domain.workflow import (
    Workflow,
    WorkflowDraft,
    plaintext_secret_locations,
    validate_dependencies,
)
from akasha.errors import DomainValidationError

logger = logging.getLogger("akasha.application.packages")


@dataclass(frozen=True)
class ImportResult:
    workflow: Workflow
    validation: ValidationResult
    # Referenced credentials this installation does not have yet (names
    # only). Not an error: the operator creates them; until then a run
    # fails with a structured credential error.
    missing_credentials: tuple[str, ...]


class WorkflowPackageService:
    """``node_definitions`` and ``plugins`` are the same live views
    ``WorkflowService`` validates against."""

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

    async def export_workflow(
        self, workflow_id: str, *, version: int | None = None
    ) -> bytes:
        """The package of published ``version``, or of the draft if None."""
        async with self._uow() as uow:
            workflow = await load_workflow(uow, workflow_id)
            if version is None:
                draft = workflow.draft
            else:
                found = await uow.versions.get(workflow_id, version)
                if found is None:
                    raise WorkflowVersionNotFoundError(
                        "Workflow version not found.",
                        details={"workflow_id": workflow_id, "version": version},
                    )
                draft = WorkflowDraft(
                    graph=found.graph,
                    triggers=list(found.triggers),
                    dependencies=list(found.dependencies),
                    description=found.description,
                    files=dict(found.files),
                )
            data = write_package(
                WorkflowPackage(
                    name=workflow.name,
                    draft=draft,
                    requires=self._requirements(draft),
                    engine_version=self._engine_version,
                    source_workflow_version=version,
                )
            )
            exported = Event(
                event_type=EventType.WORKFLOW_EXPORTED,
                occurred_at=utcnow(),
                workflow_id=workflow.id,
                workflow_version=version,
            )
            await uow.events.append([exported])
            await uow.commit()
        await self._events.publish([exported])
        logger.info(
            "workflow_exported",
            extra={
                "event": "workflow_exported",
                "workflow_id": workflow.id,
                "workflow_version": version,
                "package_bytes": len(data),
            },
        )
        return data

    async def import_workflow(
        self, data: bytes, *, owner_id: str | None = None
    ) -> ImportResult:
        package = read_package(data, engine_version=self._engine_version)
        draft = package.draft
        issues = self._requirement_issues(package.requires) + validate_dependencies(
            draft.dependencies, self._definitions, self._plugins
        )
        if issues:
            raise WorkflowPackageIncompatibleError(
                "This installation does not provide what the workflow requires.",
                issues=issues,
            )
        workflow = Workflow.create(
            package.name, description=draft.description, owner_id=owner_id
        )
        # The same write gate as every draft: file rules, no secret values.
        workflow.replace_draft(draft, name=package.name)
        async with self._uow() as uow:
            missing = tuple(
                [
                    name
                    for name in package.requires.credentials
                    if await uow.credentials.get(name) is None
                ]
            )
            imported = Event(
                event_type=EventType.WORKFLOW_IMPORTED,
                occurred_at=utcnow(),
                workflow_id=workflow.id,
            )
            await uow.workflows.save(workflow)
            await uow.events.append([imported])
            await uow.commit()
        await self._events.publish([imported])
        logger.info(
            "workflow_imported",
            extra={
                "event": "workflow_imported",
                "workflow_id": workflow.id,
                "revision": workflow.revision,
                "source_engine_version": package.engine_version,
            },
        )
        return ImportResult(
            workflow=workflow,
            validation=workflow.draft.validate(self._definitions, self._plugins),
            missing_credentials=missing,
        )

    def _requirements(self, draft: WorkflowDraft) -> Requirements:
        leaks = plaintext_secret_locations(draft.graph, draft.triggers, draft.files)
        if leaks:
            # Refuse, never strip: a silently altered export would import
            # as a different workflow. Reachable only for content stored
            # before Phase 13 enforced references.
            raise PlaintextSecretError(
                "The workflow contains secret values and cannot be exported;"
                " replace them with credential references first.",
                details={"paths": leaks},
            )
        nodes = []
        for node_type in sorted({n.node_type for n in draft.graph.nodes.values()}):
            definition = self._definitions.get(node_type)
            if definition is None:
                raise WorkflowPackageIncompatibleError(
                    "The workflow uses a node type that is not installed, so its"
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
